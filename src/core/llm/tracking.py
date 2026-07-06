"""LLM analiz cache'i (Redis) ve kullanım kaydı (Postgres).

Cache: aynı sembol + aynı feature snapshot + aynı prompt versiyonu + aynı
model için TTL'li cache — gereksiz tekrar çağrıyı önler (kota tasarrufu).

Usage tracking: her çağrı llm_usage_log tablosuna yazılır; günlük/aylık
kota tüketimi Grafana'da izlenebilir. Kayıt hatası analizi ASLA bozmamalı
(best-effort).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.core.database.postgres_repo import PostgresRepository
from src.core.database.redis_cache import RedisCache
from src.core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# Cache
# =============================================================================

def build_cache_key(
    symbol: str,
    features: dict[str, Any],
    prompt_version: str,
    model_id: str,
) -> str:
    """Deterministik cache anahtarı üret.

    Feature dict'i canonical JSON'a (sort_keys) çevrilip sha256'lanır —
    alan sırası farklı olsa bile aynı snapshot aynı anahtarı üretir.
    """
    canonical = json.dumps(features, sort_keys=True, default=str)
    digest = hashlib.sha256(
        f"{symbol}|{prompt_version}|{model_id}|{canonical}".encode()
    ).hexdigest()[:32]
    return f"llm:analysis:{symbol.lower()}:{digest}"


class AnalysisCache:
    """RedisCache üzerinde ince LLM-analiz cache'i."""

    def __init__(self, redis_cache: RedisCache, ttl_seconds: int) -> None:
        self._cache = redis_cache
        self._ttl = ttl_seconds

    async def get(self, key: str) -> dict[str, Any] | None:
        try:
            return await self._cache.get_json(key)
        except Exception as e:
            logger.warning("llm_cache_get_failed", error=str(e))
            return None

    async def set(self, key: str, analysis: dict[str, Any]) -> None:
        try:
            await self._cache.set_json(key, analysis, ttl_seconds=self._ttl)
        except Exception as e:
            logger.warning("llm_cache_set_failed", error=str(e))


# =============================================================================
# Usage tracking
# =============================================================================

CREATE_USAGE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS llm_usage_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol TEXT NOT NULL,
    model_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    latency_seconds DOUBLE PRECISION,
    success BOOLEAN NOT NULL,
    cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage_log (ts);
CREATE INDEX IF NOT EXISTS idx_llm_usage_model ON llm_usage_log (model_id, ts);
"""

INSERT_USAGE_SQL = """
INSERT INTO llm_usage_log
    (symbol, model_id, prompt_version, prompt_tokens, completion_tokens,
     latency_seconds, success, cache_hit, error)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


class UsageTracker:
    """llm_usage_log tablosuna best-effort kayıt."""

    def __init__(self, repo: PostgresRepository) -> None:
        self._repo = repo
        self._ready = False

    async def ensure_table(self) -> None:
        try:
            await self._repo.execute(CREATE_USAGE_TABLE_SQL)
            self._ready = True
        except Exception as e:
            logger.warning("llm_usage_table_init_failed", error=str(e))
            self._ready = False

    async def record(
        self,
        *,
        symbol: str,
        model_id: str,
        prompt_version: str,
        usage: dict[str, Any] | None = None,
        latency_seconds: float | None = None,
        success: bool,
        cache_hit: bool = False,
        error: str | None = None,
    ) -> None:
        """Tek çağrı kaydı. Hata analizi bozmaz, sadece loglanır."""
        if not self._ready:
            return
        u = usage or {}
        try:
            await self._repo.execute(
                INSERT_USAGE_SQL,
                (
                    symbol,
                    model_id,
                    prompt_version,
                    u.get("prompt_tokens"),
                    u.get("completion_tokens"),
                    latency_seconds,
                    success,
                    cache_hit,
                    (error or "")[:500] or None,
                ),
            )
        except Exception as e:
            logger.warning("llm_usage_record_failed", error=str(e))
