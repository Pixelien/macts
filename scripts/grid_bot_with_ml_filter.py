"""Grid Bot + ML Regime Filter — Faz B Adım 2.

Yeni ekleme: HistGradientBoostingClassifier ile rejim tespit.
Sadece RANGING tahmin edilirse grid aktif, TRENDING tahmin edilirse pasif.

Target tasarımı (rejim):
  4-saatlik pencerede:
    RANGING (0):       max-min ≤ ATR(60) × 3
    TRENDING_UP (1):   close[t+240] - close[t] > ATR × 2
    TRENDING_DOWN (2): close[t] - close[t+240] > ATR × 2

Model her saat retrain edilir (mini walk-forward).
"""
import os
import numpy as np
import pandas as pd
import talib
from datetime import datetime, timedelta
from influxdb_client import InfluxDBClient
from sklearn.ensemble import HistGradientBoostingClassifier

INFLUX_URL = "http://influxdb:8086"
INFLUX_ORG = "gazifintech"
INFLUX_BUCKET = "macts_market_data"
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
if not INFLUX_TOKEN:
    raise RuntimeError("INFLUXDB_TOKEN yok!")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
TOTAL_DAYS = 9
INITIAL_CAPITAL = 1000.0
N_GRIDS = 12
RANGE_LOOKBACK_HOURS = 48
GRID_REBALANCE_HOURS = 12
FEE_RATE = 0.0004
ORDER_SIZE_USDT = 25.0

# === ML Filter Konfigi ===
REGIME_WINDOW_MINUTES = 240   # 4 saatlik rejim tahmini
ML_TRAIN_HOURS = 48           # Son 48 saat model eğitim verisi
ML_RETRAIN_HOURS = 4          # Her 4 saatte model yeniden eğitilir
RANGING_THRESHOLD_ATR = 3.0   # max-min ≤ ATR×3 → RANGING
TRENDING_THRESHOLD_ATR = 2.0  # |close_change| > ATR×2 → TRENDING

# Feature'lar (mevcut Per-Coin Learning ile aynı)
FEATURE_COLS = ["rsi_14", "macd", "macd_signal", "macd_hist",
                "bb_upper", "bb_middle", "bb_lower",
                "ema_9", "ema_21", "ema_50",
                "sma_20", "sma_50", "atr_14"]


def fetch_klines(symbol, days):
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    flux = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -{days}d)
      |> filter(fn: (r) => r._measurement == "klines" and r.symbol == "{symbol}")
      |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")
      |> sort(columns:["_time"])
    '''
    tables = client.query_api().query(flux)
    records = []
    for table in tables:
        for record in table.records:
            row = {"_time": record.get_time()}
            for key, val in record.values.items():
                if key.startswith("_") or key in ("result", "table", "symbol", "interval"):
                    continue
                row[key] = val
            records.append(row)
    client.close()
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records).set_index("_time").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def compute_features(df):
    close = df["close"].values.astype(np.float64)
    high = df["high"].values.astype(np.float64)
    low = df["low"].values.astype(np.float64)
    df = df.copy()
    df["rsi_14"] = talib.RSI(close, timeperiod=14)
    macd, macd_signal, macd_hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
    df["macd"] = macd
    df["macd_signal"] = macd_signal
    df["macd_hist"] = macd_hist
    bb_upper, bb_middle, bb_lower = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2)
    df["bb_upper"] = bb_upper
    df["bb_middle"] = bb_middle
    df["bb_lower"] = bb_lower
    df["ema_9"] = talib.EMA(close, timeperiod=9)
    df["ema_21"] = talib.EMA(close, timeperiod=21)
    df["ema_50"] = talib.EMA(close, timeperiod=50)
    df["sma_20"] = talib.SMA(close, timeperiod=20)
    df["sma_50"] = talib.SMA(close, timeperiod=50)
    df["atr_14"] = talib.ATR(high, low, close, timeperiod=14)
    return df


def add_regime_target(df, window=REGIME_WINDOW_MINUTES):
    """4-saatlik pencerede rejim sınıfı belirle.

    0 = RANGING (max-min ≤ ATR × 3)
    1 = TRENDING_UP (close artışı > ATR × 2)
    2 = TRENDING_DOWN (close düşüşü > ATR × 2)
    """
    df = df.copy()
    df["future_high"] = df["high"].rolling(window=window, min_periods=window).max().shift(-window)
    df["future_low"] = df["low"].rolling(window=window, min_periods=window).min().shift(-window)
    df["future_close"] = df["close"].shift(-window)
    atr_60 = talib.ATR(df["high"].values, df["low"].values, df["close"].values, timeperiod=60)
    df["atr_60"] = atr_60

    # Rejim sınıflandırma
    range_size = df["future_high"] - df["future_low"]
    change = df["future_close"] - df["close"]
    df["target"] = 0  # default RANGING
    # TRENDING_UP: close artışı > ATR×2 ve toplam değişiklik yarıdan fazla
    df.loc[(change > df["atr_60"] * TRENDING_THRESHOLD_ATR), "target"] = 1
    # TRENDING_DOWN: close düşüşü > ATR×2
    df.loc[(change < -df["atr_60"] * TRENDING_THRESHOLD_ATR), "target"] = 2
    # Override: range çok küçükse zorunlu RANGING
    df.loc[(range_size <= df["atr_60"] * RANGING_THRESHOLD_ATR), "target"] = 0

    # Geçici sütunları temizle
    df = df.drop(columns=["future_high", "future_low", "future_close", "atr_60"])
    return df


def train_regime_model(df_window):
    """Mini model eğit, döndür."""
    available_features = [c for c in FEATURE_COLS if c in df_window.columns]
    clean = df_window.dropna(subset=available_features + ["target"])
    if len(clean) < 200:
        return None, available_features
    X = clean[available_features].values
    y = clean["target"].values
    if len(np.unique(y)) < 2:
        return None, available_features
    model = HistGradientBoostingClassifier(
        max_iter=100, max_depth=5, learning_rate=0.1, random_state=42
    )
    model.fit(X, y)
    return model, available_features


def predict_regime(model, features, available_features):
    """Tek satır feature → rejim tahmin."""
    if model is None:
        return None
    X = features[available_features].values.reshape(1, -1)
    if np.any(np.isnan(X)):
        return None
    return int(model.predict(X)[0])


def compute_grid_levels(low_price, high_price, n_grids):
    ratio = (high_price / low_price) ** (1.0 / (n_grids - 1))
    return [low_price * (ratio ** i) for i in range(n_grids)]


def backtest_grid_with_ml(df, symbol):
    print(f"\n{'='*70}")
    print(f"GRID BOT + ML FILTER: {symbol}")
    print(f"{'='*70}")

    if len(df) < (RANGE_LOOKBACK_HOURS + ML_TRAIN_HOURS) * 60 + 100:
        print(f"  ✗ Yetersiz veri")
        return None

    # Feature ve target hesapla
    df = compute_features(df)
    df = add_regime_target(df)

    # State
    cash = INITIAL_CAPITAL
    inventory_qty = 0.0
    inventory_cost = 0.0
    total_fees_paid = 0.0
    trade_count = 0
    cycle_pnls = []
    equity_curve = []

    # Stats
    ml_predictions = {"ranging": 0, "trending_up": 0, "trending_down": 0, "no_pred": 0}
    grid_active_minutes = 0
    grid_paused_minutes = 0

    # İlk pencere
    lookback_minutes = RANGE_LOOKBACK_HOURS * 60
    rebalance_minutes = GRID_REBALANCE_HOURS * 60
    ml_train_minutes = ML_TRAIN_HOURS * 60
    ml_retrain_minutes = ML_RETRAIN_HOURS * 60

    start_idx = max(lookback_minutes, ml_train_minutes)
    initial_window = df.iloc[start_idx - lookback_minutes:start_idx]
    low = initial_window["low"].min()
    high = initial_window["high"].max()
    grid_levels = compute_grid_levels(low, high, N_GRIDS)
    last_grid_idx = None
    minutes_since_rebalance = 0
    minutes_since_retrain = 0

    # İlk model
    train_window = df.iloc[start_idx - ml_train_minutes:start_idx]
    model, ml_features = train_regime_model(train_window)

    current_regime = None  # mevcut tahmin

    for i in range(start_idx, len(df)):
        current = df.iloc[i]
        price = current["close"]
        high_i = current["high"]
        low_i = current["low"]

        # Saatte bir rejim tahmini
        if (i - start_idx) % 60 == 0:
            current_regime = predict_regime(model, current, ml_features)
            if current_regime == 0:
                ml_predictions["ranging"] += 1
            elif current_regime == 1:
                ml_predictions["trending_up"] += 1
            elif current_regime == 2:
                ml_predictions["trending_down"] += 1
            else:
                ml_predictions["no_pred"] += 1

        # ML Filter: sadece RANGING'de aktif
        grid_active = (current_regime == 0)

        if grid_active:
            grid_active_minutes += 1
            # Grid tetikleme kontrolü
            for grid_idx, grid_price in enumerate(grid_levels):
                if low_i <= grid_price <= high_i:
                    if last_grid_idx is None:
                        last_grid_idx = grid_idx
                        continue
                    if grid_idx == last_grid_idx:
                        continue
                    if grid_idx > last_grid_idx:
                        # SELL
                        if inventory_qty > 0:
                            qty_to_sell = min(inventory_qty, ORDER_SIZE_USDT / grid_price)
                            gross_proceeds = qty_to_sell * grid_price
                            fee = gross_proceeds * FEE_RATE
                            cash += gross_proceeds - fee
                            avg_cost = inventory_cost / inventory_qty
                            inventory_cost -= qty_to_sell * avg_cost
                            inventory_qty -= qty_to_sell
                            cycle_pnls.append(qty_to_sell * (grid_price - avg_cost) - fee)
                            total_fees_paid += fee
                            trade_count += 1
                    else:
                        # BUY
                        if cash >= ORDER_SIZE_USDT:
                            qty_bought = ORDER_SIZE_USDT / grid_price
                            fee = ORDER_SIZE_USDT * FEE_RATE
                            cash -= ORDER_SIZE_USDT + fee
                            inventory_qty += qty_bought
                            inventory_cost += ORDER_SIZE_USDT
                            total_fees_paid += fee
                            trade_count += 1
                    last_grid_idx = grid_idx
                    break
        else:
            grid_paused_minutes += 1

        # Range rebalance
        minutes_since_rebalance += 1
        if minutes_since_rebalance >= rebalance_minutes:
            window = df.iloc[max(0, i - lookback_minutes):i]
            new_low = window["low"].min()
            new_high = window["high"].max()
            grid_levels = compute_grid_levels(new_low, new_high, N_GRIDS)
            last_grid_idx = None
            minutes_since_rebalance = 0

        # ML retrain
        minutes_since_retrain += 1
        if minutes_since_retrain >= ml_retrain_minutes:
            train_window = df.iloc[max(0, i - ml_train_minutes):i]
            model, ml_features = train_regime_model(train_window)
            minutes_since_retrain = 0

        # Equity
        equity_curve.append(cash + inventory_qty * price)

    # Sonuçlar
    final_equity = cash + inventory_qty * df.iloc[-1]["close"]
    total_pnl_pct = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    equity_arr = np.array(equity_curve)
    if len(equity_arr) > 0:
        peak = np.maximum.accumulate(equity_arr)
        drawdown = (peak - equity_arr) / np.where(peak > 0, peak, 1) * 100
        max_drawdown = drawdown.max()
    else:
        max_drawdown = 0
    wins = [p for p in cycle_pnls if p > 0]
    losses = [p for p in cycle_pnls if p < 0]

    print(f"  PnL:                  ${final_equity - INITIAL_CAPITAL:+.2f} ({total_pnl_pct:+.2f}%)")
    print(f"  Max drawdown:         {max_drawdown:.2f}%")
    print(f"  Trade sayısı:         {trade_count}")
    print(f"  Cycle: {len(cycle_pnls)} (win={len(wins)}, loss={len(losses)})")
    if wins:
        print(f"    Win ort:  ${np.mean(wins):.4f}")
    if losses:
        print(f"    Loss ort: ${np.mean(losses):.4f}")
    print(f"  Fee toplam:           ${total_fees_paid:.2f}")

    # ML stats
    total_pred = sum(ml_predictions.values())
    if total_pred > 0:
        print(f"  ML Filter Tahminleri (saat bazında):")
        for regime, count in sorted(ml_predictions.items(), key=lambda x: -x[1]):
            pct = count / total_pred * 100
            print(f"    {regime:<15}: {count} ({pct:.1f}%)")
        active_pct = grid_active_minutes / (grid_active_minutes + grid_paused_minutes) * 100
        print(f"  Grid aktif: %{active_pct:.1f} | Pasif: %{100-active_pct:.1f}")

    return {
        "symbol": symbol,
        "pnl_pct": total_pnl_pct,
        "max_dd_pct": max_drawdown,
        "trades": trade_count,
        "cycles": len(cycle_pnls),
        "wins": len(wins),
        "losses": len(losses),
        "active_pct": grid_active_minutes / max(1, grid_active_minutes + grid_paused_minutes) * 100,
    }


def main():
    print("=" * 70)
    print("GRID BOT + ML FILTER — Faz B Adım 2 (REJIM TAHMİNLİ)")
    print("=" * 70)
    print(f"Semboller: {SYMBOLS}")
    print(f"N_GRIDS={N_GRIDS}, Order=${ORDER_SIZE_USDT}, Range_lb={RANGE_LOOKBACK_HOURS}h")
    print(f"ML: Train_lb={ML_TRAIN_HOURS}h, Retrain={ML_RETRAIN_HOURS}h, Window={REGIME_WINDOW_MINUTES}m")
    print()

    results = []
    for symbol in SYMBOLS:
        df = fetch_klines(symbol, TOTAL_DAYS)
        if df.empty:
            continue
        result = backtest_grid_with_ml(df, symbol)
        if result:
            results.append(result)

    # ÖZET
    print("\n" + "=" * 70)
    print("KARŞILAŞTIRMA: Filtresiz vs Filtreli Grid Bot")
    print("=" * 70)
    print(f"{'Symbol':<12} {'PnL%':>10} {'MaxDD%':>10} {'Trades':>8} {'Cycles':>8} {'GridActive':>12}")
    print("-" * 70)
    for r in results:
        print(f"{r['symbol']:<12} {r['pnl_pct']:>+9.2f}% {r['max_dd_pct']:>9.2f}% "
              f"{r['trades']:>8} {r['cycles']:>8} {r['active_pct']:>11.1f}%")
    print("-" * 70)
    if results:
        avg_pnl = np.mean([r["pnl_pct"] for r in results])
        avg_dd = np.mean([r["max_dd_pct"] for r in results])
        avg_active = np.mean([r["active_pct"] for r in results])
        print(f"{'ORT':<12} {avg_pnl:>+9.2f}% {avg_dd:>9.2f}%   active={avg_active:.1f}%")
        print()
        print("=" * 70)
        print("YORUM")
        print("=" * 70)
        print(f"Filtresiz tuned PnL:    -%1.48 (baseline)")
        print(f"Filtreli ML PnL:        {avg_pnl:+.2f}%")
        improvement = avg_pnl - (-1.48)
        print(f"İyileşme:               {improvement:+.2f} puan")
        if avg_pnl > 0:
            print(f"✅ ML filter pozitif sonuç verdi — paper trade adayı.")
        elif avg_pnl > -0.5:
            print(f"🟢 ML filter neredeyse başabaş yapıyor — tuning ile pozitife dönebilir.")
        elif improvement > 0:
            print(f"🟡 ML filter iyileştirdi ama henüz negatif — devam edilebilir.")
        else:
            print(f"⚠️  ML filter beklenen değeri vermedi — başka bir yaklaşım gerekli.")
    print("=" * 70)


if __name__ == "__main__":
    main()
