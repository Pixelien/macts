"""Portfolio Manager Agent.

Sorumluluklar:
- Tüm açık pozisyonları, marjini, unrealized PnL'i takip etmek
- Binance User Data Stream üzerinden hesap güncellemelerini dinlemek
- Periyodik portföy snapshot'ı yayınlamak
- Hedge stratejilerini uygulamak (delta-neutral, cross-margin opt.)
- Toplam sermaye yönetimi ve allocation kararları

Yayınladığı:
- stream:portfolio.snapshot      -> Anlık portföy durumu

Tüketim:
- stream:trades.executed         -> Gerçekleşen trade'ler
- stream:orders.events           -> Order güncellemeleri
- User Data Stream (Binance)     -> Pozisyon ve margin güncellemeleri
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.agents.base import BaseAgent, run_agent


class PortfolioManagerAgent(BaseAgent):
    """Portfolio Manager Agent."""

    agent_name = "portfolio_manager"
    heartbeat_interval = 5.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # TODO: Agent-specific state initialization

    async def _initialize(self) -> None:
        """Agent kaynaklarını hazırla."""
        self.logger.info("portfolio_manager_initializing")
        # TODO: Initialize required resources

    async def _run(self) -> None:
        """Ana iş döngüsü."""
        self.logger.info("portfolio_manager_loop_started")
        # TODO Implementation roadmap:
        # 1. PostgreSQL'de pozisyon ve trade tablolarını oluştur (alembic migration)
        # 2. User Data Stream listener (account_update, order_update event'leri)
        # 3. Drawdown hesabı: rolling 24h, 7d, 30d
        # 4. Hedge MVP'de devre dışı (config'de hedge.enabled=false)

        # Stub: stop event gelene kadar bekle
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.heartbeat_interval)
            except asyncio.TimeoutError:
                continue

    async def _shutdown(self) -> None:
        """Kaynak temizliği."""
        self.logger.info("portfolio_manager_shutting_down")

    async def _health_check(self) -> dict[str, float]:
        return {}


if __name__ == "__main__":
    asyncio.run(run_agent(PortfolioManagerAgent))
