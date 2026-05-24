"""Market Scanner Agent — Faz 1 implementasyonu.

Sorumluluklar:
- Binance Futures mainnet REST API'sinden tüm USDT-perpetual sembolleri al
- 24 saatlik hacim verisini topla, top N en likit coin'i seç
- Trade universe'ünü Redis stream'e yayınla
- Universe değişikliklerini diğer agent'lara duyur
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import aiohttp

from src.agents.base import BaseAgent, run_agent

# Mainnet market data URL (testnet'in market data'sı yetersiz, mainnet kullan)
BINANCE_MAINNET_REST = "https://fapi.binance.com"

# Stream isimleri (config'den okunabilir ama hardcoded başlıyoruz)
STREAM_UNIVERSE_UPDATE = "stream:universe.update"
STREAM_UNIVERSE_SNAPSHOT = "stream:universe.snapshot"

# Top N coin (basit başlıyoruz, config-driven sonra)
TOP_N_COINS = 20


class MarketScannerAgent(BaseAgent):
    """Trade universe'ünü dinamik olarak yöneten agent."""

    agent_name = "market_scanner"
    heartbeat_interval = 10.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._current_universe: set[str] = set()
        self._scan_interval = self.config.universe.refresh_interval_seconds
        self._http_session: aiohttp.ClientSession | None = None

    async def _initialize(self) -> None:
        """HTTP session başlat ve ilk universe taraması yap."""
        self.logger.info("market_scanner_initializing")
        timeout = aiohttp.ClientTimeout(total=30)
        self._http_session = aiohttp.ClientSession(timeout=timeout)

        # İlk taramayı hemen yap — agent başladığı an universe hazır olsun
        try:
            initial_universe = await self._scan_market()
            self._current_universe = initial_universe
            await self._publish_snapshot()
            self.logger.info(
                "market_scanner_initialized",
                universe_size=len(self._current_universe),
                top_5=list(self._current_universe)[:5],
            )
        except Exception as e:
            self.logger.exception("initial_scan_failed", error=str(e))
            # İlk tarama başarısız olsa bile agent çalışmaya devam etsin

    async def _run(self) -> None:
        """Periyodik olarak universe'ü tara ve değişiklikleri yayınla."""
        self.logger.info(
            "market_scanner_loop_started",
            scan_interval_seconds=self._scan_interval,
        )

        while not self._stop_event.is_set():
            try:
                # İlk iterasyon zaten _initialize'da yapıldı, burada bekle
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._scan_interval
                )
            except asyncio.TimeoutError:
                # Timeout = scan zamanı geldi
                try:
                    new_universe = await self._scan_market()
                    await self._diff_and_publish(new_universe)
                    self._current_universe = new_universe
                except Exception as e:
                    self.logger.exception("market_scan_failed", error=str(e))
                continue
            except Exception as e:
                self.logger.exception("market_scan_iteration_failed", error=str(e))
                await asyncio.sleep(5)

    async def _scan_market(self) -> set[str]:
        """Binance'den USDT-perpetual sembolleri çek, top N likit olanı seç."""
        if self._http_session is None:
            raise RuntimeError("HTTP session başlatılmadı")

        # 1. Tüm exchange bilgisini al (hangi semboller TRADING durumda, perpetual mi?)
        async with self._http_session.get(
            f"{BINANCE_MAINNET_REST}/fapi/v1/exchangeInfo"
        ) as resp:
            resp.raise_for_status()
            exchange_info = await resp.json()

        # USDT-margined PERPETUAL kontratları filtrele
        active_perpetuals: set[str] = set()
        for symbol_info in exchange_info.get("symbols", []):
            if (
                symbol_info.get("quoteAsset") == "USDT"
                and symbol_info.get("contractType") == "PERPETUAL"
                and symbol_info.get("status") == "TRADING"
            ):
                active_perpetuals.add(symbol_info["symbol"])

        self.logger.info(
            "exchange_info_fetched",
            total_perpetuals=len(active_perpetuals),
        )

        # 2. 24 saatlik ticker istatistiklerini çek (tek istek, tüm semboller)
        async with self._http_session.get(
            f"{BINANCE_MAINNET_REST}/fapi/v1/ticker/24hr"
        ) as resp:
            resp.raise_for_status()
            tickers = await resp.json()

        # Sadece bizim universe'deki semboller, hacme göre sırala
        relevant_tickers = [
            t for t in tickers if t["symbol"] in active_perpetuals
        ]
        relevant_tickers.sort(
            key=lambda t: float(t.get("quoteVolume", 0)),
            reverse=True,
        )

        # Top N
        top_symbols = {t["symbol"] for t in relevant_tickers[:TOP_N_COINS]}

        self.logger.info(
            "market_scan_completed",
            total_symbols=len(relevant_tickers),
            selected=len(top_symbols),
            top_5_with_volume=[
                {
                    "symbol": t["symbol"],
                    "volume_24h_usd": round(float(t["quoteVolume"]), 0),
                }
                for t in relevant_tickers[:5]
            ],
        )

        return top_symbols

    async def _diff_and_publish(self, new_universe: set[str]) -> None:
        """Eski ve yeni universe'ü karşılaştır, değişiklikleri yayınla."""
        added = new_universe - self._current_universe
        removed = self._current_universe - new_universe

        if added:
            for symbol in added:
                await self._publish_universe_event("added", symbol)
            self.logger.info("universe_symbols_added", count=len(added), symbols=list(added))

        if removed:
            for symbol in removed:
                await self._publish_universe_event("removed", symbol)
            self.logger.info("universe_symbols_removed", count=len(removed), symbols=list(removed))

        # Snapshot her zaman yayınla (heartbeat gibi)
        await self._publish_snapshot()

    async def _publish_universe_event(self, event_type: str, symbol: str) -> None:
        """Universe update event'ini Redis stream'e yayınla."""
        if self._redis_bus is None:
            return
        await self._redis_bus.publish(
            STREAM_UNIVERSE_UPDATE,
            {
                "event_type": event_type,
                "symbol": symbol,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    async def _publish_snapshot(self) -> None:
        """Mevcut universe'ün tam snapshot'ını yayınla."""
        if self._redis_bus is None:
            return
        await self._redis_bus.publish(
            STREAM_UNIVERSE_SNAPSHOT,
            {
                "symbols": sorted(self._current_universe),
                "size": len(self._current_universe),
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

    async def _shutdown(self) -> None:
        """Cleanup."""
        self.logger.info("market_scanner_shutting_down")
        if self._http_session is not None:
            await self._http_session.close()

    async def _health_check(self) -> dict[str, float]:
        return {"universe_size": float(len(self._current_universe))}


if __name__ == "__main__":
    asyncio.run(run_agent(MarketScannerAgent))
