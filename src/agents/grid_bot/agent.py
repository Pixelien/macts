"""Grid Bot Agent — Faz B (ML-Filtered Grid Trading, Paper Mode).

Backtest sonucu (19 Mayıs 2026):
  v1 ham grid:           -%4.42
  v1.5 tuned:            -%1.48
  v2 ML filter:          -%0.27
  v3 + liquidation:      -%0.03
  v4 loose filter:       +%0.02   ← paper trade adayı

Mimari:
- Her sembol için kendi GridBotState (in-memory)
- ML regime detection (4-saatte bir retrain)
- Sadece RANGING rejimde grid aktif
- TRENDING tespit edildiyse inventory likide
- In-memory paper wallet ($1000 başlangıç sermayesi)
- Trade'ler InfluxDB'ye yazılır (grid_paper_trades measurement)

Akış:
- Her dakika InfluxDB'den son kline+feature çek
- Her sembol için GridBotState.tick(price, regime)
- Trade üretilirse paper wallet güncellenir + InfluxDB'ye yazılır
- Hiçbir gerçek emir gönderilmez (paper mode)
"""
from __future__ import annotations

import asyncio
import os
from collections import deque
from datetime import datetime, timedelta
from typing import Any

import aiohttp
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from src.agents.base import BaseAgent, run_agent

# =============================================================================
# KONFİG — Backtest v4 parametreleri
# =============================================================================

# Hangi semboller — backtest'te kullanılan 5 likit
GRID_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]

# Grid bot parametreleri
INITIAL_CAPITAL_USDT = 1000.0       # Her sembol için ayrı paper wallet
N_GRIDS = 12                         # 12 grid çizgisi
RANGE_LOOKBACK_HOURS = 48            # Range belirleme: son 48 saat
GRID_REBALANCE_HOURS = 12            # 12 saatte bir range'i güncelle
ORDER_SIZE_USDT = 100.0              # Her tetiklemede $100 emir (v2: edge testi)
FEE_RATE = 0.0004                    # Binance futures taker fee 0.04%

# ML Regime Detection parametreleri
ML_TRAIN_HOURS = 48                  # Son 48 saat veriyle eğit
ML_RETRAIN_HOURS = 4                 # 4 saatte bir yeniden eğit
REGIME_WINDOW_MINUTES = 240          # 4-saatlik rejim hedefi
RANGING_PROB_THRESHOLD = 0.40        # v4: ranging prob >= 0.40 → RANGING kabul
TRENDING_THRESHOLD_ATR = 2.0
RANGING_THRESHOLD_ATR = 3.0

# Tick döngüsü
TICK_INTERVAL_SECONDS = 60.0         # Her 60 saniyede bir tick (1 dk = 1 mum)

# Feature listesi (Per-Coin Learning ile aynı)
FEATURE_COLS = [
    "rsi_14", "macd", "macd_signal", "macd_hist",
    "bb_upper", "bb_middle", "bb_lower",
    "ema_9", "ema_21", "ema_50",
    "sma_20", "sma_50", "atr_14",
]

# Stats heartbeat
STATS_INTERVAL = 60.0

# InfluxDB
INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "gazifintech")
INFLUX_BUCKET = os.environ.get("INFLUXDB_BUCKET", "macts_market_data")

# Sembol bazında verinin yetersiz olduğunu anlamak için minimum
MIN_KLINES_FOR_TRAINING = ML_TRAIN_HOURS * 60  # 2880 dakikalık veri
MIN_KLINES_FOR_GRID = RANGE_LOOKBACK_HOURS * 60  # 2880 dakikalık veri


# =============================================================================
# Grid Bot Agent
# =============================================================================

class GridBotAgent(BaseAgent):
    """ML-Filtered Grid Trading — Paper Mode.

    Her sembol için:
      - In-memory paper wallet ($1000 başlangıç)
      - ML regime detector (HistGradientBoostingClassifier)
      - Grid state (grid levels, last grid idx, rebalance timer)
      - Inventory tracking
    """

    agent_name = "grid_bot"
    heartbeat_interval = 5.0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        # Her sembol için ayrı state
        # state[symbol] = {
        #   "cash": float,
        #   "inventory_qty": float,
        #   "inventory_cost": float,
        #   "grid_levels": list[float] | None,
        #   "last_grid_idx": int | None,
        #   "last_rebalance_at": datetime | None,
        #   "ml_model": Any | None,
        #   "ml_features": list[str],
        #   "last_ml_train_at": datetime | None,
        #   "last_regime": int | None,        # 0=ranging, 1=trend_up, 2=trend_down
        #   "trade_count": int,
        #   "cycle_count": int,
        #   "total_pnl": float,
        # }
        self._states: dict[str, dict[str, Any]] = {}

        # Global sayaçlar
        self._total_ticks = 0
        self._total_trades = 0
        self._total_liquidations = 0
        self._influx_writes = 0
        self._influx_errors = 0

        # HTTP session (InfluxDB için)
        self._influx_session: aiohttp.ClientSession | None = None

    # =========================================================================
    # ML Regime Detection
    # =========================================================================

    @staticmethod
    def _add_regime_target(df: pd.DataFrame) -> pd.DataFrame:
        """4-saatlik pencerede rejim sınıfı belirle.

        0 = RANGING (max-min ≤ ATR × 3)
        1 = TRENDING_UP (close artışı > ATR × 2)
        2 = TRENDING_DOWN (close düşüşü > ATR × 2)
        """
        import talib
        df = df.copy()
        df["future_high"] = (
            df["high"].rolling(window=REGIME_WINDOW_MINUTES,
                               min_periods=REGIME_WINDOW_MINUTES)
            .max().shift(-REGIME_WINDOW_MINUTES)
        )
        df["future_low"] = (
            df["low"].rolling(window=REGIME_WINDOW_MINUTES,
                              min_periods=REGIME_WINDOW_MINUTES)
            .min().shift(-REGIME_WINDOW_MINUTES)
        )
        df["future_close"] = df["close"].shift(-REGIME_WINDOW_MINUTES)
        atr_60 = talib.ATR(
            df["high"].values.astype(np.float64),
            df["low"].values.astype(np.float64),
            df["close"].values.astype(np.float64),
            timeperiod=60,
        )
        df["atr_60"] = atr_60

        range_size = df["future_high"] - df["future_low"]
        change = df["future_close"] - df["close"]

        df["target"] = 0  # default RANGING
        df.loc[(change > df["atr_60"] * TRENDING_THRESHOLD_ATR), "target"] = 1
        df.loc[(change < -df["atr_60"] * TRENDING_THRESHOLD_ATR), "target"] = 2
        # Override: range çok küçükse zorunlu RANGING
        df.loc[
            (range_size <= df["atr_60"] * RANGING_THRESHOLD_ATR), "target"
        ] = 0

        df = df.drop(columns=["future_high", "future_low", "future_close",
                              "atr_60"])
        return df

    def _train_regime_model(
        self, symbol: str, df_window: pd.DataFrame
    ) -> tuple[Any, list[str]]:
        """Verilen pencereyle yeni model eğit, döndür."""
        available_features = [c for c in FEATURE_COLS if c in df_window.columns]
        clean = df_window.dropna(subset=available_features + ["target"])

        if len(clean) < 200:
            self.logger.warning(
                "ml_train_skipped_insufficient",
                symbol=symbol,
                rows=len(clean),
            )
            return None, available_features

        X = clean[available_features].values
        y = clean["target"].values

        if len(np.unique(y)) < 2:
            self.logger.warning(
                "ml_train_skipped_single_class",
                symbol=symbol,
            )
            return None, available_features

        model = HistGradientBoostingClassifier(
            max_iter=100,
            max_depth=5,
            learning_rate=0.1,
            random_state=42,
        )
        model.fit(X, y)

        # Eğitim sınıf dağılımı
        unique, counts = np.unique(y, return_counts=True)
        dist = {int(u): int(c) for u, c in zip(unique, counts)}
        self.logger.info(
            "ml_train_completed",
            symbol=symbol,
            n_train=len(X),
            class_dist=dist,
        )
        return model, available_features

    def _predict_regime(
        self,
        model: Any,
        features_row: pd.Series,
        available_features: list[str],
    ) -> int | None:
        """Tek satır feature → rejim tahmin.

        v4 logic: predict_proba kullanılır. Ranging probability >= 0.40 ise
        RANGING kabul edilir (grid'in daha çok aktif olmasını sağlar).
        """
        if model is None:
            return None

        X = features_row[available_features].values.reshape(1, -1)
        if np.any(np.isnan(X)):
            return None

        try:
            probs = model.predict_proba(X)[0]
            classes = list(model.classes_)
            ranging_idx = classes.index(0) if 0 in classes else None
            if ranging_idx is not None:
                ranging_prob = probs[ranging_idx]
                if ranging_prob >= RANGING_PROB_THRESHOLD:
                    return 0
            return int(classes[np.argmax(probs)])
        except Exception as e:
            self.logger.warning(
                "predict_regime_failed",
                error=str(e),
            )
            return None

    # =========================================================================
    # Grid Management
    # =========================================================================

    @staticmethod
    def _compute_grid_levels(low: float, high: float) -> list[float]:
        """Geometrik (yüzdesel eşit) grid çizgileri."""
        if low <= 0 or high <= low:
            return []
        ratio = (high / low) ** (1.0 / (N_GRIDS - 1))
        return [low * (ratio ** i) for i in range(N_GRIDS)]

    def _get_or_init_state(self, symbol: str) -> dict[str, Any]:
        """Sembol için state yoksa oluştur, varsa döndür."""
        if symbol not in self._states:
            self._states[symbol] = {
                "cash": INITIAL_CAPITAL_USDT,
                "inventory_qty": 0.0,
                "inventory_cost": 0.0,
                "grid_levels": None,
                "last_grid_idx": None,
                "last_rebalance_at": None,
                "ml_model": None,
                "ml_features": [],
                "last_ml_train_at": None,
                "last_regime": None,           # 0/1/2 or None
                "trade_count": 0,
                "cycle_count": 0,
                "total_pnl": 0.0,
                "total_fees": 0.0,
            }
            self.logger.info(
                "grid_state_initialized",
                symbol=symbol,
                initial_capital=INITIAL_CAPITAL_USDT,
            )
        return self._states[symbol]

    # =========================================================================
    # Paper Wallet Operations
    # =========================================================================

    async def _execute_paper_buy(
        self, symbol: str, state: dict[str, Any], price: float
    ) -> None:
        """ORDER_SIZE_USDT kadar BUY emri simüle et."""
        if state["cash"] < ORDER_SIZE_USDT:
            return  # Yetersiz cash, atla

        qty_bought = ORDER_SIZE_USDT / price
        fee = ORDER_SIZE_USDT * FEE_RATE

        state["cash"] -= (ORDER_SIZE_USDT + fee)
        state["inventory_qty"] += qty_bought
        state["inventory_cost"] += ORDER_SIZE_USDT
        state["total_fees"] += fee
        state["trade_count"] += 1
        self._total_trades += 1

        await self._log_trade_to_influx(
            symbol=symbol,
            side="BUY",
            price=price,
            qty=qty_bought,
            usdt=ORDER_SIZE_USDT,
            fee=fee,
            reason="grid_buy",
            state=state,
        )

    async def _execute_paper_sell(
        self,
        symbol: str,
        state: dict[str, Any],
        price: float,
        qty_to_sell: float | None = None,
        reason: str = "grid_sell",
    ) -> None:
        """Inventory'den sell — qty_to_sell None ise ORDER_SIZE_USDT kadar.

        reason: 'grid_sell' (normal grid tetikleme) veya 'liquidation' (trending).
        """
        if state["inventory_qty"] <= 0:
            return

        if qty_to_sell is None:
            qty_to_sell = min(state["inventory_qty"], ORDER_SIZE_USDT / price)

        if qty_to_sell > state["inventory_qty"]:
            qty_to_sell = state["inventory_qty"]
        if qty_to_sell <= 0:
            return

        gross_proceeds = qty_to_sell * price
        fee = gross_proceeds * FEE_RATE
        state["cash"] += (gross_proceeds - fee)
        state["total_fees"] += fee

        # Cycle PnL (ortalama maliyet yöntemi)
        avg_cost = state["inventory_cost"] / state["inventory_qty"]
        cost_basis = qty_to_sell * avg_cost
        state["inventory_cost"] -= cost_basis
        state["inventory_qty"] -= qty_to_sell

        cycle_pnl = qty_to_sell * (price - avg_cost) - fee
        state["total_pnl"] += cycle_pnl
        state["cycle_count"] += 1
        state["trade_count"] += 1
        self._total_trades += 1

        if reason == "liquidation":
            self._total_liquidations += 1

        await self._log_trade_to_influx(
            symbol=symbol,
            side="SELL",
            price=price,
            qty=qty_to_sell,
            usdt=gross_proceeds,
            fee=fee,
            reason=reason,
            cycle_pnl=cycle_pnl,
            state=state,
        )

    @staticmethod
    def _equity(state: dict[str, Any], current_price: float) -> float:
        """Mevcut toplam değer (cash + inventory × fiyat)."""
        return state["cash"] + state["inventory_qty"] * current_price

    # =========================================================================
    # InfluxDB Veri Çekme (read)
    # =========================================================================

    async def _fetch_klines_features(
        self, symbol: str, days: int
    ) -> pd.DataFrame | None:
        """InfluxDB'den son N günlük kline çek + TA-Lib feature hesapla."""
        import talib

        flux = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: -{days}d)
          |> filter(fn: (r) => r._measurement == "klines"
                               and r.symbol == "{symbol}")
          |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")
          |> sort(columns:["_time"])
        '''

        url = f"{INFLUX_URL}/api/v2/query?org={INFLUX_ORG}"
        headers = {
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        }

        if self._influx_session is None:
            return None

        try:
            async with self._influx_session.post(
                url, headers=headers, data=flux, timeout=30
            ) as resp:
                if resp.status != 200:
                    self.logger.warning(
                        "influx_query_failed",
                        symbol=symbol,
                        status=resp.status,
                    )
                    return None
                csv_text = await resp.text()
        except Exception as e:
            self.logger.warning(
                "influx_fetch_error",
                symbol=symbol,
                error=str(e),
            )
            return None

        # CSV parse
        from io import StringIO
        try:
            df = pd.read_csv(StringIO(csv_text), comment="#")
        except Exception as e:
            self.logger.warning("influx_csv_parse_failed", error=str(e))
            return None

        if df.empty or "_time" not in df.columns:
            return None

        # InfluxDB CSV gereksiz sütunları siler
        keep_cols = ["_time", "open", "high", "low", "close", "volume",
                     "quote_volume", "trades"]
        df = df[[c for c in keep_cols if c in df.columns]]
        df["_time"] = pd.to_datetime(df["_time"], utc=True)
        df = df.set_index("_time").sort_index()
        df = df[~df.index.duplicated(keep="last")]

        if len(df) < 100:
            return None

        # TA-Lib feature hesabı
        close = df["close"].values.astype(np.float64)
        high = df["high"].values.astype(np.float64)
        low = df["low"].values.astype(np.float64)

        df["rsi_14"] = talib.RSI(close, timeperiod=14)
        macd, macd_sig, macd_hist = talib.MACD(
            close, fastperiod=12, slowperiod=26, signalperiod=9
        )
        df["macd"] = macd
        df["macd_signal"] = macd_sig
        df["macd_hist"] = macd_hist
        bbu, bbm, bbl = talib.BBANDS(
            close, timeperiod=20, nbdevup=2, nbdevdn=2
        )
        df["bb_upper"] = bbu
        df["bb_middle"] = bbm
        df["bb_lower"] = bbl
        df["ema_9"] = talib.EMA(close, timeperiod=9)
        df["ema_21"] = talib.EMA(close, timeperiod=21)
        df["ema_50"] = talib.EMA(close, timeperiod=50)
        df["sma_20"] = talib.SMA(close, timeperiod=20)
        df["sma_50"] = talib.SMA(close, timeperiod=50)
        df["atr_14"] = talib.ATR(high, low, close, timeperiod=14)

        return df

    # =========================================================================
    # Tick Loop (her dakika çalışır)
    # =========================================================================

    async def _process_symbol_tick(self, symbol: str) -> None:
        """Tek bir sembol için bir tick döngüsü."""
        state = self._get_or_init_state(symbol)
        now = datetime.utcnow()

        # 1. Veri çek (son 4 günlük, ML train + range için yeter)
        df = await self._fetch_klines_features(symbol, days=4)
        if df is None or len(df) < MIN_KLINES_FOR_TRAINING:
            self.logger.debug(
                "tick_skipped_insufficient_data",
                symbol=symbol,
                rows=0 if df is None else len(df),
            )
            return

        latest = df.iloc[-1]
        price = float(latest["close"])
        high_i = float(latest["high"])
        low_i = float(latest["low"])

        # 2. ML model — retrain gerekiyor mu?
        need_retrain = (
            state["ml_model"] is None
            or state["last_ml_train_at"] is None
            or (now - state["last_ml_train_at"]) >= timedelta(hours=ML_RETRAIN_HOURS)
        )
        if need_retrain:
            df_with_target = self._add_regime_target(df)
            model, features = self._train_regime_model(symbol, df_with_target)
            state["ml_model"] = model
            state["ml_features"] = features
            state["last_ml_train_at"] = now

        # 3. Regime tahmini
        regime = self._predict_regime(
            state["ml_model"], latest, state["ml_features"]
        )
        state["last_regime"] = regime

        # 4. Grid range rebalance gerekiyor mu?
        need_rebalance = (
            state["grid_levels"] is None
            or state["last_rebalance_at"] is None
            or (now - state["last_rebalance_at"])
                >= timedelta(hours=GRID_REBALANCE_HOURS)
        )
        if need_rebalance:
            lookback = df.iloc[-MIN_KLINES_FOR_GRID:]
            low = float(lookback["low"].min())
            high = float(lookback["high"].max())
            grid_levels = self._compute_grid_levels(low, high)
            if grid_levels:
                state["grid_levels"] = grid_levels
                state["last_grid_idx"] = None
                state["last_rebalance_at"] = now
                self.logger.info(
                    "grid_rebalanced",
                    symbol=symbol,
                    low=low,
                    high=high,
                    n_levels=len(grid_levels),
                )

        if not state["grid_levels"]:
            return

        # 5. ML Filter — TRENDING ise inventory likide
        grid_active = (regime == 0)
        if not grid_active and state["inventory_qty"] > 0 and regime is not None:
            await self._execute_paper_sell(
                symbol=symbol,
                state=state,
                price=price,
                qty_to_sell=state["inventory_qty"],
                reason="liquidation",
            )
            state["last_grid_idx"] = None

        if not grid_active:
            return  # Trending, grid pasif

        # 6. Grid tetikleme kontrolü (1 dakikalık mum bazlı)
        # Bu mumda hangi grid'ler kesildi?
        for grid_idx, grid_price in enumerate(state["grid_levels"]):
            if low_i <= grid_price <= high_i:
                if state["last_grid_idx"] is None:
                    state["last_grid_idx"] = grid_idx
                    continue
                if grid_idx == state["last_grid_idx"]:
                    continue
                # Yön belirle
                if grid_idx > state["last_grid_idx"]:
                    # Fiyat yukarı, SELL
                    await self._execute_paper_sell(
                        symbol=symbol,
                        state=state,
                        price=grid_price,
                        reason="grid_sell",
                    )
                else:
                    # Fiyat aşağı, BUY
                    await self._execute_paper_buy(
                        symbol=symbol,
                        state=state,
                        price=grid_price,
                    )
                state["last_grid_idx"] = grid_idx
                break  # Bir tick'te tek tetikleme

    # =========================================================================
    # InfluxDB Logging (write)
    # =========================================================================

    async def _log_trade_to_influx(
        self,
        symbol: str,
        side: str,           # "BUY" veya "SELL"
        price: float,
        qty: float,
        usdt: float,
        fee: float,
        reason: str,         # "grid_buy", "grid_sell", "liquidation"
        state: dict[str, Any],
        cycle_pnl: float | None = None,
    ) -> None:
        """Paper trade'i InfluxDB'ye yaz (grid_paper_trades measurement)."""
        if self._influx_session is None:
            return

        equity = self._equity(state, price)
        ts_ns = int(datetime.utcnow().timestamp() * 1e9)

        # InfluxDB line protocol
        # measurement,tag1=v1,tag2=v2 field1=v1,field2=v2 timestamp
        cycle_pnl_str = (
            f",cycle_pnl={cycle_pnl:.6f}" if cycle_pnl is not None else ""
        )
        line = (
            f"grid_paper_trades,symbol={symbol},side={side},reason={reason} "
            f"price={price:.8f},"
            f"qty={qty:.8f},"
            f"usdt={usdt:.6f},"
            f"fee={fee:.6f},"
            f"cash={state['cash']:.4f},"
            f"inventory_qty={state['inventory_qty']:.8f},"
            f"equity={equity:.4f},"
            f"total_pnl={state['total_pnl']:.4f},"
            f"total_fees={state['total_fees']:.4f},"
            f"trade_count={state['trade_count']}i,"
            f"cycle_count={state['cycle_count']}i"
            f"{cycle_pnl_str} "
            f"{ts_ns}"
        )

        url = (
            f"{INFLUX_URL}/api/v2/write"
            f"?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}&precision=ns"
        )
        headers = {
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "text/plain; charset=utf-8",
        }

        try:
            async with self._influx_session.post(
                url, headers=headers, data=line, timeout=10
            ) as resp:
                if resp.status not in (200, 204):
                    text = await resp.text()
                    self.logger.warning(
                        "influx_write_failed",
                        symbol=symbol,
                        status=resp.status,
                        body=text[:200],
                    )
                    self._influx_errors += 1
                else:
                    self._influx_writes += 1
        except Exception as e:
            self.logger.warning(
                "influx_write_error",
                symbol=symbol,
                error=str(e),
            )
            self._influx_errors += 1

    # =========================================================================
    # Agent Lifecycle
    # =========================================================================

    async def _initialize(self) -> None:
        """Agent başlangıç: InfluxDB session aç, state'leri initialize et."""
        if not INFLUX_TOKEN:
            self.logger.error("influx_token_missing")
            raise RuntimeError("INFLUXDB_TOKEN environment variable yok!")

        # Tüm semboller için state oluştur
        for sym in GRID_SYMBOLS:
            self._get_or_init_state(sym)

        self._influx_session = aiohttp.ClientSession()

        self.logger.info(
            "grid_bot_initialized",
            symbols=GRID_SYMBOLS,
            initial_capital=INITIAL_CAPITAL_USDT,
            n_grids=N_GRIDS,
            order_size=ORDER_SIZE_USDT,
            fee_rate=FEE_RATE,
            mode="paper",
        )

    async def _shutdown(self) -> None:
        """Agent kapanış: session kapat, son istatistik."""
        if self._influx_session:
            await self._influx_session.close()
            self._influx_session = None

        # Final equity raporu
        for sym, state in self._states.items():
            self.logger.info(
                "grid_state_final",
                symbol=sym,
                cash=round(state["cash"], 2),
                inventory_qty=round(state["inventory_qty"], 6),
                total_pnl=round(state["total_pnl"], 4),
                trade_count=state["trade_count"],
                cycle_count=state["cycle_count"],
            )

    async def _print_stats(self) -> None:
        """Periyodik istatistik."""
        for sym, state in self._states.items():
            # Equity hesabı için son fiyatı bilmiyoruz — sadece state göster
            regime_label = {0: "RANGING", 1: "TREND_UP",
                            2: "TREND_DOWN"}.get(state["last_regime"], "NONE")
            self.logger.info(
                "grid_stats",
                symbol=sym,
                cash=round(state["cash"], 2),
                inventory_qty=round(state["inventory_qty"], 6),
                pnl=round(state["total_pnl"], 4),
                trades=state["trade_count"],
                cycles=state["cycle_count"],
                regime=regime_label,
            )
        self.logger.info(
            "grid_global_stats",
            total_ticks=self._total_ticks,
            total_trades=self._total_trades,
            total_liquidations=self._total_liquidations,
            influx_writes=self._influx_writes,
            influx_errors=self._influx_errors,
        )

    async def _run(self) -> None:
        """Ana döngü — her TICK_INTERVAL_SECONDS'ta tick at."""
        self.logger.info("grid_bot_run_starting")

        stats_task = asyncio.create_task(self._stats_loop())

        try:
            while True:
                tick_start = datetime.utcnow()

                # Tüm semboller paralel tick
                tasks = [
                    self._process_symbol_tick(sym) for sym in GRID_SYMBOLS
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for sym, res in zip(GRID_SYMBOLS, results):
                    if isinstance(res, Exception):
                        self.logger.error(
                            "tick_failed",
                            symbol=sym,
                            error=str(res),
                        )

                self._total_ticks += 1

                # Sonraki tick'e kadar uyu
                elapsed = (datetime.utcnow() - tick_start).total_seconds()
                sleep_for = max(0.0, TICK_INTERVAL_SECONDS - elapsed)
                await asyncio.sleep(sleep_for)

        except asyncio.CancelledError:
            self.logger.info("grid_bot_run_cancelled")
            raise
        finally:
            stats_task.cancel()
            try:
                await stats_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _stats_loop(self) -> None:
        """Periyodik stats yazdırma loop'u."""
        try:
            while True:
                await asyncio.sleep(STATS_INTERVAL)
                await self._print_stats()
        except asyncio.CancelledError:
            pass


# =============================================================================
# Entrypoint
# =============================================================================

if __name__ == "__main__":
    asyncio.run(run_agent(GridBotAgent))
