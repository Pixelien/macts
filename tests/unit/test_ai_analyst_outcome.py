"""Outcome değerlendirme modülü unit testleri (Faz 3 / Aşama 4).

Kapsam:
- classify_outcome: 3 yön × 3 gerçekleşme matrisi, flat band sınırları,
  guard'lar (entry<=0, bilinmeyen direction)
- parse_kline_close: Redis string alanları, kapanmamış mum filtresi
- enrich_features_with_price: fiyat ekleme / None toleransı
- HORIZON_SECONDS sözleşmesi (AIAnalysis.time_horizon ile birebir)
"""

from __future__ import annotations

import pytest

from src.agents.ai_analyst.outcome import (
    HORIZON_SECONDS,
    classify_outcome,
    enrich_features_with_price,
    parse_kline_close,
)


class TestClassifyOutcome:
    """3x3 doğruluk matrisi: direction × gerçekleşen hareket."""

    @pytest.mark.parametrize(
        ("direction", "entry", "exit", "expected_actual", "expected_correct"),
        [
            # long tahminleri
            ("long", 100.0, 101.0, "up", True),      # +1.0% > band
            ("long", 100.0, 99.0, "down", False),
            ("long", 100.0, 100.05, "flat", False),  # band içinde
            # short tahminleri
            ("short", 100.0, 99.0, "down", True),
            ("short", 100.0, 101.0, "up", False),
            ("short", 100.0, 100.05, "flat", False),
            # neutral tahminleri
            ("neutral", 100.0, 100.05, "flat", True),
            ("neutral", 100.0, 102.0, "up", False),
            ("neutral", 100.0, 98.0, "down", False),
        ],
    )
    def test_matrix(self, direction, entry, exit, expected_actual, expected_correct):
        return_pct, actual, correct = classify_outcome(direction, entry, exit)
        assert actual == expected_actual
        assert correct is expected_correct

    def test_return_pct_computed(self) -> None:
        return_pct, _, _ = classify_outcome("long", 200.0, 203.0)
        assert return_pct == pytest.approx(1.5)

    def test_flat_band_boundary_is_exclusive(self) -> None:
        """Tam band değeri (%0.10) flat sayılır; ancak üstü yön sayılır."""
        _, actual, _ = classify_outcome("long", 1000.0, 1001.0)  # tam +0.10%
        assert actual == "flat"
        _, actual, _ = classify_outcome("long", 1000.0, 1001.1)  # +0.11%
        assert actual == "up"

    def test_custom_band(self) -> None:
        _, actual, _ = classify_outcome("long", 100.0, 100.3, flat_band_pct=0.5)
        assert actual == "flat"

    def test_invalid_entry_rejected(self) -> None:
        with pytest.raises(ValueError):
            classify_outcome("long", 0.0, 100.0)
        with pytest.raises(ValueError):
            classify_outcome("long", -5.0, 100.0)

    def test_unknown_direction_rejected(self) -> None:
        with pytest.raises(ValueError):
            classify_outcome("buy", 100.0, 101.0)


class TestParseKlineClose:
    def test_closed_kline_string_fields(self) -> None:
        """Redis Streams her alanı string yapar — tipik prod mesajı."""
        msg = {"symbol": "BTCUSDT", "close": "107250.5", "is_closed": "True"}
        assert parse_kline_close(msg) == pytest.approx(107250.5)

    @pytest.mark.parametrize("raw", ["true", "1", True])
    def test_is_closed_variants(self, raw) -> None:
        assert parse_kline_close({"close": "10", "is_closed": raw}) == 10.0

    @pytest.mark.parametrize("raw", ["False", "false", "0", False, None])
    def test_open_kline_ignored(self, raw) -> None:
        """Kapanmamış mumun fiyatı hâlâ oynar — kullanılmamalı."""
        msg = {"close": "10", "is_closed": raw} if raw is not None else {"close": "10"}
        assert parse_kline_close(msg) is None

    def test_missing_or_garbage_close(self) -> None:
        assert parse_kline_close({"is_closed": "True"}) is None
        assert parse_kline_close({"is_closed": "True", "close": "n/a"}) is None


class TestEnrichFeatures:
    def test_adds_close(self) -> None:
        out = enrich_features_with_price({"rsi_14": "38.2"}, 107250.0)
        assert out["close"] == 107250.0
        assert out["rsi_14"] == "38.2"

    def test_none_price_passthrough(self) -> None:
        original = {"rsi_14": "38.2"}
        assert enrich_features_with_price(original, None) is original

    def test_original_not_mutated(self) -> None:
        original = {"rsi_14": "38.2"}
        enrich_features_with_price(original, 1.0)
        assert "close" not in original


class TestHorizonContract:
    def test_matches_ai_analysis_schema_literals(self) -> None:
        """AIAnalysis.time_horizon Literal'ları ile birebir aynı olmalı."""
        assert set(HORIZON_SECONDS) == {"1h", "4h", "1d"}
        assert HORIZON_SECONDS["1h"] == 3600
        assert HORIZON_SECONDS["4h"] == 14400
        assert HORIZON_SECONDS["1d"] == 86400
