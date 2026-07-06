"""Sistem genelinde paylaşılan Pydantic veri modelleri.

Bu modeller agent'lar arası mesajlaşmada, veritabanı kayıtlarında ve
API response'larında kullanılır. Tek tip kaynağı olarak iş mantığı
sınıflarından ayrı tutulur.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Enum'lar
# =============================================================================

class Side(str, Enum):
    """Pozisyon/emir yönü."""
    LONG = "LONG"
    SHORT = "SHORT"


class OrderType(str, Enum):
    """Binance Futures emir tipleri."""
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT = "TAKE_PROFIT"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
    TRAILING_STOP_MARKET = "TRAILING_STOP_MARKET"


class OrderStatus(str, Enum):
    """Emir durumu."""
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class TimeInForce(str, Enum):
    """Emir süresi."""
    GTC = "GTC"   # Good Till Cancel
    IOC = "IOC"   # Immediate Or Cancel
    FOK = "FOK"   # Fill Or Kill
    GTX = "GTX"   # Good Till Crossing (post-only)


class SystemMode(str, Enum):
    """Sistem çalışma modu."""
    TESTNET = "testnet"
    PAPER = "paper"
    LIVE = "live"


class AlertSeverity(str, Enum):
    """Uyarı önem derecesi."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AgentStatus(str, Enum):
    """Agent durumu."""
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


# =============================================================================
# Market Data Modelleri
# =============================================================================

class BaseMessage(BaseModel):
    """Tüm mesajların temel sınıfı."""
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_encoders={Decimal: lambda v: str(v)},
    )

    message_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.utcnow())
    source_agent: str | None = None


class Kline(BaseMessage):
    """Binance kline (mum) verisi."""
    symbol: str
    interval: str            # 1m, 5m, 15m, 1h, 4h
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int
    is_closed: bool = False


class Trade(BaseMessage):
    """Tek bir trade event'i."""
    symbol: str
    trade_id: int
    price: Decimal
    quantity: Decimal
    is_buyer_maker: bool
    event_time: datetime


class OrderBookLevel(BaseModel):
    """Order book'ta tek bir fiyat seviyesi."""
    price: Decimal
    quantity: Decimal


class OrderBookSnapshot(BaseMessage):
    """Order book anlık görüntüsü (L2)."""
    symbol: str
    last_update_id: int
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]

    @property
    def mid_price(self) -> Decimal | None:
        """Bid-ask orta fiyatı."""
        if not self.bids or not self.asks:
            return None
        return (self.bids[0].price + self.asks[0].price) / Decimal("2")

    @property
    def spread_bps(self) -> Decimal | None:
        """Bid-ask spread'i baz puan cinsinden."""
        mid = self.mid_price
        if mid is None or not self.bids or not self.asks:
            return None
        spread = self.asks[0].price - self.bids[0].price
        return (spread / mid) * Decimal("10000")


class MarkPrice(BaseMessage):
    """Mark price + funding rate."""
    symbol: str
    mark_price: Decimal
    index_price: Decimal | None = None
    funding_rate: Decimal | None = None
    next_funding_time: datetime | None = None


class OpenInterest(BaseMessage):
    """Açık pozisyon (open interest) snapshot."""
    symbol: str
    open_interest: Decimal
    open_interest_value: Decimal | None = None  # USDT cinsinden


# =============================================================================
# Feature Modelleri
# =============================================================================

class FeatureSnapshot(BaseMessage):
    """Bir coin için belirli bir andaki tüm hesaplanmış feature'lar."""
    symbol: str
    interval: str

    # Teknik indikatörler
    rsi_14: float | None = None
    rsi_21: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    atr_14: float | None = None
    vwap: float | None = None

    # Mikroyapı
    bid_ask_imbalance: float | None = None
    order_book_pressure: float | None = None
    trade_flow_imbalance: float | None = None

    # Volatilite
    realized_volatility_5m: float | None = None
    realized_volatility_15m: float | None = None
    realized_volatility_60m: float | None = None

    # Rejim
    regime: str | None = None   # bull | bear | sideways
    regime_confidence: float | None = None

    # Genişletilebilir alan
    custom: dict[str, float] = Field(default_factory=dict)


# =============================================================================
# Sinyal & Karar Modelleri
# =============================================================================

class Prediction(BaseMessage):
    """ML modelin fiyat tahmini."""
    symbol: str
    model_name: str
    model_version: str
    horizon_minutes: int
    predicted_return: float
    predicted_price: Decimal | None = None
    confidence: float
    quantiles: dict[str, float] | None = None   # {"p10": ..., "p50": ..., "p90": ...}


class Signal(BaseMessage):
    """Trading sinyali."""
    symbol: str
    side: Side
    confidence: float = Field(ge=0.0, le=1.0)
    expected_return: float
    expected_risk_reward: float
    suggested_entry: Decimal
    suggested_stop_loss: Decimal
    suggested_take_profit: Decimal
    horizon_minutes: int
    reasoning: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Pozisyon & Emir Modelleri
# =============================================================================

class Order(BaseMessage):
    """Emir kaydı."""
    client_order_id: str = Field(default_factory=lambda: f"macts_{uuid4().hex[:16]}")
    exchange_order_id: int | None = None
    symbol: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Decimal | None = None
    commission: Decimal = Decimal("0")
    created_at: datetime = Field(default_factory=lambda: datetime.utcnow())
    updated_at: datetime = Field(default_factory=lambda: datetime.utcnow())
    parent_signal_id: UUID | None = None


class Position(BaseMessage):
    """Açık pozisyon."""
    symbol: str
    side: Side
    entry_price: Decimal
    quantity: Decimal
    leverage: int
    margin: Decimal
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    opened_at: datetime
    closed_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        """Pozisyon hala açık mı?"""
        return self.closed_at is None


# =============================================================================
# Risk & Portföy Modelleri
# =============================================================================

class RiskAssessment(BaseMessage):
    """Bir sinyal/pozisyon için risk değerlendirmesi."""
    symbol: str
    approved: bool
    rejection_reasons: list[str] = Field(default_factory=list)
    suggested_position_size: Decimal | None = None
    suggested_leverage: int | None = None
    var_95: float | None = None
    cvar_95: float | None = None
    correlation_risk: float | None = None


class PortfolioSnapshot(BaseMessage):
    """Portföyün anlık durumu."""
    total_balance: Decimal
    available_balance: Decimal
    margin_used: Decimal
    unrealized_pnl: Decimal
    realized_pnl_today: Decimal
    open_positions: list[Position] = Field(default_factory=list)
    daily_drawdown_pct: float = 0.0
    weekly_drawdown_pct: float = 0.0
    monthly_drawdown_pct: float = 0.0


# =============================================================================
# Sistem Sağlığı & Uyarı Modelleri
# =============================================================================

class HealthCheckResult(BaseMessage):
    """Bir agent'ın health-check sonucu."""
    agent_name: str
    status: AgentStatus
    last_heartbeat: datetime
    metrics: dict[str, float] = Field(default_factory=dict)
    error_message: str | None = None


class Alert(BaseMessage):
    """Sistem uyarısı / bildirimi."""
    severity: AlertSeverity
    title: str
    message: str
    agent_name: str | None = None
    symbol: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CircuitBreakerEvent(BaseMessage):
    """Circuit breaker tetiklenme event'i."""
    trigger_type: str   # flash_crash | exchange_outage | daily_loss_breach | ...
    severity: AlertSeverity
    symbol: str | None = None
    description: str
    action_taken: str   # close_all | halt_new_orders | reduce_positions
    metadata: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# AI Analyst Modelleri (Faz 3)
# =============================================================================

class AIAnalysis(BaseMessage):
    """AI Analyst (LLM) agent'ının tek sembol için ürettiği analiz.

    Yayınlandığı stream: stream:ai_analysis.{symbol}
    Şema, docs/AI_ANALYST_MODEL_SELECTION.md Aşama 3 sözleşmesiyle birebir
    aynıdır ve scripts/nim_model_probe.py ile canlıda doğrulanmıştır.
    """
    symbol: str
    direction: Literal["long", "short", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    risk_flags: list[str] = Field(default_factory=list)
    time_horizon: Literal["1h", "4h", "1d"]

    # LLM çağrı metadata'sı (usage tracking + bandit için)
    model_id: str
    prompt_version: str
    latency_seconds: float | None = None
    cache_hit: bool = False
