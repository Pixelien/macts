"""Risk Management Agent — Faz 4 Adım 1 (Basit kurallar).

Signal Generation'dan ham sinyalleri alır, basit kurallarla değerlendirir,
onayladıklarını stream:signals.approved'a yayınlar.

Akış:
- stream:signals.raw'ı dinle
- 4 kuralla değerlendir:
  1. Confidence floor (>= 0.5)
  2. Position size cap (<= 0.08)
  3. Same-direction limit (5dk içinde 3+ aynı yön reddedilir)
  4. Universe filter (sembol şu anda universe'te mi)
- Approved → stream:signals.approved'a yayınla
- Tüm kararları InfluxDB'ye risk_decisions measurement'ına yaz

Gelecekte (Faz 4 sonrası):
- Half Kelly position sizing (gerçek win/loss verisi olunca)
- Korelasyon matrisi (BTC ve ETH aynı anda LONG değil)
- VaR/CVaR
- Drawdown circuit breaker
- Portfolio balance tracking
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

import aiohttp

from src.agents.base import BaseAgent, run_agent

# Risk kuralları
# v2: Backtest sonuçlarına göre daha sıkı filtreler (12 Mayıs 2026)
# Mevcut hit rate: %52.05 → Filtre sonrası beklenen: %58-62
MIN_CONFIDENCE_APPROVAL = 0.7  # 0.5 → 0.7 (backtest: 0.7+ = %56 hit rate)
MAX_POSITION_PCT_APPROVAL = 0.10
MAX_OPEN_POSITIONS_PER_DIRECTION = 3
OPEN_POSITION_TTL_SECONDS = 300

# Yön whitelist: LONG (%48.97) rastgele, SHORT (%55.10) edge var
ALLOWED_DIRECTIONS = {"LONG", "SHORT"}  # her iki yön açık (sembol whitelist yeter)

# Sembol whitelist: backtest'te hit rate >%60 olan top 5
ALLOWED_SYMBOLS = {
    "ONDOUSDT",      # 70.37%
    "ETHUSDT",       # 66.67%
    "TAOUSDT",       # 64.86%
    "ZECUSDT",       # 64.52%
    "1000PEPEUSDT",  # 61.54%
    "BNBUSDT",       # 59.46%
    "TONUSDT",       # 55.56%
}

STATS_INTERVAL = 30.0

# Streams
STREAM_UNIVERSE_SNAPSHOT = "stream:universe.snapshot"
STREAM_SIGNALS_RAW = "stream:signals.raw"
STREAM_SIGNALS_APPROVED = "stream:signals.approved"

# InfluxDB
INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "gazifintech")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "macts_market_data")


class RiskManagementAgent(BaseAgent):
    """Basit kural tabanlı risk değerlendirme."""

    agent_name = "risk_management"
    heartbeat_interval = 5.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # State
        self._current_universe: set[str] = set()
        # Açık pozisyonlar: {(symbol, direction): timestamp_secs}
        self._open_positions: dict[tuple[str, str], float] = {}
        # Sayaçlar
        self._signals_received = 0
        self._signals_approved = 0
        self._signals_rejected = 0
        self._rejection_counts: dict[str, int] = {
            "low_confidence": 0,
            "position_too_large": 0,
            "max_open_positions": 0,
            "not_in_universe": 0,
            "direction_not_allowed": 0,
            "symbol_not_whitelisted": 0,
        }
        self._influx_writes = 0
        self._influx_errors = 0
        # HTTP session
        self._influx_session: aiohttp.ClientSession | None = None

    async def _initialize(self) -> None:
        self.logger.info(
            "risk_management_initializing",
            min_confidence=MIN_CONFIDENCE_APPROVAL,
            max_position_pct=MAX_POSITION_PCT_APPROVAL,
            max_open_per_direction=MAX_OPEN_POSITIONS_PER_DIRECTION,
            position_ttl_seconds=OPEN_POSITION_TTL_SECONDS,
            allowed_directions=sorted(ALLOWED_DIRECTIONS),
            allowed_symbols=sorted(ALLOWED_SYMBOLS),
            filter_version="v2_pragmatic_post_backtest",
        )
        timeout = aiohttp.ClientTimeout(total=10)
        headers = {
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "text/plain; charset=utf-8",
            "Accept": "application/json",
        }
        self._influx_session = aiohttp.ClientSession(timeout=timeout, headers=headers)

    async def _run(self) -> None:
        self.logger.info("risk_management_loop_started")

        universe_task = asyncio.create_task(self._listen_universe())
        signals_task = asyncio.create_task(self._listen_signals())
        stats_task = asyncio.create_task(self._stats_loop())
        cleanup_task = asyncio.create_task(self._cleanup_expired_positions())

        try:
            await self._stop_event.wait()
        finally:
            universe_task.cancel()
            signals_task.cancel()
            stats_task.cancel()
            cleanup_task.cancel()
            await asyncio.gather(
                universe_task,
                signals_task,
                stats_task,
                cleanup_task,
                return_exceptions=True,
            )

    async def _listen_universe(self) -> None:
        """Market Scanner'dan universe güncellemelerini al."""
        if self._redis_bus is None:
            return
        self.logger.info("universe_listener_started")
        try:
            async for msg in self._redis_bus.subscribe(
                STREAM_UNIVERSE_SNAPSHOT, from_beginning=True
            ):
                if self._stop_event.is_set():
                    break
                await self._apply_universe(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.exception("universe_listener_failed", error=str(e))

    async def _apply_universe(self, msg: dict[str, Any]) -> None:
        symbols_raw = msg.get("symbols")
        if symbols_raw is None:
            return
        if isinstance(symbols_raw, str):
            import json
            try:
                self._current_universe = set(json.loads(symbols_raw))
            except json.JSONDecodeError:
                return
        elif isinstance(symbols_raw, list):
            self._current_universe = set(symbols_raw)

    async def _listen_signals(self) -> None:
        """stream:signals.raw'ı dinle."""
        if self._redis_bus is None:
            return
        self.logger.info("signals_listener_started", stream=STREAM_SIGNALS_RAW)
        try:
            async for msg in self._redis_bus.subscribe(
                STREAM_SIGNALS_RAW, from_beginning=False
            ):
                if self._stop_event.is_set():
                    break
                await self._evaluate_signal(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.exception("signals_listener_failed", error=str(e))

    async def _evaluate_signal(self, signal: dict[str, Any]) -> None:
        """Sinyali kurallara göre değerlendir."""
        self._signals_received += 1

        try:
            symbol = str(signal["symbol"])
            direction = str(signal["direction"])
            confidence = float(signal["confidence"])
            position_size_pct = float(signal["position_size_pct"])
            prob_up = float(signal["prob_up"])
            feature_time = int(signal.get("feature_time", 0))
        except (KeyError, ValueError, TypeError) as e:
            self.logger.warning("signal_parse_failed", error=str(e))
            return

        # Universe taze değilse (henüz alınmadıysa) tüm sembolleri geçici onayla
        # (genelde 1-2 saniye içinde universe gelir)
        universe_check_enabled = len(self._current_universe) > 0

        # Kuralları değerlendir (sırayla, ilk fail = reject)
        # Whitelist'ler en başta — hesaplama yükünü azaltır
        rejection_reason = None

        # Kural 0a: Yön whitelist
        if direction not in ALLOWED_DIRECTIONS:
            rejection_reason = "direction_not_allowed"

        # Kural 0b: Sembol whitelist
        elif symbol not in ALLOWED_SYMBOLS:
            rejection_reason = "symbol_not_whitelisted"

        # Kural 1: Confidence floor
        elif confidence < MIN_CONFIDENCE_APPROVAL:
            rejection_reason = "low_confidence"

        # Kural 2: Position size cap
        elif position_size_pct > MAX_POSITION_PCT_APPROVAL:
            rejection_reason = "position_too_large"

        # Kural 3: Universe filter
        elif universe_check_enabled and symbol not in self._current_universe:
            rejection_reason = "not_in_universe"

        # Kural 4: Same-direction limit (open positions sayımı)
        else:
            same_direction_count = sum(
                1
                for (_sym, _dir) in self._open_positions.keys()
                if _dir == direction
            )
            if same_direction_count >= MAX_OPEN_POSITIONS_PER_DIRECTION:
                rejection_reason = "max_open_positions"

        approved = rejection_reason is None

        if approved:
            self._signals_approved += 1
            # Açık pozisyon listesine ekle
            now = asyncio.get_event_loop().time()
            self._open_positions[(symbol, direction)] = now

            self.logger.info(
                "signal_approved",
                symbol=symbol,
                direction=direction,
                confidence=round(confidence, 3),
                position_size_pct=round(position_size_pct, 4),
                open_long=sum(1 for (_s, d) in self._open_positions.keys() if d == "LONG"),
                open_short=sum(1 for (_s, d) in self._open_positions.keys() if d == "SHORT"),
            )

            # stream:signals.approved'a yayınla
            approved_signal = {
                "symbol": symbol,
                "direction": direction,
                "confidence": round(confidence, 4),
                "prob_up": round(prob_up, 4),
                "position_size_pct": round(position_size_pct, 4),
                "feature_time": feature_time,
                "approved_at": datetime.utcnow().isoformat(),
                "source": signal.get("source", "unknown"),
            }
            if self._redis_bus is not None:
                await self._redis_bus.publish(STREAM_SIGNALS_APPROVED, approved_signal)
        else:
            self._signals_rejected += 1
            self._rejection_counts[rejection_reason] = (
                self._rejection_counts.get(rejection_reason, 0) + 1
            )
            self.logger.info(
                "signal_rejected",
                symbol=symbol,
                direction=direction,
                reason=rejection_reason,
                confidence=round(confidence, 3),
                position_size_pct=round(position_size_pct, 4),
            )

        # InfluxDB'ye karar yaz
        await self._write_decision_to_influx(
            symbol=symbol,
            direction=direction,
            approved=approved,
            rejection_reason=rejection_reason,
            confidence=confidence,
            position_size_pct=position_size_pct,
            prob_up=prob_up,
            feature_time=feature_time,
        )

    async def _cleanup_expired_positions(self) -> None:
        """5 dakikadan eski açık pozisyon kayıtlarını sil."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)
                break
            except asyncio.TimeoutError:
                now = asyncio.get_event_loop().time()
                expired_keys = [
                    k
                    for k, ts in self._open_positions.items()
                    if now - ts > OPEN_POSITION_TTL_SECONDS
                ]
                for k in expired_keys:
                    del self._open_positions[k]
                if expired_keys:
                    self.logger.debug(
                        "expired_positions_cleaned",
                        count=len(expired_keys),
                        remaining=len(self._open_positions),
                    )

    async def _write_decision_to_influx(
        self,
        symbol: str,
        direction: str,
        approved: bool,
        rejection_reason: str | None,
        confidence: float,
        position_size_pct: float,
        prob_up: float,
        feature_time: int,
    ) -> None:
        if self._influx_session is None or not INFLUX_TOKEN:
            return

        # Tags: symbol, direction, approved (str), reason (str or "none")
        approved_tag = "true" if approved else "false"
        reason_tag = rejection_reason if rejection_reason else "none"

        # Fields: confidence, prob_up, position_size_pct, approved_int
        line = (
            f"risk_decisions,symbol={symbol},direction={direction},"
            f"approved={approved_tag},reason={reason_tag} "
            f"confidence={confidence},"
            f"prob_up={prob_up},"
            f"position_size_pct={position_size_pct},"
            f"approved_int={1 if approved else 0}i "
            f"{feature_time * 1_000_000}"  # ns
        )

        url = f"{INFLUX_URL}/api/v2/write"
        params = {"org": INFLUX_ORG, "bucket": INFLUX_BUCKET, "precision": "ns"}
        try:
            async with self._influx_session.post(
                url, params=params, data=line.encode("utf-8")
            ) as resp:
                if resp.status == 204:
                    self._influx_writes += 1
                else:
                    self._influx_errors += 1
        except Exception:
            self._influx_errors += 1

    async def _stats_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=STATS_INTERVAL)
                break
            except asyncio.TimeoutError:
                self.logger.info(
                    "risk_management_stats",
                    signals_received=self._signals_received,
                    signals_approved=self._signals_approved,
                    signals_rejected=self._signals_rejected,
                    rejections_by_reason=dict(self._rejection_counts),
                    open_positions_total=len(self._open_positions),
                    open_long=sum(
                        1 for (_s, d) in self._open_positions.keys() if d == "LONG"
                    ),
                    open_short=sum(
                        1 for (_s, d) in self._open_positions.keys() if d == "SHORT"
                    ),
                    universe_size=len(self._current_universe),
                    influx_writes=self._influx_writes,
                    influx_errors=self._influx_errors,
                )

    async def _shutdown(self) -> None:
        self.logger.info("risk_management_shutting_down")
        if self._influx_session is not None:
            await self._influx_session.close()

    async def _health_check(self) -> dict[str, float]:
        return {
            "signals_received": float(self._signals_received),
            "signals_approved": float(self._signals_approved),
            "signals_rejected": float(self._signals_rejected),
            "open_positions": float(len(self._open_positions)),
        }


if __name__ == "__main__":
    asyncio.run(run_agent(RiskManagementAgent))
