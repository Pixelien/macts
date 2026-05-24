"""Teknik indikatör testleri."""

from __future__ import annotations

import numpy as np
import pytest

from src.core.ml.indicators import (
    atr,
    bid_ask_imbalance,
    ema,
    realized_volatility,
    rsi,
    sma,
    trade_flow_imbalance,
)


class TestEMA:
    def test_basic(self) -> None:
        values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = ema(values, period=3)
        assert len(result) == 5
        assert result[0] == 1.0
        # EMA monoton artmalı
        for i in range(1, len(result)):
            assert result[i] > result[i - 1]


class TestSMA:
    def test_basic(self) -> None:
        values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = sma(values, period=3)
        assert np.isnan(result[0])
        assert np.isnan(result[1])
        assert result[2] == pytest.approx(2.0)
        assert result[3] == pytest.approx(3.0)
        assert result[4] == pytest.approx(4.0)

    def test_too_short(self) -> None:
        values = np.array([1.0, 2.0])
        result = sma(values, period=5)
        assert all(np.isnan(result))


class TestRSI:
    def test_constant_prices(self) -> None:
        # Sabit fiyat -> avg_gain ve avg_loss=0 -> RSI=100 (formülde)
        values = np.full(30, 50.0)
        result = rsi(values, period=14)
        # period kadar başlangıçta NaN olması beklenir
        assert np.isnan(result[0])

    def test_uptrend(self) -> None:
        # Sürekli yükseliş -> RSI yüksek olmalı
        values = np.arange(1.0, 31.0)
        result = rsi(values, period=14)
        # Son değer NaN olmamalı ve > 50 olmalı
        assert not np.isnan(result[-1])
        assert result[-1] > 50


class TestATR:
    def test_basic(self) -> None:
        high = np.array([10.0, 11.0, 12.0, 11.5, 13.0])
        low = np.array([9.0, 10.0, 11.0, 10.5, 12.0])
        close = np.array([9.5, 10.5, 11.5, 11.0, 12.5])
        result = atr(high, low, close, period=3)
        assert len(result) == 5
        assert all(result > 0)


class TestMicrostructure:
    def test_bid_ask_imbalance_balanced(self) -> None:
        bid_vols = np.array([10.0, 5.0])
        ask_vols = np.array([10.0, 5.0])
        assert bid_ask_imbalance(bid_vols, ask_vols) == 0.0

    def test_bid_ask_imbalance_buy_pressure(self) -> None:
        bid_vols = np.array([20.0])
        ask_vols = np.array([10.0])
        # (20 - 10) / 30 = 0.333
        assert bid_ask_imbalance(bid_vols, ask_vols) == pytest.approx(1 / 3)

    def test_bid_ask_imbalance_empty(self) -> None:
        assert bid_ask_imbalance(np.array([]), np.array([])) == 0.0

    def test_trade_flow_imbalance(self) -> None:
        assert trade_flow_imbalance(100.0, 50.0) == pytest.approx(1 / 3)
        assert trade_flow_imbalance(0.0, 0.0) == 0.0

    def test_realized_volatility(self) -> None:
        log_returns = np.array([0.01, -0.01, 0.02, -0.02])
        vol = realized_volatility(log_returns)
        assert vol > 0
        assert vol == pytest.approx(np.std(log_returns))
