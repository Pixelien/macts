"""Backtesting Agent.

Sorumluluklar:
- Tarihsel veri üzerinde walk-forward analizi yapmak
- Optuna ile hyperparameter optimization
- Backtest sonuçlarını MLflow'a kaydetmek
- Yeni model adaylarını staging'e promote etmek için karar vermek

NOT: Bu agent CONTINUOUS değil, ON-DEMAND çalışır. CLI veya cron ile
tetiklenir, işini bitirince exit eder.

Yayınladığı:
- stream:backtest.results        -> Backtest sonuçları
- stream:model.candidates        -> Staging'e promote edilebilir modeller

Tüketim:
- stream:backtest.requests       -> Backtest tetikleme istekleri
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.agents.base import BaseAgent, run_agent


class BacktestingAgent(BaseAgent):
    """Backtesting Agent."""

    agent_name = "backtesting"
    heartbeat_interval = 60.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # TODO: Agent-specific state initialization

    async def _initialize(self) -> None:
        """Agent kaynaklarını hazırla."""
        self.logger.info("backtesting_initializing")
        # TODO: Initialize required resources

    async def _run(self) -> None:
        """Ana iş döngüsü."""
        self.logger.info("backtesting_loop_started")
        # TODO Implementation roadmap:
        # 1. Walk-forward: 180 gün train + 30 gün test, 30 gün step
        # 2. Optuna study: per-coin, n_trials=100, MedianPruner
        # 3. vectorbt veya custom backtester (TFT inference + PPO act)
        # 4. MLflow logging: parametreler, metrikler, artifact (model file)
        # 5. Yarışma modu: birden fazla strateji birden, en iyiyi seç

        # Stub: stop event gelene kadar bekle
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.heartbeat_interval)
            except asyncio.TimeoutError:
                continue

    async def _shutdown(self) -> None:
        """Kaynak temizliği."""
        self.logger.info("backtesting_shutting_down")

    async def _health_check(self) -> dict[str, float]:
        return {}


if __name__ == "__main__":
    asyncio.run(run_agent(BacktestingAgent))
