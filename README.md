# MACTS — Multi-Agent Crypto Trading System

> Kişisel mühendislik projesi: Binance Futures üzerinde çalışacak, 11 bağımsız agent'tan oluşan, ML tabanlı bir otonom kripto trading sistemi. Bu repo **kişisel kullanım** içindir, paylaşıma açık değildir.

---

## Bu Repo Ne?

İki nedenle var:

1. **Sistem mühendisliği egzersizi**: Mikroservis mimarisi, mesaj kuyrukları, time-series DB, ML pipeline'ı, observability — hepsini bir arada uygulamak için bir laboratuvar.
2. **Trading araştırması**: Teknik indikatörlerle (RSI, MACD, Bollinger, vs.) gerçek kripto piyasasında ne kadar sinyal yakalanabileceğini ölçmek.

**Para kazandıran bir bot değildir** ve henüz canlı işlem yapmaz. Hâlâ inşaat aşamasında.

---

## Mevcut Durum (11 Mayıs 2026)

Detaylı durum için: [STATUS.md](STATUS.md) — yol haritası için: [docs/ROADMAP.md](docs/ROADMAP.md)

Tek satır özet: **Faz 2 (Feature Engineering + Per-Coin Learning) tamamlandı.** 20 sembol için canlı veri akıyor, dakikada 14 teknik indikatör hesaplanıyor, her sembol için ayrı ML modeli eğitildi ve MLflow'da kayıt altına alındı.

Aşağıdaki özet sayılar 11 Mayıs 2026 itibarıyladır:

- **20 servis** Docker container'larında ayakta
- **24 saat** kesintisiz veri akışı
- **~252,000** feature satırı InfluxDB'de
- **20 ML modeli** MLflow'da, ortalama %86.7 accuracy
- **Sıfır** yazma hatası

---

## Mimari (Kısaca)

```
Binance REST API
│
▼
[Market Scanner] ─── top 20 universe ───┐
│                                │
▼                                ▼
[Data Collection] ──── kline akışı ──> InfluxDB (persist)
│                                │
▼                                ▼
│              [Feature Engineering] ─── 14 indikatör ──> InfluxDB
│                                │
│                                ▼
│              [Per-Coin Learning] ─── 20 model ──> MLflow + MinIO
│
▼
[Signal Generation*] ── [Risk Mgmt*] ── [Execution*]
│
▼
Binance Testnet
```

> `*` ile işaretli agent'lar henüz iskelet halinde, Faz 3-4'te aktive edilecek.

Detaylı mimari için: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## Teknoloji Yığını

| Katman | Teknoloji |
|---|---|
| Container | Docker Compose |
| Mesajlaşma | Redis Streams (düşük gecikme), Kafka (yedek) |
| Time-Series DB | InfluxDB 2.7 |
| Relational DB | PostgreSQL 16 |
| Object Storage | MinIO (S3-compatible) |
| ML Tracking | MLflow 3.0 |
| Observability | Prometheus + Grafana |
| ML Framework | scikit-learn, PyTorch, stable-baselines3 (Faz 3) |
| Tek. İndikatör | TA-Lib 0.6.8 (158 fonksiyon) |
| Dil | Python 3.11 |

---

## Hızlı Komutlar

```bash
# Sistem ayakta mı?
docker compose ps

# Belirli bir agent'ın logu
docker compose logs -f agent-feature-engineering

# Tüm sistemi başlat
make up-testnet     # docker-compose.testnet.yml overlay'i ile

# Tüm sistemi durdur
docker compose down

# Imajı yeniden derle (kod değişikliği sonrası)
make build && docker compose up -d --force-recreate <servis_adi>
```

Operasyonel detaylar için: [docs/RUNBOOK.md](docs/RUNBOOK.md)

---

## Faz Yol Haritası — Özet

| Faz | İçerik | Durum |
|---|---|---|
| **Faz 0** | Altyapı (Docker, Redis, Kafka, InfluxDB, Postgres, MinIO, Grafana, MLflow, Prometheus) | ✅ Tamamlandı |
| **Faz 1** | Market Scanner + Data Collection + InfluxDB persist + Grafana dashboard | ✅ Tamamlandı |
| **Faz 2** | Feature Engineering (TA-Lib indikatörler) + Per-Coin Learning (LightGBM benzeri ML) + MLflow tracking | ✅ Tamamlandı |
| **Faz 3** | Signal Generation, canlı tahmin yayını, retrain loop | 🚧 Sıradaki |
| **Faz 4** | Risk Management, Execution, Testnet'te paper trading | ⏳ Planlandı |
| **Faz 5** | Promotion pipeline (Testnet → Paper → Canary Live) | ⏳ Planlandı |

---

## Önemli Uyarı

Bu yazılım:
- **Yatırım tavsiyesi değildir**
- Henüz canlı işlem yapmaz, sadece testnet'e bağlanır
- Kişisel araştırma ve mühendislik egzersizi içindir
- Paylaşıma açık değildir, MIT/Apache/vb. lisans verilmemiştir

---

## Dosya Yapısı (Özet)

```
macts/
├── src/agents/                  # 11 agent (her biri ayrı modül)
│   ├── market_scanner/          # ✅ Çalışıyor
│   ├── data_collection/         # ✅ Çalışıyor
│   ├── feature_engineering/     # ✅ Çalışıyor
│   ├── per_coin_learning/       # ✅ Çalışıyor
│   ├── signal_generation/       # 🚧 İskelet
│   ├── risk_management/         # 🚧 İskelet
│   ├── execution/               # 🚧 İskelet
│   ├── portfolio_manager/       # 🚧 İskelet
│   ├── circuit_breaker/         # 🚧 İskelet
│   ├── model_registry/          # 🚧 İskelet
│   └── monitoring/              # ✅ Çalışıyor (Prometheus expose)
├── src/core/                    # Ortak: messaging, ml, utils
├── docker/                      # Dockerfile.agent, Dockerfile.mlflow
├── docker-compose.yml           # Ana stack
├── docker-compose.testnet.yml   # Testnet overlay
├── monitoring/grafana/          # Dashboard JSON'ları + provisioning
├── docs/                        # Mimari, runbook, vision spec'ler
├── STATUS.md                    # Mevcut durum (canlı)
└── README.md                    # Bu dosya
```
