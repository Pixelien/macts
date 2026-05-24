"""Feature Engineering Agent — Faz 2 (Temel teknik indikatörler + REST backfill).

Kline akışından teknik indikatörleri hesaplar ve yayınlar.

Akış:
- Universe'i Market Scanner'dan dinle (stream:universe.snapshot)
- Yeni sembol eklendiğinde Binance REST'ten son 200 kline'ı backfill et
- Her sembol için kline akışını dinle (stream:ticks.{symbol}.kline.1m)
- Closed kline geldiğinde TA-Lib ile indikatörleri hesapla
- Yayınla: stream:features.{symbol} (Redis) + InfluxDB (measurement: features)

Hesaplanan indikatörler:
- RSI(14)
- MACD(12, 26, 9): macd_line, signal_line, histogram
- Bollinger Bands(20, 2): upper, middle, lower
- EMA(9, 21, 50)
- SMA(20, 50)
- ATR(14)
"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from datetime import datetime
from typing import Any

import aiohttp
import numpy as np
import talib

from src.agents.base import BaseAgent, run_agent

# Buffer
BUFFER_SIZE = 200
MIN_BARS_FOR_FEATURES = 50

# Backfill
BACKFILL_LIMIT = 200  # Binance API max 1500, biz 200 yeterli
BINANCE_MAINNET_REST = "https://fapi.binance.com"

# Stream isimleri
STREAM_UNIVERSE_SNAPSHOT = "stream:universe.snapshot"
KLINE_INTERVAL = "1m"


def kline_stream(symbol: str, interval: str = KLINE_INTERVAL) -> str:
    return f"stream:ticks.{symbol.lower()}.kline.{interval}"


def features_stream(symbol: str) -> str:
    return f"stream:features.{symbol.lower()}"


# InfluxDB config
INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "gazifintech")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "macts_market_data")


class SymbolBuffer:
    """Bir sembol için OHLCV ring buffer."""

    def __init__(self, maxlen: int = BUFFER_SIZE) -> None:
        self.open: deque[float] = deque(maxlen=maxlen)
        self.high: deque[float] = deque(maxlen=maxlen)
        self.low: deque[float] = deque(maxlen=maxlen)
        self.close: deque[float] = deque(maxlen=maxlen)
        self.volume: deque[float] = deque(maxlen=maxlen)
        self.open_time: deque[int] = deque(maxlen=maxlen)
        self._last_open_time: int = -1

    def add_or_update(
        self,
        open_time: int,
        o: float,
        h: float,
        l: float,
        c: float,
        v: float,
        is_closed: bool,
    ) -> bool:
        if open_time == self._last_open_time:
            if self.close:
                self.high[-1] = max(self.high[-1], h)
                self.low[-1] = min(self.low[-1], l)
                self.close[-1] = c
                self.volume[-1] = v
            return False
        self.open.append(o)
        self.high.append(h)
        self.low.append(l)
        self.close.append(c)
        self.volume.append(v)
        self.open_time.append(open_time)
        self._last_open_time = open_time
        return is_closed

    def __len__(self) -> int:
        return len(self.close)


class FeatureEngineeringAgent(BaseAgent):
    """Teknik indikatör hesaplayan agent — REST backfill destekli."""

    agent_name = "feature_engineering"
    heartbeat_interval = 5.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._buffers: dict[str, SymbolBuffer] = {}
        self._kline_listeners: dict[str, asyncio.Task[None]] = {}
        self._features_published = 0
        self._influx_writes = 0
        self._influx_errors = 0
        self._backfills_done = 0
        self._backfill_lock = asyncio.Lock()  # Rate limit için sıralı backfill
        self._http_session: aiohttp.ClientSession | None = None
        self._influx_session: aiohttp.ClientSession | None = None

    async def _initialize(self) -> None:
        self.logger.info(
            "feature_engineering_initializing",
            buffer_size=BUFFER_SIZE,
            min_bars_for_features=MIN_BARS_FOR_FEATURES,
            backfill_limit=BACKFILL_LIMIT,
        )
        timeout = aiohttp.ClientTimeout(total=15)
        self._http_session = aiohttp.ClientSession(timeout=timeout)

        influx_headers = {
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "text/plain; charset=utf-8",
            "Accept": "application/json",
        }
        self._influx_session = aiohttp.ClientSession(
            timeout=timeout, headers=influx_headers
        )

    async def _run(self) -> None:
        self.logger.info("feature_engineering_loop_started")

        universe_task = asyncio.create_task(self._listen_universe())
        stats_task = asyncio.create_task(self._stats_loop())

        try:
            await self._stop_event.wait()
        finally:
            for task in self._kline_listeners.values():
                task.cancel()
            universe_task.cancel()
            stats_task.cancel()
            await asyncio.gather(
                *self._kline_listeners.values(),
                universe_task,
                stats_task,
                return_exceptions=True,
            )

    async def _listen_universe(self) -> None:
        if self._redis_bus is None:
            return
        self.logger.info("universe_listener_started")
        try:
            async for msg in self._redis_bus.subscribe(
                STREAM_UNIVERSE_SNAPSHOT,
                from_beginning=True,
            ):
                if self._stop_event.is_set():
                    break
                await self._apply_universe(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.exception("universe_listener_failed", error=str(e))

    async def _apply_universe(self, msg: dict[str, Any]) -> None:
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

        current = set(self._kline_listeners.keys())
        added = symbols - current
        removed = current - symbols

        if added or removed:
            self.logger.info(
                "feature_universe_changed",
                added=sorted(added),
                removed=sorted(removed),
                new_size=len(symbols),
            )

        # Yeni eklenen semboller — backfill + listener başlat
        for sym in added:
            # Guard: zaten dinlenen sembolü tekrar başlatma (race condition önlemi)
            if sym in self._kline_listeners:
                continue
            self._buffers[sym] = SymbolBuffer()
            # Task'ı HEMEN dict'e ekle — race condition önlemi
            self._kline_listeners[sym] = asyncio.create_task(
                self._backfill_and_listen(sym)
            )

        # Çıkan semboller — listener iptal
        for sym in removed:
            task = self._kline_listeners.pop(sym, None)
            if task and not task.done():
                task.cancel()
            self._buffers.pop(sym, None)

    async def _backfill_and_listen(self, symbol: str) -> None:
        """Redis'ten backfill yap (paralel, rate-limit yok), sonra canlı listener."""
        try:
            await self._backfill_buffer(symbol)
        except Exception as e:
            self.logger.warning(
                "backfill_failed_continuing_with_live",
                symbol=symbol,
                error=str(e),
            )

        # Canlı kline dinlemeye başla
        await self._listen_klines(symbol)

    async def _backfill_buffer(self, symbol: str) -> None:
        """Redis stream'den son N kline'ı okuyup buffer'ı doldur.
        
        Data Collection agent zaten Redis'e kline yazıyor, oradan okumak
        Binance rate limit sorunlarını tamamen ortadan kaldırır.
        """
        if self._redis_bus is None or self._redis_bus._client is None:
            return

        stream = kline_stream(symbol)
        # XRANGE ile stream'in tamamını oku, son BUFFER_SIZE kadarı al
        # Redis client'a doğrudan eriş (RedisStreamsBus üzerinden)
        try:
            # Son N entry: XREVRANGE + - COUNT N → reverse order, sonra reversle
            entries = await self._redis_bus._client.xrevrange(
                stream, max="+", min="-", count=BUFFER_SIZE
            )
        except Exception as e:
            self.logger.warning("redis_backfill_failed", symbol=symbol, error=str(e))
            return

        buf = self._buffers.get(symbol)
        if buf is None:
            return

        # Reverse: en eski → en yeni
        entries = list(reversed(entries))

        loaded = 0
        for stream_id, fields in entries:
            try:
                # fields: dict-like, byte string'ler veya str (decode_responses bağlı)
                def get_field(key: str) -> str:
                    val = fields.get(key) or fields.get(key.encode() if isinstance(list(fields.keys())[0], bytes) else key)
                    if isinstance(val, bytes):
                        return val.decode()
                    return val
                
                open_time = int(get_field("open_time"))
                o = float(get_field("open"))
                h = float(get_field("high"))
                low = float(get_field("low"))
                c = float(get_field("close"))
                v = float(get_field("volume"))
                is_closed_str = str(get_field("is_closed")).lower()
                is_closed = is_closed_str == "true"
                
                buf.add_or_update(open_time, o, h, low, c, v, is_closed)
                loaded += 1
            except (KeyError, ValueError, TypeError, AttributeError):
                continue

        self._backfills_done += 1
        self.logger.info(
            "backfill_completed",
            symbol=symbol,
            bars_loaded=len(buf),
            entries_processed=loaded,
            source="redis",
        )

    async def _listen_klines(self, symbol: str) -> None:
        if self._redis_bus is None:
            return
        stream = kline_stream(symbol)
        self.logger.info("kline_listener_started", symbol=symbol, stream=stream)
        try:
            async for msg in self._redis_bus.subscribe(stream, from_beginning=False):
                if self._stop_event.is_set():
                    break
                await self._handle_kline(symbol, msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.exception(
                "kline_listener_failed", symbol=symbol, error=str(e)
            )

    async def _handle_kline(self, symbol: str, msg: dict[str, Any]) -> None:
        try:
            open_time = int(msg.get("open_time", 0))
            o = float(msg["open"])
            h = float(msg["high"])
            low = float(msg["low"])
            c = float(msg["close"])
            v = float(msg["volume"])
            is_closed_raw = msg.get("is_closed", False)
            is_closed = (
                is_closed_raw is True
                or str(is_closed_raw).lower() == "true"
            )
        except (KeyError, ValueError, TypeError) as e:
            self.logger.warning("kline_parse_failed", symbol=symbol, error=str(e))
            return

        buf = self._buffers.get(symbol)
        if buf is None:
            return

        new_closed_bar = buf.add_or_update(
            open_time, o, h, low, c, v, is_closed
        )

        if new_closed_bar and len(buf) >= MIN_BARS_FOR_FEATURES:
            await self._compute_and_publish_features(symbol, buf, open_time)

    async def _compute_and_publish_features(
        self,
        symbol: str,
        buf: SymbolBuffer,
        open_time: int,
    ) -> None:
        close = np.array(buf.close, dtype=np.float64)
        high = np.array(buf.high, dtype=np.float64)
        low = np.array(buf.low, dtype=np.float64)

        try:
            rsi_14 = talib.RSI(close, timeperiod=14)
            macd, macd_signal, macd_hist = talib.MACD(
                close, fastperiod=12, slowperiod=26, signalperiod=9
            )
            bb_upper, bb_middle, bb_lower = talib.BBANDS(
                close, timeperiod=20, nbdevup=2, nbdevdn=2
            )
            ema_9 = talib.EMA(close, timeperiod=9)
            ema_21 = talib.EMA(close, timeperiod=21)
            ema_50 = talib.EMA(close, timeperiod=50)
            sma_20 = talib.SMA(close, timeperiod=20)
            sma_50 = talib.SMA(close, timeperiod=50)
            atr_14 = talib.ATR(high, low, close, timeperiod=14)
        except Exception as e:
            self.logger.warning(
                "indicator_calc_failed", symbol=symbol, error=str(e)
            )
            return

        def last_val(arr: np.ndarray) -> float | None:
            if len(arr) == 0:
                return None
            v = float(arr[-1])
            return None if np.isnan(v) else v

        features = {
            "symbol": symbol,
            "open_time": open_time,
            "close": float(close[-1]),
            "rsi_14": last_val(rsi_14),
            "macd": last_val(macd),
            "macd_signal": last_val(macd_signal),
            "macd_hist": last_val(macd_hist),
            "bb_upper": last_val(bb_upper),
            "bb_middle": last_val(bb_middle),
            "bb_lower": last_val(bb_lower),
            "ema_9": last_val(ema_9),
            "ema_21": last_val(ema_21),
            "ema_50": last_val(ema_50),
            "sma_20": last_val(sma_20),
            "sma_50": last_val(sma_50),
            "atr_14": last_val(atr_14),
            "computed_at": datetime.utcnow().isoformat(),
        }

        if self._redis_bus is not None:
            await self._redis_bus.publish(features_stream(symbol), features)
            self._features_published += 1

        await self._write_features_to_influx(symbol, features, open_time)

    async def _write_features_to_influx(
        self,
        symbol: str,
        features: dict[str, Any],
        open_time_ms: int,
    ) -> None:
        if self._influx_session is None or not INFLUX_TOKEN:
            return

        field_parts: list[str] = []
        for key, val in features.items():
            if key in ("symbol", "open_time", "computed_at"):
                continue
            if val is None:
                continue
            field_parts.append(f"{key}={val}")

        if not field_parts:
            return

        timestamp_ns = open_time_ms * 1_000_000
        line = (
            f"features,symbol={symbol} "
            + ",".join(field_parts)
            + f" {timestamp_ns}"
        )

        url = f"{INFLUX_URL}/api/v2/write"
        params = {
            "org": INFLUX_ORG,
            "bucket": INFLUX_BUCKET,
            "precision": "ns",
        }
        try:
            async with self._influx_session.post(
                url, params=params, data=line.encode("utf-8")
            ) as resp:
                if resp.status == 204:
                    self._influx_writes += 1
                else:
                    body = await resp.text()
                    self.logger.warning(
                        "influx_features_write_failed",
                        status=resp.status,
                        body=body[:300],
                    )
                    self._influx_errors += 1
        except Exception as e:
            self.logger.warning("influx_features_write_exception", error=str(e))
            self._influx_errors += 1

    async def _stats_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)
                break
            except asyncio.TimeoutError:
                ready = sum(
                    1 for buf in self._buffers.values()
                    if len(buf) >= MIN_BARS_FOR_FEATURES
                )
                self.logger.info(
                    "feature_engineering_stats",
                    active_symbols=len(self._buffers),
                    symbols_ready_for_features=ready,
                    backfills_done=self._backfills_done,
                    features_published=self._features_published,
                    influx_writes=self._influx_writes,
                    influx_errors=self._influx_errors,
                )

    async def _shutdown(self) -> None:
        self.logger.info("feature_engineering_shutting_down")
        if self._http_session is not None:
            await self._http_session.close()
        if self._influx_session is not None:
            await self._influx_session.close()

    async def _health_check(self) -> dict[str, float]:
        return {
            "active_symbols": float(len(self._buffers)),
            "features_published": float(self._features_published),
            "backfills_done": float(self._backfills_done),
            "influx_writes": float(self._influx_writes),
            "influx_errors": float(self._influx_errors),
        }


if __name__ == "__main__":
    asyncio.run(run_agent(FeatureEngineeringAgent))
