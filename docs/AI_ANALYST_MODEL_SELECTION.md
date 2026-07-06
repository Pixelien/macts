# AI Analyst — NVIDIA NIM Model Seçim Raporu (Aşama 0)

> **Durum**: KARAR KİLİTLENDİ — canlı probe tamamlandı (§8), Aşama 1 onayı bekleniyor.
> **Tarih**: 5 Temmuz 2026 (probe: aynı gün, VPS/Frankfurt)
> **Kapsam**: Faz 3 — AI Analyst Katmanı, model seçimi ve kota stratejisi
> **Hedef dosya konumu**: `docs/AI_ANALYST_MODEL_SELECTION.md`

---

## 1. Yönetici Özeti

| Karar | Seçim |
|---|---|
| **Birincil model** | `nvidia/nemotron-3-super-120b-a12b` ✅ probe geçti (5/5 parse, p50 12.7s) |
| **Yedek model (fallback-1)** | `deepseek-ai/deepseek-v4-pro` ✅ probe geçti (5/5 parse, p50 13.0s) |
| **Deneysel bandit kolu (opsiyonel)** | `z-ai/glm-5.2` ✅ probe geçti (5/5 parse, p50 32.6s — 15 dk kadansta kabul edilebilir) |
| **Elenen** | `qwen/qwen3.5-397b-a17b` ❌ HTTP 500 + p95 84s (kriter ihlali) |
| **Fallback-2** | Kural tabanlı: LLM'siz, yalnızca ML sinyali (mevcut Faz 2 davranışı) |

> **Probe sonrası revizyon notu**: İlk taslakta yedek GLM idi. Probe verisi DeepSeek-V4-Pro'nun
> aynı parse güvenilirliğinde ve 2.5× daha hızlı olduğunu gösterdi; "farklı sağlayıcı =
> bağımsız arıza alanı" gerekçesi her ikisi için de geçerli olduğundan yedek koltuğu
> DeepSeek'e geçti, GLM deneysel kola alındı. Ayrıca üçüncü taraf kaynaklardaki model
> ID'leri eksik/eskiydi; doğru ID'ler `/v1/models` endpoint'inden canlı çekildi.
| **AI Analyst kadansı** | Sembol başına **15 dakikada 1** (dakikalık DEĞİL — gerekçe §5) |
| **Rate limit bütçesi** | Token bucket: **30 istek/dk** (40 RPM'in %75'i), günlük soft cap: 2.000 istek |
| **max_tokens** | ≥ 4096 (thinking/reasoning modelleri için zorunlu — §4.3) |

**Kritik ön koşul**: Model kimlikleri (ID) canlı probe ile doğrulanmadan kod yazılmayacak.
Bağımsız bir Nisan 2026 benchmark'ına göre NIM kataloğundaki modellerin ~%38'i API'de
404 dönüyor — katalogda görünmek, endpoint'in çalıştığı anlamına gelmiyor.
Bu rapordaki model ID'leri üçüncü taraf kaynaklardan derlendi; `scripts/nim_model_probe.py`
VPS'te çalıştırılıp sonuçları §8'e işlenmeden nihai karar verilmemelidir.

---

## 2. Ücretsiz Katman Gerçekleri (Temmuz 2026)

Kota mimarisinin tamamı bu kısıtlar üzerine kurulu:

| Kısıt | Değer | Kaynak güvenilirliği |
|---|---|---|
| Rate limit | **~40 istek/dk**, API anahtarı bazında **global** (tüm modeller toplamda paylaşır) | NVIDIA forum yanıtları + çok kaynaklı teyit |
| Kredi sistemi | Belirsiz/geçiş halinde: bazı kaynaklara göre kredi sistemi kaldırıldı, saf rate limit; bazılarına göre 1.000–5.000 kredi hâlâ geçerli | Çelişkili — probe ile 402 hatası izlenmeli |
| Limit artırma | Ücretsiz katmanda **resmi yol yok** ("There is no official way to circumvent this rate limit") | NVIDIA forum, resmi yanıt |
| Ağ gecikmesi | Frankfurt → ABD veri merkezleri: **+80–150 ms** taban gecikme | Çok kaynaklı |
| Model gecikmesi | Reasoning modelleri: 15–25s; orta boy modeller: 7–10s; ücretsiz katmanda 27s'ye varan spike'lar raporlanmış | Bağımsız benchmark |
| ToS sınırı | Ücretsiz endpoint yalnızca **development / test / research / evaluation** için; "production" (gerçek son kullanıcı, ticari işlem) AI Enterprise gerektirir | Resmi FAQ |

**ToS değerlendirmesi (MACTS özelinde)**: Sistem paper/testnet modunda, gerçek para
işlemi yok, tek kullanıcı araştırma projesi → mevcut kapsam **uyumlu**. Ancak ileride
gerçek parayla canlı işleme geçilirse LLM katmanının kararlara girdi vermesi "production"
sınırını zorlar. O aşamada seçenekler: (a) self-hosted NIM container (16 GPU'ya kadar
dev/test ücretsiz ama GPU maliyeti bizde), (b) ücretli managed sağlayıcıya geçiş
(OpenRouter/Together vb. — istemci OpenAI-uyumlu olduğu için base_url değişikliğiyle),
(c) LLM katmanını canlıda kapatmak (`ENABLE_AI_ANALYST=false`). İstemci katmanı bu
taşınabilirliği baştan destekleyecek şekilde tasarlanacak.

**En kritik mimari sonuç**: 40 RPM limiti *anahtar bazında global* olduğu için
**fallback zinciri de aynı kotayı paylaşır**. Birincil model 429 verdiğinde yedek modele
geçmek kotayı rahatlatmaz — 429'un sebebi bizim kotamızsa fallback denemesi de yanar.
Bu yüzden `fallback_chain.py` 429 (kota) ile 5xx (servis hatası) durumlarını **farklı**
ele almalı: 5xx → yedek modele geç; 429 → backoff + bu turda LLM'siz devam (fallback-2).

---

## 3. Katalog Durumu: Prompttaki Aday Liste Güncel Değil

Görev tanımındaki adaylar (llama-3.1-nemotron-70b/253b, mixtral-8x22b, phi-3.5-moe,
qwen2.5-72b) 2024–2025 nesli. Aralık 2025 – Nisan 2026 arasında katalog büyük ölçüde
yenilendi:

- **Nemotron 3 ailesi** (Aralık 2025 → Mart 2026): Nano Omni (30B, multimodal),
  **Super 120B** (12B aktif, MoE, 11 Mart 2026, agentic/tool-calling odaklı, ~5x throughput),
  Ultra (500B sınıfı, 1M+ context). Eski Llama-Nemotron serisinin halefi.
- **DeepSeek V4 / V4-Flash / V4-Pro**: V4-Pro CoT reasoning odaklı; V4-Flash 284B MoE,
  1M context, hız odaklı (ancak bir bağımsız testte ücretsiz katmanda timeout raporlanmış).
- **Qwen 3.5** (Mart 2026): 397B toplam / 17B aktif.
- **GLM-5 / GLM-5.1** (Zhipu AI): yapılandırılmış çıktı / function calling için
  bağımsız kaynaklarda Nemotron-3-Super ile birlikte ilk sırada önerilen model.
- **Kimi K2.5/K2.6**: 200K–1M context, uzun doküman odaklı.
- **MiniMax M2.7**: reasoning güçlü ama analitik modda ~88s gecikme → bizim döngüye uygunsuz.

---

## 4. Kriter Bazlı Değerlendirme

Puanlama: ● güçlü / ◐ orta / ○ zayıf. Kaynaklar: NVIDIA resmi dokümantasyonu,
bağımsız Nisan 2026 NIM benchmark'ı (21 model, gerçek üretim iş yükü), çok kaynaklı
katalog analizleri. **Canlı probe ile teyit edilecek.**

| Kriter | Nemotron-3-Super-120B | GLM-5.1 | DeepSeek-V4-Pro | Qwen-3.5-397B | Gemma-4-31B |
|---|---|---|---|---|---|
| Akıl yürütme kalitesi (indikatör yorumu) | ● en zor çok-kaynaklı analiz görevlerinde benchmark birincisi | ◐/● güçlü genel model | ● CoT reasoning odaklı | ● ham benchmark lideri | ◐ basit görevler için |
| Yapılandırılmış çıktı (JSON/function calling) | ● bağımsız kaynaklarda 1. öneri | ● bağımsız kaynaklarda 1. öneri (Nemotron ile birlikte) | ◐ thinking modu parse riskini artırır | ◐ | ◐ |
| Context window | ● (Nemotron 3 ailesi 1M'e kadar) | ● | ● | ● | ◐ |
| Rate limit / kota verimi | ● 12B aktif MoE → düşük compute/istek | ◐ | ○ büyük reasoning modeli, istek başına ağır | ○ | ● en ucuz |
| Gecikme | ◐ ~24.6s (reasoning tier) — 15 dk kadansta kabul edilebilir | ◐ orta | ○ thinking + büyük model | ◐ | ● hızlı |
| Kararlılık / halüsinasyon | ● yapılandırılmış görevlerde tutarlı; NVIDIA birinci parti → endpoint'in 404/kaldırılma riski en düşük | ◐ | ◐ thinking çıktısı token bütçesini yiyip yapıyı bozabiliyor | ◐ | ◐ |
| Fallback uygunluğu | — (birincil) | ● farklı sağlayıcı (Zhipu) → bağımsız arıza alanı | ◐ deneysel kol | ◐ | ◐ acil ucuz yedek |

### 4.1 Birincil: `nvidia/nemotron-3-super-120b` — gerekçe

1. **Görev profili birebir örtüşüyor**: bizim görev "çok kaynaklı yapılandırılmış veriden
   (14 indikatör + N mum) gerekçeli, JSON şemalı karar üretmek" — yani agentic/tool-calling
   profili. Nemotron-3-Super tam bu profil için eğitilmiş ve bağımsız üretim benchmark'ında
   en zor çok-servisli analiz problemlerini kazanan model.
2. **Kota ekonomisi**: 120B toplam / 12B aktif MoE → istek başına compute düşük. Ücretsiz
   katmanda "istek başına ağırlık" gecikme spike'larını da belirliyor.
3. **Operasyonel risk en düşük**: NVIDIA'nın kendi amiral modeli — kataloğun %38'i 404
   dönerken birinci parti modelin endpoint'inin kalkma/bozulma olasılığı en düşük.
4. **Bilinen zayıflığı yönetilebilir**: ~25s gecikme dakikalık döngüye uymaz ama zaten
   §5'te kadansı 15 dk'ya çekiyoruz; `time_horizon` şeması (1h/4h/1d) da bunu destekliyor.

### 4.2 Yedek: `zhipuai/glm-5.1` — gerekçe

- Yapılandırılmış çıktıda Nemotron ile birlikte ilk sırada önerilen model → JSON şema
  disiplini fallback'te de korunur.
- **Farklı sağlayıcı** (Zhipu): Nemotron endpoint'ine özgü bir arıza GLM'i etkilemez.
- Not: fallback yalnızca **5xx/model-hatası** durumunda devreye girer; 429'da girmez (§2).

### 4.3 Deneysel kol: `deepseek-ai/deepseek-v4-pro` — koşullu

Aşama 4'teki epsilon-greedy bandit'e düşük ağırlıkla (örn. %10) üçüncü kol olarak
eklenebilir; CoT reasoning'in finansal yorumda fark yaratıp yaratmadığını gerçek
sonuç verisiyle ölçeriz. **Zorunlu ayarlar**: `max_tokens ≥ 4096` (thinking modelleri
görünür akıl yürütmeye ~1500 token harcayıp yapılandırılmış cevabı üretemeden
kesilebiliyor) ve sıkı pydantic parse + tek retry. Probe'da parse başarısı <%95 çıkarsa
bu kol iptal.

### 4.4 Elenenler

- **MiniMax M2.7**: analitik modda ~88s — kadansımıza uygunsuz.
- **DeepSeek-V4-Flash**: bağımsız testte ücretsiz katmanda 180s timeout raporu — probe'da
  temiz çıkarsa yeniden değerlendirilebilir.
- **Eski nesil (llama-3.1-nemotron-70b vb.)**: halefleri katalogda; yeni entegrasyonu
  eski nesle bağlamak teknik borç.
- **Gemma-4-31B**: kalite tavanı düşük; yalnızca "acil ucuz yedek" senaryosu için not edildi.

---

## 5. Kota Bütçesi ve Kadans Tasarımı

Dakikalık döngü ücretsiz katmanda **matematiksel olarak imkânsız**:
20 sembol × dakikada 1 = 20 istek/dk = kotanın %50'si (cache miss varsayımıyla),
üstüne 15–25s model gecikmesi + Frankfurt→ABD 80–150ms ağ gecikmesi.

**Tasarım**: AI Analyst, sembol başına **15 dakikada 1** analiz üretir; dakikalık sinyal
üretimi ML modelinden kesintisiz akmaya devam eder (Aşama 5'teki "LLM eksikse ML-only
devam" ilkesinin doğal hali). Şemadaki `time_horizon` (1h/4h/1d) zaten dakikalık LLM
görüşü gerektirmiyor — LLM'in rolü rejim/bağlam yorumu, tik bazlı sinyal değil.

| Kalem | Hesap | Sonuç |
|---|---|---|
| Taban yük | 20 sembol / 15 dk | ~1,33 istek/dk ortalama |
| Burst koruması | Semboller 15 dk pencereye yayılır (staggered scheduling) | anlık ≤ 2–3 istek/dk |
| Günlük taban | 20 × 96 analiz | 1.920 istek/gün |
| Cache hedefi | Aynı sembol + aynı feature snapshot + aynı prompt versiyonu → Redis TTL cache | ≥ %20 tasarruf hedefi |
| Token bucket | 40 RPM × 0,75 | **30 istek/dk** tavan |
| Günlük soft cap | `usage_tracker` + Grafana alarm | 2.000 istek/gün |
| Yedek kapasite | Bandit deneyleri + retry + manuel test | bütçe içinde kalan ~%25 |

Bu tasarımla bandit mekanizması (Aşama 4) ek model kollarını da aynı bütçe içinde
deneyebilir — ör. isteklerin %80'i birincil, %10 GLM-5.1, %10 DeepSeek-V4-Pro.

---

## 6. Canlı Probe Planı (`scripts/nim_model_probe.py`)

Bu ortamdan NVIDIA API'ye erişim yok (API anahtarı + ağ kısıtı); probe **VPS'te**
çalıştırılacak. Script rapora ekli teslim edildi. Her aday model için:

1. **Erişilebilirlik**: endpoint 200 mü, 404 mü? (kataloğun %38'i 404 — ilk elek bu)
2. **Yapılandırılmış çıktı**: gerçek bir feature snapshot'ıyla (BTCUSDT, 14 indikatör,
   son 20 mum) hedef JSON şemasını isteyip pydantic ile doğrulama — 5 tekrar,
   parse başarı oranı.
3. **Gecikme**: p50/p95 (5 tekrar üzerinden kaba tahmin).
4. **Kalite (manuel)**: `reasoning` alanı indikatör verisiyle tutarlı mı, uydurma
   seviye/değer var mı — çıktılar `probe_results/` altına kaydedilir, göz ile
   karşılaştırılır.
5. **Kota davranışı**: 429/402 hata gövdelerini kaydet (kredi sistemi belirsizliğini
   §2'de netleştirmek için).

**Kabul kriterleri**: erişilebilir + parse başarısı ≥ %95 + p95 gecikme < 60s.
Birincil aday kriterleri geçemezse tabloda sıradaki aday yükselir.

## 7. Sonraki Adımlar (onay sonrası)

1. Probe'u VPS'te çalıştır, sonuçları §8'e işle, model kararını kilitle.
2. Aşama 1: `src/agents/ai_analyst/` iskeleti (BaseAgent konvansiyonu, feature flag,
   circuit_breaker + model_registry entegrasyonu).
3. Aşama 2: `src/core/llm/` istemci katmanı + unit testler.
4. Claude Skills: `macts-agent-scaffold` ve `nvidia-nim-client` ilk ikisi, probe
   bulgularıyla birlikte yazılır (yaşayan doküman).

## 8. Canlı Probe Sonuçları

**Yürütme**: 5 Temmuz 2026, Kamatera VPS (Frankfurt), model başına 5 istek,
`max_tokens=4096`, `temperature=0.2`. Ham çıktılar: `probe_results/probe_20260705T181614Z.json`
ve `probe_20260705T182950Z.json`.

**İlk tur bulgusu**: Üçüncü taraf kaynaklardan derlenen 4 ID'nin 3'ü 404 döndü
("%38'i 404" uyarısı doğrulandı). Doğru ID'ler `GET /v1/models` ile canlı çekildi;
farklar: aktif parametre eki (`-a12b`, `-a17b`) ve org adı değişikliği (`zhipuai/glm-5.1`
→ `z-ai/glm-5.2`).

| Model | Erişim | Parse | Gecikme (s) | Karar |
|---|---|---|---|---|
| `nvidia/nemotron-3-super-120b-a12b` | ✅ | 5/5 (%100) | p50 12.7, min 8.3, max 13.6 | **BİRİNCİL** |
| `deepseek-ai/deepseek-v4-pro` | ✅ | 5/5 (%100) | p50 13.0, min 5.1, max 19.6 | **YEDEK (fallback-1)** |
| `z-ai/glm-5.2` | ✅ | 5/5 (%100) | p50 32.6, min 30.0, max 44.0 | Bandit deneysel kolu (düşük ağırlık) |
| `qwen/qwen3.5-397b-a17b` | ⚠️ | 4/5 (%80) | p50 ~55, max 84.0 | **ELENDİ** (HTTP 500 ×1, p95 > 60s) |

**Kalite gözlemleri (manuel inceleme)**:
- Dört modelin tümü aynı yönde (short) hemfikir — sample veri gerçekten bearish yapıdaydı,
  tutarlılık beklenen davranış.
- Halüsinasyon yok: tüm modeller prompttaki gerçek değerlere atıf yaptı (RSI 38.2,
  BB alt bandı 106950, ATR 385) — uydurma seviye/değer görülmedi.
- Nemotron en temkinli confidence aralığını verdi (0.62–0.68 vs diğerlerinde 0.72–0.78)
  ve son mumun yeşil kapandığını fark etti ("despite a small bullish candle") —
  veri okuma hassasiyeti işareti. Trading bağlamında temkinli kalibrasyon tercih sebebi.
- Qwen'in 500 hatası ("invalid type: unit variant...") sunucu tarafı serileştirme
  hatası — bizim payload'dan değil; yine de güvenilirlik kriterini ihlal ediyor.

**Gecikme bütçesi güncellemesi**: Birincil + yedek ikilisinin p95'i ~20s civarı —
rapordaki 60s tavanının çok altında. 15 dk kadans korunuyor (kota gerekçesi geçerli),
ancak gecikme artık kısıt değil.
