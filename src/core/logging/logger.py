"""Structured logging modülü.

Tüm sistem genelinde JSON formatlı, context-aware loglar üretir.
Production'da log aggregator'lara (Loki/ELK) doğrudan beslenebilir.

Kullanım:
    from src.core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("agent_started", agent_name="market_scanner", coin_count=150)
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

_CONFIGURED = False


def _add_severity(_logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Cloud-friendly severity field ekler."""
    severity_map = {
        "debug": "DEBUG",
        "info": "INFO",
        "warning": "WARNING",
        "warn": "WARNING",
        "error": "ERROR",
        "critical": "CRITICAL",
        "exception": "ERROR",
    }
    event_dict["severity"] = severity_map.get(method_name.lower(), "INFO")
    return event_dict


def _add_service_context(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
    """Servis context'i ekler (ortam değişkenlerinden)."""
    event_dict.setdefault("service", "macts")
    event_dict.setdefault("environment", os.environ.get("MACTS_ENV", "unknown"))
    event_dict.setdefault("mode", os.environ.get("MACTS_MODE", "unknown"))
    return event_dict


def configure_logging(
    level: str = "INFO",
    json_output: bool | None = None,
    extra_processors: list[Processor] | None = None,
) -> None:
    """Logging sistemini global olarak konfigüre eder.

    Args:
        level: Log seviyesi (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_output: True ise JSON, False ise renkli console output.
            None ise environment'a göre karar verilir
            (production = JSON, development = console).
        extra_processors: Eklenecek custom processor'lar.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    if json_output is None:
        json_output = os.environ.get("MACTS_ENV", "development") == "production"

    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _add_severity,
        _add_service_context,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.format_exc_info,
    ]

    if extra_processors:
        shared_processors.extend(extra_processors)

    if json_output:
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Standart logging modülünü de yapılandır (3. parti kütüphaneler için)
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    # Gürültülü kütüphaneleri sustur
    for noisy in ("urllib3", "asyncio", "aiokafka", "kafka"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str | None = None, **initial_context: Any) -> Any:
    """Konfigüre edilmiş bir structlog logger döndürür.

    Args:
        name: Logger adı (genelde __name__ geçilir).
        **initial_context: Logger'a baştan bağlanacak context değişkenleri.

    Returns:
        structlog BoundLogger instance.
    """
    if not _CONFIGURED:
        configure_logging(level=os.environ.get("LOG_LEVEL", "INFO"))

    logger = structlog.get_logger(name)
    if initial_context:
        logger = logger.bind(**initial_context)
    return logger


def bind_request_context(**kwargs: Any) -> None:
    """Mevcut async context'e log değişkenleri bağlar.

    Bu sayede o context içindeki tüm loglar bu değişkenleri içerir.
    Genelde request başında çağrılır.

    Örnek:
        bind_request_context(request_id="abc123", coin="BTCUSDT")
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    """Mevcut async context'teki log değişkenlerini temizler."""
    structlog.contextvars.clear_contextvars()
