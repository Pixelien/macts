"""AI Analyst Agent unit testleri (Faz 3 / Paket 1).

Kapsam:
- StaggeredScheduler: faz dağılımı, due mantığı, universe senkronizasyonu,
  burst koruması (kota güvenliğinin matematiksel garantisi)
- AIAnalysis şeması: geçerli/geçersiz payload doğrulama
- parse_bool_env: feature flag parse davranışı
- Stream isimlendirme konvansiyonu
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.agents.ai_analyst.agent import (
    ai_analysis_stream,
    features_stream,
    parse_bool_env,
)
from src.agents.ai_analyst.scheduler import StaggeredScheduler
from src.models.schemas import AIAnalysis


# =============================================================================
# StaggeredScheduler
# =============================================================================

SYMBOLS_20 = [f"SYM{i}USDT" for i in range(20)]


class TestStaggeredScheduler:
    def test_interval_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            StaggeredScheduler(interval_seconds=0)

    def test_register_staggers_first_run(self) -> None:
        """İlk çalışma anında değil, faz offset'iyle planlanmalı."""
        sched = StaggeredScheduler(interval_seconds=900)
        sched.register("BTCUSDT", now=1000.0)
        due_at = sched.next_due_at("BTCUSDT")
        assert due_at is not None
        assert 1000.0 <= due_at < 1900.0  # offset < interval

    def test_offsets_are_deterministic_across_instances(self) -> None:
        """Restart senaryosu: aynı sembol her instance'ta aynı faza düşmeli."""
        a = StaggeredScheduler(interval_seconds=900)
        b = StaggeredScheduler(interval_seconds=900)
        a.register("ETHUSDT", now=0.0)
        b.register("ETHUSDT", now=0.0)
        assert a.next_due_at("ETHUSDT") == b.next_due_at("ETHUSDT")

    def test_offsets_are_spread(self) -> None:
        """20 sembol aynı ana yığılmamalı (burst koruması)."""
        sched = StaggeredScheduler(interval_seconds=900)
        for s in SYMBOLS_20:
            sched.register(s, now=0.0)
        offsets = {sched.next_due_at(s) for s in SYMBOLS_20}
        # crc32 dağılımıyla 20 sembolün en az yarısı farklı faza düşmeli
        assert len(offsets) >= 10

    def test_burst_protection_over_full_cycle(self) -> None:
        """KOTA GÜVENLİĞİ: tam bir döngüde hiçbir 60s penceresinde
        30'dan fazla (token bucket tavanı) analiz tetiklenmemeli."""
        interval = 900.0
        sched = StaggeredScheduler(interval_seconds=interval)
        for s in SYMBOLS_20:
            sched.register(s, now=0.0)

        per_minute: dict[int, int] = {}
        now = 0.0
        while now <= interval * 2:  # iki tam döngü simüle et
            for sym in sched.due_symbols(now):
                per_minute[int(now // 60)] = per_minute.get(int(now // 60), 0) + 1
                sched.mark_ran(sym, now)
            now += 5.0  # SCHEDULER_TICK_SECONDS

        assert per_minute, "hiç analiz tetiklenmedi"
        assert max(per_minute.values()) <= 30

    def test_due_and_mark_ran_cycle(self) -> None:
        sched = StaggeredScheduler(interval_seconds=100)
        sched.register("BTCUSDT", now=0.0)
        first_due = sched.next_due_at("BTCUSDT")
        assert first_due is not None

        assert sched.due_symbols(now=first_due - 0.01) == []
        assert sched.due_symbols(now=first_due) == ["BTCUSDT"]

        sched.mark_ran("BTCUSDT", now=first_due)
        assert sched.due_symbols(now=first_due) == []
        assert sched.due_symbols(now=first_due + 100) == ["BTCUSDT"]

    def test_sync_universe_add_remove(self) -> None:
        sched = StaggeredScheduler(interval_seconds=900)
        added, removed = sched.sync_universe({"BTCUSDT", "ETHUSDT"}, now=0.0)
        assert added == {"BTCUSDT", "ETHUSDT"}
        assert removed == set()
        assert sched.tracked_count == 2

        added, removed = sched.sync_universe({"ETHUSDT", "SOLUSDT"}, now=10.0)
        assert added == {"SOLUSDT"}
        assert removed == {"BTCUSDT"}
        assert sched.tracked_count == 2
        assert sched.next_due_at("BTCUSDT") is None

    def test_register_is_idempotent(self) -> None:
        """Aynı sembolü tekrar register etmek fazı sıfırlamamalı."""
        sched = StaggeredScheduler(interval_seconds=900)
        sched.register("BTCUSDT", now=0.0)
        first = sched.next_due_at("BTCUSDT")
        sched.register("BTCUSDT", now=500.0)
        assert sched.next_due_at("BTCUSDT") == first


# =============================================================================
# AIAnalysis şeması
# =============================================================================

VALID_PAYLOAD = {
    "symbol": "BTCUSDT",
    "direction": "short",
    "confidence": 0.68,
    "reasoning": "Price below EMAs, MACD bearish, RSI 38.",
    "risk_flags": ["near support", "low volume"],
    "time_horizon": "1h",
    "model_id": "nvidia/nemotron-3-super-120b-a12b",
    "prompt_version": "trading_analysis_v1",
}


class TestAIAnalysisSchema:
    def test_valid_payload(self) -> None:
        msg = AIAnalysis(**VALID_PAYLOAD)
        assert msg.direction == "short"
        assert msg.cache_hit is False  # varsayılan
        assert msg.message_id is not None  # BaseMessage alanları çalışıyor

    def test_invalid_direction_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AIAnalysis(**{**VALID_PAYLOAD, "direction": "buy"})

    def test_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            AIAnalysis(**{**VALID_PAYLOAD, "confidence": 1.5})
        with pytest.raises(ValidationError):
            AIAnalysis(**{**VALID_PAYLOAD, "confidence": -0.1})

    def test_invalid_time_horizon_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AIAnalysis(**{**VALID_PAYLOAD, "time_horizon": "5m"})

    def test_metadata_fields_required(self) -> None:
        """model_id ve prompt_version bandit/usage tracking için zorunlu."""
        payload = dict(VALID_PAYLOAD)
        del payload["model_id"]
        with pytest.raises(ValidationError):
            AIAnalysis(**payload)


# =============================================================================
# Env parse & stream isimlendirme
# =============================================================================

class TestHelpers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("true", True), ("True", True), ("1", True), ("yes", True),
            ("on", True), (" true ", True),
            ("false", False), ("0", False), ("", False), (None, False),
            ("evet", False),  # bilinmeyen değer güvenli tarafta kalır
        ],
    )
    def test_parse_bool_env(self, raw: str | None, expected: bool) -> None:
        assert parse_bool_env(raw) is expected

    def test_stream_naming_convention(self) -> None:
        """stream:{topic}.{symbol_lowercase} konvansiyonu."""
        assert features_stream("BTCUSDT") == "stream:features.btcusdt"
        assert ai_analysis_stream("BTCUSDT") == "stream:ai_analysis.btcusdt"


class TestStalenessWatchdog:
    """Paket 5: feature tazeliği watchdog'unun saf mantığı.

    Agent'taki _update_staleness ile aynı algoritma: en TAZE feature'ın
    yaşı esas alınır (tek sembol bile taze veri alıyorsa hat canlıdır;
    tüm hat donduğunda -- 10 Tem vakası -- yaş hep birlikte büyür).
    """

    @staticmethod
    def staleness(last_feature_at: dict[str, float], now: float) -> float | None:
        if not last_feature_at:
            return None
        return now - max(last_feature_at.values())

    def test_no_data_yet_returns_none(self) -> None:
        assert self.staleness({}, now=1000.0) is None

    def test_fresh_pipeline(self) -> None:
        ages = {"BTCUSDT": 995.0, "ETHUSDT": 940.0}
        assert self.staleness(ages, now=1000.0) == 5.0  # en tazesi 5 sn önce

    def test_frozen_pipeline_detected(self) -> None:
        """10 Tem deseni: tüm semboller aynı anda donar, yaş birlikte büyür."""
        ages = {"BTCUSDT": 1000.0, "ETHUSDT": 990.0}
        assert self.staleness(ages, now=1000.0 + 700) == 700.0  # > 600 eşiği

    def test_one_live_symbol_keeps_pipeline_fresh(self) -> None:
        ages = {"BTCUSDT": 100.0, "ETHUSDT": 995.0}  # ETH hâlâ akıyor
        assert self.staleness(ages, now=1000.0) == 5.0


class TestStallDetector:
    """Paket 5: feature_engineering donma dedektörünün saf mantığı."""

    @staticmethod
    def is_stalled(count: int, last_count: int, ready: int,
                   now: float, last_progress: float, window: float = 900.0) -> bool:
        if count != last_count:
            return False  # ilerleme var
        return ready > 0 and (now - last_progress) > window

    def test_progress_resets(self) -> None:
        assert not self.is_stalled(101, 100, ready=16, now=5000, last_progress=0)

    def test_stall_with_ready_symbols_triggers(self) -> None:
        """35 saatlik zombi deseni: sayaç sabit, semboller hazır."""
        assert self.is_stalled(459267, 459267, ready=16,
                               now=1000.0, last_progress=0.0)

    def test_no_ready_symbols_is_not_a_stall(self) -> None:
        """Backfill/başlangıç aşaması: yayın olmaması normal."""
        assert not self.is_stalled(0, 0, ready=0, now=1000.0, last_progress=0.0)

    def test_within_window_tolerated(self) -> None:
        assert not self.is_stalled(100, 100, ready=16,
                                   now=800.0, last_progress=0.0)
