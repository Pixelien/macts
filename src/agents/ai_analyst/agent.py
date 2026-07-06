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
        """Tek sembol için LLM analizi üret ve yayınla.

        # TODO(Faz 3 / Paket 2) Implementation roadmap:
        # 1. src/core/llm/nvidia_client.py üzerinden birincil modele istek
        #    (nvidia/nemotron-3-super-120b-a12b, max_tokens>=4096, temp=0.2)
        # 2. rate_limiter (30 RPM token bucket) + backoff + Redis cache
        # 3. fallback_chain: 5xx -> deepseek-ai/deepseek-v4-pro; 429 -> skip
        #    (429'da fallback denenmez — kota anahtar bazında global, rapor §2)
        # 4. Pydantic parse (AIAnalysis) -> stream:ai_analysis.{symbol} publish
        # 5. usage_tracker: Postgres llm_usage_log kaydı
        # 6. Circuit breaker: ardışık hata eşiği aşılırsa
        #    stream:circuit_breaker.events üzerinden merkezi kesinti
        """
        features = self._latest_features.get(symbol)
        if features is None:
            AI_ANALYSES_TOTAL.labels(
                symbol=symbol, model_id="none", status="skipped"
            ).inc()
            self.logger.debug("analysis_skipped_no_features", symbol=symbol)
            return

        # Paket 1 stub: LLM entegrasyonu henüz bağlı değil. Sahte analiz
        # YAYINLANMAZ — signal_generation'a asla uydurma veri akmamalı.
        AI_ANALYSES_TOTAL.labels(
            symbol=symbol, model_id="stub", status="skipped"
        ).inc()
        self.logger.info(
            "analysis_stub",
            symbol=symbol,
            note="LLM istemci katmanı Paket 2'de bağlanacak",
        )


if __name__ == "__main__":
    asyncio.run(run_agent(AIAnalystAgent))
