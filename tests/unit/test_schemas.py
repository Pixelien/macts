"""Pydantic schemas için unit testler."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from src.models import (
    Kline,
    Order,
    OrderBookLevel,
    OrderBookSnapshot,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Signal,
    SystemMode,
    TimeInForce,
)


class TestKline:
    def test_create_kline(self) -> None:
        kline = Kline(
            symbol="BTCUSDT",
            interval="1m",
            open_time=datetime(2026, 1, 1, 12, 0, 0),
            close_time=datetime(2026, 1, 1, 12, 1, 0),
            open=Decimal("50000.0"),
            high=Decimal("50100.0"),
            low=Decimal("49900.0"),
            close=Decimal("50050.0"),
            volume=Decimal("100.5"),
            quote_volume=Decimal("5025000.0"),
            trade_count=1234,
        )
        assert kline.symbol == "BTCUSDT"
        assert kline.is_closed is False


class TestOrderBookSnapshot:
    def test_mid_price_and_spread(self) -> None:
        snap = OrderBookSnapshot(
            symbol="BTCUSDT",
            last_update_id=1,
            bids=[OrderBookLevel(price=Decimal("100"), quantity=Decimal("1"))],
            asks=[OrderBookLevel(price=Decimal("101"), quantity=Decimal("1"))],
        )
        assert snap.mid_price == Decimal("100.5")
        spread = snap.spread_bps
        assert spread is not None
        # 1 / 100.5 * 10000 ≈ 99.50 bps
        assert Decimal("99") < spread < Decimal("100")

    def test_empty_book(self) -> None:
        snap = OrderBookSnapshot(
            symbol="BTCUSDT", last_update_id=1, bids=[], asks=[]
        )
        assert snap.mid_price is None
        assert snap.spread_bps is None


class TestOrder:
    def test_default_status(self) -> None:
        order = Order(
            symbol="BTCUSDT",
            side=Side.LONG,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.1"),
            price=Decimal("50000"),
            time_in_force=TimeInForce.GTC,
        )
        assert order.status == OrderStatus.PENDING
        assert order.client_order_id.startswith("macts_")
        assert order.filled_quantity == Decimal("0")


class TestPosition:
    def test_is_open(self) -> None:
        pos = Position(
            symbol="BTCUSDT",
            side=Side.LONG,
            entry_price=Decimal("50000"),
            quantity=Decimal("0.1"),
            leverage=3,
            margin=Decimal("1666.67"),
            opened_at=datetime.utcnow(),
        )
        assert pos.is_open is True

        pos.closed_at = datetime.utcnow()
        assert pos.is_open is False


class TestSignal:
    def test_confidence_bounds(self) -> None:
        # Geçerli
        sig = Signal(
            symbol="BTCUSDT",
            side=Side.LONG,
            confidence=0.75,
            expected_return=0.02,
            expected_risk_reward=2.5,
            suggested_entry=Decimal("50000"),
            suggested_stop_loss=Decimal("49500"),
            suggested_take_profit=Decimal("51250"),
            horizon_minutes=60,
        )
        assert sig.confidence == 0.75

        # Geçersiz: confidence > 1
        with pytest.raises(Exception):
            Signal(
                symbol="BTCUSDT",
                side=Side.LONG,
                confidence=1.5,
                expected_return=0.02,
                expected_risk_reward=2.5,
                suggested_entry=Decimal("50000"),
                suggested_stop_loss=Decimal("49500"),
                suggested_take_profit=Decimal("51250"),
                horizon_minutes=60,
            )


class TestSystemMode:
    def test_enum_values(self) -> None:
        assert SystemMode.TESTNET.value == "testnet"
        assert SystemMode.PAPER.value == "paper"
        assert SystemMode.LIVE.value == "live"
