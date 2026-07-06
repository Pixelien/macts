---
name: rate-limit-guardian
description: MACTS'ta rate-limitli dış servis entegre etme deseni (token bucket + backoff + cache + fallback + circuit breaker). "Rate limit sorunu", "kota aşımı", "429 hatası", "dış API entegrasyonu" taleplerinde kullanılır. Somut implementasyon src/core/llm/ altındadır.
---

# Rate Limit Guardian (MACTS)

Referans implementasyon: `src/core/llm/` (NVIDIA NIM için). Yeni bir
rate-limitli dış servis eklerken aynı desen kopyalanır.

## Katman sırası (her çağrıda)

1. **Cache** (`tracking.AnalysisCache`, Redis TTL) — çağrıyı hiç yapma.
   Anahtar: deterministik sha256 (`build_cache_key`: girdi + versiyon + model,
   sort_keys ile alan sırası bağımsız).
2. **Token bucket** (`rate_limiter.TokenBucketLimiter`) — NON-BLOCKING:
   `try_acquire()` False dönerse TUR ATLANIR, beklenmez. Trading sisteminde
   doğru davranış budur: sinyal üretimi asla dış servise takılmaz.
   Tavan = gerçek limitin %75'i (NIM: 40 RPM → 30). Günlük soft cap ayrıca.
3. **Fallback zinciri** (`fallback_chain.FallbackChain`) — hata SINIFINA göre:
   - 5xx/ağ → backoff'lu 1 retry → sıradaki sağlayıcı/model
   - 429    → FALLBACK YOK (kota anahtar bazında globalse yedek de yanar) → yükselt
   - 402    → fallback yok, yükselt (circuit breaker kararı)
   - 404    → retry etme, sıradakine geç + config hatası logla
4. **Backoff** (`utils.compute_backoff`): min(cap, base*2^n) ± %25 jitter.
5. **Usage tracking** (`tracking.UsageTracker`, Postgres) — BEST-EFFORT:
   kayıt hatası asıl işi asla bozmaz (try/except + warning log).
6. **Circuit breaker**: ardışık hata eşiği kararı MERKEZİ circuit_breaker
   agent'ındadır; istemci kendi başına kalıcı devre kesmez.

## Yeni entegrasyon checklist'i

- [ ] Gerçek limiti belgele + kaynağını yaz (forum/doc linki, tarih)
- [ ] Limit anahtar/IP/hesap bazında mı GLOBAL mi? (fallback politikasını belirler)
- [ ] Token bucket tavanını %75'e ayarla, config dosyasına koy (koda gömme)
- [ ] try_acquire ile non-blocking kullan; skip metriği ekle (status=rate_limited)
- [ ] Hata kodlarını typed exception'lara sınıfla (nvidia_client deseni)
- [ ] FakeClock ile limiter testi yaz: burst + rolling window (test_llm_core örnek)
- [ ] Kota tüketimini Postgres'e logla, Grafana paneli planla
