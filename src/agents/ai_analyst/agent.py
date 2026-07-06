"""AI Analyst Agent (Faz 3).

Feature Engineering çıktısını LLM ile yorumlayıp yapılandırılmış analiz
üreten katman. Model seçimi ve kota stratejisi:
docs/AI_ANALYST_MODEL_SELECTION.md (canlı probe ile doğrulanmış).

Veri akışı:
    stream:features.{symbol}  --tüketir-->  [AI Analyst]
    [AI Analyst]  --yayınlar-->  stream:ai_analysis.{symbol}
    (+ InfluxDB ham çıktı, Postgres llm_usage_log, MLflow prompt run — Paket 2/3)

Feature flag:
    ENABLE_AI_ANALYST=false (varsayılan) -> agent boşta bekler, sistem
    Faz 2 davranışıyla aynen devam eder. true -> analiz döngüsü aktif.

Kota tasarımı:
    Sembol başına AI_ANALYST_INTERVAL_SECONDS'ta (varsayılan 900 = 15 dk)
    bir analiz; StaggeredScheduler burst'ü engeller. Dakikalık sinyal
    üretimi HER ZAMAN ML modelinden akar — LLM analizi zenginleştiricidir,
    bloklamaz (bkz. rapor §5).

Model konfigürasyonu (probe ile doğrulanmış ID'ler):
    Birincil : nvidia/nemotron-3-super-120b-a12b
    Yedek    : deepseek-ai/deepseek-v4-pro
    Deneysel : z-ai/glm-5.2 (bandit, Aşama 4)

Tüketim:
- stream:universe.snapshot       -> takip edilecek semboller
- stream:features.{symbol}       -> son feature snapshot (sembol başına)

Yayınladığı:
- stream:ai_analysis.{symbol}    -> AIAnalysis mesajı (src/models/schemas.py)
- stream:heartbeats              -> BaseAgent standart heartbeat
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from prometheus_client import Counter, Gauge

from src.agents.ai_analyst.scheduler import StaggeredScheduler
from src.agents.base import BaseAgent, run_agent
from src.core.database.postgres_repo import PostgresRepository
from src.core.database.redis_cache import RedisCache
from src.core.llm import (
    AllModelsFailedError,
    AnalysisCache,
    FallbackChain,
    LLMModelConfig,
    NIMQuotaError,
    NIMRateLimitError,
    NvidiaNIMClient,
    TokenBucketLimiter,
    UsageTracker,
    build_cache_key,
    build_messages,
    extract_json,
    load_llm_config,
    load_prompt,
)
from src.models.schemas import AIAnalysis

# Stream isimleri (feature_engineering konvansiyonuyla aynı)
STREAM_UNIVERSE_SNAPSHOT = "stream:universe.snapshot"


def features_stream(symbol: str) -> str:
    return f"stream:features.{symbol.lower()}"


def ai_analysis_stream(symbol: str) -> str:
    return f"stream:ai_analysis.{symbol.lower()}"


# Env tabanlı konfigürasyon (Paket 2'de config/llm_config.yaml'a taşınacak)
DEFAULT_INTERVAL_SECONDS = 900.0   # 15 dk — rapor §5 kota bütçesi
SCHEDULER_TICK_SECONDS = 5.0       # due kontrol sıklığı


def parse_bool_env(value: str | None) -> bool:
    """ENABLE_AI_ANALYST gibi bool env değerlerini güvenli parse et."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


# =============================================================================
# Prometheus Metrikleri
# =============================================================================

AI_ANALYST_ENABLED = Gauge(
    "macts_ai_analyst_enabled",
    "AI Analyst feature flag durumu (1=aktif, 0=kapalı)",
)

AI_ANALYSES_TOTAL = Counter(
    "macts_ai_analyst_analyses_total",
    "Üretilen analiz sayısı",
    ["symbol", "model_id", "status"],  # status: ok | error | skipped
)

AI_TRACKED_SYMBOLS = Gauge(
    "macts_ai_analyst_tracked_symbols",
    "Zamanlayıcıda takip edilen sembol sayısı",
)


class AIAnalystAgent(BaseAgent):
    """AI Analyst Agent."""

    agent_name = "ai_analyst"
    heartbeat_interval = 5.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._enabled: bool = False
        self._interval: float = DEFAULT_INTERVAL_SECONDS
        self._scheduler: StaggeredScheduler | None = None
        self._latest_features: dict[str, dict[str, Any]] = {}
        self._feature_listeners: dict[str, asyncio.Task[None]] = {}
        # LLM katmanı (yalnızca flag açıkken kurulur)
        self._llm_config = None
        self._llm_client: NvidiaNIMClient | None = None
        self._chain: FallbackChain | None = None
        self._limiter: TokenBucketLimiter | None = None
        self._analysis_cache: AnalysisCache | None = None
        self._usage: UsageTracker | None = None
        self._prompt = None
        self._llm_ready = False

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def _initialize(self) -> None:
        """Feature flag ve zamanlayıcıyı hazırla."""
        self._enabled = parse_bool_env(os.environ.get("ENABLE_AI_ANALYST"))
        self._interval = float(
            os.environ.get("AI_ANALYST_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)
        )
        self._scheduler = StaggeredScheduler(interval_seconds=self._interval)

        AI_ANALYST_ENABLED.set(1 if self._enabled else 0)
        self.logger.info(
            "ai_analyst_initializing",
            enabled=self._enabled,
            interval_seconds=self._interval,
        )

        if not self._enabled:
            self.logger.info(
                "ai_analyst_disabled",
                hint="Aktive etmek için ENABLE_AI_ANALYST=true (env) ayarlayın",
            )
            return

        await self._setup_llm_stack()

    async def _setup_llm_stack(self) -> None:
        """LLM istemci katmanını kur (config, client, limiter, cache, tracker)."""
        self._llm_config = load_llm_config()
        api_key = self._llm_config.api_key
        if not api_key:
            # Anahtar yoksa çökmek yerine devre dışı kal — sistem Faz 2
            # davranışıyla devam eder, operatör loglardan durumu görür.
            self.logger.error(
                "nvidia_api_key_missing",
                hint=".env dosyasına NVIDIA_API_KEY ekleyin",
            )
            self._llm_ready = False
            return

        self._llm_client = NvidiaNIMClient(
            api_key=api_key,
            base_url=self._llm_config.base_url,
            timeout_seconds=self._llm_config.request_timeout_seconds,
        )
        self._chain = FallbackChain(
            [self._llm_config.primary, self._llm_config.fallback]
        )
        self._limiter = TokenBucketLimiter(
            requests_per_minute=self._llm_config.rate_limit.requests_per_minute,
            requests_per_day=self._llm_config.rate_limit.requests_per_day,
        )
        self._prompt = load_prompt(self._llm_config.prompt_version)

        # Redis cache (best-effort — bağlanamazsa cache'siz devam)
        try:
            rc = RedisCache(
                host=os.environ.get("REDIS_HOST", "redis"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                password=os.environ.get("REDIS_PASSWORD") or None,
            )
            await rc.connect()
            self._analysis_cache = AnalysisCache(
                rc, ttl_seconds=self._llm_config.cache.ttl_seconds
            )
        except Exception as e:
            self.logger.warning("llm_cache_unavailable", error=str(e))
            self._analysis_cache = None

        # Usage tracker (best-effort)
        try:
            repo = PostgresRepository(
                host=os.environ.get("POSTGRES_HOST", "postgres"),
                port=int(os.environ.get("POSTGRES_PORT", "5432")),
                database=os.environ.get("POSTGRES_DB", "macts"),
                user=os.environ.get("POSTGRES_USER", "macts_user"),
                password=os.environ.get("POSTGRES_PASSWORD", ""),
            )
            await repo.connect()
            self._usage = UsageTracker(repo)
            await self._usage.ensure_table()
        except Exception as e:
            self.logger.warning("llm_usage_tracker_unavailable", error=str(e))
            self._usage = None

        self._llm_ready = True
        self.logger.info(
            "llm_stack_ready",
            primary=self._llm_config.primary.model_id,
            fallback=self._llm_config.fallback.model_id,
            rpm_cap=self._llm_config.rate_limit.requests_per_minute,
            prompt_version=self._llm_config.prompt_version,
        )

    async def _run(self) -> None:
        """Ana iş döngüsü."""
        if not self._enabled:
            # Feature flag kapalı: container healthy kalır, hiçbir şey yapmaz.
            # Sistem Faz 2 davranışıyla aynen devam eder.
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue
            return

        self.logger.info("ai_analyst_loop_started")

        # Universe dinleyicisi arka planda
        self._tasks.append(asyncio.create_task(self._universe_listener()))

        # Zamanlayıcı döngüsü
        while not self._stop_event.is_set():
            now = time.time()
            assert self._scheduler is not None
            for symbol in self._scheduler.due_symbols(now):
                await self._analyze_symbol(symbol)
                self._scheduler.mark_ran(symbol, now=time.time())

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=SCHEDULER_TICK_SECONDS
                )
            except asyncio.TimeoutError:
                continue

    async def _shutdown(self) -> None:
        """Kaynak temizliği."""
        for task in self._feature_listeners.values():
            if not task.done():
                task.cancel()
        if self._feature_listeners:
            await asyncio.gather(
                *self._feature_listeners.values(), return_exceptions=True
            )
        if self._llm_client is not None:
            await self._llm_client.close()
        self.logger.info("ai_analyst_shutting_down")

    async def _health_check(self) -> dict[str, float]:
        return {
            "enabled": 1.0 if self._enabled else 0.0,
            "tracked_symbols": float(
                self._scheduler.tracked_count if self._scheduler else 0
            ),
            "symbols_with_features": float(len(self._latest_features)),
        }

    # =========================================================================
    # Universe & Feature Tüketimi
    # =========================================================================

    async def _universe_listener(self) -> None:
        """Universe snapshot'larını dinle, zamanlayıcı ve listener'ları eşitle."""
        try:
            async for msg in self.redis.subscribe(
                STREAM_UNIVERSE_SNAPSHOT, from_beginning=True
            ):
                if self._stop_event.is_set():
                    break
                await self._apply_universe(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.exception("universe_listener_failed", error=str(e))

    async def _apply_universe(self, msg: dict[str, Any]) -> None:
        """Universe değişimini uygula (feature_engineering deseniyle aynı parse)."""
        symbols_raw = msg.get("symbols")
        if symbols_raw is None:
            return
        if isinstance(symbols_raw, str):
            try:
                symbols = set(json.loads(symbols_raw))
            except json.JSONDecodeError:
                return
        elif isinstance(symbols_raw, list):
            symbols = set(symbols_raw)
        else:
            return

        assert self._scheduler is not None
        added, removed = self._scheduler.sync_universe(symbols, now=time.time())
        AI_TRACKED_SYMBOLS.set(self._scheduler.tracked_count)

        for symbol in added:
            self._feature_listeners[symbol] = asyncio.create_task(
                self._feature_listener(symbol)
            )
        for symbol in removed:
            task = self._feature_listeners.pop(symbol, None)
            if task and not task.done():
                task.cancel()
            self._latest_features.pop(symbol, None)

        if added or removed:
            self.logger.info(
                "ai_analyst_universe_changed",
                added=sorted(added),
                removed=sorted(removed),
                new_size=len(symbols),
            )

    async def _feature_listener(self, symbol: str) -> None:
        """Bir sembolün feature stream'ini dinle, son snapshot'ı sakla."""
        stream = features_stream(symbol)
        try:
            async for msg in self.redis.subscribe(stream, from_beginning=False):
                if self._stop_event.is_set():
                    break
                self._latest_features[symbol] = msg
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.exception(
                "feature_listener_failed", symbol=symbol, error=str(e)
            )

    # =========================================================================
    # Analiz (Paket 2'de LLM istemcisine bağlanacak)
    # =========================================================================

    async def _analyze_symbol(self, symbol: str) -> None:
        """Tek sembol için LLM analizi üret ve stream:ai_analysis.{symbol}'e yayınla.

        Akış: cache -> rate limit -> fallback zinciri -> parse/validate ->
        publish -> usage kaydı. Hiçbir hata sinyal üretimini bloklamaz;
        başarısız tur sessizce atlanır (metrik + log ile izlenir).
        """
        features = self._latest_features.get(symbol)
        if features is None:
            AI_ANALYSES_TOTAL.labels(
                symbol=symbol, model_id="none", status="skipped"
            ).inc()
            return
        if not self._llm_ready:
            AI_ANALYSES_TOTAL.labels(
                symbol=symbol, model_id="none", status="skipped"
            ).inc()
            return

        assert self._llm_config and self._chain and self._limiter and self._prompt

        # 1) Cache kontrolü
        cache_key = build_cache_key(
            symbol, features, self._llm_config.prompt_version,
            self._llm_config.primary.model_id,
        )
        if self._analysis_cache is not None:
            cached = await self._analysis_cache.get(cache_key)
            if cached is not None:
                await self._publish_analysis(symbol, cached, cache_hit=True)
                return

        # 2) Rate limit (non-blocking: kota yoksa tur atlanır, BEKLENMEZ)
        if not self._limiter.try_acquire():
            AI_ANALYSES_TOTAL.labels(
                symbol=symbol, model_id="none", status="rate_limited"
            ).inc()
            self.logger.warning(
                "analysis_skipped_rate_limit",
                symbol=symbol,
                retry_in_seconds=round(self._limiter.seconds_until_available(), 1),
                daily_used=self._limiter.daily_used,
            )
            return

        # 3) Fallback zinciriyle çağrı
        messages = build_messages(self._prompt, features)

        async def call(model: LLMModelConfig):
            assert self._llm_client is not None
            return await self._llm_client.chat_completion(
                model.model_id, messages,
                max_tokens=model.max_tokens, temperature=model.temperature,
            )

        try:
            content, usage, latency, used_model = await self._chain.run(call)
        except (NIMRateLimitError, NIMQuotaError) as e:
            # Kota hataları: fallback yok (anahtar bazında global kota).
            AI_ANALYSES_TOTAL.labels(
                symbol=symbol, model_id="none", status="quota_error"
            ).inc()
            self.logger.warning("analysis_quota_error", symbol=symbol, error=str(e))
            await self._record_usage(symbol, "quota", None, None, False, str(e))
            return
        except AllModelsFailedError as e:
            AI_ANALYSES_TOTAL.labels(
                symbol=symbol, model_id="none", status="error"
            ).inc()
            self.logger.error("analysis_all_models_failed", symbol=symbol, error=str(e))
            await self._record_usage(symbol, "chain", None, None, False, str(e))
            return

        # 4) Parse + şema doğrulama (doğrulanamayan çıktı ASLA yayınlanmaz)
        try:
            payload = extract_json(content)
            payload.setdefault("symbol", symbol)
            analysis = AIAnalysis(
                **payload,
                model_id=used_model.model_id,
                prompt_version=self._llm_config.prompt_version,
                latency_seconds=round(latency, 3),
            )
        except Exception as e:
            AI_ANALYSES_TOTAL.labels(
                symbol=symbol, model_id=used_model.model_id, status="parse_error"
            ).inc()
            self.logger.warning(
                "analysis_parse_failed", symbol=symbol,
                model=used_model.model_id, error=str(e),
            )
            await self._record_usage(
                symbol, used_model.model_id, usage, latency, False, f"parse: {e}"
            )
            return

        # 5) Yayınla + cache'le + kaydet
        analysis_dict = analysis.model_dump(mode="json")
        await self._publish_analysis(symbol, analysis_dict, cache_hit=False)
        if self._analysis_cache is not None:
            await self._analysis_cache.set(cache_key, analysis_dict)
        await self._record_usage(
            symbol, used_model.model_id, usage, latency, True, None
        )
        AI_ANALYSES_TOTAL.labels(
            symbol=symbol, model_id=used_model.model_id, status="ok"
        ).inc()
        self.logger.info(
            "analysis_published", symbol=symbol, model=used_model.model_id,
            direction=analysis.direction, confidence=analysis.confidence,
            latency_s=round(latency, 2),
        )

    async def _publish_analysis(
        self, symbol: str, analysis: dict[str, Any], *, cache_hit: bool
    ) -> None:
        analysis = {**analysis, "cache_hit": cache_hit}
        await self.redis.publish(ai_analysis_stream(symbol), analysis)
        if cache_hit:
            AI_ANALYSES_TOTAL.labels(
                symbol=symbol, model_id="cache", status="ok"
            ).inc()

    async def _record_usage(
        self, symbol: str, model_id: str,
        usage: dict[str, Any] | None, latency: float | None,
        success: bool, error: str | None,
    ) -> None:
        if self._usage is None:
            return
        assert self._llm_config is not None
        await self._usage.record(
            symbol=symbol, model_id=model_id,
            prompt_version=self._llm_config.prompt_version,
            usage=usage, latency_seconds=latency,
            success=success, error=error,
        )


if __name__ == "__main__":
    asyncio.run(run_agent(AIAnalystAgent))
