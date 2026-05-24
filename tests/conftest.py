"""Pytest fixture'ları."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest

from src.core.config import AppConfig


@pytest.fixture(scope="session")
def event_loop_policy():
    """Async test için event loop policy."""
    import asyncio
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def mock_config() -> AppConfig:
    """Test için mock konfigürasyon.

    TODO: gerçek bir test config'i hazırla (config/config.test.yaml).
    """
    from src.core.config import load_config
    return load_config("config/config.example.yaml")


@pytest.fixture
async def mock_redis_bus() -> AsyncGenerator[AsyncMock, None]:
    """Mock Redis bus."""
    bus = AsyncMock()
    bus.publish = AsyncMock()
    bus.subscribe = AsyncMock()
    bus.acknowledge = AsyncMock()
    bus.health_check = AsyncMock(return_value=True)
    yield bus


@pytest.fixture
async def mock_kafka_bus() -> AsyncGenerator[AsyncMock, None]:
    """Mock Kafka bus."""
    bus = AsyncMock()
    bus.publish = AsyncMock()
    bus.subscribe = AsyncMock()
    bus.health_check = AsyncMock(return_value=True)
    yield bus
