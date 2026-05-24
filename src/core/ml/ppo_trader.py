"""PPO (Proximal Policy Optimization) tabanlı trading kararı.

Stable-Baselines3 PPO implementasyonu üzerine kurulmuş, custom Gym
environment ile kripto trading ajanı. TFT modelinin tahmini bu agent'a
state olarak beslenir, agent action seçer (BUY / SELL / HOLD).

NOT: Bu modül iskelet seviyesindedir.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from src.core.logging import get_logger

logger = get_logger(__name__)


class TradingEnv:
    """Custom Gym environment for crypto trading.

    State: [features..., predicted_return, current_position, unrealized_pnl, ...]
    Action: 0=HOLD, 1=BUY, 2=SELL
    Reward: realized PnL - transaction costs - drawdown penalty
    """

    # TODO: gymnasium.Env'i extend et ve gerekli metotları implement et:
    # - reset() -> (observation, info)
    # - step(action) -> (observation, reward, terminated, truncated, info)
    # - observation_space (Box)
    # - action_space (Discrete(3))

    def __init__(
        self,
        symbol: str,
        feature_dim: int = 32,
        initial_balance: float = 10000.0,
        fee_bps: float = 4.0,
    ) -> None:
        self.symbol = symbol
        self.feature_dim = feature_dim
        self.initial_balance = initial_balance
        self.fee_bps = fee_bps

    def reset(self) -> tuple[np.ndarray, dict[str, Any]]:
        """Environment'ı sıfırla."""
        # TODO: state'i baştan başlat
        return np.zeros(self.feature_dim), {}

    def step(self, action: int) -> tuple[np.ndarray, float, bool, bool, dict[str, Any]]:
        """Bir adım at."""
        # TODO: action'u uygula, reward hesapla, next state üret
        return np.zeros(self.feature_dim), 0.0, False, False, {}


class PPOTrader:
    """Stable-Baselines3 PPO wrapper."""

    def __init__(
        self,
        symbol: str,
        feature_dim: int = 32,
        learning_rate: float = 3e-4,
        n_steps: int = 2048,
        batch_size: int = 64,
        n_epochs: int = 10,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        clip_range: float = 0.2,
    ) -> None:
        """PPO trader başlat."""
        self.symbol = symbol
        self.feature_dim = feature_dim
        self.learning_rate = learning_rate
        self.n_steps = n_steps
        self.batch_size = batch_size
        self.n_epochs = n_epochs
        self.gamma = gamma
        self.gae_lambda = gae_lambda
        self.clip_range = clip_range
        self._model: Any = None
        self._is_trained = False

    def train(
        self,
        env: TradingEnv,
        total_timesteps: int = 1_000_000,
    ) -> dict[str, float]:
        """PPO modelini eğit.

        TODO:
        1. stable_baselines3.PPO("MlpPolicy", env, ...) ile model kur
        2. model.learn(total_timesteps=total_timesteps) ile eğit
        3. Mean reward, episode length gibi metrikleri döndür
        """
        logger.info(
            "ppo_training_stub",
            symbol=self.symbol,
            timesteps=total_timesteps,
        )
        return {"mean_reward": 0.0, "explained_variance": 0.0}

    def predict(
        self,
        observation: np.ndarray,
        deterministic: bool = True,
    ) -> tuple[int, dict[str, float]]:
        """Action seç.

        Returns:
            (action, metadata)
            action: 0=HOLD, 1=BUY, 2=SELL
            metadata: {action_probs: [...], value: ...}
        """
        # TODO: self._model.predict(observation, deterministic=...)
        return 0, {"action_probs": [1.0, 0.0, 0.0], "value": 0.0}

    def save(self, path: str) -> None:
        """Modeli kaydet."""
        # TODO: self._model.save(path)
        logger.info("ppo_saved", symbol=self.symbol, path=path)

    def load(self, path: str) -> None:
        """Modeli yükle."""
        # TODO: PPO.load(path)
        logger.info("ppo_loaded", symbol=self.symbol, path=path)
        self._is_trained = True

    @property
    def is_trained(self) -> bool:
        return self._is_trained
