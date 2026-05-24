"""Grid Bot Backtest — Faz B Adım 1.

Mantık:
  - Range'i son N saatin min-max'ından belirle
  - N grid çiz, her grid'e ters yönlü limit emir koy
  - Fiyat grid'i kestikçe emir tetiklenir
  - Cycle başına kar = grid_spacing - 2*fee
  - TRENDING piyasada: range dışına çıkar, kayıp
  - RANGING piyasada: cycle'lar para basar

Bu oturum hedefi: 9 günlük veriyle grid bot karlı mı, kayıp mı görmek.
"""
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from influxdb_client import InfluxDBClient

# === KONFİG ===
INFLUX_URL = "http://influxdb:8086"
INFLUX_ORG = "gazifintech"
INFLUX_BUCKET = "macts_market_data"
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
if not INFLUX_TOKEN:
    raise RuntimeError("INFLUXDB_TOKEN yok!")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
TOTAL_DAYS = 9
INITIAL_CAPITAL = 1000.0
N_GRIDS = 20
RANGE_LOOKBACK_HOURS = 24
GRID_REBALANCE_HOURS = 6
FEE_RATE = 0.0004
ORDER_SIZE_USDT = 50.0  # Her tetiklemede yatırılan büyüklük


def fetch_klines(symbol, days):
    """InfluxDB'den OHLC verisi çek."""
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


def compute_grid_levels(low_price, high_price, n_grids):
    """N grid çizgisi hesapla — geometrik aralık (yüzdesel eşit)."""
    # Geometric (multiplicative) grid — crypto için daha uygun
    ratio = (high_price / low_price) ** (1.0 / (n_grids - 1))
    levels = [low_price * (ratio ** i) for i in range(n_grids)]
    return levels


def detect_regime(df_window):
    """Verilen pencerede piyasa rejimi: ranging, trending_up, trending_down."""
    if len(df_window) < 10:
        return "unknown"
    first_close = df_window["close"].iloc[0]
    last_close = df_window["close"].iloc[-1]
    max_high = df_window["high"].max()
    min_low = df_window["low"].min()
    total_change_pct = (last_close - first_close) / first_close * 100
    range_pct = (max_high - min_low) / min_low * 100

    # Trending: net hareket toplam range'in %60+'ı
    if range_pct == 0:
        return "ranging"
    trend_strength = abs(total_change_pct) / range_pct
    if trend_strength > 0.6:
        return "trending_up" if total_change_pct > 0 else "trending_down"
    return "ranging"


def backtest_grid(df, symbol):
    """Tek sembol için grid bot simülasyonu."""
    print(f"\n{'='*70}")
    print(f"GRID BOT BACKTEST: {symbol}")
    print(f"{'='*70}")

    if len(df) < RANGE_LOOKBACK_HOURS * 60 + 100:
        print(f"  ✗ Yetersiz veri ({len(df)} dakika)")
        return None

    # İlk N saat: range belirleme için baseline
    lookback_minutes = RANGE_LOOKBACK_HOURS * 60
    rebalance_minutes = GRID_REBALANCE_HOURS * 60

    # State
    cash = INITIAL_CAPITAL
    inventory_qty = 0.0   # Kaç adet BTC tutuyoruz
    inventory_cost = 0.0  # Toplam alış maliyeti (USDT)
    total_fees_paid = 0.0
    trade_count = 0
    cycle_pnls = []       # Her tamamlanmış cycle'ın karı
    equity_curve = []     # Her dakika toplam değer

    # İlk grid hesapla
    initial_window = df.iloc[:lookback_minutes]
    low = initial_window["low"].min()
    high = initial_window["high"].max()
    grid_levels = compute_grid_levels(low, high, N_GRIDS)
    print(f"  İlk range: {low:.2f} - {high:.2f} ({(high/low-1)*100:.2f}%)")
    print(f"  Grid spacing: ~{((high/low)**(1/(N_GRIDS-1))-1)*100:.2f}%/grid")

    # Bir önceki tetiklenen grid (cycle tespit için)
    last_grid_idx = None

    # Backtest döngüsü (lookback'ten sonra başlat)
    start_idx = lookback_minutes
    minutes_since_rebalance = 0

    # Rejim takibi (her saat)
    regime_history = {"ranging": 0, "trending_up": 0, "trending_down": 0, "unknown": 0}

    for i in range(start_idx, len(df)):
        current = df.iloc[i]
        price = current["close"]
        high_i = current["high"]
        low_i = current["low"]

        # Rejim her saat tespit edilsin
        if (i - start_idx) % 60 == 0:
            window = df.iloc[max(0, i - lookback_minutes):i]
            regime = detect_regime(window)
            regime_history[regime] = regime_history.get(regime, 0) + 1

        # Grid tetikleme kontrolü — bu mumda fiyat hangi grid'leri geçti?
        # 1 dakikalık mumda high ve low arasındaki tüm grid'ler tetiklenmiş sayılır
        for grid_idx, grid_price in enumerate(grid_levels):
            if low_i <= grid_price <= high_i:
                # Bu grid bu mumda kesildi
                if last_grid_idx is None:
                    last_grid_idx = grid_idx
                    continue

                if grid_idx == last_grid_idx:
                    continue  # Aynı grid, hareket yok

                # Hangi yön?
                if grid_idx > last_grid_idx:
                    # Fiyat yukarı çıktı → SELL tetiklenir (eğer inventory varsa)
                    if inventory_qty > 0:
                        qty_to_sell = min(inventory_qty, ORDER_SIZE_USDT / grid_price)
                        gross_proceeds = qty_to_sell * grid_price
                        fee = gross_proceeds * FEE_RATE
                        cash += gross_proceeds - fee
                        # Cost basis FIFO basit: ortalama maliyet
                        if inventory_qty > 0:
                            avg_cost = inventory_cost / inventory_qty
                            inventory_cost -= qty_to_sell * avg_cost
                            inventory_qty -= qty_to_sell
                            cycle_pnl = qty_to_sell * (grid_price - avg_cost) - fee
                            cycle_pnls.append(cycle_pnl)
                        total_fees_paid += fee
                        trade_count += 1
                else:
                    # Fiyat aşağı indi → BUY tetiklenir
                    cost = ORDER_SIZE_USDT
                    if cash >= cost:
                        qty_bought = cost / grid_price
                        fee = cost * FEE_RATE
                        cash -= cost + fee
                        inventory_qty += qty_bought
                        inventory_cost += cost
                        total_fees_paid += fee
                        trade_count += 1

                last_grid_idx = grid_idx
                break  # Bir mumda tek tetikleme

        # Rebalance: range'i yeniden hesapla
        minutes_since_rebalance += 1
        if minutes_since_rebalance >= rebalance_minutes:
            window = df.iloc[max(0, i - lookback_minutes):i]
            new_low = window["low"].min()
            new_high = window["high"].max()
            grid_levels = compute_grid_levels(new_low, new_high, N_GRIDS)
            last_grid_idx = None  # Yeni grid, eski referans iptal
            minutes_since_rebalance = 0

        # Equity hesapla
        equity = cash + inventory_qty * price
        equity_curve.append(equity)

    # === Sonuçlar ===
    final_equity = cash + inventory_qty * df.iloc[-1]["close"]
    total_pnl = final_equity - INITIAL_CAPITAL
    total_pnl_pct = total_pnl / INITIAL_CAPITAL * 100

    # Drawdown
    equity_arr = np.array(equity_curve)
    peak = np.maximum.accumulate(equity_arr)
    drawdown = (peak - equity_arr) / peak * 100
    max_drawdown = drawdown.max()

    # Cycle stats
    winning_cycles = [p for p in cycle_pnls if p > 0]
    losing_cycles = [p for p in cycle_pnls if p < 0]

    print(f"  Başlangıç sermayesi:     ${INITIAL_CAPITAL:.2f}")
    print(f"  Son sermaye:             ${final_equity:.2f}")
    print(f"  Toplam PnL:              ${total_pnl:+.2f} ({total_pnl_pct:+.2f}%)")
    print(f"  Max drawdown:            {max_drawdown:.2f}%")
    print(f"  Toplam trade sayısı:     {trade_count}")
    print(f"  Toplam fee:              ${total_fees_paid:.2f}")
    print(f"  Tamamlanmış cycle:       {len(cycle_pnls)}")
    if cycle_pnls:
        print(f"    Kazanan cycle:         {len(winning_cycles)} (ort. ${np.mean(winning_cycles):.4f} ise)")
        print(f"    Kaybeden cycle:        {len(losing_cycles)} (ort. ${np.mean(losing_cycles):.4f} ise)")

    print(f"  Final inventory:         {inventory_qty:.6f} {symbol.replace('USDT','')}")

    # Rejim dağılımı
    total_regime = sum(regime_history.values())
    if total_regime > 0:
        print(f"  Rejim dağılımı (saat):")
        for r, c in sorted(regime_history.items(), key=lambda x: -x[1]):
            print(f"    {r:<15}: {c} saat ({c/total_regime*100:.1f}%)")

    return {
        "symbol": symbol,
        "initial": INITIAL_CAPITAL,
        "final": final_equity,
        "pnl_pct": total_pnl_pct,
        "max_dd_pct": max_drawdown,
        "trades": trade_count,
        "cycles": len(cycle_pnls),
        "fees": total_fees_paid,
        "regime_dist": regime_history,
    }


def main():
    print("=" * 70)
    print("GRID BOT BACKTEST — Faz B Adım 1 (ML Filter YOK)")
    print("=" * 70)
    print(f"Semboller: {SYMBOLS}")
    print(f"Veri: {TOTAL_DAYS} gün")
    print(f"Sermaye: ${INITIAL_CAPITAL}, N_GRIDS={N_GRIDS}, Order size: ${ORDER_SIZE_USDT}")
    print(f"Range lookback: {RANGE_LOOKBACK_HOURS}h, Rebalance: {GRID_REBALANCE_HOURS}h")
    print(f"Fee: {FEE_RATE*100}%")

    results = []
    for symbol in SYMBOLS:
        df = fetch_klines(symbol, TOTAL_DAYS)
        if df.empty:
            print(f"\n{symbol}: ✗ veri yok")
            continue
        print(f"\n{symbol}: {len(df)} kline çekildi")
        result = backtest_grid(df, symbol)
        if result:
            results.append(result)

    # ÖZET TABLO
    print("\n" + "=" * 70)
    print("ÖZET")
    print("=" * 70)
    print(f"{'Symbol':<12} {'PnL%':>10} {'MaxDD%':>10} {'Trades':>8} {'Cycles':>8} {'Fees':>8}")
    print("-" * 70)
    for r in results:
        print(f"{r['symbol']:<12} {r['pnl_pct']:>+9.2f}% {r['max_dd_pct']:>9.2f}% "
              f"{r['trades']:>8} {r['cycles']:>8} ${r['fees']:>6.2f}")
    print("-" * 70)
    if results:
        avg_pnl = np.mean([r["pnl_pct"] for r in results])
        avg_dd = np.mean([r["max_dd_pct"] for r in results])
        print(f"{'ORTALAMA':<12} {avg_pnl:>+9.2f}% {avg_dd:>9.2f}%")
        print()
        print("=" * 70)
        print("YORUM")
        print("=" * 70)
        if avg_pnl > 2:
            print(f"✅ Grid Bot {TOTAL_DAYS} günde +{avg_pnl:.2f}% — RANGING piyasaymış, ML filter şart olmayabilir.")
        elif avg_pnl > 0:
            print(f"🟡 Grid Bot {TOTAL_DAYS} günde +{avg_pnl:.2f}% — Marjinal kar.")
            print(f"   ML filter ekleyince anlamlı olur mu görmek lazım.")
        elif avg_pnl > -2:
            print(f"🟠 Grid Bot {TOTAL_DAYS} günde {avg_pnl:.2f}% — Başabaş civarı.")
            print(f"   Bu dönem MIXED piyasa — ML filter trending'i bloklayabilir.")
        else:
            print(f"⚠️  Grid Bot {TOTAL_DAYS} günde {avg_pnl:.2f}% — Trending market, grid çalışmamış.")
            print(f"   ML filter yardımcı olabilir ama temel parametreler tekrar gözden geçirilmeli.")
    print("=" * 70)


if __name__ == "__main__":
    main()
