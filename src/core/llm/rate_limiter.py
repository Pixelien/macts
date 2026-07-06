"""Token bucket rate limiter (dakikalık) + günlük kota sayacı.

NVIDIA NIM ücretsiz katmanı ~40 RPM (API anahtarı bazında GLOBAL) — bu
limiter tavanı 30 RPM'e sabitler (config/llm_config.yaml). Günlük soft cap
ayrıca izlenir. Bkz. docs/AI_ANALYST_MODEL_SELECTION.md §2, §5.

Saf/test edilebilir tasarım: saat (clock) enjekte edilebilir, ağ yok.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class TokenBucketLimiter:
    """Sürekli dolan token bucket + gün bazlı sayaç.

    Kullanım:
        limiter = TokenBucketLimiter(requests_per_minute=30, requests_per_day=2000)
        if limiter.try_acquire():
            ... API çağrısı ...
        else:
            ... bu turu atla (LLM'siz devam) ...

    NOT: Bilinçli olarak bloklamayan (non-blocking) tasarım. AI Analyst'te
    kota yoksa doğru davranış BEKLEMEK değil, o turu atlamaktır — sinyal
    üretimi asla LLM'e takılmamalı (rapor §5, Aşama 5 kuralı).
    """

    def __init__(
        self,
        requests_per_minute: int,
        requests_per_day: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if requests_per_minute <= 0 or requests_per_day <= 0:
            raise ValueError("limitler pozitif olmalı")
        self.rpm = requests_per_minute
        self.rpd = requests_per_day
        self._clock = clock
        self._tokens = float(requests_per_minute)  # dolu başla
        self._last_refill = clock()
        # Gün sayacı: monotonic saat üzerinden 24h pencere (restart'ta sıfırlanır;
        # kalıcı takip usage_tracker/Postgres'te — burada süreç-içi emniyet)
        self._day_window_start = clock()
        self._day_count = 0

    # -------------------------------------------------------------------

    def try_acquire(self) -> bool:
        """1 token almayı dene. Alınamazsa False (bekleme YOK)."""
        now = self._clock()
        self._refill(now)
        self._roll_day_window(now)

        if self._day_count >= self.rpd:
            return False
        if self._tokens < 1.0:
            return False

        self._tokens -= 1.0
        self._day_count += 1
        return True

    def seconds_until_available(self) -> float:
        """Bir sonraki token'a kalan tahmini süre (metrik/log için)."""
        now = self._clock()
        self._refill(now)
        if self._tokens >= 1.0:
            return 0.0
        deficit = 1.0 - self._tokens
        return deficit * (60.0 / self.rpm)

    @property
    def daily_used(self) -> int:
        return self._day_count

    @property
    def tokens_available(self) -> float:
        self._refill(self._clock())
        return self._tokens

    # -------------------------------------------------------------------

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._last_refill)
        self._tokens = min(float(self.rpm), self._tokens + elapsed * (self.rpm / 60.0))
        self._last_refill = now

    def _roll_day_window(self, now: float) -> None:
        if now - self._day_window_start >= 86400.0:
            self._day_window_start = now
            self._day_count = 0
