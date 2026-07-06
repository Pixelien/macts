---
name: nvidia-nim-client
description: MACTS projesinde NVIDIA NIM API'ye bağlanma standardı. "NIM entegrasyonu", "LLM çağrısı ekle", "NVIDIA API hatası", "model değiştir", "rate limit sorunu" gibi taleplerde kullanılır. Doğrulanmış model ID'leri, auth, kota gerçekleri, hata kodları, JSON mode kuralları ve fallback politikasını içerir. Tüm rakamlar 5 Temmuz 2026 canlı probe ile doğrulanmıştır.
---

# NVIDIA NIM Client (MACTS)

Karar kaynağı: `docs/AI_ANALYST_MODEL_SELECTION.md` (Aşama 0 raporu + canlı
probe sonuçları). Probe aracı: `scripts/nim_model_probe.py`. Bu skill'deki
rakamlar 5 Temmuz 2026'da Frankfurt VPS'ten canlı doğrulandı.

## Bağlantı

- Base URL: `https://integrate.api.nvidia.com/v1` (OpenAI-uyumlu)
- Auth: `Authorization: Bearer $NVIDIA_API_KEY` (`nvapi-` prefix'li)
- Anahtar SADECE env'den okunur (`.env` → compose `${NVIDIA_API_KEY:-}`),
  ASLA commit edilmez, ASLA loglanmaz
- Endpoint: `POST /chat/completions` (standart OpenAI şeması)

## Doğrulanmış model ID'leri (5 Tem 2026 probe)

| Rol | Model ID | Probe sonucu |
|---|---|---|
| Birincil | `nvidia/nemotron-3-super-120b-a12b` | 5/5 parse, p50 12.7s |
| Yedek (fallback-1) | `deepseek-ai/deepseek-v4-pro` | 5/5 parse, p50 13.0s |
| Deneysel (bandit) | `z-ai/glm-5.2` | 5/5 parse, p50 32.6s |
| KULLANMA | `qwen/qwen3.5-397b-a17b` | HTTP 500 ×1, p95 84s — elendi |

UYARI — model ID tuzakları (probe'da yaşandı):
- Üçüncü taraf kaynaklardaki ID'ler güvenilmez; kataloğun ~%38'i 404 döner.
  ID'yi HER ZAMAN canlı listeden doğrula:
  `GET /v1/models` (curl + jq/grep ile filtrele)
- ID formatı aktif parametre eki içerir: `-a12b`, `-a17b` (atlanırsa 404)
- Org adları değişebilir: `zhipuai/glm-5.1` → `z-ai/glm-5.2` oldu

## Kota gerçekleri (ücretsiz katman)

- ~40 istek/dk, API ANAHTARI BAZINDA GLOBAL — tüm modeller toplamda paylaşır.
  SONUÇ: fallback zinciri kotayı RAHATLATMAZ.
- MACTS token bucket tavanı: 30 RPM (%75 güvenlik payı), günlük soft cap 2.000
- Limit artırma yolu yok (NVIDIA resmi forum yanıtı)
- ToS: ücretsiz endpoint dev/test/research için; canlı para moduna geçişte
  bu katman ya kapatılır ya ücretli sağlayıcıya taşınır (istemci OpenAI-uyumlu,
  base_url değişikliği yeter)

## Hata kodları ve politika

| Kod | Anlamı | Politika |
|---|---|---|
| 404 | Model ID yanlış/kaldırılmış | `/v1/models`'ten doğrula, retry ETME |
| 429 | Kota aşımı (bizim anahtar) | Backoff + bu turda LLM'siz devam. FALLBACK MODELE GEÇME (aynı kota, o da yanar) |
| 402 | Kredi tükenmesi (belirsiz sistem) | Gövdesini logla, circuit breaker'a bildir |
| 5xx | Sunucu/model hatası | 1 retry → yedek modele geç (deepseek-v4-pro) |
| 200 + parse hatası | Model şemaya uymadı | 1 retry, yine bozuksa `status=error` say, yayınlama |

Ardışık hata eşiği aşılırsa karar MERKEZİ circuit_breaker agent'ındadır —
istemci kendi başına sessiz kalıcı devre kesmez (mimari kural).

## İstek kuralları (probe ile doğrulanmış)

- `max_tokens >= 4096` ZORUNLU — thinking modelleri (deepseek-v4-pro) görünür
  akıl yürütmeye ~1500+ token harcar, düşük bütçede yapılandırılmış cevap
  üretemeden kesilir
- `temperature: 0.2` (analiz tutarlılığı; probe'da confidence std sapması düşüktü)
- Sistem prompt'u "ONLY a JSON object, no prose, no markdown fences" der;
  yine de parse katmanı şunlara toleranslı olmalı (probe'daki `extract_json`):
  `<think>...</think>` blokları, ```json fence'leri, JSON öncesi/sonrası metin
- Çıktı şeması: `src/models/schemas.py::AIAnalysis` — pydantic ile doğrula,
  doğrulanamayan çıktı ASLA stream'e yayınlanmaz

## Yayın öncesi kontrol listesi (yeni model / prompt versiyonu)

1. `GET /v1/models` ile ID'yi doğrula
2. `scripts/nim_model_probe.py --models <id>` çalıştır (5 istek)
3. Kabul kriteri: erişilebilir + parse ≥ %95 + p95 < 60s
4. Sonucu `docs/AI_ANALYST_MODEL_SELECTION.md` §8 tablosuna ekle
5. model_registry üzerinden aktif konfigürasyonu güncelle (MLflow kaydı)
