---
name: macts-agent-scaffold
description: MACTS projesine yeni bir agent eklerken izlenecek standart prosedür. "Yeni agent ekle", "agent iskeleti oluştur", "X agent'ını kur" gibi taleplerde kullanılır. Dizin yapısı, BaseAgent konvansiyonu, CLI registry, docker-compose girdisi, Redis Streams isimlendirme, feature flag, Prometheus metrikleri ve test iskeleti kurallarını içerir.
---

# MACTS Agent Scaffold

Bu repoya yeni bir agent eklerken aşağıdaki adımları SIRASIYLA uygula.
Referans implementasyonlar: `src/agents/ai_analyst/` (tam örnek, Faz 3),
`src/agents/feature_engineering/` (stream tüketim desenleri),
`src/agents/circuit_breaker/` (minimal iskelet).

## 1. Dizin yapısı

```
src/agents/{agent_adi}/
├── __init__.py      # Agent sınıfını re-export eder
├── agent.py         # {AgentAdi}Agent(BaseAgent)
└── {yardimci}.py    # Saf (pure) mantık ayrı modüle — unit test edilebilirlik için
```

Kural: Redis/ağ gerektirmeyen mantığı (zamanlayıcı, hesaplama, parse) agent.py
dışına saf modül olarak çıkar. Örnek: `ai_analyst/scheduler.py` — hiç ağ
bağımlılığı yok, 8 unit test ile korunuyor.

## 2. agent.py konvansiyonu

- `from src.agents.base import BaseAgent, run_agent`
- Class attribute'ları: `agent_name = "snake_case_ad"`, `heartbeat_interval`
- Override sırası: `_initialize()` → `_run()` → `_shutdown()` → `_health_check()`
- `_run()` içinde ana döngü `while not self._stop_event.is_set():` ile döner;
  bekleme `asyncio.wait_for(self._stop_event.wait(), timeout=...)` deseni ile
  (böylece stop sinyali beklemeyi anında keser)
- Arka plan görevleri `self._tasks.append(asyncio.create_task(...))` ile eklenir
  — BaseAgent cleanup'ta bunları otomatik iptal eder
- Modül docstring'i Türkçe, şu bölümleri içerir: amaç, veri akışı, Tüketim
  (stream listesi), Yayınladığı (stream listesi)
- Dosya sonu: `if __name__ == "__main__": asyncio.run(run_agent(XAgent))`

## 3. Stream isimlendirme

- Genel format: `stream:{topic}.{alt_konu}` — sembol içeriyorsa sembol
  KÜÇÜK harfle: `stream:features.btcusdt`, `stream:ai_analysis.btcusdt`
- Stream adları modül seviyesinde sabit/fonksiyon olarak tanımlanır:
  `def features_stream(symbol): return f"stream:features.{symbol.lower()}"`
- Universe takibi gereken agent'larda: `stream:universe.snapshot`'a
  `from_beginning=True` ile abone ol, `_apply_universe()` deseni ile
  (JSON string veya list olarak gelebilir — iki tipi de parse et,
  örnek: `ai_analyst/agent.py::_apply_universe`)

## 4. Konfigürasyon ve feature flag

- Sırlar ve flag'ler env'den okunur, `config.example.yaml`a YAPISAL config
  eklenir (AppConfig'e alan ekliyorsan `src/core/config/loader.py`'da
  pydantic modeli de ekle — DİKKAT: AppConfig'in tüm alanları zorunlu,
  yeni alan eklersen VPS'teki config.yaml'ı da güncellemek gerekir;
  kırılma riskini önlemek için yeni agent'larda önce env-only başla)
- Bool env parse için hazır yardımcı: `ai_analyst.agent.parse_bool_env`
- Feature flag deseni: flag kapalıyken agent container'ı healthy kalır ama
  hiçbir şey yapmaz (idle bekleme döngüsü) — sistem eski davranışıyla devam
  eder. Örnek: `ENABLE_AI_ANALYST`

## 5. CLI registry

`src/cli.py` içindeki `AGENT_REGISTRY` dict'ine satır ekle:
```python
"agent_adi": "src.agents.agent_adi.agent:AgentAdiAgent",
```
Doğrulama: `python -m src.cli agent list`

## 6. docker-compose.yml servis girdisi

`<<: *agent-base` anchor'ını kullan (Dockerfile.agent, ortak volume/network
otomatik gelir). `AGENT_NAME` zorunlu; agent'a özel env'leri
`${VAR:-varsayilan}` formatıyla geçir:

```yaml
  agent-ai-analyst:
    <<: *agent-base
    container_name: macts-agent-ai-analyst
    environment:
      AGENT_NAME: ai_analyst
      ENABLE_AI_ANALYST: ${ENABLE_AI_ANALYST:-false}
```

NOT: Bu VPS'te docker-compose v1 sözdizimi kullanılıyor (`docker-compose`,
`docker compose` değil).

## 7. Prometheus metrikleri

- BaseAgent zaten şunları verir: `macts_agent_up`, heartbeat, messages,
  errors — bunları TEKRAR tanımlama
- Agent'a özel metrikler modül seviyesinde, `macts_{agent}_{metrik}` adıyla:
  Counter'larda `status` etiketi (ok|error|skipped) konvansiyonu kullan
- Port 8000 BaseAgent tarafından otomatik açılır

## 8. Test iskeleti

- `tests/unit/test_{agent_adi}.py` — saf modülleri ve şemaları test et,
  Redis/ağ gerektiren kısımları değil (onlar tests/integration/)
- Mesaj şeması eklediysen `src/models/schemas.py`'ye `BaseMessage` alt sınıfı
  olarak ekle ve geçersiz payload'ların reddedildiğini test et
- Çalıştırma: `python3 -m pytest tests/unit/test_{agent_adi}.py -q`
- Teslimattan önce TÜM suite'i çalıştır (regresyon):
  `python3 -m pytest tests/unit/ -q`

## 9. Dokümantasyon

Her agent eklemesinde güncelle: `STATUS.md` (mevcut durum),
`docs/ROADMAP.md` (faz ilerlemesi), gerekirse `docs/ARCHITECTURE.md` (diyagram).
