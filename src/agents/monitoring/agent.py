"""Monitoring & Logging Agent.

Sorumluluklar:
- Tüm agent'ların heartbeat'lerini izlemek; 30 sn boyunca heartbeat
  yoksa "stale" olarak işaretlemek
- Sistem metriklerini Prometheus'a expose etmek (HTTP /metrics)
- Anomali tespiti (Isolation Forest) — beklenmeyen sinyal/emir
  davranışları, fiyat sapmaları, latency spike'ları
- Critical event'leri Telegram + Email ile bildirmek
- Tüm trade'leri ve kararları PostgreSQL audit log'a yazmak

Yayınladığı:
- stream:alerts                  -> Bildirim sistemi için uyarılar

Tüketim:
- stream:heartbeats              -> Tüm agent heartbeat'leri
- stream:trades.executed         -> Audit için
- stream:orders.events           -> Audit için
- stream:simulation.metrics      -> Performans degradasyonu izleme
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.agents.base import BaseAgent, run_agent


class MonitoringAgent(BaseAgent):
    """Monitoring & Logging Agent."""

    agent_name = "monitoring"
    heartbeat_interval = 5.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # TODO: Agent-specific state initialization

    async def _initialize(self) -> None:
        """Agent kaynaklarını hazırla."""
        self.logger.info("monitoring_initializing")
        # TODO: Initialize required resources

    async def _run(self) -> None:
        """Ana iş döngüsü."""
        self.logger.info("monitoring_loop_started")
        # TODO Implementation roadmap:
        # 1. Prometheus HTTP server (port 8000) /metrics endpoint
        # 2. Stale agent detection: last_heartbeat'e göre Telegram alert
        # 3. IsolationForest: scikit-learn, daily retrain on rolling window
        # 4. Notifier classes: TelegramNotifier, EmailNotifier
        # 5. Audit log: structured JSON, PostgreSQL append-only table

        # Stub: stop event gelene kadar bekle
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.heartbeat_interval)
            except asyncio.TimeoutError:
                continue

    async def _shutdown(self) -> None:
        """Kaynak temizliği."""
        self.logger.info("monitoring_shutting_down")

    async def _health_check(self) -> dict[str, float]:
        return {}


if __name__ == "__main__":
    asyncio.run(run_agent(MonitoringAgent))
