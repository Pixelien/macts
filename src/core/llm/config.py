"""LLM katmanı konfigürasyonu.

config/llm_config.yaml'dan yüklenir; dosya yoksa probe ile doğrulanmış
güvenli varsayılanlar kullanılır (docs/AI_ANALYST_MODEL_SELECTION.md).
API anahtarı HER ZAMAN env'den okunur (NVIDIA_API_KEY), yaml'a yazılmaz.

AppConfig'e bilinçli olarak DAHİL EDİLMEDİ: AppConfig'in tüm alanları
zorunlu olduğundan yeni alan eklemek VPS'teki mevcut config.yaml'ı
kırardı. LLM konfigürasyonu kendi dosyasında yaşar.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path("config/llm_config.yaml")


class LLMModelConfig(BaseModel):
    """Tek bir model tanımı."""

    model_id: str
    max_tokens: int = 4096  # thinking modelleri için zorunlu taban (rapor §4.3)
    temperature: float = 0.2


class LLMRateLimitConfig(BaseModel):
    """Kota kısıtları — NVIDIA ücretsiz katman: ~40 RPM, anahtar bazında global."""

    requests_per_minute: int = 20   # canlıda 30 RPM'de dahi 429 gözlendi (151 adet/2.5 gün) -> tavan düşürüldü
    requests_per_day: int = 2000    # günlük soft cap (rapor §5)


class LLMCacheConfig(BaseModel):
    ttl_seconds: int = 840  # analiz aralığının (900s) hemen altı


class LLMConfig(BaseModel):
    """LLM katmanının tam konfigürasyonu."""

    base_url: str = "https://integrate.api.nvidia.com/v1"
    primary: LLMModelConfig = Field(
        default_factory=lambda: LLMModelConfig(
            model_id="nvidia/nemotron-3-super-120b-a12b"
        )
    )
    fallback: LLMModelConfig = Field(
        default_factory=lambda: LLMModelConfig(
            model_id="deepseek-ai/deepseek-v4-pro"
        )
    )
    rate_limit: LLMRateLimitConfig = Field(default_factory=LLMRateLimitConfig)
    cache: LLMCacheConfig = Field(default_factory=LLMCacheConfig)
    request_timeout_seconds: float = 90.0
    prompt_version: str = "trading_analysis_v1"
    # A/B: birden fazla versiyon verilirse sembol bazında dönüşümlü seçilir;
    # boşsa yalnızca prompt_version kullanılır. Karşılaştırma: llm_prediction
    # tablosundaki prompt_version alanı üzerinden (continuous-improvement-loop skill).
    prompt_versions: list[str] = Field(default_factory=list)

    @property
    def effective_prompt_versions(self) -> list[str]:
        return self.prompt_versions or [self.prompt_version]

    @property
    def api_key(self) -> str | None:
        """API anahtarı yalnızca env'den (asla yaml'dan / asla loglanmaz)."""
        return os.environ.get("NVIDIA_API_KEY") or None


def load_llm_config(path: Path | str = DEFAULT_CONFIG_PATH) -> LLMConfig:
    """llm_config.yaml'ı yükle; yoksa/bozuksa varsayılanlarla devam et."""
    p = Path(path)
    if not p.exists():
        return LLMConfig()
    try:
        data = yaml.safe_load(p.read_text()) or {}
        return LLMConfig(**data)
    except Exception:
        # Bozuk config sistemin çalışmasını engellememeli — varsayılana dön.
        return LLMConfig()
