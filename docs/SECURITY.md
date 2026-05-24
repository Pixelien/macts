# MACTS — Güvenlik Politikası

> ⚠️ **VISION SPEC** — Bu doküman projenin **hedef durumunu** tanımlar, mevcut canlı sistemi değil.
> Mevcut durum için [STATUS.md](../STATUS.md), yol haritası için [ROADMAP.md](ROADMAP.md).
> Bu spec'in tamamı henüz uygulanmamıştır; bazı bölümler Faz 3+ tamamlandıkça hayata geçecektir.

---


## API Anahtar Yönetimi

### Binance API
- **Withdrawal izni KESİNLİKLE KAPALI** olmalı
- IP whitelist aktif (sunucunun statik IP'si)
- Mainnet ve testnet için ayrı anahtarlar
- Anahtar rotasyonu: 90 günde bir
- Kompromise şüphesi → derhal API key'i Binance'te disable et

### Sırların Saklanması
- `.env` dosyası `.gitignore`'da
- Production'da: Docker secrets veya HashiCorp Vault önerilir
- Encryption key (Fernet): `ENCRYPTION_KEY` ile config içindeki hassas alanlar şifrelenir

## Network Güvenliği

```bash
# Firewall (UFW)
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp           # SSH
sudo ufw deny 5432/tcp          # Postgres - dış dünyaya kapalı
sudo ufw deny 6379/tcp          # Redis - dış dünyaya kapalı
sudo ufw deny 3000/tcp          # Grafana - SSH tunnel ile eriş
sudo ufw enable
```

Grafana, MLflow gibi servislere SSH tunnel ile eriş:
```bash
ssh -L 3000:localhost:3000 user@server
```

## Container Güvenliği

- Tüm container'lar non-root user (`macts`, uid 1000) ile çalışır
- Read-only mount'lar nerede mümkünse kullanılır (config volume)
- `--no-new-privileges` flag'i deploy'da eklenmeli
- Düzenli imaj güncelleme: `docker compose pull && docker compose up -d`

## Audit Trail

Tüm trade kararları PostgreSQL `audit_log` tablosuna ve Kafka `macts.audit.log` topic'ine yazılır:
- Append-only (DELETE yetkisi yok)
- Min 365 gün retention
- Forensic analiz için indexlenmiş

## Incident Response

Bir güvenlik olayı şüphesinde:
1. **Binance API key'i derhal disable et**
2. `macts emergency-stop` çalıştır
3. Kafka audit log'undan anomali ara
4. Tüm sırları rotate et (`.env` regenerate)
5. Postmortem raporu yaz
