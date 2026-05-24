"""PostgreSQL repository katmanı.

İlişkisel veriler için kullanılır:
- Trade ve order geçmişi
- Pozisyon kayıtları
- Agent state snapshot'ları
- Audit log
- Risk parametre değişiklikleri
- Cooldown durumları (restart-resilient)

NOT: İskelet seviyesindedir. Gerçek implementasyon için Alembic
migration'ları ve detaylı tablo şemaları gerekir.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg_pool import AsyncConnectionPool

from src.core.logging import get_logger

logger = get_logger(__name__)


class PostgresRepository:
    """Async PostgreSQL connection pool wrapper."""

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        *,
        min_size: int = 2,
        max_size: int = 20,
    ) -> None:
        """Postgres repository başlat."""
        self._conninfo = (
            f"host={host} port={port} dbname={database} "
            f"user={user} password={password}"
        )
        self._min_size = min_size
        self._max_size = max_size
        self._pool: AsyncConnectionPool | None = None

    async def connect(self) -> None:
        """Connection pool'u başlat."""
        self._pool = AsyncConnectionPool(
            conninfo=self._conninfo,
            min_size=self._min_size,
            max_size=self._max_size,
            open=False,
        )
        await self._pool.open()
        logger.info("postgres_pool_opened", min=self._min_size, max=self._max_size)

    async def close(self) -> None:
        """Pool'u kapat."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        """INSERT/UPDATE/DELETE çalıştır."""
        if self._pool is None:
            raise RuntimeError("Pool open değil")
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                await conn.commit()

    async def fetch_one(
        self, query: str, params: tuple[Any, ...] | None = None
    ) -> tuple[Any, ...] | None:
        """Tek satır döndür."""
        if self._pool is None:
            raise RuntimeError("Pool open değil")
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                return await cur.fetchone()

    async def fetch_all(
        self, query: str, params: tuple[Any, ...] | None = None
    ) -> list[tuple[Any, ...]]:
        """Tüm satırları döndür."""
        if self._pool is None:
            raise RuntimeError("Pool open değil")
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                return await cur.fetchall()

    # =========================================================================
    # Domain-specific queries (TODO)
    # =========================================================================

    async def insert_trade(self, trade_dict: dict[str, Any]) -> None:
        """Trade kaydını insert et.

        TODO: trades tablosu schema'sına göre INSERT yap.
        Tablo: trades(id, symbol, side, qty, price, fee, pnl, executed_at, ...)
        """

    async def insert_order(self, order_dict: dict[str, Any]) -> None:
        """Order kaydını insert et."""

    async def update_order_status(
        self,
        client_order_id: str,
        status: str,
        filled_quantity: float,
    ) -> None:
        """Order status güncelle."""

    async def get_active_cooldowns(self) -> list[dict[str, Any]]:
        """Halen aktif cooldown'ları getir."""
        return []

    async def insert_audit_log(
        self,
        event_type: str,
        agent_name: str,
        payload: dict[str, Any],
    ) -> None:
        """Audit log'a kayıt ekle (append-only tablo)."""
