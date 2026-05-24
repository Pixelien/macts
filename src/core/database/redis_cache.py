"""Redis cache wrapper.

Streaming dışı veri için Redis kullanımı:
- Feature snapshot cache (son hesaplanan feature'lar)
- Model checkpoint metadata cache
- Universe snapshot
- Korelasyon matrisi cache
- Volatility regime cache
- Distributed lock (model retraining için)

NOT: İskelet seviyesindedir.
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis

from src.core.logging import get_logger

logger = get_logger(__name__)


class RedisCache:
    """Async Redis cache wrapper."""

    def __init__(
        self,
        host: str = "redis",
        port: int = 6379,
        password: str | None = None,
        db: int = 0,
    ) -> None:
        """Redis cache başlat."""
        self._host = host
        self._port = port
        self._password = password
        self._db = db
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._client = aioredis.Redis(
            host=self._host,
            port=self._port,
            password=self._password,
            db=self._db,
            decode_responses=True,
        )
        await self._client.ping()
        logger.info("redis_cache_connected", host=self._host, db=self._db)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_json(self, key: str) -> Any | None:
        """JSON-encoded value oku."""
        if self._client is None:
            raise RuntimeError("Redis bağlı değil")
        raw = await self._client.get(key)
        return json.loads(raw) if raw else None

    async def set_json(
        self, key: str, value: Any, *, ttl_seconds: int | None = None
    ) -> None:
        """JSON-encoded value yaz."""
        if self._client is None:
            raise RuntimeError("Redis bağlı değil")
        await self._client.set(key, json.dumps(value, default=str), ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        if self._client is None:
            raise RuntimeError("Redis bağlı değil")
        await self._client.delete(key)

    async def acquire_lock(
        self, name: str, *, ttl_ms: int = 30000, blocking: bool = False
    ) -> Any:
        """Distributed lock al.

        TODO: redis.asyncio.lock.Lock kullan, blocking=False ise hemen
        dön.
        """
        if self._client is None:
            raise RuntimeError("Redis bağlı değil")
        lock = self._client.lock(name, timeout=ttl_ms / 1000)
        acquired = await lock.acquire(blocking=blocking)
        return lock if acquired else None
