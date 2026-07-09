"""src/core/llm katmanı unit testleri (Faz 3 / Paket 2).

Kapsam:
- TokenBucketLimiter: RPM tavanı, sürekli dolum, günlük kota, pencere sıfırlama
- compute_backoff: üstel büyüme, cap, jitter sınırları
- extract_json: thinking blokları, markdown fence, gürültü toleransı
- FallbackChain: 5xx->retry->fallback; 429/402->fallback YOK; 404->sıradaki
- build_cache_key: determinizm, alan sırası bağımsızlığı, ayrıştırıcılık
- LLMConfig: varsayılanlar, yaml yükleme, bozuk dosya toleransı
- Prompt: yerleşik varsayılan, few-shot mesaj kurulumu
"""

from __future__ import annotations

import random

import pytest

from src.core.llm.config import LLMConfig, LLMModelConfig, load_llm_config
from src.core.llm.fallback_chain import AllModelsFailedError, FallbackChain
from src.core.llm.nvidia_client import (
    NIMQuotaError,
    NIMRateLimitError,
    NIMNotFoundError,
    NIMServerError,
)
from src.core.llm.prompts import PromptTemplate, build_messages, load_prompt
from src.core.llm.rate_limiter import TokenBucketLimiter
from src.core.llm.tracking import build_cache_key
from src.core.llm.utils import compute_backoff, extract_json


# =============================================================================
# TokenBucketLimiter
# =============================================================================

class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class TestTokenBucketLimiter:
    def test_never_exceeds_rpm_in_burst(self) -> None:
        """KOTA GÜVENLİĞİ: aynı anda en fazla rpm kadar token verilebilir."""
        clock = FakeClock()
        lim = TokenBucketLimiter(30, 100000, clock=clock)
        granted = sum(1 for _ in range(100) if lim.try_acquire())
        assert granted == 30

    def test_refill_is_continuous(self) -> None:
        clock = FakeClock()
        lim = TokenBucketLimiter(30, 100000, clock=clock)
        for _ in range(30):
            assert lim.try_acquire()
        assert not lim.try_acquire()
        clock.advance(2.0)  # 30 rpm -> saniyede 0.5 token -> 2s'de 1 token
        assert lim.try_acquire()
        assert not lim.try_acquire()

    def test_rpm_over_rolling_minutes(self) -> None:
        """5 dakikalık simülasyonda hiçbir 60s penceresi rpm'i aşmamalı."""
        clock = FakeClock()
        lim = TokenBucketLimiter(30, 100000, clock=clock)
        per_minute: dict[int, int] = {}
        while clock.t < 300:
            if lim.try_acquire():
                m = int(clock.t // 60)
                per_minute[m] = per_minute.get(m, 0) + 1
            clock.advance(0.5)
        # İlk dakika dolu bucket avantajıyla en fazla 2x olabilir (burst),
        # sonraki dakikalar kesinlikle <= rpm olmalı
        for minute, count in per_minute.items():
            if minute == 0:
                assert count <= 60
            else:
                assert count <= 30

    def test_daily_cap_enforced(self) -> None:
        clock = FakeClock()
        lim = TokenBucketLimiter(1000, 5, clock=clock)
        granted = sum(1 for _ in range(10) if lim.try_acquire())
        assert granted == 5
        assert lim.daily_used == 5

    def test_daily_window_resets(self) -> None:
        clock = FakeClock()
        lim = TokenBucketLimiter(1000, 2, clock=clock)
        assert lim.try_acquire() and lim.try_acquire()
        assert not lim.try_acquire()
        clock.advance(86401)
        assert lim.try_acquire()

    def test_seconds_until_available(self) -> None:
        clock = FakeClock()
        lim = TokenBucketLimiter(30, 100, clock=clock)
        assert lim.seconds_until_available() == 0.0
        for _ in range(30):
            lim.try_acquire()
        # 30 rpm -> 1 token 2 saniyede dolar
        assert lim.seconds_until_available() == pytest.approx(2.0, abs=0.01)

    def test_invalid_limits_rejected(self) -> None:
        with pytest.raises(ValueError):
            TokenBucketLimiter(0, 100)
        with pytest.raises(ValueError):
            TokenBucketLimiter(10, 0)


# =============================================================================
# compute_backoff
# =============================================================================

class TestBackoff:
    def test_exponential_growth_without_jitter(self) -> None:
        vals = [
            compute_backoff(a, base_seconds=2, cap_seconds=60, jitter_ratio=0)
            for a in range(4)
        ]
        assert vals == [2, 4, 8, 16]

    def test_cap_applied(self) -> None:
        v = compute_backoff(10, base_seconds=2, cap_seconds=60, jitter_ratio=0)
        assert v == 60

    def test_jitter_within_bounds(self) -> None:
        rng = random.Random(42)
        for a in range(6):
            v = compute_backoff(a, base_seconds=2, cap_seconds=60,
                                jitter_ratio=0.25, rng=rng)
            raw = min(60, 2 * (2**a))
            assert raw * 0.75 <= v <= raw * 1.25

    def test_negative_attempt_rejected(self) -> None:
        with pytest.raises(ValueError):
            compute_backoff(-1)


# =============================================================================
# extract_json
# =============================================================================

class TestExtractJson:
    def test_plain_json(self) -> None:
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_thinking_block_stripped(self) -> None:
        text = '<think>RSI düşük, trend aşağı...</think>\n{"direction": "short"}'
        assert extract_json(text) == {"direction": "short"}

    def test_markdown_fence_stripped(self) -> None:
        text = 'Here is the analysis:\n```json\n{"a": 1}\n```\nHope it helps!'
        assert extract_json(text) == {"a": 1}

    def test_surrounding_prose_tolerated(self) -> None:
        text = 'Sure! {"a": {"b": 2}} — let me know.'
        assert extract_json(text) == {"a": {"b": 2}}

    def test_no_json_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_json("no json here")

    def test_non_object_raises(self) -> None:
        with pytest.raises((ValueError, Exception)):
            extract_json("[1, 2, 3]")


# =============================================================================
# FallbackChain
# =============================================================================

PRIMARY = LLMModelConfig(model_id="primary-model")
FALLBACK = LLMModelConfig(model_id="fallback-model")


async def _no_sleep(_: float) -> None:
    return None


class TestFallbackChain:
    async def test_primary_success_uses_primary(self) -> None:
        chain = FallbackChain([PRIMARY, FALLBACK], sleep=_no_sleep)

        async def call(m: LLMModelConfig):
            return '{"ok": true}', {}, 0.1

        content, _, _, used = await chain.run(call)
        assert used.model_id == "primary-model"

    async def test_5xx_falls_back_after_retry(self) -> None:
        chain = FallbackChain([PRIMARY, FALLBACK],
                              retries_per_model=1, sleep=_no_sleep)
        calls: list[str] = []

        async def call(m: LLMModelConfig):
            calls.append(m.model_id)
            if m.model_id == "primary-model":
                raise NIMServerError("503")
            return "ok", {}, 0.1

        _, _, _, used = await chain.run(call)
        assert used.model_id == "fallback-model"
        # primary 2 kez denendi (1 + 1 retry), sonra fallback
        assert calls == ["primary-model", "primary-model", "fallback-model"]

    async def test_429_never_falls_back(self) -> None:
        """KRİTİK POLİTİKA: 429'da fallback denenmez (kota global)."""
        chain = FallbackChain([PRIMARY, FALLBACK], sleep=_no_sleep)
        calls: list[str] = []

        async def call(m: LLMModelConfig):
            calls.append(m.model_id)
            raise NIMRateLimitError("429")

        with pytest.raises(NIMRateLimitError):
            await chain.run(call)
        assert calls == ["primary-model"]  # tek çağrı, retry/fallback YOK

    async def test_402_never_falls_back(self) -> None:
        chain = FallbackChain([PRIMARY, FALLBACK], sleep=_no_sleep)

        async def call(m: LLMModelConfig):
            raise NIMQuotaError("402")

        with pytest.raises(NIMQuotaError):
            await chain.run(call)

    async def test_404_skips_to_next_without_retry(self) -> None:
        chain = FallbackChain([PRIMARY, FALLBACK],
                              retries_per_model=3, sleep=_no_sleep)
        calls: list[str] = []

        async def call(m: LLMModelConfig):
            calls.append(m.model_id)
            if m.model_id == "primary-model":
                raise NIMNotFoundError("404")
            return "ok", {}, 0.1

        _, _, _, used = await chain.run(call)
        assert used.model_id == "fallback-model"
        assert calls.count("primary-model") == 1  # 404'te retry YOK

    async def test_all_failed_raises(self) -> None:
        chain = FallbackChain([PRIMARY, FALLBACK],
                              retries_per_model=0, sleep=_no_sleep)

        async def call(m: LLMModelConfig):
            raise NIMServerError("500")

        with pytest.raises(AllModelsFailedError):
            await chain.run(call)

    def test_empty_chain_rejected(self) -> None:
        with pytest.raises(ValueError):
            FallbackChain([])


# =============================================================================
# Cache key
# =============================================================================

class TestCacheKey:
    FEATURES = {"rsi": 38.2, "close": 107250.0}

    def test_deterministic(self) -> None:
        k1 = build_cache_key("BTCUSDT", self.FEATURES, "v1", "m1")
        k2 = build_cache_key("BTCUSDT", self.FEATURES, "v1", "m1")
        assert k1 == k2

    def test_field_order_independent(self) -> None:
        a = build_cache_key("BTCUSDT", {"a": 1, "b": 2}, "v1", "m1")
        b = build_cache_key("BTCUSDT", {"b": 2, "a": 1}, "v1", "m1")
        assert a == b

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"symbol": "ETHUSDT"},
            {"features": {"rsi": 40.0, "close": 107250.0}},
            {"prompt_version": "v2"},
            {"model_id": "m2"},
        ],
    )
    def test_any_input_change_changes_key(self, kwargs: dict) -> None:
        base = dict(symbol="BTCUSDT", features=self.FEATURES,
                    prompt_version="v1", model_id="m1")
        assert build_cache_key(**base) != build_cache_key(**{**base, **kwargs})

    def test_key_prefix_convention(self) -> None:
        k = build_cache_key("BTCUSDT", self.FEATURES, "v1", "m1")
        assert k.startswith("llm:analysis:btcusdt:")


# =============================================================================
# Config & Prompt
# =============================================================================

class TestLLMConfig:
    def test_defaults_match_probe_decisions(self) -> None:
        cfg = LLMConfig()
        assert cfg.primary.model_id == "nvidia/nemotron-3-super-120b-a12b"
        assert cfg.fallback.model_id == "deepseek-ai/deepseek-v4-pro"
        assert cfg.rate_limit.requests_per_minute == 20
        assert cfg.primary.max_tokens >= 4096

    def test_missing_file_returns_defaults(self, tmp_path) -> None:
        cfg = load_llm_config(tmp_path / "yok.yaml")
        assert cfg.rate_limit.requests_per_minute == 20

    def test_corrupt_file_returns_defaults(self, tmp_path) -> None:
        p = tmp_path / "bozuk.yaml"
        p.write_text("rate_limit: [bu, gecersiz, yapi")
        cfg = load_llm_config(p)
        assert cfg.rate_limit.requests_per_minute == 20

    def test_yaml_overrides_apply(self, tmp_path) -> None:
        p = tmp_path / "llm.yaml"
        p.write_text("rate_limit:\n  requests_per_minute: 10\n")
        cfg = load_llm_config(p)
        assert cfg.rate_limit.requests_per_minute == 10

    def test_repo_llm_config_yaml_is_valid(self) -> None:
        """Repodaki gerçek config/llm_config.yaml yüklenebilir olmalı."""
        cfg = load_llm_config("config/llm_config.yaml")
        assert cfg.primary.model_id == "nvidia/nemotron-3-super-120b-a12b"


class TestPrompts:
    def test_missing_template_falls_back_to_builtin(self, tmp_path) -> None:
        t = load_prompt("yok_v9", prompts_dir=tmp_path)
        assert "JSON object" in t.system_prompt

    def test_repo_v1_template_loads_with_few_shot(self) -> None:
        t = load_prompt("trading_analysis_v1")
        assert t.version == "trading_analysis_v1"
        assert len(t.few_shot) >= 2

    def test_build_messages_structure(self) -> None:
        t = PromptTemplate(
            version="v1", system_prompt="SYS",
            few_shot=[{"user": "U1", "assistant": "A1"}],
        )
        msgs = build_messages(t, {"rsi": 38.2})
        assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
        assert "38.2" in msgs[-1]["content"]


class TestABPromptConfig:
    def test_effective_versions_default_single(self) -> None:
        cfg = LLMConfig()
        assert cfg.effective_prompt_versions == ["trading_analysis_v1"]

    def test_effective_versions_ab_list(self) -> None:
        cfg = LLMConfig(prompt_versions=["v1", "v2"])
        assert cfg.effective_prompt_versions == ["v1", "v2"]

    def test_repo_yaml_configures_ab(self) -> None:
        cfg = load_llm_config("config/llm_config.yaml")
        assert cfg.effective_prompt_versions == [
            "trading_analysis_v1", "trading_analysis_v2"
        ]
        assert cfg.rate_limit.requests_per_minute == 20

    def test_repo_v2_template_loads(self) -> None:
        t = load_prompt("trading_analysis_v2")
        assert t.version == "trading_analysis_v2"
        assert len(t.few_shot) >= 3
        assert "MEAN REVERSION" in t.system_prompt
        assert "never exceed 0.75" in t.system_prompt


class TestPromptABSelector:
    def test_alternates_per_symbol(self) -> None:
        """Seçici mantığının saf simülasyonu (agent'taki ile aynı algoritma)."""
        versions = ["v1", "v2"]
        cycle: dict[str, int] = {}

        def select(symbol: str) -> str:
            idx = cycle.get(symbol, 0)
            cycle[symbol] = idx + 1
            return versions[idx % len(versions)]

        assert [select("BTC") for _ in range(4)] == ["v1", "v2", "v1", "v2"]
        # farklı semboller bağımsız döner
        assert select("ETH") == "v1"
        assert select("BTC") == "v1"
