"""MACTS Komut Satırı Arayüzü.

Kullanım:
    macts agent run <agent-name>     # Tek bir agent çalıştır
    macts agent list                  # Mevcut agent'ları listele
    macts config validate             # Konfigürasyonu doğrula
    macts db init                     # Veritabanı şemasını oluştur
    macts mode show                   # Mevcut mod (testnet/paper/live)
    macts promote --to paper          # Mod geçişi (manuel onay)
    macts backtest run --symbol BTCUSDT
    macts version

NOT: Bu CLI iskelet seviyesindedir; her komut için TODO yorumları var.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from src.core.config import load_config
from src.core.logging import configure_logging, get_logger

app = typer.Typer(
    name="macts",
    help="MACTS - Multi-Agent Crypto Trading System",
    no_args_is_help=True,
)

agent_app = typer.Typer(name="agent", help="Agent yönetimi")
config_app = typer.Typer(name="config", help="Konfigürasyon işlemleri")
db_app = typer.Typer(name="db", help="Veritabanı işlemleri")
mode_app = typer.Typer(name="mode", help="Mod işlemleri")
backtest_app = typer.Typer(name="backtest", help="Backtest işlemleri")

app.add_typer(agent_app)
app.add_typer(config_app)
app.add_typer(db_app)
app.add_typer(mode_app)
app.add_typer(backtest_app)

console = Console()
logger = get_logger("cli")


# =============================================================================
# Agent Registry
# =============================================================================

AGENT_REGISTRY: dict[str, str] = {
    "market_scanner": "src.agents.market_scanner.agent:MarketScannerAgent",
    "data_collection": "src.agents.data_collection.agent:DataCollectionAgent",
    "feature_engineering": "src.agents.feature_engineering.agent:FeatureEngineeringAgent",
    "per_coin_learning": "src.agents.per_coin_learning.agent:PerCoinLearningAgent",
    "risk_management": "src.agents.risk_management.agent:RiskManagementAgent",
    "signal_generation": "src.agents.signal_generation.agent:SignalGenerationAgent",
    "execution": "src.agents.execution.agent:ExecutionAgent",
    "simulation": "src.agents.simulation.agent:SimulationAgent",
    "portfolio_manager": "src.agents.portfolio_manager.agent:PortfolioManagerAgent",
    "monitoring": "src.agents.monitoring.agent:MonitoringAgent",
    "backtesting": "src.agents.backtesting.agent:BacktestingAgent",
    "model_registry": "src.agents.model_registry.agent:ModelRegistryAgent",
    "circuit_breaker": "src.agents.circuit_breaker.agent:CircuitBreakerAgent",
    "grid_bot": "src.agents.grid_bot.agent:GridBotAgent",
    "ai_analyst": "src.agents.ai_analyst.agent:AIAnalystAgent",
}


# =============================================================================
# Agent Commands
# =============================================================================

@agent_app.command("list")
def agent_list() -> None:
    """Mevcut agent'ları listele."""
    table = Table(title="MACTS Agent'ları")
    table.add_column("Agent Adı", style="cyan")
    table.add_column("Modül Yolu", style="white")

    for name, path in AGENT_REGISTRY.items():
        table.add_row(name, path)

    console.print(table)


@agent_app.command("run")
def agent_run(
    name: Annotated[str, typer.Argument(help="Çalıştırılacak agent adı")],
) -> None:
    """Tek bir agent'ı çalıştır."""
    if name not in AGENT_REGISTRY:
        console.print(f"[red]Hata:[/red] '{name}' diye bir agent yok.")
        console.print("\nMevcut agent'lar için: macts agent list")
        raise typer.Exit(code=1)

    configure_logging()

    module_path, class_name = AGENT_REGISTRY[name].split(":")
    module = __import__(module_path, fromlist=[class_name])
    agent_class = getattr(module, class_name)

    from src.agents.base import run_agent

    console.print(f"[green]Başlatılıyor:[/green] {name}")
    try:
        asyncio.run(run_agent(agent_class))
    except KeyboardInterrupt:
        console.print("\n[yellow]Kullanıcı tarafından durduruldu[/yellow]")
        sys.exit(0)


# =============================================================================
# Config Commands
# =============================================================================

@config_app.command("validate")
def config_validate() -> None:
    """Konfigürasyonun geçerli olup olmadığını kontrol et."""
    try:
        cfg = load_config()
        console.print("[green]✓[/green] Konfigürasyon geçerli")
        console.print(f"  Mode: [cyan]{cfg.system.mode}[/cyan]")
        console.print(f"  Environment: [cyan]{cfg.system.environment}[/cyan]")
        console.print(f"  Active endpoint: [cyan]{cfg.get_active_endpoint().rest_url}[/cyan]")
    except Exception as e:
        console.print(f"[red]✗ Konfigürasyon geçersiz:[/red] {e}")
        raise typer.Exit(code=1) from e


@config_app.command("show")
def config_show() -> None:
    """Mevcut konfigürasyonu yazdır."""
    cfg = load_config()
    console.print_json(data=cfg.model_dump(mode="json"))


# =============================================================================
# DB Commands
# =============================================================================

@db_app.command("init")
def db_init() -> None:
    """Veritabanı şemasını oluştur (Alembic migration uygular)."""
    console.print("[yellow]TODO:[/yellow] Alembic migration'larını uygula")
    # TODO: alembic upgrade head


@db_app.command("status")
def db_status() -> None:
    """DB bağlantı durumunu kontrol et."""
    console.print("[yellow]TODO:[/yellow] Postgres + InfluxDB + Redis ping")


# =============================================================================
# Mode Commands
# =============================================================================

@mode_app.command("show")
def mode_show() -> None:
    """Mevcut çalışma modunu göster."""
    cfg = load_config()
    console.print(f"Aktif mod: [bold cyan]{cfg.system.mode}[/bold cyan]")
    endpoint = cfg.get_active_endpoint()
    console.print(f"REST URL: {endpoint.rest_url}")
    console.print(f"WS URL: {endpoint.ws_url}")


@mode_app.command("promote")
def mode_promote(
    to: Annotated[
        str,
        typer.Option("--to", help="Hedef mod: paper veya live"),
    ],
    confirm: Annotated[
        bool, typer.Option("--confirm", help="Onay olmadan çalıştır")
    ] = False,
) -> None:
    """Mod geçişi (testnet→paper, paper→live).

    Promotion criteria'larını kontrol eder ve manuel onay ister.
    """
    if to not in ("paper", "live"):
        console.print(f"[red]Geçersiz hedef mod: {to}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[yellow]TODO:[/yellow] {to} modu için promotion check'leri:")
    console.print("  - Sharpe Ratio ≥ 1.5")
    console.print("  - Max Drawdown ≤ 15%")
    console.print("  - Win Rate ≥ 52%")
    console.print("  - Profit Factor ≥ 1.4")
    console.print("  - Forward/Backtest Sharpe ≥ 0.7")

    if not confirm:
        proceed = typer.confirm(f"\n{to} moduna geçmek istiyor musun?")
        if not proceed:
            console.print("[yellow]İptal edildi[/yellow]")
            raise typer.Exit()

    # TODO: scripts/promote_to_next_stage.sh çağır
    console.print(f"[green]✓[/green] {to} moduna geçiliyor...")


# =============================================================================
# Backtest Commands
# =============================================================================

@backtest_app.command("run")
def backtest_run(
    symbol: Annotated[str, typer.Option("--symbol", help="Backtest edilecek sembol")],
    days: Annotated[int, typer.Option("--days", help="Geriye kaç gün")] = 365,
) -> None:
    """Tek sembol için backtest çalıştır."""
    console.print(f"[yellow]TODO:[/yellow] Backtest: {symbol} ({days} gün)")
    # TODO: BacktestingAgent'ı one-shot modda çalıştır


# =============================================================================
# Top-level Commands
# =============================================================================

@app.command("version")
def version() -> None:
    """Versiyon bilgisini göster."""
    from src import __version__
    console.print(f"MACTS [cyan]{__version__}[/cyan]")


@app.command("health")
def health() -> None:
    """Tüm sistemin sağlık durumunu özet göster."""
    console.print("[yellow]TODO:[/yellow] Tüm agent'ların heartbeat durumunu listele")


def main() -> None:
    """Console entrypoint."""
    app()


if __name__ == "__main__":
    main()
