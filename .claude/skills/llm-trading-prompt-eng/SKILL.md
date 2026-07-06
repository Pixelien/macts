---
name: llm-trading-prompt-eng
description: MACTS'ta LLM trading analiz promptu yazma/versiyonlama kuralları. "Prompt değiştir", "yeni prompt versiyonu", "few-shot ekle", "LLM çıktı şeması" taleplerinde kullanılır. Şablonlar config/prompts/ altında, şema src/models/schemas.py::AIAnalysis.
---

# LLM Trading Prompt Engineering (MACTS)

## Dosya düzeni ve versiyonlama

- Şablonlar: `config/prompts/{ad}_v{N}.yaml` (örn. trading_analysis_v1.yaml)
- **Yayınlanmış versiyon IMMUTABLE'dır** — davranış değişikliği = yeni dosya
  (v2). Bandit/MLflow karşılaştırması `AIAnalysis.prompt_version` alanına
  dayanır; aynı ada farklı içerik yazmak metrikleri anlamsızlaştırır.
- Aktif versiyon: `config/llm_config.yaml -> prompt_version`
- Şablon formatı (`src/core/llm/prompts.py::PromptTemplate`):
  `version`, `system_prompt`, `few_shot: [{user, assistant}, ...]`

## Çıktı şeması (değiştirilemez sözleşme)

`src/models/schemas.py::AIAnalysis` — direction: long|short|neutral,
confidence: 0-1, reasoning (<=60 kelime), risk_flags: [str],
time_horizon: 1h|4h|1d. Şemayı genişletmek istiyorsan önce AIAnalysis'i
güncelle + test ekle + tüketicileri (signal_generation) kontrol et.

## Prompt yazım kuralları (probe bulgularıyla)

- System prompt "ONLY a JSON object, no prose, no markdown fences" içermeli;
  yine de parse `utils.extract_json` ile toleranslı yapılır (thinking
  blokları, fence'ler) — İKİSİ BİRDEN gerekli.
- Feature verisi user mesajında STRUCTURED JSON olarak verilir (serbest
  metin değil) — build_messages bunu otomatik yapar.
- Few-shot: 2-3 örnek, GERÇEKLEŞMİŞ senaryolardan (uydurma değil). Her
  örnek şemaya birebir uyan assistant cevabı içermeli. Farklı direction'lar
  kapsanmalı (v1: short + neutral; v2'ye long örneği eklenebilir).
- Confidence kalibrasyonu için kural yaz ("0.8+ requires multiple aligned
  signals", "prefer neutral when indicators conflict") — probe'da temkinli
  modeller tercih edildi.

## Yeni versiyon yayınlama akışı

1. `config/prompts/{ad}_v{N+1}.yaml` oluştur
2. `scripts/nim_model_probe.py`'yi yeni prompt'la çalıştır (5+ istek,
   parse >= %95 şartı)
3. Unit test: şablonun yüklenebildiğini doğrula (test_llm_core deseni)
4. `llm_config.yaml`'da prompt_version'ı güncelle VEYA bandit koluna ekle
5. STATUS.md + bu skill'i güncelle; eski versiyonun dosyasını SİLME
   (geçmiş metriklerin referansı)
