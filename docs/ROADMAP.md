# MACTS — Yol Haritası (Roadmap)

> Hangi fazda neyin yapıldığı, hangisinin sırada olduğu.
> Her faz için "definition of done" net olarak belirtilmiştir.

---

## Faz 0 — Altyapı ✅ Tamamlandı

**Hedef**: Production-grade altyapı stack'i ayakta.

**Tamamlananlar**:
- [x] Docker Compose ile 20 servis
- [x] Redis 7 + Kafka cluster (mesajlaşma)
- [x] PostgreSQL 16 (relasyonel)
- [x] InfluxDB 2.7 (time-series)
- [x] MinIO (object storage, S3-compatible)
- [x] MLflow 3.0 (ML tracking)
- [x] Prometheus + Grafana (observability)
- [x] Log rotation (max-size: 50m, max-file: 3)
- [x] Restart policies (`unless-stopped`)
- [x] Custom Docker image (TA-Lib derlemeli, PyTorch CPU)
- [x] Healthcheck'ler (Kafka start_period: 60s düzeltmesi dahil)

**Definition of Done**: `docker compose ps` ile tüm servisler `Up (healthy)` görünür, kapanıp tekrar açıldığında otomatik gelir.

---

## Faz 1 — Veri Pipeline'ı ✅ Tamamlandı

**Hedef**: Canlı piyasa verisini topla, kalıcı olarak sakla, görselleştir.

**Tamamlananlar**:
- [x] Market Scanner Agent (528 perpetual → top 20)
- [x] Data Collection Agent (REST polling fallback)
- [x] Dinamik universe (Market Scanner'dan otomatik)
- [x] InfluxDB persist (kline measurement)
- [x] Grafana dashboard (canlı fiyat, trade count)
- [x] Prometheus metrics expose

**Karşılaşılan engeller**:
- VPS WebSocket erişimi yok → REST polling fallback
- Universe değişimini canlı yansıtma → Redis stream subscribe + diff logic

**Definition of Done**: 24 saat boyunca 20 sembol için kesintisiz kline akışı, Grafana'da canlı görünüm.

---

## Faz 2 — Feature + ML Pipeline ✅ Tamamlandı

**Hedef**: Teknik indikatörlerden ML modeli, MLflow ile tracking.

**Tamamlananlar**:

### Adım 1 — Feature Engineering ✅
- [x] TA-Lib 0.6.8 entegrasyonu (158 fonksiyon)
- [x] Per-symbol ring buffer (200 bar)
- [x] 14 indikatör hesaplama (RSI, MACD×3, BB×3, EMA×3, SMA×2, ATR)
- [x] Redis stream yayını (`stream:features.{symbol}`)
- [x] InfluxDB persist (features measurement)
- [x] Backfill (Redis stream'den, Binance rate limit bypass)
- [x] Grafana panelleri (RSI bargauge, MACD histogram)

### Adım 2 — Per-Coin Learning ✅
- [x] InfluxDB'den feature okuma (pivot ile)
- [x] Target oluşturma (1-min ahead binary)
- [x] HistGradientBoostingClassifier
- [x] Train/test split (time-series aware, %80/%20)
- [x] MLflow tracking (params + metrics + model artifact)
- [x] Shuffle test (data leakage doğrulaması)
- [x] 20 sembol için batch training
- [x] Leaderboard logging

**Karşılaşılan engeller**:
- Race condition (duplicate listeners) → task atama sırası düzeltildi
- Binance 418 Teapot (rate limit ban) → Redis-based backfill
- MLflow 2.x → 3.x upgrade (artifact API uyumluluğu)
- MinIO SignatureDoesNotMatch → AWS_SECRET = MINIO_ROOT_PASSWORD eşitlendi
- File descriptor leak (too many open files) → race condition fix bunu da çözdü

**Definition of Done**: 20 sembol için MLflow'da kayıtlı model artifact'ları, ortalama %85+ accuracy, sıfır data leakage.

**Sonuçlar (11 Mayıs 2026)**:
- Mean accuracy: %86.7
- Median accuracy: %86.5
- Best: SOLUSDT (%91.5)
- Worst: ONDOUSDT (%81.1)

---

## Faz 3 — Canlı Inference + Signal Generation 🚧 Sıradaki

**Hedef**: Eğitilen modellerden canlı tahmin, trading sinyali üret.

**Yapılacaklar**:
- [ ] Per-Coin Learning'i live inference moda al
  - Her dakika feature oku, model.predict(), `stream:predictions.{symbol}` yayınla
  - Tahmin payload: {prob_up, confidence, timestamp}
- [ ] Saatlik retrain loop
  - Yeni veriyle eğit, eski modeli aşıyorsa promote et
  - MLflow Model Registry kullanımı (Staging / Production stages)
- [ ] Signal Generation Agent
  - Predictions stream'ini dinle
  - Confidence threshold (örn. >0.7) altı sinyalleri filtrele
  - Position sizing önerisi (Fractional Kelly)
  - `stream:signals.{symbol}` yayınla
- [ ] InfluxDB retention policy (otomatik eski veri temizleme)
- [ ] Grafana'ya prediction confidence paneli

### Faz 3 — AI Analyst Katmanı (LLM) 🚧 Başladı

**Hedef**: NVIDIA NIM üzerinden LLM analiz katmanı — ML sinyalini zenginleştirir, asla bloklamaz.

**Karar kaynağı**: `docs/AI_ANALYST_MODEL_SELECTION.md` (Aşama 0 tamamlandı, canlı probe ile)

- [x] Aşama 0: Model seçimi + canlı probe (birincil: nemotron-3-super-120b-a12b, yedek: deepseek-v4-pro)
- [x] Aşama 1 / Paket 1: Agent iskeleti (`src/agents/ai_analyst/`), feature flag (`ENABLE_AI_ANALYST`), StaggeredScheduler (kota-güvenli 15 dk kadans), AIAnalysis şeması, compose girdisi, 25 unit test
- [x] Aşama 2 / Paket 2: `src/core/llm/` — nvidia_client, rate_limiter (30 RPM token bucket), backoff, Redis cache, fallback_chain (429≠5xx politikası), usage_tracker (Postgres llm_usage_log), agent'a tam entegrasyon, 39 unit test
- [~] Aşama 3: Prompt versiyonlama — config/prompts/trading_analysis_v1.yaml (semver + few-shot) teslim edildi; MLflow experiment entegrasyonu Aşama 4 ile birlikte gelecek
- [ ] Aşama 4: Kendi kendini geliştirme döngüsü (prediction outcome job, bandit, Grafana "AI Analyst Performance" dashboard)
- [ ] Aşama 5: Signal Generation entegrasyonu (ML_WEIGHT/LLM_WEIGHT, başlangıçta LLM ağırlığı düşük)

**Definition of Done**: Her dakika her sembol için canlı tahmin yayınlanıyor, Signal Generation Agent threshold üstü sinyalleri yayınlıyor, retrain loop her saat çalışıyor.

**Tahmini süre**: 2-3 oturum (1 oturum = 1-2 saat)

---

## Faz 4 — Risk + Execution ⏳ Planlandı

**Hedef**: Sinyali Binance Testnet'te gerçek emre çevir, risk altında tut.

**Yapılacaklar**:
- [ ] Risk Management Agent
  - Pozisyon limitleri (max per-symbol, max portfolio)
  - Drawdown koruması (günlük max kayıp %)
  - Korelasyon kontrolü (çok benzer pozisyon almasın)
  - Sinyali onayla veya reddet
- [ ] Execution Agent
  - Binance Testnet API (ccxt veya direct REST)
  - clientOrderId ile idempotent
  - Stop-loss / take-profit otomatik
  - Slippage kontrolü
- [ ] Portfolio Manager
  - Aggregate pozisyon takibi
  - Realized/unrealized PnL hesabı
  - Grafana'da portföy paneli
- [ ] Circuit Breaker
  - Acil durum stop (manuel veya otomatik tetik)
  - Tüm pozisyonları market emir ile kapat
  - Sebep loglama

**Definition of Done**: Testnet'te 1 hafta boyunca otomatik trade yapılıyor, hiçbir teknik bug yok, PnL takibi çalışıyor.

**Tahmini süre**: 3-4 oturum

---

## Faz 5 — Promotion Pipeline ⏳ Planlandı

**Hedef**: Testnet'ten production'a güvenli geçiş.

**Yapılacaklar**:
- [ ] Backtesting Agent
  - InfluxDB'deki geçmiş veriyle strateji simülasyonu
  - Fee, slippage dahil
  - Sharpe, max drawdown, win rate hesabı
- [ ] Mainnet Forward Paper Trading
  - Mainnet gerçek fiyatlar, sahte emirler
  - En az 30 gün
  - Manuel onaylı geçiş
- [ ] Canary Live Trading
  - %10 → %25 → %50 → %100 kademeli ölçekleme
  - Her aşama arası manuel onay + minimum süre
  - Otomatik geri çekilme tetikleyicileri

**Definition of Done**: Production trading aktif, sürdürülebilir performans, dokümante edilmiş risk limitleri.

**Tahmini süre**: Aylar (gözlem süresi gerektirir)

---

## Faz Geçiş Kriterleri

Her faz arası geçiş için **manuel kontrol noktası**:

1. Definition of Done maddeleri karşılandı mı?
2. Bilinen sorunlar listesi STATUS.md'ye eklendi mi?
3. Geri dönüş planı var mı? (rollback nasıl yapılır?)
4. Bir sonraki faza geçmeden 24 saat gözlem yapıldı mı?

---

## Şu An (11 Mayıs 2026)

**Faz 2 bitti, Faz 3'ün eşiğindeyiz.**

İlk hedef: Per-Coin Learning Agent'ı **live inference mode**'a almak. Mevcut kodda `_train_once()` var, buna `_predict_loop()` eklenecek — eğitim sonrası her dakika feature okuyup tahmin yayınlayacak.
