"""Streaming teknik indikatör hesaplama modülü.

TA-Lib üzerine kurulmuş ama streaming (incremental) hesap için optimize
edilmiş wrapper. Hot path'lerde numba JIT kullanılır.

NOT: Bu modül iskelet seviyesindedir. TA-Lib'in kendi fonksiyonları
batch çalışır; gerçek streaming için her sembole ait state ring
buffer'larında tutulmalıdır.
"""

from __future__ import annotations

import numpy as np
from numba import njit


# =============================================================================
# JIT-compiled hot path indikatörleri
# =============================================================================

@njit(cache=True)
def ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average — incremental hesap."""
    alpha = 2.0 / (period + 1)
    result = np.empty_like(values, dtype=np.float64)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result


@njit(cache=True)
def sma(values: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average."""
    n = len(values)
    result = np.full(n, np.nan, dtype=np.float64)
    if n < period:
        return result
    cumsum = np.cumsum(values)
    result[period - 1] = cumsum[period - 1] / period
    for i in range(period, n):
        result[i] = (cumsum[i] - cumsum[i - period]) / period
    return result


@njit(cache=True)
def rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index — Wilder smoothing."""
    n = len(values)
    result = np.full(n, np.nan, dtype=np.float64)
    if n <= period:
        return result

    deltas = np.diff(values)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, n - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - (100.0 / (1 + rs))
    return result


@njit(cache=True)
def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range."""
    n = len(close)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    return ema(tr, period)


# =============================================================================
# Mikroyapı metrikleri
# =============================================================================

def bid_ask_imbalance(
    bid_volumes: np.ndarray, ask_volumes: np.ndarray
) -> float:
    """L2 order book bid-ask imbalance.

    > 0: alıcı baskın, < 0: satıcı baskın.
    Normalize edilmiş [-1, +1] aralığında.
    """
    bid_sum = bid_volumes.sum()
    ask_sum = ask_volumes.sum()
    total = bid_sum + ask_sum
    if total == 0:
        return 0.0
    return float((bid_sum - ask_sum) / total)


def trade_flow_imbalance(
    buy_volume: float, sell_volume: float
) -> float:
    """Trade flow imbalance (taker buy / sell oranı)."""
    total = buy_volume + sell_volume
    if total == 0:
        return 0.0
    return (buy_volume - sell_volume) / total


def realized_volatility(
    log_returns: np.ndarray, annualize: bool = False
) -> float:
    """Realized volatility (log return std).

    Args:
        log_returns: log return array.
        annualize: True ise sqrt(365*24*60) ile çarp (1m bar varsayımı).
    """
    vol = float(np.std(log_returns))
    if annualize:
        vol *= np.sqrt(365 * 24 * 60)
    return vol


# =============================================================================
# TODO: Streaming wrapper
# =============================================================================
# Her sembol için bir IndicatorState class'ı tutulmalı:
#   class IndicatorState:
#       def __init__(self, max_window: int = 5000): ...
#       def update(self, kline: Kline) -> FeatureSnapshot: ...
#
# Bu sayede her yeni kline geldiğinde sadece yeni değer hesaplanır,
# tüm geçmiş yeniden işlenmez. Ring buffer + Wilder/EMA recursive
# formülleri ile O(1) güncelleme yapılır.
