# AI Analyst Rol Değişikliği Tasarımı: Yön Tahmincisi → Rejim/Risk Yorumcusu

> **Durum**: TASARIM — onay sonrası uygulanacak (kod bu dokümanla birlikte GELMEZ).
> **Tarih**: 18 Temmuz 2026
> **Dayanak**: §"Neden" bölümündeki 12 günlük canlı ölçüm.

## Neden

12 günde ~5.900 değerlendirilmiş tahmin üzerinde LLM'in yön doğruluğu, her
vade ve yön kombinasyonunda baz oranların ±3 puan bandında kaldı (nihai
tablo: docs/AI_ANALYST_MODEL_SELECTION.md §9). İki prompt versiyonu (v1
momentum, v2 mean-reversion + kalibrasyon) denenmiş, v2 kalibrasyonu
düzeltmiş ama baz oran üstü edge üretememiştir. Karar: **LLM sinyal
üretimine bağlanmaz (LLM_WEIGHT=0 kalıcı)**.

Ancak aynı ölçüm döngüsü, modelin `reasoning` ve `risk_flags` çıktılarının
nitel olarak tutarlı olduğunu gösterdi (halüsinasyonsuz, veriye bağlı).
LLM'lerin zayıf olduğu iş kısa vadeli fiyat yönü; güçlü olduğu iş çok
göstergeli durumun nitel sentezi. Rol değişikliği bu ayrıma dayanır.

## Yeni rol: Risk Context Provider

AI Analyst "yarın ne olacak" sorusunu bırakır, "şu an neredeyiz" sorusuna
cevap verir. Çıktısı sinyal DEĞİL, risk yönetimine bağlamdır.

### Yeni şema: `RiskContext` (AIAnalysis'in yerine geçmez, yanına gelir)

```
symbol, regime: trending_up|trending_down|ranging|volatile|squeeze,
regime_confidence: 0-1,
risk_flags: [str]           # ör. "funding aşırı pozitif", "hacim divergence"
suggested_posture: normal|reduced|defensive,
reasoning: str (<=60 kelime),
model_id, prompt_version, latency_seconds
```

`suggested_posture` üç değerlidir ve TAVSİYEDİR — pozisyon açtırmaz,
kapattırmaz; yalnızca risk_management'ın mevcut kurallarına çarpan önerir.

### Veri akışı

```
stream:features + kline buffer --> [AI Analyst / risk_context_v1 promptu]
    --> stream:risk_context.{symbol}
    --> [Risk Management] (opsiyonel tüketici, feature flag: ENABLE_LLM_RISK_CONTEXT=false)
```

Risk Management entegrasyonu 2 kademeli:
- **Kademe 1 (gölge mod)**: risk_management context'i yalnızca LOGLAR ve
  Postgres'e yazar; karar mantığına DOKUNMAZ. En az 1 hafta gölge veri.
- **Kademe 2 (etki modu, ayrı onayla)**: `suggested_posture=defensive` iken
  yeni pozisyon boyutu çarpanı (ör. 0.5x) — yalnızca kısıcı yönde etki
  edebilir, asla pozisyon büyütmez/açtırmaz (asimetrik güvenlik ilkesi).

### Ölçüm (yine outcome döngüsü — bu kez rejim için)

Rejim tahmini de doğrulanabilir: "trending_up" dediği pencerede
gerçekleşen volatilite ve yön dağılımı, "ranging" dediğiyle ayrışıyor mu?
Mevcut llm_prediction altyapısı küçük uyarlamayla (direction yerine regime
alanı) bunu ölçer. Kademe 2'ye geçiş şartı: rejim sınıflandırmasının
karışıklık matrisi rastgeleden anlamlı ayrışacak.

### Kota bütçesi

Yeni kadans (30 dk) ve 900/gün soft cap içinde ekstra maliyet YOK — aynı
çağrı, farklı prompt ve şema. v2 yön promptu emekli edilir (dosya kalır,
metrik geçmişi referansı).

## Uygulama sırası (onay sonrası, tek paket)

1. `RiskContext` şeması + `config/prompts/risk_context_v1.yaml`
2. ai_analyst: şema/stream değişimi + outcome döngüsünün rejim uyarlaması
3. risk_management: Kademe 1 gölge tüketici (feature flag'li)
4. Grafana: rejim dağılımı + gölge posture paneli
5. STATUS/ROADMAP + skill güncellemeleri
