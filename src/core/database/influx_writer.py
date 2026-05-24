"""InfluxDB time-series writer.

Tüm market data (kline, trade, depth snapshot, mark price, vs.) ve
performans metrikleri InfluxDB 2.x'e batch olarak yazılır.

NOT: İskelet seviyesindedir.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.core.logging import get_logger

logger = get_logger(__name__)


class InfluxDBWriter:
    """Async batched InfluxDB writer."""

    def __init__(
        self,
        url: str,
        token: str,
        org: str,
        bucket: str,
        *,
        batch_size: int = 5000,
        flush_interval_seconds: float = 5.0,
    ) -> None:
        """InfluxDB writer başlat.

        Args:
            url: http://influxdb:8086
            token: InfluxDB token.
            org: Organization.
            bucket: Bucket adı.
            batch_size: Bir batch'te kaç point toplanacak.
            flush_interval_seconds: Max bekleme süresi.
        """
        self._url = url
        self._token = token
        self._org = org
        self._bucket = bucket
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._buffer: list[Any] = []
        self._buffer_lock = asyncio.Lock()
        self._client: Any = None

    async def connect(self) -> None:
        """InfluxDB'ye bağlan."""
        # TODO: from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync
        # TODO: self._client = InfluxDBClientAsync(url=..., token=..., org=...)
        logger.info("influxdb_connected", url=self._url, bucket=self._bucket)

    async def close(self) -> None:
        """Bağlantıyı kapat (kalanları flush et)."""
        await self.flush()
        # TODO: await self._client.close()

    async def write_kline(
        self,
        symbol: str,
        interval: str,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        timestamp: int,
    ) -> None:
        """Kline verisini buffer'a ekle."""
        # TODO: Point("klines").tag("symbol", symbol).tag("interval", interval)...
        async with self._buffer_lock:
            self._buffer.append(
                {
                    "measurement": "klines",
                    "tags": {"symbol": symbol, "interval": interval},
                    "fields": {
                        "open": open_,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": volume,
                    },
                    "time": timestamp,
                }
            )
        if len(self._buffer) >= self._batch_size:
            await self.flush()

    async def flush(self) -> None:
        """Buffer'daki tüm point'leri InfluxDB'ye yaz."""
        async with self._buffer_lock:
            if not self._buffer:
                return
            points_to_write = self._buffer.copy()
            self._buffer.clear()

        # TODO: write_api.write(bucket=..., record=points_to_write)
        logger.debug("influxdb_flushed", count=len(points_to_write))
