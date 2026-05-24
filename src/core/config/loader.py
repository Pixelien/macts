"""Konfigürasyon yönetim modülü.

YAML dosyalarını okur, ${ENV_VAR} formatındaki environment variable'ları
çözümler ve Pydantic Settings ile validate eder.

Kullanım:
    from src.core.config import load_config
    cfg = load_config()
    print(cfg.exchange.testnet.rest_url)
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Environment dosyasını yükle (varsa)
load_dotenv()

# ${VAR} veya ${VAR:-default} formatını yakalayan regex
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")


def _resolve_env_vars(value: Any) -> Any:
    """Bir değerin içindeki ${ENV_VAR} referanslarını çözer.

    Args:
        value: Çözümlenecek değer (str, dict, list veya başka tip).

    Returns:
        Environment variable'ları çözülmüş değer.
    """
    if isinstance(value, str):
        def _replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            default = match.group(2) or ""
            return os.environ.get(var_name, default)

        return _ENV_VAR_PATTERN.sub(_replace, value)

    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]

    return value


def load_yaml_config(path: str | Path) -> dict[str, Any]:
    """YAML konfigürasyon dosyasını yükler ve env vars çözümler.

    Args:
        path: Konfigürasyon dosyası yolu.

    Returns:
        Parse edilmiş ve env vars çözümlenmiş dict.

    Raises:
        FileNotFoundError: Dosya bulunamazsa.
        yaml.YAMLError: YAML parse hatası olursa.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Konfigürasyon dosyası bulunamadı: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    return _resolve_env_vars(raw)


# =============================================================================
# Pydantic Modeller (config.yaml yapısını yansıtır)
# =============================================================================

class SystemConfig(BaseModel):
    """Sistem geneli ayarları."""
    name: str
    version: str
    mode: str = Field(..., pattern="^(testnet|paper|live)$")
    environment: str
    log_level: str = "INFO"


class ExchangeEndpoint(BaseModel):
    """Borsa endpoint URL'leri."""
    rest_url: str
    ws_url: str


class ExchangeConfig(BaseModel):
    """Borsa konfigürasyonu."""
    name: str
    testnet: ExchangeEndpoint
    mainnet: ExchangeEndpoint
    rate_limits: dict[str, int]
    connection: dict[str, Any]


class UniverseConfig(BaseModel):
    """İşlem evreni filtreleri."""
    quote_asset: str
    contract_type: str
    filters: dict[str, Any]
    refresh_interval_seconds: int


class DataCollectionConfig(BaseModel):
    """Veri toplama ayarları."""
    streams: dict[str, Any]
    backfill: dict[str, Any]
    buffer: dict[str, Any]


class FeatureEngineeringConfig(BaseModel):
    """Feature engineering ayarları."""
    technical_indicators: dict[str, Any]
    microstructure: dict[str, Any]
    regime_detection: dict[str, Any]


class PerCoinLearningConfig(BaseModel):
    """ML model ayarları."""
    forecasting_model: dict[str, Any]
    rl_agent: dict[str, Any]
    retraining: dict[str, Any]


class RiskManagementConfig(BaseModel):
    """Risk yönetimi parametreleri."""
    position_sizing: dict[str, Any]
    loss_limits: dict[str, Any]
    leverage: dict[str, Any]
    correlation: dict[str, Any]
    var_cvar: dict[str, Any]
    flash_crash: dict[str, Any]


class SignalGenerationConfig(BaseModel):
    """Sinyal üretim ayarları."""
    ensemble: dict[str, Any]
    confidence_threshold: float
    min_expected_rr_ratio: float
    signals: dict[str, float]


class ExecutionConfig(BaseModel):
    """Emir yürütme ayarları."""
    default_order_type: str
    algorithms: dict[str, Any]
    slippage: dict[str, Any]
    retry: dict[str, Any]


class SimulationConfig(BaseModel):
    """Simülasyon (paper trading) ayarları."""
    initial_capital_usdt: float
    fee_bps: float
    slippage_model: str
    slippage_bps: float
    promotion_criteria: dict[str, Any]


class PortfolioManagerConfig(BaseModel):
    """Portföy yönetimi ayarları."""
    rebalance_interval_minutes: int
    hedge: dict[str, Any]


class MonitoringConfig(BaseModel):
    """İzleme ve uyarı ayarları."""
    health_check_interval_seconds: int
    metrics_export_interval_seconds: int
    anomaly_detection: dict[str, Any]
    alerts: dict[str, Any]


class BacktestingConfig(BaseModel):
    """Backtesting ayarları."""
    walk_forward: dict[str, Any]
    optuna: dict[str, Any]


class ModelRegistryConfig(BaseModel):
    """MLflow model registry ayarları."""
    experiment_name: str
    staging_threshold: dict[str, Any]
    production_threshold: dict[str, Any]
    canary_traffic_pct: float


class CircuitBreakerConfig(BaseModel):
    """Circuit breaker ayarları."""
    triggers: dict[str, Any]
    actions: dict[str, Any]


class MessagingConfig(BaseModel):
    """Mesajlaşma altyapısı ayarları."""
    redis_streams: dict[str, Any]
    kafka: dict[str, Any]


class AppConfig(BaseModel):
    """Tüm uygulama konfigürasyonunu kapsayan ana model."""
    system: SystemConfig
    exchange: ExchangeConfig
    universe: UniverseConfig
    data_collection: DataCollectionConfig
    feature_engineering: FeatureEngineeringConfig
    per_coin_learning: PerCoinLearningConfig
    risk_management: RiskManagementConfig
    signal_generation: SignalGenerationConfig
    execution: ExecutionConfig
    simulation: SimulationConfig
    portfolio_manager: PortfolioManagerConfig
    monitoring: MonitoringConfig
    backtesting: BacktestingConfig
    model_registry: ModelRegistryConfig
    circuit_breaker: CircuitBreakerConfig
    messaging: MessagingConfig

    def get_active_endpoint(self) -> ExchangeEndpoint:
        """Mevcut moda göre aktif borsa endpoint'ini döndürür."""
        if self.system.mode == "testnet":
            return self.exchange.testnet
        return self.exchange.mainnet


# =============================================================================
# Public API
# =============================================================================

@lru_cache(maxsize=1)
def load_config(config_path: str | None = None) -> AppConfig:
    """Konfigürasyonu yükler ve cache'ler.

    Args:
        config_path: Konfigürasyon dosyası yolu. None ise varsayılan
            yollar denenir.

    Returns:
        Validate edilmiş AppConfig instance'ı.

    Raises:
        FileNotFoundError: Hiçbir konfigürasyon dosyası bulunamazsa.
    """
    if config_path is None:
        candidates = [
            os.environ.get("MACTS_CONFIG_PATH"),
            "config/config.yaml",
            "/app/config/config.yaml",
            "config/config.example.yaml",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                config_path = candidate
                break
        else:
            raise FileNotFoundError(
                "Konfigürasyon dosyası bulunamadı. MACTS_CONFIG_PATH "
                "environment variable'ını ayarlayın veya config/config.yaml "
                "oluşturun."
            )

    raw = load_yaml_config(config_path)
    return AppConfig.model_validate(raw)


def reload_config() -> AppConfig:
    """Cache'i temizleyip konfigürasyonu yeniden yükler."""
    load_config.cache_clear()
    return load_config()
