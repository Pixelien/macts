"""Temporal Fusion Transformer (TFT) tabanlı fiyat tahmini.

pytorch-forecasting kütüphanesi üzerine kurulmuş, kripto fiyatları için
multi-horizon forecasting yapan model wrapper'ı.

NOT: Bu modül iskelet seviyesindedir. Tam implementasyon için TODO'lara
bakın.
"""

from __future__ import annotations

from typing import Any

from src.core.logging import get_logger

logger = get_logger(__name__)


class TFTForecaster:
    """Temporal Fusion Transformer wrapper."""

    def __init__(
        self,
        symbol: str,
        encoder_length: int = 60,
        prediction_horizon: int = 5,
        hidden_size: int = 64,
        attention_head_size: int = 4,
        dropout: float = 0.2,
        learning_rate: float = 1e-3,
    ) -> None:
        """TFT modelini başlat.

        Args:
            symbol: Hangi coin için model.
            encoder_length: Geçmiş kaç bar görsün (lookback window).
            prediction_horizon: Kaç bar ileri tahmin etsin.
            hidden_size: TFT hidden dim.
            attention_head_size: Multi-head attention head sayısı.
            dropout: Dropout oranı.
            learning_rate: Adam optimizer lr.
        """
        self.symbol = symbol
        self.encoder_length = encoder_length
        self.prediction_horizon = prediction_horizon
        self.hidden_size = hidden_size
        self.attention_head_size = attention_head_size
        self.dropout = dropout
        self.learning_rate = learning_rate
        self._model: Any = None
        self._dataset: Any = None
        self._is_trained = False

    def fit(
        self,
        train_df: Any,
        val_df: Any | None = None,
        max_epochs: int = 50,
        batch_size: int = 64,
    ) -> dict[str, float]:
        """Modeli eğit.

        TODO:
        1. pytorch_forecasting.TimeSeriesDataSet oluştur (target=close,
           known_reals=[time_idx], unknown_reals=[features...])
        2. TemporalFusionTransformer.from_dataset() ile modeli kur
        3. pytorch_lightning.Trainer ile eğit (early stopping, gradient clip)
        4. Validation loss ve metrikleri döndür
        """
        logger.info(
            "tft_training_stub",
            symbol=self.symbol,
            train_size=getattr(train_df, "shape", None),
        )
        return {"val_loss": 0.0, "mae": 0.0, "rmse": 0.0}

    def predict(self, recent_df: Any) -> dict[str, Any]:
        """Tahmin yap.

        Returns:
            {
                "predicted_returns": [...],     # her horizon için
                "predicted_prices": [...],
                "quantiles": {"p10": ..., "p50": ..., "p90": ...},
                "confidence": ...,
            }
        """
        # TODO: model.predict(...) ile inference
        return {
            "predicted_returns": [0.0] * self.prediction_horizon,
            "predicted_prices": [],
            "quantiles": {},
            "confidence": 0.0,
        }

    def save(self, path: str) -> None:
        """Modeli diske kaydet (TorchScript veya .ckpt)."""
        # TODO: torch.save(model.state_dict(), path)
        logger.info("tft_saved", symbol=self.symbol, path=path)

    def load(self, path: str) -> None:
        """Modeli diskten yükle."""
        # TODO: model.load_state_dict(torch.load(path))
        logger.info("tft_loaded", symbol=self.symbol, path=path)
        self._is_trained = True

    @property
    def is_trained(self) -> bool:
        return self._is_trained
