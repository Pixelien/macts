"""NVIDIA NIM API async istemcisi (OpenAI-uyumlu /chat/completions).

Hata sınıflandırması fallback politikasının temelidir
(bkz. .claude/skills/nvidia-nim-client/SKILL.md):
- 429 -> NIMRateLimitError : kota BİZİM anahtarımızda — fallback modele
  geçilmez (aynı kotayı paylaşır), tur atlanır.
- 402 -> NIMQuotaError     : kredi tükenmesi (belirsiz sistem) — gövde loglanır.
- 404 -> NIMNotFoundError  : model ID yanlış/kaldırılmış — retry edilmez.
- 5xx -> NIMServerError    : sunucu hatası — retry + fallback adayı.

API anahtarı asla loglanmaz.
"""

from __future__ import annotations

import time
from typing import Any

import httpx


class NIMError(Exception):
    """NIM istemci hataları için taban sınıf."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body[:500]


class NIMRateLimitError(NIMError):
    """429 — anahtar bazlı global kota aşımı."""


class NIMQuotaError(NIMError):
    """402 — kredi/kota tükenmesi."""


class NIMNotFoundError(NIMError):
    """404 — model endpoint'i yok."""


class NIMServerError(NIMError):
    """5xx — sunucu/model tarafı hata."""


class NvidiaNIMClient:
    """İnce async istemci. Rate limit/fallback/cache sorumluluğu ÜST katmandadır."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        timeout_seconds: float = 90.0,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=timeout_seconds,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> tuple[str, dict[str, Any], float]:
        """Tek chat completion çağrısı.

        Returns:
            (content, usage_dict, latency_seconds)
        """
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        t0 = time.monotonic()
        try:
            resp = await self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as e:
            raise NIMServerError(f"ağ hatası: {type(e).__name__}") from e
        latency = time.monotonic() - t0

        if resp.status_code == 429:
            raise NIMRateLimitError("429 kota aşımı", status_code=429, body=resp.text)
        if resp.status_code == 402:
            raise NIMQuotaError("402 kredi tükenmesi", status_code=402, body=resp.text)
        if resp.status_code == 404:
            raise NIMNotFoundError(
                f"404 model bulunamadı: {model}", status_code=404, body=resp.text
            )
        if resp.status_code >= 500:
            raise NIMServerError(
                f"{resp.status_code} sunucu hatası",
                status_code=resp.status_code,
                body=resp.text,
            )
        if resp.status_code != 200:
            raise NIMError(
                f"beklenmeyen durum kodu: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
            )

        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise NIMServerError("cevap gövdesi beklenen şemada değil") from e
        usage = data.get("usage", {}) or {}
        return content, usage, latency
