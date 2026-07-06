"""Fallback zinciri — 429 ≠ 5xx politikasının uygulandığı yer.

Politika (docs/AI_ANALYST_MODEL_SELECTION.md §2 + skill: nvidia-nim-client):
- 5xx / ağ hatası : birincil modele 1 retry (backoff'lu), sonra yedek model.
- 429 (kota)      : FALLBACK DENENMEZ — kota API anahtarı bazında global,
                    yedek model de aynı kotayı paylaşır. Tur atlanır.
- 402 (kredi)     : fallback denenmez, üst katmana yükselir (circuit breaker
                    değerlendirmesi için).
- 404             : o model retry edilmez, sıradaki modele geçilir ve
                    konfigürasyon hatası olarak loglanır.

Çağrı fonksiyonu enjekte edilir -> ağ olmadan unit test edilebilir.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from src.core.llm.config import LLMModelConfig
from src.core.llm.nvidia_client import (
    NIMNotFoundError,
    NIMQuotaError,
    NIMRateLimitError,
    NIMServerError,
)
from src.core.llm.utils import compute_backoff

# call_fn imzası: (model_config) -> (content, usage, latency)
CallFn = Callable[[LLMModelConfig], Awaitable[tuple[str, dict[str, Any], float]]]


class AllModelsFailedError(Exception):
    """Zincirdeki tüm modeller başarısız oldu."""


class FallbackChain:
    """Sıralı model zinciri yürütücüsü."""

    def __init__(
        self,
        models: list[LLMModelConfig],
        *,
        retries_per_model: int = 1,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not models:
            raise ValueError("en az bir model gerekli")
        self.models = models
        self.retries_per_model = retries_per_model
        self._sleep = sleep

    async def run(
        self, call_fn: CallFn
    ) -> tuple[str, dict[str, Any], float, LLMModelConfig]:
        """Zinciri yürüt.

        Returns:
            (content, usage, latency, kullanılan_model)

        Raises:
            NIMRateLimitError: 429 — anında yükselir, fallback yok.
            NIMQuotaError: 402 — anında yükselir.
            AllModelsFailedError: zincirin tamamı tükendi.
        """
        errors: list[str] = []
        for model in self.models:
            attempt = 0
            while attempt <= self.retries_per_model:
                try:
                    content, usage, latency = await call_fn(model)
                    return content, usage, latency, model
                except (NIMRateLimitError, NIMQuotaError):
                    # Kota hataları zincir genelinde anlamlı — yükselt.
                    raise
                except NIMNotFoundError as e:
                    errors.append(f"{model.model_id}: 404 (config hatası)")
                    _ = e
                    break  # bu modeli retry etme, sıradakine geç
                except NIMServerError as e:
                    errors.append(f"{model.model_id}: {e}")
                    attempt += 1
                    if attempt <= self.retries_per_model:
                        await self._sleep(compute_backoff(attempt - 1))
                    # retry hakkı bitti -> while biter, sıradaki modele geç
        raise AllModelsFailedError("; ".join(errors) or "bilinmeyen hata")
