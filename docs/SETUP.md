# MACTS — Detaylı Kurulum Kılavuzu

> ⚠️ **VISION SPEC** — Bu doküman projenin **hedef durumunu** tanımlar, mevcut canlı sistemi değil.
> Mevcut durum için [STATUS.md](../STATUS.md), yol haritası için [ROADMAP.md](ROADMAP.md).
> Bu spec'in tamamı henüz uygulanmamıştır; bazı bölümler Faz 3+ tamamlandıkça hayata geçecektir.

---


## Sistem Gereksinimleri

| Bileşen | Minimum | Önerilen |
|---------|---------|----------|
| RAM | 16 GB | 32 GB+ |
| CPU | 4 core | 8+ core |
| Disk | 50 GB SSD | 200 GB+ NVMe |
| OS | Ubuntu 22.04+ / Debian 12+ | Ubuntu 24.04 LTS |
| Docker | 24.0+ | Latest |
| Docker Compose | 2.20+ | Latest |
| Python (yerel dev) | 3.11 | 3.11 |
| Network | 100 Mbps | 1 Gbps + düşük gecikme |

---

## 1. Binance Hesap Hazırlığı

### Testnet (Aşama 1)

1. <https://testnet.binancefuture.com> adresine kaydol
2. Sol menüden **API Management** → **Create API Key**
3. Şu izinleri ver:
   - ✅ Reading
   - ✅ Futures Trading
   - ❌ **Withdrawal (KESİNLİKLE KAPALI olmalı)**
4. API Key + Secret'ı `.env` dosyasındaki `BINANCE_TESTNET_API_KEY` ve `BINANCE_TESTNET_API_SECRET` alanlarına yaz
5. Testnet faucet'tan sahte USDT al (genelde 10000)

### Mainnet (Aşama 2-3)

1. Binance ana hesabında 2FA'yı aktif et
2. **API Management** → **Create API Key**
3. **IP whitelist** etkinleştir → sadece sunucunun statik IP'sini ekle
4. İzinler:
   - ✅ Reading
   - ✅ Futures Trading
   - ❌ Withdrawal
   - ❌ Spot Trading
5. Mainnet API Key + Secret'ı `.env` dosyasındaki `BINANCE_MAINNET_API_KEY` ve `BINANCE_MAINNET_API_SECRET` alanlarına yaz

---

## 2. Sunucu Hazırlığı

### Ubuntu 24.04 LTS

```bash
# Sistem güncellemesi
sudo apt update && sudo apt upgrade -y

# Docker kurulumu
curl -fsSL https://get.docker.com | sudo bash
sudo usermod -aG docker $USER
newgrp docker

# Docker compose plugin (Docker Desktop ile gelir, server'da kur)
sudo apt install -y docker-compose-plugin

# Doğrulama
docker --version
docker compose version

# Saat senkronizasyonu (Binance API timestamp tolerance: 1000ms)
sudo apt install -y chrony
sudo systemctl enable --now chrony
```

### NTP / Sistem Saati

Binance API request'lerde server time ile <1000ms fark olmalı. **chrony** ile sürekli senkronize tut:

```bash
sudo chronyc sources -v
sudo chronyc tracking
```

---

## 3. MACTS Kurulumu

```bash
# Repository klonla
# Repo kişisel — clone adımı yok
cd macts

# Konfigürasyonu hazırla
cp .env.example .env
cp config/config.example.yaml config/config.yaml

# .env'yi düzenle ve gerçek API anahtarlarını gir
nano .env
```

### Kritik `.env` Alanları

```bash
# Mod (testnet ile başla!)
MACTS_MODE=testnet

# Binance Testnet
BINANCE_TESTNET_API_KEY=<senin_testnet_key>
BINANCE_TESTNET_API_SECRET=<senin_testnet_secret>

# Strong passwords (her biri en az 16 karakter)
POSTGRES_PASSWORD=<güçlü_şifre>
INFLUXDB_PASSWORD=<güçlü_şifre>
INFLUXDB_TOKEN=<rastgele_64_char>
REDIS_PASSWORD=<güçlü_şifre>
GRAFANA_ADMIN_PASSWORD=<güçlü_şifre>
MINIO_ROOT_PASSWORD=<güçlü_şifre>

# Telegram (opsiyonel ama önerilen)
TELEGRAM_BOT_TOKEN=<bot_father'dan_aldığın_token>
TELEGRAM_CHAT_ID=<senin_chat_id>

# JWT secret (32+ char random string)
JWT_SECRET=<rastgele_string>

# Encryption key (üretmek için aşağıdaki komutu çalıştır)
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=<fernet_key>
```

---

## 4. İlk Başlatma

```bash
# Docker imajını build et (ilk kez ~10 dakika sürer, TA-Lib derlemesi)
docker compose build

# Tüm servisleri başlat (testnet mode)
docker compose -f docker-compose.yml -f docker-compose.testnet.yml up -d

# Servisleri kontrol et
docker compose ps

# Logları izle
docker compose logs -f --tail=100

# Belirli bir agent'ın loguna bak
docker compose logs -f agent-data-collection
```

---

## 5. Doğrulama

### Health Check

```bash
# Konfigürasyon valid mi?
docker compose exec agent-monitoring python -m src.cli config validate

# Mevcut mod
docker compose exec agent-monitoring python -m src.cli mode show

# Tüm agent'ların durumu
docker compose exec agent-monitoring python -m src.cli health
```

### Servis URL'leri

| Servis | URL | Login |
|--------|-----|-------|
| Grafana | <http://localhost:3000> | admin / `.env`'deki şifre |
| Prometheus | <http://localhost:9090> | - |
| MLflow | <http://localhost:5000> | - |
| MinIO Console | <http://localhost:9001> | `MINIO_ROOT_USER` / Pass |
| InfluxDB | <http://localhost:8086> | admin / Pass |

### İlk Saat İçinde Görmeniz Gerekenler

- ✅ Tüm 13 agent container'ı `healthy` durumunda
- ✅ Grafana'da heartbeat metrikleri akıyor
- ✅ Data Collection logları "kline received" / "trade received" mesajları içeriyor
- ✅ InfluxDB'de yeni measurement'lar görünüyor
- ✅ Telegram'a "MACTS started in testnet mode" mesajı geldi

---

## 6. Yerel Geliştirme Ortamı

```bash
# Python sanal ortam
python3.11 -m venv venv
source venv/bin/activate

# TA-Lib C kütüphanesini sisteme kur (Linux)
sudo apt install -y libta-lib0 libta-lib-dev
# veya kaynaktan: docker/Dockerfile.agent içinde aynı adımlar

# Python paketleri (dev extras dahil)
pip install -e ".[dev]"

# Pre-commit hook
pre-commit install

# Sadece bağımlılık servislerini Docker'da çalıştır
docker compose up -d redis postgres influxdb kafka

# Agent'ı yerel Python'da çalıştır
export $(cat .env | xargs)
python -m src.cli agent run market_scanner

# Test
pytest tests/ -v --cov=src
```

---

## 7. Yaygın Sorunlar

### TA-Lib derleme hatası

```
error: command 'gcc' failed with exit status 1
```
→ `libta-lib-dev` paketi eksik. Yerel makinede:
```bash
sudo apt install -y libta-lib0 libta-lib-dev build-essential
```

### Binance API timestamp hatası (-1021)

```
APIError(code=-1021): Timestamp for this request was 1000ms ahead
```
→ Sunucu saati senkronize değil. `sudo chronyc tracking` kontrol et.

### Kafka container `unhealthy`

→ Zookeeper'ın hazır olmasını bekle. İlk başlatmada 30 saniye sürebilir:
```bash
docker compose logs kafka | tail -50
```

### Permission denied: /app/logs

→ Volume'lerin sahibi yanlış:
```bash
docker compose down
sudo chown -R 1000:1000 ./data ./logs
docker compose up -d
```

---

## 8. Güvenlik Önerileri

1. **Asla** API anahtarlarını commit etme. `.gitignore` `.env`'i içerir.
2. Production'da Docker secrets veya Vault kullan.
3. Binance API'de **withdrawal kapalı** olduğundan emin ol.
4. Sunucu firewall'unda sadece zorunlu portları aç (22 SSH).
5. Grafana, MLflow gibi servisleri public açmak yerine SSH tunnel ile eriş.
6. Düzenli olarak audit log'u kontrol et.
7. 2FA aktif et (Binance, GitHub, sunucu).

Daha fazla için: [SECURITY.md](./SECURITY.md)
