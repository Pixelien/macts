# MACTS — Katkı Kuralları (İç Ekip)

> ⚠️ **VISION SPEC** — Bu doküman projenin **hedef durumunu** tanımlar, mevcut canlı sistemi değil.
> Mevcut durum için [STATUS.md](../STATUS.md), yol haritası için [ROADMAP.md](ROADMAP.md).
> Bu spec'in tamamı henüz uygulanmamıştır; bazı bölümler Faz 3+ tamamlandıkça hayata geçecektir.

---


## Kod Stili

- Python 3.11+ (type hints zorunlu)
- Format: `ruff format`
- Lint: `ruff check`
- Type-check: `mypy --strict`
- Docstring: Google style, **Türkçe** (sistem dahili dili)
- Test coverage: ≥%80

## Branch Stratejisi

- `main` — production-ready
- `develop` — entegrasyon branch
- `feature/*` — yeni feature
- `bugfix/*` — bug fix
- `hotfix/*` — production hotfix

## Pull Request

1. `develop`'tan branch al
2. Commit mesajları: [Conventional Commits](https://www.conventionalcommits.org/)
   - `feat: yeni feature`
   - `fix: bug düzeltme`
   - `refactor: refactor`
   - `test: test ekleme`
   - `docs: dokümantasyon`
3. PR şablonunu doldur (test, risk, rollback planı)
4. CI yeşil olmalı
5. En az 1 reviewer onayı
6. Squash merge

## Test Yazımı

Her yeni feature için:
- Unit test (mock'lar ile)
- Integration test (gerçek Redis/Postgres)
- Hata path'leri için negative test'ler

## Risk-Sensitive Değişiklikler

Şu alanlarda yapılan değişiklikler ek inceleme gerektirir:
- `risk_management/`
- `circuit_breaker/`
- `execution/`
- `models/schemas.py` (Order, Position, Signal)

Bu PR'lar minimum 2 reviewer onayı + risk komite üyesinin sign-off'u ile merge edilir.

## Sürüm Yönetimi

Semantic Versioning:
- `MAJOR` — breaking change
- `MINOR` — yeni feature, geriye dönük uyumlu
- `PATCH` — bug fix

Release tag'leri `v0.1.0`, `v0.2.0` formatında.
