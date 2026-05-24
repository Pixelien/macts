"""Signal Generation Agent — Faz 3 Adım 3.

ML predictions'ı tradable trading signal'lara dönüştürür.

Akış:
- Per-Coin Learning'in stream:predictions.{symbol} yayınlarını dinle
- Confidence threshold (>=0.4 ≈ prob_up>0.7 veya prob_up<0.3) filtresi
- Yön belirle: LONG (prob_up>0.5) | SHORT (prob_up<0.5)
- Position sizing: confidence × MAX_POSITION_PCT (basit yaklaşım)
- Cooldown: aynı sembol için 5 dakika
- stream:signals.raw'a yayınla (Risk Mgmt sonradan onaylayacak)
- InfluxDB'ye persist (signals measurement)

Gelecekte (Faz 4):
- Risk Management Agent'ı bekle, stream:signals.approved'a aktar
- Half Kelly position sizing
- Multi-source ensemble (ML + TA + mikroyapı)
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

import aiohttp

from src.agents.base import BaseAgent, run_agent

# Config
MIN_CONFIDENCE = 0.4
MAX_POSITION_PCT = 0.10  # max %10 portföy
COOLDOWN_SECONDS = 300  # 5 dakika
STATS_INTERVAL = 30.0

# Streams
STREAM_UNIVERSE_SNAPSHOT = "stream:universe.snapshot"
STREAM_SIGNALS_RAW = "stream:signals.raw"


def predictions_stream(symbol: str) -> str:
    return f"stream:predictions.{symbol.lower()}"


# InfluxDB
INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "gazifintech")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "macts_market_data")


class SignalGenerationAgent(BaseAgent):
    """Predictions → Trading signals dönüşümü."""

    agent_name = "signal_generation"
    heartbeat_interval = 5.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Listener task'ları per-symbol
        self._listeners: dict[str, asyncio.Task[None]] = {}
        # Cooldown takibi: {symbol: last_signal_timestamp}
        self._last_signal_time: dict[str, float] = {}
        # Sayaçlar
        self._predictions_received = 0
        self._signals_published = 0
        self._signals_long = 0
        self._signals_short = 0
        self._filtered_low_confidence = 0
        self._filtered_cooldown = 0
        self._influx_writes = 0
        self._influx_errors = 0
        # HTTP session
        self._influx_session: aiohttp.ClientSession | None = None

    async def _initialize(self) -> None:
        self.logger.info(
            "signal_generation_initializing",
            min_confidence=MIN_CONFIDENCE,
            max_position_pct=MAX_POSITION_PCT,
            cooldown_seconds=COOLDOWN_SECONDS,
        )
        timeout = aiohttp.ClientTimeout(total=10)
        headers = {
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "text/plain; charset=utf-8",
            "Accept": "application/json",
        }
        self._influx_session = aiohttp.ClientSession(timeout=timeout, headers=headers)

    async def _run(self) -> None:
        self.logger.info("signal_generation_loop_started")

        universe_task = asyncio.create_task(self._listen_universe())
        stats_task = asyncio.create_task(self._stats_loop())

        try:
            await self._stop_event.wait()
        finally:
            for task in self._listeners.values():
                task.cancel()
            universe_task.cancel()
            stats_task.cancel()
            await asyncio.gather(
                *self._listeners.values(),
                universe_task,
                stats_task,
                return_exceptions=True,
            )

    async def _listen_universe(self) -> None:
        """Universe snapshot'ı dinle, listener'ları yönet."""
        if self._redis_bus is None:
            return
        self.logger.info("universe_listener_started", stream=STREAM_UNIVERSE_SNAPSHOT)
        try:
            async for msg in self._redis_bus.subscribe(
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
        """Universe değişimine göre listener'ları aç/kapat."""
        symbols_raw = msg.get("symbols")
        if symbols_raw is None:
            return
        if isinstance(symbols_raw, str):
            import json
            try:
                symbols = set(json.loads(symbols_raw))
            except json.JSONDecodeError:
                return
        elif isinstance(symbols_raw, list):
            symbols = set(symbols_raw)
        else:
            return

        current = set(self._listeners.keys())
        added = symbols - current
        removed = current - symbols

        if added or removed:
            self.logger.info(
                "signal_universe_changed",
                added=sorted(added),
                removed=sorted(removed),
                new_size=len(symbols),
            )

        for sym in added:
            # Race condition guard
            if sym in self._listeners:
                continue
            self._listeners[sym] = asyncio.create_task(self._listen_predictions(sym))

        for sym in removed:
            task = self._listeners.pop(sym, None)
            if task and not task.done():
                task.cancel()

    async def _listen_predictions(self, symbol: str) -> None:
        """Bir sembol için predictions stream'ini dinle."""
        if self._redis_bus is None:
            return
        stream = predictions_stream(symbol)
        self.logger.info("predictions_listener_started", symbol=symbol, stream=stream)
        try:
            async for msg in self._redis_bus.subscribe(stream, from_beginning=False):
                if self._stop_event.is_set():
                    break
                await self._handle_prediction(symbol, msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.exception(
                "predictions_listener_failed", symbol=symbol, error=str(e)
            )

    async def _handle_prediction(self, symbol: str, msg: dict[str, Any]) -> None:
        """Bir prediction geldiğinde değerlendir, sinyal üret."""
        self._predictions_received += 1

        try:
            prob_up = float(msg["prob_up"])
            confidence = float(msg["confidence"])
            feature_time = int(msg.get("feature_time", 0))
        except (KeyError, ValueError, TypeError) as e:
            self.logger.warning("prediction_parse_failed", symbol=symbol, error=str(e))
            return

        # Filtre 1: Confidence threshold
        if confidence < MIN_CONFIDENCE:
            self._filtered_low_confidence += 1
            return

        # Filtre 2: Cooldown
        now = asyncio.get_event_loop().time()
        last_signal = self._last_signal_time.get(symbol, 0)
        if now - last_signal < COOLDOWN_SECONDS:
            self._filtered_cooldown += 1
            return

        # Yön belirle
        if prob_up > 0.5:
            direction = "LONG"
            self._signals_long += 1
        else:
            direction = "SHORT"
            self._signals_short += 1

        # Position sizing — basit: confidence × max_position
        position_size_pct = round(confidence * MAX_POSITION_PCT, 4)

        signal = {
            "symbol": symbol,
            "direction": direction,
            "confidence": round(confidence, 4),
            "prob_up": round(prob_up, 4),
            "position_size_pct": position_size_pct,
            "feature_time": feature_time,
            "generated_at": datetime.utcnow().isoformat(),
            "source": "per_coin_learning_v1",
            "approved": False,  # Risk Mgmt henüz yok, false bırak
        }

        # Yayınla
        if self._redis_bus is not None:
            await self._redis_bus.publish(STREAM_SIGNALS_RAW, signal)
            self._signals_published += 1
            self._last_signal_time[symbol] = now

        # Log (önemli olay, info seviyesi)
        self.logger.info(
            "signal_generated",
            symbol=symbol,
            direction=direction,
            confidence=round(confidence, 3),
            prob_up=round(prob_up, 3),
            position_size_pct=position_size_pct,
        )

        # InfluxDB'ye persist
        await self._write_signal_to_influx(symbol, signal, feature_time)

    async def _write_signal_to_influx(
        self,
        symbol: str,
        signal: dict[str, Any],
        feature_time_ms: int,
    ) -> None:
        if self._influx_session is None or not INFLUX_TOKEN:
            return

        # Tags: symbol, direction
        # Fields: confidence, prob_up, position_size_pct, approved (int)
        direction_tag = signal["direction"]
        approved_int = 1 if signal["approved"] else 0

        line = (
            f"signals,symbol={symbol},direction={direction_tag} "
            f"confidence={signal['confidence']},"
            f"prob_up={signal['prob_up']},"
            f"position_size_pct={signal['position_size_pct']},"
            f"approved={approved_int}i "
            f"{feature_time_ms * 1_000_000}"  # ns
        )

        url = f"{INFLUX_URL}/api/v2/write"
        params = {"org": INFLUX_ORG, "bucket": INFLUX_BUCKET, "precision": "ns"}
        try:
            async with self._influx_session.post(
                url, params=params, data=line.encode("utf-8")
            ) as resp:
                if resp.status == 204:
                    self._influx_writes += 1
                else:
                    self._influx_errors += 1
        except Exception:
            self._influx_errors += 1

    async def _stats_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=STATS_INTERVAL)
                break
            except asyncio.TimeoutError:
                self.logger.info(
                    "signal_generation_stats",
                    active_listeners=len(self._listeners),
                    predictions_received=self._predictions_received,
                    signals_published=self._signals_published,
                    signals_long=self._signals_long,
                    signals_short=self._signals_short,
                    filtered_low_confidence=self._filtered_low_confidence,
                    filtered_cooldown=self._filtered_cooldown,
                    influx_writes=self._influx_writes,
                    influx_errors=self._influx_errors,
                )

    async def _shutdown(self) -> None:
        self.logger.info("signal_generation_shutting_down")
        if self._influx_session is not None:
            await self._influx_session.close()

    async def _health_check(self) -> dict[str, float]:
        return {
            "active_listeners": float(len(self._listeners)),
            "predictions_received": float(self._predictions_received),
            "signals_published": float(self._signals_published),
            "filtered_low_confidence": float(self._filtered_low_confidence),
            "filtered_cooldown": float(self._filtered_cooldown),
        }


if __name__ == "__main__":
    asyncio.run(run_agent(SignalGenerationAgent))
