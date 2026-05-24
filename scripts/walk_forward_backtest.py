"""Walk-Forward Backtest — Gerçek Baseline Ölçümü."""
import os
import sys
import time
import numpy as np
import pandas as pd
import talib
from datetime import datetime, timedelta
from influxdb_client import InfluxDBClient
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score

INFLUX_URL = "http://influxdb:8086"
INFLUX_ORG = "gazifintech"
INFLUX_BUCKET = "macts_market_data"
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
if not INFLUX_TOKEN:
    raise RuntimeError("INFLUXDB_TOKEN yok!")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
TOTAL_DAYS = 7
TRAIN_DAYS = 4
TEST_DAYS = 1
N_WINDOWS = 3
CONFIDENCE_THRESHOLD = 0.55
FEE_RATE = 0.0004
HOLD_PERIOD_MINUTES = 1
MIN_TRAIN_SAMPLES = 500
MODEL_PARAMS = {"max_iter": 100, "max_depth": 5, "learning_rate": 0.1, "random_state": 42}
FEATURE_COLS = ["rsi_14", "macd", "macd_signal", "macd_hist", "bb_upper", "bb_middle",
                "bb_lower", "ema_9", "ema_21", "ema_50", "sma_20", "sma_50", "atr_14"]


def fetch_klines(symbol, days=7):
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


def add_target(df):
    df = df.copy()
    df["future_close"] = df["close"].shift(-1)
    df["target"] = (df["future_close"] > df["close"]).astype(int)
    return df


def generate_windows(df, train_days, test_days, n_windows):
    if len(df) == 0:
        return []
    end_time = df.index[-1]
    start_time = df.index[0]
    total_span = end_time - start_time
    train_delta = timedelta(days=train_days)
    test_delta = timedelta(days=test_days)
    needed = train_delta + test_delta
    if total_span < needed:
        usable_days = total_span.total_seconds() / 86400
        train_days = max(1.5, usable_days - 1)
        test_days = max(0.5, usable_days - train_days)
        train_delta = timedelta(days=train_days)
        test_delta = timedelta(days=test_days)
    windows = []
    cursor = end_time
    for i in range(n_windows):
        test_end = cursor
        test_start = test_end - test_delta
        train_end = test_start
        train_start = train_end - train_delta
        if train_start < start_time:
            break
        windows.append({"window_id": n_windows - i, "train_start": train_start,
                        "train_end": train_end, "test_start": test_start, "test_end": test_end})
        cursor = cursor - timedelta(days=1)
    windows.reverse()
    for idx, w in enumerate(windows):
        w["window_id"] = idx + 1
    return windows


def run_window(df, window):
    train_df = df.loc[window["train_start"]:window["train_end"]].copy()
    test_df = df.loc[window["test_start"]:window["test_end"]].copy()
    available_features = [c for c in FEATURE_COLS if c in train_df.columns]
    train_clean = train_df.dropna(subset=available_features + ["target"])
    test_clean = test_df.dropna(subset=available_features + ["target"])
    n_train = len(train_clean)
    n_test = len(test_clean)
    if n_train < MIN_TRAIN_SAMPLES or n_test < 50:
        return {"window_id": window["window_id"], "status": "insufficient_data",
                "n_train": n_train, "n_test": n_test}
    X_train = train_clean[available_features].values
    y_train = train_clean["target"].values
    if len(np.unique(y_train)) < 2:
        return {"window_id": window["window_id"], "status": "single_class",
                "n_train": n_train, "n_test": n_test}
    model = HistGradientBoostingClassifier(**MODEL_PARAMS)
    model.fit(X_train, y_train)
    X_test = test_clean[available_features].values
    y_test = test_clean["target"].values
    proba = model.predict_proba(X_test)[:, 1]
    pred = (proba > 0.5).astype(int)
    test_accuracy = accuracy_score(y_test, pred)
    confidence = np.abs(proba - 0.5) * 2
    signals_mask = confidence >= (CONFIDENCE_THRESHOLD - 0.5) * 2
    n_signals = int(signals_mask.sum())
    if n_signals == 0:
        return {"window_id": window["window_id"], "status": "no_signals",
                "n_train": n_train, "n_test": n_test, "test_accuracy": test_accuracy}
    test_close = test_clean["close"].values
    signal_pnls = []
    signal_hits = []
    for idx in np.where(signals_mask)[0]:
        if idx + HOLD_PERIOD_MINUTES >= len(test_close):
            continue
        entry_price = test_close[idx]
        exit_price = test_close[idx + HOLD_PERIOD_MINUTES]
        direction = 1 if pred[idx] == 1 else -1
        gross_return = direction * (exit_price - entry_price) / entry_price
        net_return = gross_return - 2 * FEE_RATE
        signal_pnls.append(net_return)
        actual_direction = 1 if exit_price > entry_price else 0
        hit = 1 if pred[idx] == actual_direction else 0
        signal_hits.append(hit)
    if not signal_pnls:
        return {"window_id": window["window_id"], "status": "no_executable_signals",
                "n_train": n_train, "n_test": n_test}
    signal_pnls = np.array(signal_pnls)
    signal_hits = np.array(signal_hits)
    hit_rate = signal_hits.mean()
    avg_return = signal_pnls.mean()
    total_return = signal_pnls.sum()
    wins = signal_pnls[signal_pnls > 0]
    losses = signal_pnls[signal_pnls < 0]
    profit_factor = wins.sum() / abs(losses.sum()) if losses.sum() < 0 else float("inf")
    return {"window_id": window["window_id"], "status": "ok",
            "train_start": window["train_start"], "train_end": window["train_end"],
            "test_start": window["test_start"], "test_end": window["test_end"],
            "n_train": n_train, "n_test": n_test, "test_accuracy": float(test_accuracy),
            "n_signals": n_signals, "hit_rate": float(hit_rate),
            "avg_return_pct": float(avg_return * 100), "total_return_pct": float(total_return * 100),
            "profit_factor": float(profit_factor), "n_wins": int(len(wins)), "n_losses": int(len(losses))}


def main():
    print("=" * 70)
    print("WALK-FORWARD BACKTEST — Gerçek Baseline Ölçümü")
    print("=" * 70)
    print(f"Semboller: {SYMBOLS}")
    print(f"Toplam veri: {TOTAL_DAYS} gün, Pencere: train={TRAIN_DAYS}g, test={TEST_DAYS}g, N={N_WINDOWS}")
    print(f"Fee: {FEE_RATE*100}%, Confidence threshold: {CONFIDENCE_THRESHOLD}, Hold: {HOLD_PERIOD_MINUTES}m")
    print()
    all_results = {}
    for symbol in SYMBOLS:
        print(f"\n{'='*70}")
        print(f"SEMBOL: {symbol}")
        print(f"{'='*70}")
        t0 = time.time()
        df = fetch_klines(symbol, days=TOTAL_DAYS)
        if df.empty:
            print(f"  ✗ Veri yok")
            continue
        df = compute_features(df)
        df = add_target(df)
        print(f"  ✓ {len(df)} kline, {time.time()-t0:.1f}s")
        windows = generate_windows(df, TRAIN_DAYS, TEST_DAYS, N_WINDOWS)
        print(f"  ✓ {len(windows)} pencere oluşturuldu")
        window_results = []
        for w in windows:
            t1 = time.time()
            result = run_window(df, w)
            window_results.append(result)
            if result["status"] == "ok":
                print(f"    Pencere {result['window_id']}: accuracy={result['test_accuracy']*100:.1f}%, sinyal={result['n_signals']}, hit={result['hit_rate']*100:.1f}%, PnL_net={result['total_return_pct']:.2f}%, PF={result['profit_factor']:.2f} ({time.time()-t1:.1f}s)")
            else:
                print(f"    Pencere {result['window_id']}: {result['status']}")
        all_results[symbol] = window_results

    print()
    print("=" * 70)
    print("ÖZET — SEMBOL BAZINDA ORTALAMA")
    print("=" * 70)
    print(f"{'Symbol':<12} {'Avg Acc':>8} {'Avg Hit':>8} {'Avg PnL':>10} {'Avg PF':>8} {'Sig/W':>8}")
    print("-" * 70)
    overall_hits = []
    overall_pnls = []
    overall_pfs = []
    for symbol, results in all_results.items():
        ok = [r for r in results if r["status"] == "ok"]
        if not ok:
            print(f"{symbol:<12}  (no successful windows)")
            continue
        avg_acc = np.mean([r["test_accuracy"] for r in ok]) * 100
        avg_hit = np.mean([r["hit_rate"] for r in ok]) * 100
        avg_pnl = np.mean([r["total_return_pct"] for r in ok])
        pfs = [r["profit_factor"] for r in ok if r["profit_factor"] != float("inf")]
        avg_pf = np.mean(pfs) if pfs else float("inf")
        avg_sigs = np.mean([r["n_signals"] for r in ok])
        print(f"{symbol:<12} {avg_acc:>7.2f}% {avg_hit:>7.2f}% {avg_pnl:>+9.2f}% {avg_pf:>8.2f} {avg_sigs:>7.0f}")
        overall_hits.extend([r["hit_rate"] for r in ok])
        overall_pnls.extend([r["total_return_pct"] for r in ok])
        overall_pfs.extend([r["profit_factor"] for r in ok if r["profit_factor"] != float("inf")])

    print("-" * 70)
    if overall_hits:
        print(f"\nToplam başarılı pencere:  {len(overall_hits)}")
        print(f"Ortalama hit rate:         {np.mean(overall_hits)*100:.2f}%")
        print(f"Median hit rate:           {np.median(overall_hits)*100:.2f}%")
        print(f"Ortalama PnL (fee dahil):  {np.mean(overall_pnls):+.2f}%")
        print(f"Median PnL:                {np.median(overall_pnls):+.2f}%")
        print(f"Ortalama profit factor:    {np.mean(overall_pfs):.2f}")
        print()
        mean_hit = np.mean(overall_hits) * 100
        mean_pnl = np.mean(overall_pnls)
        mean_pf = np.mean(overall_pfs)
        print("=" * 70)
        print("YORUM")
        print("=" * 70)
        if mean_hit < 48:
            print(f"⚠️  Hit rate {mean_hit:.1f}% — random'dan kötü. Model gürültüden ibaret.")
        elif mean_hit < 52:
            print(f"⚠️  Hit rate {mean_hit:.1f}% — sınırda, bias suspect.")
        elif mean_hit < 55:
            print(f"🟡 Hit rate {mean_hit:.1f}% — endüstri baseline (52-54%) içinde.")
        else:
            print(f"✅ Hit rate {mean_hit:.1f}% — anlamlı edge sinyali.")
        if mean_pnl < -0.5:
            print(f"⚠️  PnL {mean_pnl:+.2f}% — fee dahil ciddi kayıp.")
        elif mean_pnl < 0.5:
            print(f"🟡 PnL {mean_pnl:+.2f}% — fee'den sonra başabaş veya düşük.")
        else:
            print(f"✅ PnL {mean_pnl:+.2f}% — fee dahil pozitif.")
        if mean_pf < 1.0:
            print(f"⚠️  Profit factor {mean_pf:.2f} — kayıp eden sistem.")
        elif mean_pf < 1.3:
            print(f"🟡 Profit factor {mean_pf:.2f} — marjinal.")
        else:
            print(f"✅ Profit factor {mean_pf:.2f} — sağlıklı.")
    print("\n" + "=" * 70)
    print("Walk-forward tamamlandı.")
    print("=" * 70)


if __name__ == "__main__":
    main()
