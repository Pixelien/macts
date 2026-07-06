"""Semver'li prompt şablonları (config/prompts/*.yaml) ve mesaj kurucu.

Her prompt versiyonu ayrı dosyada yaşar (trading_analysis_v1.yaml, _v2...).
Versiyon adı AIAnalysis.prompt_version alanına yazılır -> MLflow/bandit
karşılaştırması (Aşama 4) bu alan üzerinden yapılır.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

PROMPTS_DIR = Path("config/prompts")

# Yerleşik varsayılan (dosya yoksa) — probe'da doğrulanan sistem prompt'u
_BUILTIN_SYSTEM_PROMPT = (
    "You are a quantitative crypto analyst. You receive technical indicator "
    "data and recent candles for one symbol. Respond with ONLY a JSON object "
    "matching this schema, no prose, no markdown fences:\n"
    '{"symbol": str, "direction": "long"|"short"|"neutral", '
    '"confidence": float 0-1, "reasoning": str (max 60 words), '
    '"risk_flags": [str], "time_horizon": "1h"|"4h"|"1d"}'
)


class PromptTemplate(BaseModel):
    """Tek prompt versiyonu."""

    version: str
    system_prompt: str
    few_shot: list[dict[str, str]] = Field(default_factory=list)
    # few_shot elemanı: {"user": "<features json>", "assistant": "<json cevap>"}


def load_prompt(version: str, prompts_dir: Path | str = PROMPTS_DIR) -> PromptTemplate:
    """Versiyonlu şablonu yükle; dosya yoksa yerleşik varsayılana dön."""
    path = Path(prompts_dir) / f"{version}.yaml"
    if not path.exists():
        return PromptTemplate(version=version, system_prompt=_BUILTIN_SYSTEM_PROMPT)
    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("version", version)
    return PromptTemplate(**data)


def build_messages(
    template: PromptTemplate, features: dict[str, Any]
) -> list[dict[str, str]]:
    """OpenAI-format mesaj listesi kur: system + few-shot çiftleri + user."""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": template.system_prompt}
    ]
    for example in template.few_shot:
        if "user" in example and "assistant" in example:
            messages.append({"role": "user", "content": example["user"]})
            messages.append({"role": "assistant", "content": example["assistant"]})
    messages.append(
        {"role": "user", "content": json.dumps(features, default=str)}
    )
    return messages
