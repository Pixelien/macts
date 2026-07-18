---
name: continuous-improvement-loop
description: MACTS AI Analyst kendi kendini geliştirme döngüsü — tahmin-vs-gerçek karşılaştırma job'ının mantığı, metrik isimlendirme konvansiyonu ve bandit/A-B seçim mekanizmasının nasıl güncelleneceği. "Accuracy düşük", "outcome job", "bandit ekle", "prompt karşılaştır", "model performansı" taleplerinde kullanılır.
---

# Continuous Improvement Loop (MACTS AI Analyst)

## Veri akışı (mevcut, Aşama 4 ilk yarı)

1. Her `analysis_published` -> `llm_prediction` tablosuna kayıt:
   entry_price (kline stream'den son kapanış), deadline = ts + time_horizon.
   Kod: `src/agents/ai_analyst/outcome.py::PredictionStore.record_prediction`
2. Agent içi `_outcome_loop` (~300s tick): `deadline <= now() AND NOT evaluated`
   kayıtları çeker, `classify_outcome` ile sınıflar, `llm_prediction_outcome`a
   yazar, `evaluated=TRUE` işaretler.
3. Sınıflandırma kuralı (`classify_outcome`, saf/testli):
   - return_pct > +flat_band (%0.10) -> "up"; < -band -> "down"; arası "flat"
   - correct: long↔up, short↔down, neutral↔flat
   - eval_delay_seconds kaydedilir (değerlendirme vadeden ne kadar sonra yapıldı)
4. Fiyat kaynağı: `stream:ticks.{sym}.kline.1m` (SADECE is_closed mumlar).
   DİKKAT: FeatureSnapshot'ta fiyat alanı YOK — fiyat işi her zaman kline'dan.

## Metrik isimlendirme konvansiyonu

- Sayaç: `macts_ai_analyst_outcomes_total{model_id, horizon, result}`
  (result: correct|incorrect — accuracy Grafana'da orandan türetilir,
  gauge olarak EXPORT EDİLMEZ; restart-güvenli olması için counter esas)
- Kota: `macts_ai_analyst_daily_quota_used` (gauge, limiter'dan)
- Gecikme: `macts_ai_analyst_latency_seconds` (histogram, sadece başarılı çağrı)
- Dashboard: `monitoring/grafana/dashboards/ai_analyst_performance.json`
  (uid: macts-ai-analyst). Accuracy sorguları `clamp_min(..., 1)` ile sıfıra
  bölünmeye karşı korunur — yeni panel eklerken aynı deseni kullan.

## SQL ile hızlı analiz (VPS)

```sql
-- Model × horizon doğruluk özeti (son 7 gün)
SELECT p.model_id, p.time_horizon,
       count(*) AS n,
       round(100.0 * count(*) FILTER (WHERE o.correct) / count(*), 1) AS acc_pct,
       round(avg(o.return_pct)::numeric, 3) AS avg_ret
FROM llm_prediction_outcome o JOIN llm_prediction p ON p.id = o.prediction_id
WHERE o.evaluated_at > now() - interval '7 days'
GROUP BY 1, 2 ORDER BY 1, 2;
```

## İlk değerlendirme bulguları (9 Tem 2026 — 2 gün, 2.991 outcome)

- Baz oranlar: flat %41.7 / down %33.0 / up %25.2. Model liftleri: long 1.20x,
  neutral 1.12x, short 1.03x — pozitif ama trade edilemez.
- TERS KALİBRASYON: confidence arttıkça isabet düştü (%43.8 → %22.9).
  v1 momentum-takipçisi; 1h kripto mean-reverting. Yeni prompt yazarken
  bu bulgu esas alınır (v2'de mean-reversion kuralları var).
- Model v1'de HİÇ 4h/1d seçmedi -> vade talimatı promptta açık olmalı.
- A/B karşılaştırma sorgusu (v1 vs v2):

```sql
SELECT p.prompt_version, p.direction, count(*) AS n,
       round(100.0*count(*) FILTER (WHERE o.correct)/count(*),1) AS acc
FROM llm_prediction_outcome o JOIN llm_prediction p ON p.id=o.prediction_id
WHERE p.ts > 'DEPLOY_TARIHI'
GROUP BY 1,2 ORDER BY 1,2;
```

Anlamlılık için kol başına min ~500 outcome bekle (~2 gün).

## NİHAİ SONUÇ (18 Tem 2026) — bu bölüm tarihsel kayıttır

12 gün / ~5.900 outcome: yön doğruluğu tüm kombinasyonlarda baz oran ±3
puan (rapor §9 tablosu). KARAR: sinyal entegrasyonu iptal, bandit iptal,
LLM_WEIGHT=0 kalıcı. Yeni rol: rejim/risk yorumcusu
(docs/AI_RISK_CONTEXT_DESIGN.md). Ölçülen ikinci kota sınırı: ~1000
istek/gün (429 duvarı deseni: saatte istek hacmi kadar 429 + sabah reset).
Yeni analiz eklerken accuracy'yi ASLA tek başına okuma — her zaman aynı
dönemin baz oranıyla (actual_direction dağılımı) kıyasla; ilk 4h "edge"i
(n=68'de %50) örneklem büyüyünce buharlaşan gürültüydü.

## Bandit / A-B genişletmesi (İPTAL — tarihsel referans)

Ön koşul: en az ~3-5 gün outcome verisi (horizon başına 100+ örnek) birikmeli;
daha azıyla kol karşılaştırması gürültüden ibaret olur.

Uygulama planı (yazılacağı zaman):
1. `llm_config.yaml`a `arms:` listesi ekle (model_id + prompt_version + weight)
2. `_analyze_symbol` başında epsilon-greedy seçim: %ε rastgele kol, kalanı
   son N günün accuracy lideri (SQL yukarıda). Seçilen kol AIAnalysis
   metadata'sına zaten yazılıyor (model_id + prompt_version) — ek şema gerekmez.
3. Kol ağırlıkları model_registry agent'ında versiyonlanır; MLflow'a her
   ağırlık güncellemesi bir run olarak loglanır.
4. Kota bütçesi DEĞİŞMEZ: kollar aynı 30 RPM / 2000-gün bütçesini paylaşır.
5. Deneysel kol adayı: z-ai/glm-5.2 (probe geçti, düşük ağırlıkla başla ~%10).

## Bilinen sınırlamalar (dürüst notlar)

- Exit fiyatı vade anının değil, vade sonrası ilk değerlendirme tick'inin
  fiyatıdır (sapma <= ~5 dk + kline gecikmesi; eval_delay_seconds ile izlenir).
  Daha hassas istenirse InfluxDB'den tarihi kline sorgusu eklenebilir.
- Restart'ta bekleyen tahminler kaybolmaz (Postgres'te) ama _last_close
  boş başlar; ilk kapanan mumla (~1 dk) değerlendirme devam eder.
- Cache hit oranı normal akışta ~0'dır (feature snapshot her dk değişir);
  cache'in gerçek değeri restart/çift-tetikleme korumasıdır.
