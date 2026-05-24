# MACTS — Operasyon Runbook'u

> ⚠️ **VISION SPEC** — Bu doküman projenin **hedef durumunu** tanımlar, mevcut canlı sistemi değil.
> Mevcut durum için [STATUS.md](../STATUS.md), yol haritası için [ROADMAP.md](ROADMAP.md).
> Bu spec'in tamamı henüz uygulanmamıştır; bazı bölümler Faz 3+ tamamlandıkça hayata geçecektir.

---


> Sistem çalışırken karşılaşılabilecek olaylara karşı standart prosedürler.

## 🔴 P0 — Acil Durumlar

### Tüm Pozisyonları Hemen Kapat

```bash
# Yöntem 1: CLI (önerilen)
docker compose exec agent-circuit-breaker python -m src.cli emergency-stop

# Yöntem 2: Manuel circuit breaker tetikle
docker compose exec agent-circuit-breaker python -c "
import asyncio
from src.core.messaging import RedisStreamsBus
import os

async def main():
    bus = RedisStreamsBus(host=os.environ['REDIS_HOST'], password=os.environ.get('REDIS_PASSWORD'))
    await bus.connect()
    await bus.publish('stream:execution.commands', {
        'action': 'close_all',
        'reason': 'manual_emergency_stop'
    })
    await bus.disconnect()

asyncio.run(main())
"

# Yöntem 3: SON ÇARE - Binance UI'dan manuel kapat
# https://www.binance.com/en/futures/markets → Positions tab → "Close All"
```

### Sistemi Tamamen Durdur

```bash
docker compose down

# Verileri korur, sadece container'ları durdurur
# Yeniden başlatmak için: docker compose up -d
```

---

## 🟡 P1 — Yüksek Öncelik

### Bir Agent Crash Etti

1. Hangi agent crash etti?
   ```bash
   docker compose ps | grep -v "Up"
   ```

2. Logları incele:
   ```bash
   docker compose logs --tail=200 agent-<name>
   ```

3. Yeniden başlat:
   ```bash
   docker compose restart agent-<name>
   ```

4. Hala çökmeye devam ediyorsa:
   - Konfigürasyonu doğrula: `python -m src.cli config validate`
   - Bağımlılıkların ayakta olduğunu kontrol et (Redis, Kafka, vs.)
   - GitHub Issues'a bildir

### WebSocket Bağlantısı Sürekli Kopuyor

1. Network durumunu kontrol et:
   ```bash
   docker compose exec agent-data-collection ping fstream.binance.com
   ```

2. Binance status sayfasını kontrol et: <https://www.binance.com/en/support/announcement>

3. Eğer reconnect logic'i çalışmıyorsa agent'ı yeniden başlat:
   ```bash
   docker compose restart agent-data-collection
   ```

### Günlük Zarar Limiti Aşıldı

Agent otomatik olarak cooldown moduna geçer. Manuel müdahale:

1. Mevcut pozisyonları incele:
   ```sql
   SELECT * FROM positions WHERE closed_at IS NULL;
   ```

2. Cooldown'u inceleyip onayla:
   ```sql
   SELECT * FROM cooldowns WHERE ends_at > now();
   ```

3. **Cooldown süresi dolmadan trade etmeye devam ETME**. Eğer çok haklı bir nedenle erken kaldırmak gerekirse:
   ```sql
   UPDATE cooldowns SET ends_at = now() WHERE id = <id>;
   ```
   Ve audit log'a not düş.

---

## 🟢 P2 — Rutin Operasyonlar

### Günlük Kontrol Listesi

- [ ] `docker compose ps` → tüm servisler `healthy` mi?
- [ ] Grafana "System Overview" dashboard → heartbeat'ler güncel mi?
- [ ] Telegram'a son 24 saatte critical alert geldi mi?
- [ ] PnL: Pozisyon ve gerçekleşen kâr/zarar makul mü?
- [ ] InfluxDB disk doluluk oranı (< %80)
- [ ] PostgreSQL disk doluluk oranı (< %80)

### Haftalık Bakım

- [ ] Docker imajlarını güncelle: `docker compose pull && docker compose up -d`
- [ ] Sistem güncellemesi: `sudo apt update && sudo apt upgrade -y`
- [ ] Backup'ları doğrula (Postgres dump, MinIO snapshot)
- [ ] Log rotation kontrolü: `du -sh ./logs/`
- [ ] Performance raporu Telegram'a otomatik gelir mi?

### Aylık

- [ ] Tüm sırları rotate et (Postgres, InfluxDB, Redis şifreleri)
- [ ] Binance API key'i rotate et
- [ ] Disaster recovery testi: Docker volume'leri sil, backup'tan restore et
- [ ] Risk komite incelemesi
- [ ] Postmortem (varsa)

---

## 📊 Backup & Restore

### PostgreSQL Backup

```bash
# Günlük cron
docker compose exec -T postgres pg_dump -U macts_user macts | gzip > backups/postgres-$(date +%Y%m%d).sql.gz
```

### Restore

```bash
gunzip -c backups/postgres-20260101.sql.gz | docker compose exec -T postgres psql -U macts_user macts
```

### InfluxDB Backup

```bash
docker compose exec influxdb influx backup /tmp/backup --token <ADMIN_TOKEN>
docker cp macts-influxdb:/tmp/backup backups/influx-$(date +%Y%m%d)
```

### MinIO (model artifact'ler)

```bash
docker run --rm -v "$(pwd)/backups:/backup" \
    --network macts_macts-net minio/mc \
    mirror http://minio:9000/macts-models /backup/minio-$(date +%Y%m%d)
```

---

## 📈 Performans Tuning

### Yüksek CPU Kullanımı (Per-Coin Learning)

- Coin sayısını sınırla (`config.universe.filters` ile)
- TFT model boyutunu küçült (`hidden_size: 32`)
- Inference için TorchScript export kullan

### Yüksek Bellek Kullanımı

- Redis maxmemory'yi düşür
- InfluxDB retention period'unu kısalt
- Kline history buffer'ını küçült (`config.data_collection.buffer.max_kline_history`)

### Yüksek Network Trafiği

- WebSocket stream sayısını azalt (her connection 200 stream max)
- Order book derinliğini düşür (level 20 yerine 10)
