"""Simulation Agent (Forward Paper Trading).

Sorumluluklar:
- system.mode == "paper" iken Execution Agent'ı bypass edip emirleri
  in-memory bir matching engine'de simüle etmek
- GERÇEK WebSocket akışını kullanmak (backtest DEĞİL, forward paper)
- Realistic slippage ve fee modelleme
- Performans metriklerini hesaplamak: Sharpe, Sortino, max DD, win
  rate, profit factor
- Promotion criteria'larını (>=1.5 Sharpe, <=15% DD, vs.) takip
  etmek ve canlıya geçiş için onay tetikleyicisi göndermek

Yayınladığı:
- stream:simulation.metrics      -> Periyodik performans metrikleri
- stream:simulation.promotion_ready -> Eşikler aşıldı, manuel onay bekle

Tüketim:
- stream:signals.approved        -> (paper modda) sinyalleri sahte fill et
- stream:ticks.{symbol}.trade    -> Doldurma fiyatı için
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.agents.base import BaseAgent, run_agent


class SimulationAgent(BaseAgent):
    """Simulation Agent (Forward Paper Trading)."""

    agent_name = "simulation"
    heartbeat_interval = 5.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # TODO: Agent-specific state initialization

    async def _initialize(self) -> None:
        """Agent kaynaklarını hazırla."""
        self.logger.info("simulation_initializing")
        # TODO: Initialize required resources

    async def _run(self) -> None:
        """Ana iş döngüsü."""
        self.logger.info("simulation_loop_started")
        # TODO Implementation roadmap:
        # 1. In-memory PaperBroker class: order book bazlı fill simülasyonu
        # 2. Fee = 0.04% taker (config'den)
        # 3. Slippage modeli: linear (büyük emirler için impact)
        # 4. Metrik hesabı: pyfolio veya custom formüller
        # 5. Promotion: 30 gün + Sharpe>=1.5 + DD<=15% + WR>=52% + PF>=1.4
        # 6. Forward Sharpe / Backtest Sharpe >= 0.7 koşulu (overfitting)

        # Stub: stop event gelene kadar bekle
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.heartbeat_interval)
            except asyncio.TimeoutError:
                continue

    async def _shutdown(self) -> None:
        """Kaynak temizliği."""
        self.logger.info("simulation_shutting_down")

    async def _health_check(self) -> dict[str, float]:
        return {}


if __name__ == "__main__":
    asyncio.run(run_agent(SimulationAgent))
