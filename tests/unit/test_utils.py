"""Helper fonksiyonlar için unit testler."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from src.core.utils import (
    AsyncCircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    retry_async,
    safe_decimal,
)


class TestSafeDecimal:
    def test_from_float(self) -> None:
        assert safe_decimal(0.1) == Decimal("0.1")

    def test_from_str(self) -> None:
        assert safe_decimal("123.456") == Decimal("123.456")

    def test_from_int(self) -> None:
        assert safe_decimal(42) == Decimal("42")

    def test_none_returns_default(self) -> None:
        assert safe_decimal(None) == Decimal("0")
        assert safe_decimal(None, default="100") == Decimal("100")

    def test_decimal_passthrough(self) -> None:
        d = Decimal("99.99")
        assert safe_decimal(d) is d


class TestRetryAsync:
    async def test_success_first_try(self) -> None:
        call_count = 0

        @retry_async(max_attempts=3)
        async def succeed() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await succeed()
        assert result == "ok"
        assert call_count == 1

    async def test_retry_on_failure(self) -> None:
        call_count = 0

        @retry_async(max_attempts=3, initial_delay_ms=1)
        async def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("fail")
            return "ok"

        result = await flaky()
        assert result == "ok"
        assert call_count == 3

    async def test_exhausted(self) -> None:
        @retry_async(max_attempts=3, initial_delay_ms=1)
        async def always_fails() -> None:
            raise ValueError("nope")

        with pytest.raises(ValueError):
            await always_fails()


class TestCircuitBreaker:
    async def test_closed_state_passes_through(self) -> None:
        cb = AsyncCircuitBreaker(failure_threshold=3, name="test")

        async def f() -> str:
            return "ok"

        result = await cb.call(f)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    async def test_opens_after_threshold(self) -> None:
        cb = AsyncCircuitBreaker(failure_threshold=2, name="test")

        async def f() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await cb.call(f)
        with pytest.raises(ValueError):
            await cb.call(f)

        assert cb.state == CircuitState.OPEN

        # Üçüncü çağrı CB error'u almalı
        with pytest.raises(CircuitBreakerOpenError):
            await cb.call(f)

    async def test_half_open_recovery(self) -> None:
        cb = AsyncCircuitBreaker(
            failure_threshold=1,
            recovery_timeout_seconds=0.05,
            name="test",
        )

        async def fail() -> None:
            raise ValueError("boom")

        async def succeed() -> str:
            return "ok"

        with pytest.raises(ValueError):
            await cb.call(fail)
        assert cb.state == CircuitState.OPEN

        # recovery timeout'u bekle
        await asyncio.sleep(0.1)

        # Half-open'a geçecek ve başarılı çağrı CLOSED'a döndürecek
        result = await cb.call(succeed)
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED
