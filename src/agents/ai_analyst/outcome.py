"""Tahmin-vs-gerçek karşılaştırma (Faz 3 / Aşama 4 — kendi kendini geliştirme döngüsü).

Her yayınlanan LLM analizi, entry fiyatıyla birlikte llm_prediction tablosuna
yazılır. Zamanlanmış outcome döngüsü (agent içinde, ~5 dk'da bir) vadesi dolan
(time_horizon geçen) tahminleri o anki gerçek fiyatla karşılaştırıp
llm_prediction_outcome tablosuna yön doğruluğunu yazar.

Bu tablolar Aşama 4'ün ikinci yarısının (bandit / prompt karşılaştırma) ve
Grafana "AI Analyst Performance" dashboard'unun veri kaynağıdır.

Fiyat kaynağı: stream:ticks.{symbol}.kline.1m (agent'ın canlı takip ettiği
son kapanış). Değerlendirme, vade dolduktan sonraki ilk döngü tick'inde
yapılır; sapma eval_delay_seconds olarak kaydedilir (şeffaflık).

Saf sınıflandırma mantığı (classify_outcome) ağ/DB'siz — unit testli.
"""

from __future__ import annotations

import json
from typing import Any

from src.core.database.postgres_repo import PostgresRepository
from src.core.logging import get_logger

logger = get_logger(__name__)

HORIZON_SECONDS: dict[str, int] = {"1h": 3600, "4h": 14400, "1d": 86400}

# |getiri| bu bandın altındaysa piyasa "flat" sayılır -> neutral tahmin doğru.
DEFAULT_FLAT_BAND_PCT = 0.10


# =============================================================================
# Saf sınıflandırma
# =============================================================================

def classify_outcome(
    direction: str,
    entry_price: float,
    exit_price: float,
    flat_band_pct: float = DEFAULT_FLAT_BAND_PCT,
) -> tuple[float, str, bool]:
    """Tahmin sonucunu sınıflandır.

    Returns:
        (return_pct, actual_direction, correct)
        actual_direction: "up" | "down" | "flat"

    Raises:
        ValueError: entry_price <= 0 (bölme güvenliği) veya bilinmeyen direction.
    """
    if entry_price <= 0:
        raise ValueError("entry_price pozitif olmalı")
    if direction not in ("long", "short", "neutral"):
        raise ValueError(f"bilinmeyen direction: {direction}")

    return_pct = (exit_price - entry_price) / entry_price * 100.0
    if return_pct > flat_band_pct:
        actual = "up"
    elif return_pct < -flat_band_pct:
        actual = "down"
    else:
        actual = "flat"

    correct = (
        (direction == "long" and actual == "up")
        or (direction == "short" and actual == "down")
        or (direction == "neutral" and actual == "flat")
    )
    return return_pct, actual, correct


def parse_kline_close(msg: dict[str, Any]) -> float | None:
    """Redis kline mesajından kapanış fiyatını güvenli çıkar.

    Redis Streams tüm alanları string'e çevirir; is_closed "True"/"true"/bool
    gelebilir, close Decimal-string gelir. Yalnızca KAPANMIŞ mumların fiyatı
    döndürülür (kapanmamış mum fiyatı hâlâ oynar).
    """
    is_closed_raw = msg.get("is_closed", False)
    if isinstance(is_closed_raw, str):
        is_closed = is_closed_raw.strip().lower() in ("true", "1")
    else:
        is_closed = bool(is_closed_raw)
    if not is_closed:
        return None
    raw = msg.get("close")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# =============================================================================
# Postgres kalıcılığı
# =============================================================================

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS llm_prediction (
    id BIGSERIAL PRIMARY KEY,
    message_id TEXT UNIQUE,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    deadline TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    time_horizon TEXT NOT NULL,
    model_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    evaluated BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_llm_pred_due
    ON llm_prediction (deadline) WHERE NOT evaluated;

CREATE TABLE IF NOT EXISTS llm_prediction_outcome (
    id BIGSERIAL PRIMARY KEY,
    prediction_id BIGINT NOT NULL REFERENCES llm_prediction(id),
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    exit_price DOUBLE PRECISION NOT NULL,
    return_pct DOUBLE PRECISION NOT NULL,
    actual_direction TEXT NOT NULL,
    correct BOOLEAN NOT NULL,
    eval_delay_seconds DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_outcome_pred
    ON llm_prediction_outcome (prediction_id);
"""

INSERT_PREDICTION_SQL = """
INSERT INTO llm_prediction
    (message_id, deadline, symbol, direction, confidence, time_horizon,
     model_id, prompt_version, entry_price)
VALUES (%s, now() + (%s || ' seconds')::interval, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (message_id) DO NOTHING
"""

FETCH_DUE_SQL = """
SELECT id, symbol, direction, entry_price, time_horizon, model_id,
       prompt_version, EXTRACT(EPOCH FROM (now() - deadline)) AS delay_s
FROM llm_prediction
WHERE NOT evaluated AND deadline <= now()
ORDER BY deadline
LIMIT %s
"""

INSERT_OUTCOME_SQL = """
INSERT INTO llm_prediction_outcome
    (prediction_id, exit_price, return_pct, actual_direction, correct,
     eval_delay_seconds)
VALUES (%s, %s, %s, %s, %s, %s)
"""

MARK_EVALUATED_SQL = "UPDATE llm_prediction SET evaluated = TRUE WHERE id = %s"


class PredictionStore:
    """llm_prediction / llm_prediction_outcome tabloları üzerinde ince katman.

    Tüm operasyonlar best-effort: DB hatası analiz akışını asla bozmaz.
    """

    def __init__(self, repo: PostgresRepository) -> None:
        self._repo = repo
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def ensure_tables(self) -> None:
        try:
            await self._repo.execute(CREATE_TABLES_SQL)
            self._ready = True
        except Exception as e:
            logger.warning("llm_prediction_tables_init_failed", error=str(e))
            self._ready = False

    async def record_prediction(
        self, analysis: dict[str, Any], entry_price: float
    ) -> None:
        """Yayınlanan analizi tahmin olarak kaydet (vade = ts + horizon)."""
        if not self._ready:
            return
        horizon = str(analysis.get("time_horizon", "1h"))
        seconds = HORIZON_SECONDS.get(horizon, 3600)
        try:
            await self._repo.execute(
                INSERT_PREDICTION_SQL,
                (
                    str(analysis.get("message_id")),
                    str(seconds),
                    analysis["symbol"],
                    analysis["direction"],
                    float(analysis["confidence"]),
                    horizon,
                    analysis["model_id"],
                    analysis["prompt_version"],
                    float(entry_price),
                ),
            )
        except Exception as e:
            logger.warning("prediction_record_failed", error=str(e))

    async def fetch_due(self, limit: int = 200) -> list[dict[str, Any]]:
        """Vadesi dolmuş, henüz değerlendirilmemiş tahminler."""
        if not self._ready:
            return []
        try:
            rows = await self._repo.fetch_all(FETCH_DUE_SQL, (limit,))
        except Exception as e:
            logger.warning("prediction_fetch_due_failed", error=str(e))
            return []
        cols = (
            "id", "symbol", "direction", "entry_price", "time_horizon",
            "model_id", "prompt_version", "delay_s",
        )
        return [dict(zip(cols, r, strict=True)) for r in rows]

    async def record_outcome(
        self,
        prediction_id: int,
        *,
        exit_price: float,
        return_pct: float,
        actual_direction: str,
        correct: bool,
        eval_delay_seconds: float,
    ) -> bool:
        """Sonucu yaz + tahmini evaluated işaretle."""
        try:
            await self._repo.execute(
                INSERT_OUTCOME_SQL,
                (
                    prediction_id, exit_price, round(return_pct, 6),
                    actual_direction, correct, round(eval_delay_seconds, 1),
                ),
            )
            await self._repo.execute(MARK_EVALUATED_SQL, (prediction_id,))
            return True
        except Exception as e:
            logger.warning(
                "outcome_record_failed", prediction_id=prediction_id, error=str(e)
            )
            return False


def enrich_features_with_price(
    features: dict[str, Any], close: float | None
) -> dict[str, Any]:
    """LLM payload'ına son kapanış fiyatını ekle.

    FeatureSnapshot şemasında fiyat alanı YOK (yalnızca indikatörler) —
    fiyatsız prompt modelin ortalamalara göre konum değerlendirmesini
    engelliyordu (canlıda gözlenen aşırı neutral yanlılığının olası nedeni).
    """
    if close is None:
        return features
    enriched = dict(features)
    enriched["close"] = close
    # custom alanı JSON string olarak gelmiş olabilir; dokunma.
    return enriched


def parse_features_ts(features: dict[str, Any]) -> str | None:
    """Loglama için feature timestamp'ini güvenli çıkar."""
    ts = features.get("timestamp")
    if isinstance(ts, str):
        try:
            json.loads(ts)
        except (ValueError, TypeError):
            return ts
    return str(ts) if ts is not None else None
