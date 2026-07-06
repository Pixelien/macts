"""Exponential backoff + jitter ve toleranslı LLM çıktı parse yardımcıları.

Saf fonksiyonlar — unit test edilebilir, ağ/IO yok.
"""

from __future__ import annotations

import json
import random
from typing import Any


def compute_backoff(
    attempt: int,
    *,
    base_seconds: float = 2.0,
    cap_seconds: float = 60.0,
    jitter_ratio: float = 0.25,
    rng: random.Random | None = None,
) -> float:
    """attempt (0-indexed) için bekleme süresi: min(cap, base*2^attempt) ± jitter.

    429/5xx sonrası kullanılır. Jitter, çok instance'ın senkron retry
    fırtınası (thundering herd) yapmasını engeller.
    """
    if attempt < 0:
        raise ValueError("attempt negatif olamaz")
    r = rng or random
    raw = min(cap_seconds, base_seconds * (2**attempt))
    jitter = raw * jitter_ratio
    return max(0.0, raw + r.uniform(-jitter, jitter))


def extract_json(text: str) -> dict[str, Any]:
    """LLM çıktısından JSON nesnesini toleranslı şekilde çıkar.

    Probe'da doğrulanan gürültü türlerine dayanıklı:
    - <think>...</think> blokları (deepseek-v4-pro gibi thinking modelleri)
    - ```json ... ``` markdown fence'leri
    - JSON öncesi/sonrası serbest metin

    Raises:
        ValueError: metinde geçerli JSON nesnesi yoksa.
    """
    text = text.strip()
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    text = text.replace("```json", "```")
    if "```" in text:
        candidates = [p for p in text.split("```") if "{" in p]
        if candidates:
            text = candidates[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Çıktıda JSON nesnesi bulunamadı")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("JSON nesnesi bekleniyordu")
    return parsed
