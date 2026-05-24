"""Backtest script: approved sinyallerin doğruluk oranını ölç.

Her sinyal için:
- LONG: 1m sonra fiyat ARTTI mı? → doğru
- SHORT: 1m sonra fiyat DÜŞTÜ mü? → doğru

Çıktı:
- Genel hit rate
- LONG vs SHORT breakdown
- Sembol bazında hit rate
- Confidence bucket'ları
- Saat bazında hit rate
- Farklı zaman ufukları (1m, 3m, 5m, 10m)
"""

import os
from datetime import datetime
from collections import defaultdict
from influxdb_client import InfluxDBClient

# Config (host makineden çalışacak, port-forward localhost:8086'dan ulaşılabilir)
INFLUX_URL = "http://influxdb:8086"
INFLUX_ORG = "gazifintech"
INFLUX_BUCKET = "macts_market_data"

# Token'ı env'den oku (container'a inject edilecek)
INFLUX_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
if not INFLUX_TOKEN:
    raise RuntimeError("INFLUXDB_TOKEN env var boş!")

HORIZONS_MINUTES = [1, 3, 5, 10]  # tahmin ufukları
LOOKBACK_HOURS = 24


def fetch_approved_signals(client):
    """Son 24h'lik approved sinyalleri çek."""
    query = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -{LOOKBACK_HOURS}h)
      |> filter(fn: (r) => r._measurement == "risk_decisions" and r.approved == "true")
      |> filter(fn: (r) => r._field == "confidence" or r._field == "prob_up")
      |> pivot(rowKey:["_time","symbol","direction"], columnKey:["_field"], valueColumn:"_value")
      |> sort(columns: ["_time"])
    '''
    
    signals = []
    for table in client.query_api().query(query):
        for record in table.records:
            signals.append({
                "time": record.get_time(),
                "symbol": record.values.get("symbol"),
                "direction": record.values.get("direction"),
                "confidence": record.values.get("confidence"),
                "prob_up": record.values.get("prob_up"),
            })
    return signals


def fetch_kline_prices(client, symbol, start_time, end_time):
    """Bir sembol için belirli zaman aralığındaki close fiyatlarını çek."""
    start_iso = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    query = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: {start_iso}, stop: {end_iso})
      |> filter(fn: (r) => r._measurement == "klines" and r.symbol == "{symbol}" and r._field == "close")
      |> sort(columns: ["_time"])
    '''
    
    prices = []
    for table in client.query_api().query(query):
        for record in table.records:
            prices.append((record.get_time(), record.get_value()))
    return prices


def find_price_at_or_after(prices, target_time, max_skew_seconds=120):
    """Verilen zamanda veya hemen sonrasında close fiyatını bul.
    max_skew: kabul edilebilir maksimum sapma."""
    for t, price in prices:
        delta = (t - target_time).total_seconds()
        if -10 <= delta <= max_skew_seconds:
            return price, delta
    return None, None


def evaluate_signal(signal, prices, horizon_minutes):
    """Bir sinyali değerlendir: doğru mu yanlış mı?
    Return: (correct: bool, entry_price, exit_price, pnl_pct) veya None"""
    from datetime import timedelta
    
    entry_time = signal["time"]
    exit_time = entry_time + timedelta(minutes=horizon_minutes)
    
    entry_price, _ = find_price_at_or_after(prices, entry_time, max_skew_seconds=70)
    exit_price, _ = find_price_at_or_after(prices, exit_time, max_skew_seconds=70)
    
    if entry_price is None or exit_price is None:
        return None
    
    pnl_pct = (exit_price - entry_price) / entry_price * 100
    
    if signal["direction"] == "LONG":
        correct = exit_price > entry_price
        signed_pnl = pnl_pct  # LONG kazancı = fiyat artışı
    else:  # SHORT
        correct = exit_price < entry_price
        signed_pnl = -pnl_pct  # SHORT kazancı = fiyat düşüşü
    
    return correct, entry_price, exit_price, signed_pnl


def main():
    print("=" * 80)
    print(f"BACKTEST — Son {LOOKBACK_HOURS} saat | Generated: {datetime.utcnow()}")
    print("=" * 80)
    
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    
    print("\n[1/3] Approved sinyaller çekiliyor...")
    signals = fetch_approved_signals(client)
    print(f"  → {len(signals)} sinyal bulundu")
    
    # Sembol bazında grupla
    by_symbol = defaultdict(list)
    for s in signals:
        by_symbol[s["symbol"]].append(s)
    
    print(f"  → {len(by_symbol)} sembol")
    
    print("\n[2/3] Her sembol için kline fiyatları çekiliyor...")
    symbol_prices = {}
    for symbol in by_symbol.keys():
        sym_signals = by_symbol[symbol]
        min_time = min(s["time"] for s in sym_signals)
        max_time = max(s["time"] for s in sym_signals)
        # Buffer ekle: en geç sinyale 15 dakika ekle (10m horizon + 5m buffer)
        from datetime import timedelta
        prices = fetch_kline_prices(client, symbol, min_time - timedelta(minutes=2), max_time + timedelta(minutes=15))
        symbol_prices[symbol] = prices
        print(f"  {symbol}: {len(prices)} kline")
    
    print("\n[3/3] Her sinyal için her horizon'da değerlendirme...")
    
    # Sonuçlar
    # results[horizon][symbol] = list of (correct, pnl_pct)
    results = {h: defaultdict(list) for h in HORIZONS_MINUTES}
    # results_by_direction[horizon][direction] = list of (correct, pnl_pct)
    results_by_direction = {h: defaultdict(list) for h in HORIZONS_MINUTES}
    # results_by_confidence[horizon][bucket] = list of (correct, pnl_pct)
    results_by_confidence = {h: defaultdict(list) for h in HORIZONS_MINUTES}
    
    skipped = 0
    for symbol, sym_signals in by_symbol.items():
        prices = symbol_prices[symbol]
        for signal in sym_signals:
            for h in HORIZONS_MINUTES:
                res = evaluate_signal(signal, prices, h)
                if res is None:
                    if h == 1:  # sadece bir kez say
                        skipped += 1
                    continue
                correct, entry, exit_, pnl = res
                results[h][symbol].append((correct, pnl))
                results_by_direction[h][signal["direction"]].append((correct, pnl))
                # Confidence bucket
                conf = signal["confidence"]
                if conf < 0.6:
                    bucket = "0.5-0.6"
                elif conf < 0.7:
                    bucket = "0.6-0.7"
                elif conf < 0.8:
                    bucket = "0.7-0.8"
                elif conf < 0.9:
                    bucket = "0.8-0.9"
                else:
                    bucket = "0.9-1.0"
                results_by_confidence[h][bucket].append((correct, pnl))
    
    print(f"  → {skipped} sinyal değerlendirilemedi (eşleşen kline yok)")
    
    # === GENEL HIT RATE ===
    print("\n" + "=" * 80)
    print("GENEL HIT RATE (her horizon için)")
    print("=" * 80)
    print(f"{'Horizon':<10} {'Toplam':<10} {'Doğru':<10} {'Hit Rate':<12} {'Ort. PnL %':<12}")
    print("-" * 60)
    for h in HORIZONS_MINUTES:
        all_results = [r for sym_results in results[h].values() for r in sym_results]
        total = len(all_results)
        correct = sum(1 for c, _ in all_results if c)
        avg_pnl = sum(p for _, p in all_results) / total if total else 0
        hit_rate = correct / total * 100 if total else 0
        marker = "🟢" if hit_rate > 55 else ("🟡" if hit_rate > 50 else "🔴")
        print(f"{h:<2}m       {total:<10} {correct:<10} {hit_rate:>6.2f}%  {marker} {avg_pnl:>+7.4f}%")
    
    # === LONG vs SHORT ===
    print("\n" + "=" * 80)
    print("LONG vs SHORT HIT RATE (1m horizon)")
    print("=" * 80)
    print(f"{'Yön':<10} {'Toplam':<10} {'Doğru':<10} {'Hit Rate':<12} {'Ort. PnL %':<12}")
    print("-" * 60)
    for direction in ["LONG", "SHORT"]:
        rs = results_by_direction[1].get(direction, [])
        total = len(rs)
        correct = sum(1 for c, _ in rs if c)
        avg_pnl = sum(p for _, p in rs) / total if total else 0
        hit_rate = correct / total * 100 if total else 0
        marker = "🟢" if hit_rate > 55 else ("🟡" if hit_rate > 50 else "🔴")
        print(f"{direction:<10} {total:<10} {correct:<10} {hit_rate:>6.2f}%  {marker} {avg_pnl:>+7.4f}%")
    
    # === CONFIDENCE BUCKETS ===
    print("\n" + "=" * 80)
    print("CONFIDENCE BUCKET HIT RATE (1m horizon) — yüksek confidence daha mı iyi?")
    print("=" * 80)
    print(f"{'Bucket':<12} {'Toplam':<10} {'Doğru':<10} {'Hit Rate':<12} {'Ort. PnL %':<12}")
    print("-" * 60)
    for bucket in ["0.5-0.6", "0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0"]:
        rs = results_by_confidence[1].get(bucket, [])
        total = len(rs)
        correct = sum(1 for c, _ in rs if c)
        avg_pnl = sum(p for _, p in rs) / total if total else 0
        hit_rate = correct / total * 100 if total else 0
        marker = "🟢" if hit_rate > 55 else ("🟡" if hit_rate > 50 else "🔴")
        print(f"{bucket:<12} {total:<10} {correct:<10} {hit_rate:>6.2f}%  {marker} {avg_pnl:>+7.4f}%")
    
    # === SEMBOL BAZINDA (1m horizon) ===
    print("\n" + "=" * 80)
    print("SEMBOL BAZINDA HIT RATE (1m horizon) — sıralı (en iyi → en kötü)")
    print("=" * 80)
    print(f"{'Sembol':<14} {'Toplam':<10} {'Doğru':<10} {'Hit Rate':<12} {'Ort. PnL %':<12}")
    print("-" * 60)
    
    symbol_stats = []
    for symbol, rs in results[1].items():
        total = len(rs)
        if total < 3:
            continue
        correct = sum(1 for c, _ in rs if c)
        avg_pnl = sum(p for _, p in rs) / total
        hit_rate = correct / total * 100
        symbol_stats.append((symbol, total, correct, hit_rate, avg_pnl))
    
    symbol_stats.sort(key=lambda x: -x[3])
    
    for symbol, total, correct, hit_rate, avg_pnl in symbol_stats:
        marker = "🟢" if hit_rate > 55 else ("🟡" if hit_rate > 50 else "🔴")
        print(f"{symbol:<14} {total:<10} {correct:<10} {hit_rate:>6.2f}%  {marker} {avg_pnl:>+7.4f}%")
    
    # === KÜMÜLATIF PNL ===
    print("\n" + "=" * 80)
    print("KÜMÜLATIF PnL (1m horizon, her sinyal full pozisyon)")
    print("=" * 80)
    all_pnls = [p for sym_results in results[1].values() for _, p in sym_results]
    cum_pnl = sum(all_pnls)
    n = len(all_pnls)
    avg = cum_pnl / n if n else 0
    wins = [p for p in all_pnls if p > 0]
    losses = [p for p in all_pnls if p < 0]
    win_rate = len(wins) / n * 100 if n else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    profit_factor = sum(wins) / abs(sum(losses)) if losses and sum(losses) < 0 else float('inf')
    
    print(f"Toplam sinyal:    {n}")
    print(f"Kazanan:          {len(wins)} ({win_rate:.1f}%)")
    print(f"Kaybeden:         {len(losses)}")
    print(f"Kümülatif PnL:    {cum_pnl:+.4f}%")
    print(f"Ortalama PnL:     {avg:+.4f}%")
    print(f"Ort. kazanç:      {avg_win:+.4f}%")
    print(f"Ort. kayıp:       {avg_loss:+.4f}%")
    print(f"Profit factor:    {profit_factor:.2f}")
    print(f"  (1.0 = breakeven, >1.5 = iyi, >2.0 = çok iyi)")
    
    client.close()
    print("\n" + "=" * 80)
    print("Bitti.")
    print("=" * 80)


if __name__ == "__main__":
    main()
