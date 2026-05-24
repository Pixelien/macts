# MACTS — Risk Yönetimi Politikası

> ⚠️ **VISION SPEC** — Bu doküman projenin **hedef durumunu** tanımlar, mevcut canlı sistemi değil.
> Mevcut durum için [STATUS.md](../STATUS.md), yol haritası için [ROADMAP.md](ROADMAP.md).
> Bu spec'in tamamı henüz uygulanmamıştır; bazı bölümler Faz 3+ tamamlandıkça hayata geçecektir.

---


> Bu doküman MACTS'in risk yönetimi ilkelerini ve uygulanan limitleri tanımlar. Risk Management Agent ve Circuit Breaker Agent bu politikayı uygular.

## 1. Pozisyon Boyutlandırma

### Fractional Kelly (Half-Kelly)

Her pozisyon için Kelly kriteri kullanılır ancak agresif overestimation riskine karşı **0.5x** ile çarpılır:

```
f* = 0.5 × (p × b - q) / b
```

Burada:
- `p` = kazanma olasılığı (modelin tahminine göre)
- `q` = kayıp olasılığı (1 - p)
- `b` = ortalama kazanç / ortalama kayıp oranı

### Hard Limit
- Tek pozisyon maksimum: **toplam sermayenin %1'i**
- Her ne olursa olsun aşılamaz, hesaplanan Kelly daha yüksek çıksa bile clamp edilir.

---

## 2. Zarar Limitleri (Drawdown Sınırları)

| Periyot | Limit | Aksiyon |
|---------|-------|---------|
| Günlük | %3 | Yeni pozisyon yok, mevcutları yönet |
| Haftalık | %7 | 24 saat cooldown |
| Aylık | %12 | Sistem manuel inceleme moduna geçer |

**Cooldown**: Bir limit aşıldığında, ilgili periyot sonuna kadar yeni pozisyon açılmaz. Cooldown PostgreSQL'e kaydedilir → restart-resilient.

---

## 3. Kaldıraç (Leverage) Politikası

### Volatiliteye Adaptif

```
ATR(14) / Close > 0.03  →  yüksek volatilite, max 2x
ATR(14) / Close ≤ 0.03  →  düşük volatilite, max 5x
```

Açık pozisyonlarda kaldıraç değiştirilemez (Binance kuralı). Yeni pozisyon açılırken mevcut volatiliteye göre belirlenir.

### Hard Limit
- Sistem geneli maksimum: **5x**
- Live modda canary aşamalarında daha düşük (config'den ayarlanır):
  - Canary %10 aşaması: max 2x
  - Canary %25 aşaması: max 3x
  - Canary %50+ aşaması: max 5x

---

## 4. Korelasyon Riski

Aynı yönde birden fazla pozisyon açılırken korelasyon kontrolü yapılır.

### Kural
- Pearson correlation (30 günlük log return üzerinden) **0.7'yi aşan** coinlerde aynı yönde pozisyon açılmaz.
- Korelasyon matrisi her 60 dakikada bir yeniden hesaplanır ve Redis'te cache'lenir.

### Örnek
- BTCUSDT long açıldı, ETHUSDT/BTCUSDT corr = 0.85 → ETHUSDT long açılamaz
- ETHUSDT short açılabilir (zıt yön)

---

## 5. VaR & CVaR

Portföy seviyesinde Value at Risk hesaplanır:

- **VaR 95%**: 95% güvenle 1 günlük maksimum beklenen kayıp
- **CVaR 95%**: VaR aşıldığında ortalama kayıp (tail risk)
- **Yöntem**: Historical simulation, 90 günlük rolling window
- **Hard limit**: VaR 95% > sermayenin %5'i ise yeni pozisyon yok

---

## 6. Flash Crash Koruması

### Tespit
5 saniyelik rolling window'da **%3+ ani fiyat hareketi**.

### Aksiyon (Circuit Breaker)
1. Tüm açık pozisyonlar piyasa emriyle kapatılır
2. Yeni emir kabul edilmez (`halt_new_orders`)
3. Telegram'a critical alert gönderilir
4. Manuel reset gerekir (auto-resume KAPALI)

---

## 7. Funding Rate Aware Trading

Long pozisyonlarda funding rate pozitifse trader zarar eder (long ↔ short ödeme). Sistem:

- 8 saatte bir gelen funding rate beklentisini sinyale ekler
- Funding rate > **0.1%** (annual ~110%) ise long pozisyon ek confidence cezası alır
- Funding rate < **-0.1%** ise short pozisyon ceza alır

---

## 8. Slippage Yönetimi

### Limit
Maksimum kabul edilebilir slippage: **10 bps (%0.10)**

### İhlal Durumu
- Limit emirlerde aşım olursa emir iptal edilir
- Piyasa emirlerinde önceden simulation ile tahmin yapılır; tahmini aşım varsa TWAP algoritmasına geçilir

---

## 9. Manuel Override

İnsan operatör her zaman aşağıdakileri yapabilir:

- `macts emergency-stop` → Tüm pozisyonları kapat ve sistemi durdur
- `macts pause-trading` → Yeni emir alma, mevcutları yönet
- `macts resume-trading` → Pause'u kaldır

Tüm manuel müdahaleler audit log'a kaydedilir.

---

## 10. Uyarı Eşikleri

| Olay | Severity | Kanal |
|------|----------|-------|
| Günlük zarar > %2 | Warning | Telegram |
| Günlük zarar > %3 (limit aşımı) | Critical | Telegram + Email |
| Flash crash | Critical | Telegram + Email + SMS |
| Exchange outage | Critical | Telegram + Email |
| Agent crash | Error | Telegram |
| Model degradation | Warning | Telegram |
| Korelasyon spike (0.95+) | Warning | Telegram |

---

## 11. Periyodik Risk Değerlendirmesi

- **Günlük**: Otomatik metrik raporu (Telegram)
- **Haftalık**: Sharpe, Sortino, max DD, win rate, profit factor raporu
- **Aylık**: Manuel risk komite incelemesi (insan operatör)

---

## 12. Risk Komite Onay Listesi (Live'a Geçmeden Önce)

- [ ] 30+ gün paper trading hatasız tamamlandı
- [ ] Sharpe Ratio ≥ 1.5
- [ ] Max Drawdown ≤ 15%
- [ ] Win Rate ≥ 52%
- [ ] Profit Factor ≥ 1.4
- [ ] Forward/Backtest Sharpe oranı ≥ 0.7 (overfitting kontrolü)
- [ ] Tüm agent'lar 99.9% uptime
- [ ] Circuit breaker tüm trigger'larda test edildi
- [ ] API key permissions: sadece "Futures Trading", "Read"; "Withdrawal" KAPALI
- [ ] IP whitelist aktif
- [ ] 2FA aktif
- [ ] Backup ve disaster recovery prosedürleri test edildi
- [ ] On-call rotation tanımlandı
