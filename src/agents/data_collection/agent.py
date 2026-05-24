"""Data Collection Agent — Faz 1 (Dinamik Universe + REST polling + InfluxDB).

VPS lokasyonu Binance WebSocket'e izin vermediği için REST polling
fallback'i kullanıyoruz. Universe Market Scanner agent'ından dinamik olarak
alınır, sembol listesi her snapshot'ta güncellenir.

Veri akışı:
- Binance REST → kline polling (her sembol için ayrı task)
- Redis Streams: stream:ticks.{symbol}.kline.{interval}
- InfluxDB: kalıcı zaman serisi
- Universe: stream:universe.snapshot (Market Scanner'dan)
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

import aiohttp

from src.agents.base import BaseAgent, run_agent

# Mainnet REST endpoint
BINANCE_MAINNET_REST = "https://fapi.binance.com"

# Polling parametreleri
INITIAL_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]  # Market Scanner snapshot'ı gelene kadar
KLINE_INTERVAL = "1m"
POLL_INTERVAL_SECONDS = 5.0
KLINE_LIMIT = 2

# Universe stream
STREAM_UNIVERSE_SNAPSHOT = "stream:universe.snapshot"

# InfluxDB config
INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "gazifintech")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "macts_market_data")


def kline_stream(symbol: str, interval: str) -> str:
    return f"stream:ticks.{symbol.lower()}.kline.{interval}"


class DataCollectionAgent(BaseAgent):
    """Dinamik universe ile REST polling tabanlı kline veri toplama agent'ı."""

    agent_name = "data_collection"
    heartbeat_interval = 5.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._http_session: aiohttp.ClientSession | None = None
        self._influx_session: aiohttp.ClientSession | None = None
        self._messages_published = 0
        self._influx_writes = 0
        self._influx_errors = 0
        self._last_kline_time: dict[str, int] = {}
        # Dinamik polling task'ları: {symbol: asyncio.Task}
        self._poll_tasks: dict[str, asyncio.Task[None]] = {}
        self._universe_updates_received = 0

    async def _initialize(self) -> None:
        """HTTP session'ları oluştur."""
        self.logger.info(
            "data_collection_initializing",
            initial_symbols=INITIAL_SYMBOLS,
            interval=KLINE_INTERVAL,
            poll_interval_seconds=POLL_INTERVAL_SECONDS,
            mode="rest_polling+dynamic_universe",
            influx_url=INFLUX_URL,
            influx_bucket=INFLUX_BUCKET,
        )
        timeout = aiohttp.ClientTimeout(total=10)
        self._http_session = aiohttp.ClientSession(timeout=timeout)

        influx_headers = {
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "text/plain; charset=utf-8",
            "Accept": "application/json",
        }
        self._influx_session = aiohttp.ClientSession(
            timeout=timeout,
            headers=influx_headers,
        )

    async def _run(self) -> None:
        """Polling + universe listener + stats loop'u başlat."""
        self.logger.info("data_collection_loop_started")

        # 1. Initial sembollere polling başlat (Market Scanner gelene kadar)
        for sym in INITIAL_SYMBOLS:
            self._start_polling(sym)

        # 2. Universe snapshot dinleyiciyi başlat
        universe_task = asyncio.create_task(self._listen_universe_snapshots())

        # 3. Stats loglayıcı
        stats_task = asyncio.create_task(self._stats_loop())

        try:
            await self._stop_event.wait()
        finally:
            # Tüm polling task'larını iptal et
            for task in self._poll_tasks.values():
                task.cancel()
            universe_task.cancel()
            stats_task.cancel()
            await asyncio.gather(
                *self._poll_tasks.values(),
                universe_task,
                stats_task,
                return_exceptions=True,
            )

    def _start_polling(self, symbol: str) -> None:
        """Bir sembol için polling task'ı başlat (henüz yoksa)."""
        if symbol in self._poll_tasks and not self._poll_tasks[symbol].done():
            return  # zaten çalışıyor
        self._poll_tasks[symbol] = asyncio.create_task(self._poll_symbol(symbol))

    def _stop_polling(self, symbol: str) -> None:
        """Bir sembol için polling task'ını iptal et."""
        task = self._poll_tasks.pop(symbol, None)
        if task is not None and not task.done():
            task.cancel()
            self.logger.info("polling_stopped", symbol=symbol)

    async def _listen_universe_snapshots(self) -> None:
        """Market Scanner'dan gelen universe snapshot'larını dinle ve uygula."""
        if self._redis_bus is None:
            self.logger.warning("redis_bus_unavailable_for_universe_listener")
            return

        self.logger.info("universe_listener_started", stream=STREAM_UNIVERSE_SNAPSHOT)

        # from_beginning=False → sadece bizim subscribe'dan SONRA gelen snapshot'lar
        # NOT: Initial sembollerle başladığımız için bu makul; Market Scanner ~5dk içinde
        # mutlaka yeni snapshot atar.
        try:
            async for msg in self._redis_bus.subscribe(
                STREAM_UNIVERSE_SNAPSHOT,
                from_beginning=False,
            ):
                if self._stop_event.is_set():
                    break
                await self._apply_universe_snapshot(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.exception("universe_listener_failed", error=str(e))

    async def _apply_universe_snapshot(self, msg: dict[str, Any]) -> None:
        """Snapshot'ı uygula: yeni semboller için polling başlat, çıkanlar için durdur."""
        symbols_raw = msg.get("symbols")
        if symbols_raw is None:
            return

        # symbols ya list olarak gelir (subscribe deserialize eder), ya string olarak
        if isinstance(symbols_raw, str):
            import json
            try:
                new_universe = set(json.loads(symbols_raw))
            except json.JSONDecodeError:
                self.logger.warning("universe_parse_failed", symbols_raw=symbols_raw[:100])
                return
        elif isinstance(symbols_raw, list):
            new_universe = set(symbols_raw)
        else:
            return

        current = set(self._poll_tasks.keys())
        added = new_universe - current
        removed = current - new_universe

        if added or removed:
            self.logger.info(
                "universe_changed",
                added=sorted(added),
                removed=sorted(removed),
                new_size=len(new_universe),
                old_size=len(current),
            )

            for sym in added:
                self._start_polling(sym)
            for sym in removed:
                self._stop_polling(sym)

        self._universe_updates_received += 1

    async def _poll_symbol(self, symbol: str) -> None:
        """Bir sembol için periyodik kline polling."""
        self.logger.info("polling_started", symbol=symbol)
        try:
            while not self._stop_event.is_set():
                try:
                    await self._fetch_and_publish(symbol)
                except Exception as e:
                    self.logger.warning(
                        "kline_fetch_failed",
                        symbol=symbol,
                        error=str(e),
                    )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=POLL_INTERVAL_SECONDS,
                    )
                    break
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            self.logger.info("polling_cancelled", symbol=symbol)
            raise

    async def _fetch_and_publish(self, symbol: str) -> None:
        """Bir sembol için son kline'ı çek ve Redis + InfluxDB'ye yaz."""
        if self._http_session is None:
            return

        url = f"{BINANCE_MAINNET_REST}/fapi/v1/klines"
        params = {
            "symbol": symbol,
            "interval": KLINE_INTERVAL,
            "limit": KLINE_LIMIT,
        }
        async with self._http_session.get(url, params=params) as resp:
            resp.raise_for_status()
            klines = await resp.json()

        if not klines:
            return

        for kline in klines:
            await self._publish_kline(symbol, kline)

    async def _publish_kline(self, symbol: str, kline: list[Any]) -> None:
        """Tek bir kline'ı Redis ve InfluxDB'ye yaz."""
        open_time = int(kline[0])
        close_time = int(kline[6])
        now_ms = int(datetime.utcnow().timestamp() * 1000)
        is_closed = now_ms > close_time

        payload = {
            "symbol": symbol,
            "interval": KLINE_INTERVAL,
            "open_time": open_time,
            "close_time": close_time,
            "open": kline[1],
            "high": kline[2],
            "low": kline[3],
            "close": kline[4],
            "volume": kline[5],
            "quote_volume": kline[7],
            "trade_count": int(kline[8]),
            "is_closed": is_closed,
            "received_at": datetime.utcnow().isoformat(),
            "source": "rest_polling",
        }

        if self._redis_bus is not None:
            await self._redis_bus.publish(
                kline_stream(symbol, KLINE_INTERVAL),
                payload,
            )
            self._messages_published += 1
            self._last_kline_time[symbol] = open_time

        await self._write_to_influx(symbol, kline, open_time)

    async def _write_to_influx(
        self,
        symbol: str,
        kline: list[Any],
        open_time_ms: int,
    ) -> None:
        """InfluxDB'ye line protocol formatında kline yaz."""
        if self._influx_session is None or not INFLUX_TOKEN:
            return

        try:
            open_p = float(kline[1])
            high_p = float(kline[2])
            low_p = float(kline[3])
            close_p = float(kline[4])
            volume = float(kline[5])
            quote_volume = float(kline[7])
            trades = int(kline[8])
        except (ValueError, IndexError) as e:
            self.logger.warning("kline_parse_failed", error=str(e))
            return

        timestamp_ns = open_time_ms * 1_000_000

        line = (
            f"klines,symbol={symbol},interval={KLINE_INTERVAL} "
            f"open={open_p},high={high_p},low={low_p},close={close_p},"
            f"volume={volume},quote_volume={quote_volume},trades={trades}i "
            f"{timestamp_ns}"
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
                        "influx_write_unexpected_status",
                        status=resp.status,
                        body=body[:300],
                    )
                    self._influx_errors += 1
        except Exception as e:
            self.logger.warning("influx_write_failed", error=str(e))
            self._influx_errors += 1

    async def _stats_loop(self) -> None:
        """Her 30 saniyede bir istatistik logla."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)
                break
            except asyncio.TimeoutError:
                active_polling = [
                    sym for sym, t in self._poll_tasks.items() if not t.done()
                ]
                self.logger.info(
                    "data_collection_stats",
                    messages_published=self._messages_published,
                    influx_writes=self._influx_writes,
                    influx_errors=self._influx_errors,
                    active_symbols_count=len(active_polling),
                    active_symbols=sorted(active_polling),
                    universe_updates_received=self._universe_updates_received,
                )

    async def _shutdown(self) -> None:
        """Cleanup."""
        self.logger.info("data_collection_shutting_down")
        if self._http_session is not None:
            await self._http_session.close()
        if self._influx_session is not None:
            await self._influx_session.close()

    async def _health_check(self) -> dict[str, float]:
        active = sum(1 for t in self._poll_tasks.values() if not t.done())
        return {
            "active_symbols": float(active),
            "messages_published": float(self._messages_published),
            "influx_writes": float(self._influx_writes),
            "influx_errors": float(self._influx_errors),
            "universe_updates_received": float(self._universe_updates_received),
        }


if __name__ == "__main__":
    asyncio.run(run_agent(DataCollectionAgent))
