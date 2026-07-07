# MACTS — Mevcut Durum

> Bu dosya **canlı sistemin** mevcut durumunu yansıtır. Vision veya plan değildir.
> Son güncelleme: **12 Mayıs 2026** (sabah erken saat)

---

## Tek Satır Özet

**Faz 2 (Feature Engineering + Per-Coin Learning) tamamlandı.** Sistem 2+ gündür kesintisiz çalışıyor, 20 sembol için canlı veri akıyor, dakikada 14 teknik indikatör hesaplanıyor, her sembol için ayrı ML modeli eğitildi ve MLflow'da kayıt altına alındı.

---

## Sayılarla Mevcut Durum

| Metrik | Değer |
|---|---|
| Toplam servis (container) | 20 |
| Aktif agent sayısı | 4 (çalışıyor) + 7 (iskelet) |
| Tarayıcılan sembol | 528 perpetual (Binance Futures) |
| Aktif universe | Top 20 (24h hacme göre) |
| Veri toplama frekansı | 5 saniye/sembol (REST polling) |
| Feature hesaplama | dakikada 1 (closed kline başına) |
| Indikatör sayısı | 14 (RSI, MACD×3, BB×3, EMA×3, SMA×2, ATR) |
| InfluxDB kayıt sayısı | ~252,000 feature, ~10,000 kline (24h) |
| Eğitilen ML modeli | 20 (HistGradientBoostingClassifier) |
| MLflow experiment | per_coin_learning_universe_v1 (20 run) |
| Ortalama model accuracy | %86.7 (median %86.5) |
| En iyi model | SOLUSDT (%91.5 accuracy) |
| En düşük model | ONDOUSDT (%81.1 accuracy) |
| Disk kullanımı | 45GB / 99GB (%48) |
| Sistem uptime | 2 gün, sıfır crash |

---

## Çalışan Bileşenler

### Altyapı (Faz 0) ✅
- Docker Compose (20 container)
- Redis 7 — mesaj kuyruğu, healthy
- Kafka + Zookeeper — yedek mesaj kuyruğu, healthy
- PostgreSQL 16 — relasyonel veri + MLflow backend, healthy
- InfluxDB 2.7 — time-series, healthy
- MinIO — S3-compatible storage, healthy
- MLflow 3.0 — ML tracking, çalışıyor
- Prometheus — metrik toplama, çalışıyor
- Grafana 10.2 — dashboard, çalışıyor

### Veri Pipeline (Faz 1) ✅
- **Market Scanner**: 528 perpetual'ı 5 dakikada bir tarar, top 20'yi yayınlar
- **Data Collection**: 20 sembol için 5 saniyede bir REST polling, Redis + InfluxDB'ye yazar
- Universe dinamik (Market Scanner snapshot'ından)
- Sıfır yazma hatası

### Feature Pipeline (Faz 2 - Adım 1) ✅
- **Feature Engineering**: Redis'ten kline okur, per-symbol ring buffer (200 bar)
- TA-Lib 0.6.8 ile 14 indikatör hesaplar
- Redis stream'e + InfluxDB'ye yazar
- Backfill mekanizması (Redis stream'den 200 kline okur, Binance rate limit'i bypass eder)

### ML Pipeline (Faz 2 - Adım 2) ✅
- **Per-Coin Learning**: 20 sembol için batch training
- HistGradientBoostingClassifier (sklearn, LightGBM benzeri)
- Target: 1 dakika sonra fiyat artıyor mu? (binary)
- Train/test split: %80/%20, zaman serisi sırasını koruyarak
- MLflow tracking: parametreler + metrikler + model artifact (MinIO)
- Shuffle test ile data leakage olmadığı doğrulandı (gerçek vs. shuffled accuracy farkı: +34%)

### Risk Management (Faz 4 - Adım 1) ✅
- **Risk Management Agent**: signals.raw → signals.approved dönüşümü
  - 6 filtre: confidence floor, position cap, max open positions,
    universe filter, direction whitelist (v2), symbol whitelist (v2)
  - 5 dakika position TTL (cooldown)
  - Redis stream: `stream:signals.approved`
  - InfluxDB persist (risk_decisions measurement)
- **Backtest analizi (12 May, 04:00)**: 24h 586 onaylı sinyal
  - Genel hit rate: %52.05 (1m horizon)
  - SHORT > LONG: %55.10 vs %48.97
  - Confidence kalibrasyon ÇALIŞIYOR: 0.7+ bucket = %56.34 hit rate
  - Top 7 sembol: ONDO (%70), ETH (%67), TAO (%65), ZEC (%64), PEPE (%62), BNB (%59), TON (%56)
  - Kümülatif PnL (fee'siz): +%4.77, Profit factor: 1.20
- **Filtre v2 (12 May, 04:14)**: Backtest sonrasında uygulanan
  - Confidence: 0.5 → 0.7
  - Symbol whitelist: top 7 (hit rate >%55)
  - Direction: LONG + SHORT (whitelist yeterli filtre)
  - Beklenen: saatte 30-60 onay, 12-24h sonra ikinci backtest

### Live Inference + Signal Generation (Faz 3) ✅
- **Per-Coin Learning**: Eğitim sonrası live inference moduna geçer
  - Her dakika dakika başı +10sn'de tahmin yapar (19 sembol)
  - Redis stream: `stream:predictions.{symbol}`
  - InfluxDB persist (predictions measurement)
- **Signal Generation Agent** (YENİ!): Predictions → Trading sinyalleri
  - Confidence threshold: 0.4 (≈ prob_up >0.7 veya <0.3)
  - Position sizing: confidence × MAX_POSITION_PCT (max %10)
  - Cooldown: aynı sembol için 5 dakika
  - Redis stream: `stream:signals.raw`
  - InfluxDB persist (signals measurement)
- **InfluxDB retention**: 30 gün (otomatik temizleme aktif)

### Görselleştirme ✅
- Grafana dashboard: "MACTS — Canlı Market Verisi"
  - 3 büyük fiyat kartı (BTC, ETH, SOL)
  - Fiyat hareketi timeseries
  - Trade count bar chart
  - RSI(14) bargauge (20 coin, renk gradient)
  - MACD histogram (BTC/ETH/SOL momentum)

---

## Henüz Yapılmayanlar

### Faz 3 — Sıradaki
- [ ] **Canlı tahmin yayını**: Her dakika model tahmini → `stream:predictions.{symbol}`
- [ ] **Saatlik retrain loop**: Modeller yeni veriyle güncellensin
- [ ] **Model Registry**: En iyi modeli "production"a promote etme
- [ ] **Signal Generation Agent**: Tahminleri trading sinyaline çevir (confidence threshold, position sizing)

### Faz 4 — Planlandı
- [ ] **Risk Management Agent**: Pozisyon limitleri, drawdown koruması
- [ ] **Execution Agent**: Binance Testnet'e gerçek emir gönderme
- [ ] **Portfolio Manager**: Aggregate pozisyon takibi
- [ ] **Circuit Breaker**: Acil durum kapama mekanizması

### Faz 5 — Planlandı
- [ ] Promotion pipeline (Testnet → Mainnet Forward Paper → Canary Live)
- [ ] 30+ gün paper trading doğrulaması
- [ ] Backtesting agent ile geçmiş veride strateji testi

---

## Bilinen Sorunlar / Teknik Borç

1. **Binance WebSocket erişimi yok**: VPS Frankfurt lokasyonundan Binance WS bağlantısı kısıtlı. Şu an REST polling fallback ile çalışıyor (5 saniye gecikme). Mainnet'e geçişte bu çözülmeli.
2. **Per-Coin Learning tek seferlik**: Agent başlatıldığında bir kez eğitiyor, sonra idle. Retrain loop Faz 3'te eklenecek.
3. **Backtesting agent boş**: İskelet halinde. Faz 5'te aktive edilecek.
4. **Trading agent'ları (signal_generation, risk_management, execution, portfolio_manager, circuit_breaker) iskelet halinde**: Stub kodlar var, gerçek mantık Faz 3-4'te yazılacak.
5. **Test coverage düşük**: Unit/integration test'leri minimum. Faz 3-4'te artırılacak.
6. **InfluxDB retention policy yok**: Sınırsız büyüyebilir, ~5 hafta sonra disk dolar. Faz 3'te retention eklenmeli.

---

## Faz 3 — AI Analyst Katmanı (Devam Ediyor)

**Aşama 0 tamamlandı (5 Tem 2026)**: NVIDIA NIM model seçimi canlı probe ile kilitlendi.
- Birincil: `nvidia/nemotron-3-super-120b-a12b` (5/5 parse, p50 12.7s)
- Yedek: `deepseek-ai/deepseek-v4-pro` | Deneysel: `z-ai/glm-5.2` | Elenen: qwen3.5 (500 + 84s)
- Detay: `docs/AI_ANALYST_MODEL_SELECTION.md`, probe aracı: `scripts/nim_model_probe.py`

**Aşama 1 / Paket 1 teslim edildi**: `agent-ai-analyst` servisi (feature flag: `ENABLE_AI_ANALYST=false` varsayılan — sistem davranışı DEĞİŞMEDİ).
- Agent iskeleti, StaggeredScheduler (20 sembol × 15 dk, burst-korumalı, unit testle kanıtlı ≤30 istek/dk), `AIAnalysis` şeması, 25 unit test

**Aşama 2 / Paket 2 teslim edildi**: `src/core/llm/` istemci katmanı + agent'a tam entegrasyon (39 yeni unit test).
- `nvidia_client` (typed hatalar: 429/402/404/5xx), `rate_limiter` (30 RPM token bucket + 2000/gün, non-blocking), `fallback_chain` (**429'da fallback YOK** — kota anahtar bazında global; 5xx'te retry+backoff sonrası deepseek-v4-pro), `tracking` (Redis TTL cache + Postgres `llm_usage_log`), `prompts` (semver şablon: `config/prompts/trading_analysis_v1.yaml`, few-shot'lu)
- Akış: cache → rate limit → zincir → pydantic doğrulama → `stream:ai_analysis.{symbol}` publish → usage kaydı. Doğrulanamayan çıktı asla yayınlanmaz; hiçbir hata sinyal üretimini bloklamaz.
- Aktivasyon: `.env`'e `NVIDIA_API_KEY` + `ENABLE_AI_ANALYST=true`, sonra imaj rebuild + container restart. Anahtar yoksa agent çökmez, hata loglayıp boşta kalır.

**Aşama 4 / Paket 3 teslim edildi (ilk yarı)**: Tahmin-vs-gerçek döngüsü + gözlemlenebilirlik.
- Agent artık `stream:ticks.{sym}.kline.1m`'den son kapanışı izliyor; LLM payload'ı fiyatla zenginleştiriliyor (FeatureSnapshot'ta fiyat alanı yoktu — canlıdaki aşırı neutral yanlılığının olası nedeni buydu, düzeltildi)
- Her yayınlanan analiz `llm_prediction`a entry_price + deadline ile kaydediliyor; 5 dk'lık `_outcome_loop` vadesi dolanları `classify_outcome` (±%0.10 flat band) ile `llm_prediction_outcome`a yazıyor
- Yeni metrikler: `macts_ai_analyst_outcomes_total`, `latency_seconds` (histogram), `daily_quota_used` (gauge). Dashboard: Grafana → "AI Analyst Performance" (uid: macts-ai-analyst)
- Düzeltme notu: cache hit oranı normal akışta ~0 (feature snapshot her dk benzersiz); cache'in değeri restart koruması — rapordaki %20 tasarruf hedefi geri çekildi
- 5/5 Claude Skill tamam (`continuous-improvement-loop` eklendi — bandit planı orada)

---

## Önemli Mimari Kararlar

- **Mainnet REST + Testnet trade**: Veri mainnet'ten (gerçek piyasa), trade'ler testnet'e (risk yok)
- **Redis Streams öncelikli mesajlaşma**: Düşük gecikme; Kafka yedek olarak duruyor
- **Per-symbol ring buffer**: Feature Engineering RAM'de tutar, kalıcı state için InfluxDB
- **Race condition fix**: `_kline_listeners` dict'e task ataması, `_backfill_and_listen` öncesi yapılır (duplicate listener engellenir)
- **Redis-based backfill**: Binance rate limit'i (418 Teapot) sorununu çözmek için, Data Collection'ın Redis'e yazdığı kline'lardan okuruz
- **HistGradientBoostingClassifier**: LightGBM kurmak yerine sklearn-native muadili kullanıldı (yeni paket yok, eşdeğer performans)
- **MLflow 3.0**: Logged Models API'si, model dosyalarının MinIO'ya kaydedilmesi
- **AWS_SECRET = MINIO_ROOT_PASSWORD**: Aksi takdirde SignatureDoesNotMatch hatası
