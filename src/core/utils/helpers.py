"""Yardımcı async utility'ler.

- retry_async: Exponential backoff retry decorator
- AsyncCircuitBreaker: Circuit breaker pattern (closed/open/half-open)
- async_chunked: Async iterable'ı chunk'lara böl
- safe_decimal: Float -> Decimal güvenli dönüşüm
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from decimal import Decimal
from enum import Enum
from typing import Any, TypeVar

from src.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


# =============================================================================
# Retry decorator
# =============================================================================

def retry_async(
    *,
    max_attempts: int = 3,
    initial_delay_ms: int = 100,
    max_delay_ms: int = 30_000,
    backoff_factor: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Async fonksiyonlar için exponential backoff retry decorator.

    Args:
        max_attempts: Maksimum deneme sayısı (ilk dahil).
        initial_delay_ms: İlk retry'dan önceki bekleme.
        max_delay_ms: Maksimum bekleme.
        backoff_factor: Her retry'da gecikmeyi kaç katına çıkar.
        exceptions: Hangi exception'lar retry'a yol açar.

    Örnek:
        @retry_async(max_attempts=5, exceptions=(aiohttp.ClientError,))
        async def fetch_data(): ...
    """
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            delay_ms = initial_delay_ms
            last_exc: BaseException | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_attempts:
                        logger.error(
                            "retry_exhausted",
                            func=func.__name__,
                            attempts=attempt,
                            error=str(e),
                        )
                        raise
                    logger.warning(
                        "retry_attempt",
                        func=func.__name__,
                        attempt=attempt,
                        delay_ms=delay_ms,
                        error=str(e),
                    )
                    await asyncio.sleep(delay_ms / 1000)
                    delay_ms = min(int(delay_ms * backoff_factor), max_delay_ms)

            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator


# =============================================================================
# Circuit Breaker Pattern
# =============================================================================

class CircuitState(str, Enum):
    """Circuit breaker durumu."""
    CLOSED = "closed"      # Normal çalışma
    OPEN = "open"          # Hata var, istekler red ediliyor
    HALF_OPEN = "half_open"  # Test modu, sınırlı istek geçiyor


class CircuitBreakerOpenError(Exception):
    """Circuit açık olduğunda raise edilir."""


class AsyncCircuitBreaker:
    """Async circuit breaker.

    failure_threshold kadar ardışık hata olursa devre OPEN'a geçer.
    recovery_timeout sonra HALF_OPEN'a geçer ve tek bir test isteği
    geçirir; başarılıysa CLOSED'a, başarısızsa OPEN'a geri döner.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 60.0,
        name: str = "circuit",
    ) -> None:
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout_seconds
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    async def call(self, func: Callable[..., Awaitable[T]], *args: Any, **kwargs: Any) -> T:
        """Fonksiyonu circuit breaker üzerinden çağır."""
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if (
                    self._opened_at is not None
                    and time.time() - self._opened_at > self._recovery_timeout
                ):
                    self._state = CircuitState.HALF_OPEN
                    logger.info("circuit_half_open", name=self.name)
                else:
                    raise CircuitBreakerOpenError(f"Circuit {self.name} OPEN")

        try:
            result = await func(*args, **kwargs)
        except Exception:
            await self._on_failure()
            raise
        else:
            await self._on_success()
            return result

    async def _on_success(self) -> None:
        async with self._lock:
            self._failure_count = 0
            if self._state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
                self._state = CircuitState.CLOSED
                self._opened_at = None
                logger.info("circuit_closed", name=self.name)

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            if self._failure_count >= self._failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.time()
                logger.warning(
                    "circuit_opened",
                    name=self.name,
                    failures=self._failure_count,
                )

    @property
    def state(self) -> CircuitState:
        return self._state


# =============================================================================
# Misc helpers
# =============================================================================

async def async_chunked(
    iterable: AsyncIterator[T], size: int
) -> AsyncIterator[list[T]]:
    """Async iterable'ı belirli boyutlarda chunk'lara böler."""
    chunk: list[T] = []
    async for item in iterable:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def safe_decimal(value: Any, default: str = "0") -> Decimal:
    """Float/str/int -> Decimal güvenli dönüşüm.

    Float'tan Decimal'e çevirirken precision loss yaşanmaması için str
    üzerinden geçer.
    """
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
