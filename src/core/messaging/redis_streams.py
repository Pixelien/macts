"""Redis Streams tabanlı mesajlaşma backend'i.

Düşük gecikmeli, yüksek frekanslı mesajlar için optimize edilmiştir
(tick verisi, feature güncellemeleri, sinyaller). Mesajlar XADD ile
yayınlanır, XREAD/XREADGROUP ile tüketilir.

Not: Bu modül iskelet seviyesindedir. Tam implementasyon için
TODO'lara bakın.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis

from src.core.logging import get_logger
from src.core.messaging.base import MessageBus

logger = get_logger(__name__)


class RedisStreamsBus(MessageBus):
    """Redis Streams tabanlı message bus."""

    def __init__(
        self,
        host: str = "redis",
        port: int = 6379,
        password: str | None = None,
        db: int = 0,
        max_stream_length: int = 100_000,
        block_ms: int = 100,
    ) -> None:
        """RedisStreamsBus başlat.

        Args:
            host: Redis sunucu adresi.
            port: Redis port.
            password: Redis şifresi (None ise auth yok).
            db: Redis veritabanı numarası.
            max_stream_length: Stream'in maksimum mesaj sayısı (XADD MAXLEN).
            block_ms: XREAD block timeout (ms).
        """
        self._host = host
        self._port = port
        self._password = password
        self._db = db
        self._max_stream_length = max_stream_length
        self._block_ms = block_ms
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        """Redis'e bağlan."""
        self._client = aioredis.Redis(
            host=self._host,
            port=self._port,
            password=self._password,
            db=self._db,
            decode_responses=True,
        )
        await self._client.ping()
        logger.info(
            "redis_streams_connected",
            host=self._host,
            port=self._port,
            db=self._db,
        )

    async def disconnect(self) -> None:
        """Bağlantıyı kapat."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("redis_streams_disconnected")

    async def publish(
        self,
        channel: str,
        message: dict[str, Any],
        *,
        key: str | None = None,  # noqa: ARG002 (Redis'te kullanılmıyor)
    ) -> None:
        """Stream'e mesaj ekle.

        Args:
            channel: Stream adı.
            message: Mesaj.
            key: (yoksayılır)
        """
        if self._client is None:
            raise RuntimeError("RedisStreamsBus connect() çağrılmamış")

        # Redis Streams alanları string olmalı; nested dict'leri JSON'a çevir
        flat_payload = {
            k: json.dumps(v, default=str) if not isinstance(v, str | int | float) else str(v)
            for k, v in message.items()
        }

        await self._client.xadd(
            channel,
            flat_payload,
            maxlen=self._max_stream_length,
            approximate=True,
        )

    async def subscribe(
        self,
        channel: str,
        *,
        group: str | None = None,
        consumer: str | None = None,
        from_beginning: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream'i tüket.

        TODO: Implementasyon detayları:
        - group ve consumer verilmişse XGROUP CREATE + XREADGROUP kullan
        - Aksi halde XREAD ile son mesajdan veya başından oku
        - Mesajları parse edip dict olarak yield et
        - Her mesajla birlikte _stream_id'yi de döndür (acknowledge için)
        - Bağlantı kopması durumunda exponential backoff ile reconnect
        """
        if self._client is None:
            raise RuntimeError("RedisStreamsBus connect() çağrılmamış")

        last_id = "0" if from_beginning else "$"

        # Consumer group setup
        if group is not None:
            try:
                await self._client.xgroup_create(channel, group, id="0", mkstream=True)
            except aioredis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    raise

        while True:
            try:
                if group and consumer:
                    response = await self._client.xreadgroup(
                        group,
                        consumer,
                        {channel: ">"},
                        count=100,
                        block=self._block_ms,
                    )
                else:
                    response = await self._client.xread(
                        {channel: last_id},
                        count=100,
                        block=self._block_ms,
                    )

                if not response:
                    continue

                for _stream, messages in response:
                    for msg_id, fields in messages:
                        # Field'leri parse et
                        parsed: dict[str, Any] = {"_stream_id": msg_id}
                        for k, v in fields.items():
                            try:
                                parsed[k] = json.loads(v)
                            except (json.JSONDecodeError, TypeError):
                                parsed[k] = v
                        last_id = msg_id
                        yield parsed
            except Exception as e:
                logger.exception("redis_subscribe_error", channel=channel, error=str(e))
                # TODO: exponential backoff reconnect
                raise

    async def acknowledge(
        self,
        channel: str,
        message_id: str,
        *,
        group: str | None = None,
    ) -> None:
        """Mesajı işlendi olarak işaretle (XACK)."""
        if self._client is None or group is None:
            return
        await self._client.xack(channel, group, message_id)

    async def health_check(self) -> bool:
        """Redis bağlantısı sağlıklı mı?"""
        if self._client is None:
            return False
        try:
            return bool(await self._client.ping())
        except Exception:
            return False
