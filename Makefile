# =============================================================================
# MACTS Makefile
# =============================================================================

.PHONY: help install dev-install lint format test test-unit test-integration \
        cov build up up-testnet up-paper up-live down logs ps clean \
        verify-testnet verify-paper verify-live promote-paper promote-live

# Varsayılan
.DEFAULT_GOAL := help

help: ## Yardım göster
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# -----------------------------------------------------------------------------
# Yerel Geliştirme
# -----------------------------------------------------------------------------

install: ## Production paketleri kur
	pip install -e .

dev-install: ## Dev paketler dahil kur
	pip install -e ".[dev]"
	pre-commit install

lint: ## Ruff lint
	ruff check src/ tests/

format: ## Ruff format
	ruff format src/ tests/

typecheck: ## Mypy type check
	mypy src/

test: test-unit test-integration ## Tüm testler

test-unit: ## Unit testler
	pytest tests/unit/ -v

test-integration: ## Integration testler (Docker servisleri açık olmalı)
	pytest tests/integration/ -v

cov: ## Coverage raporu
	pytest --cov=src --cov-report=html --cov-report=term-missing
	@echo "Rapor: file://$(PWD)/htmlcov/index.html"

# -----------------------------------------------------------------------------
# Docker
# -----------------------------------------------------------------------------

build: ## Docker imajını build et
	docker compose build

up: up-testnet ## Default: testnet modunda başlat

up-testnet: ## Testnet modunda başlat
	docker compose -f docker-compose.yml -f docker-compose.testnet.yml up -d

up-paper: ## Paper modunda başlat
	docker compose -f docker-compose.yml -f docker-compose.paper.yml up -d

up-live: ## Live modunda başlat (ONAYLANDIKTAN SONRA)
	@echo "⚠️  LIVE moda geçiyorsun. Tüm criteria'lar geçti mi?"
	@read -p "Devam için 'YES' yaz: " confirm; \
	if [ "$$confirm" != "YES" ]; then echo "İptal"; exit 1; fi
	docker compose -f docker-compose.yml -f docker-compose.live.yml up -d

down: ## Tüm servisleri durdur
	docker compose down

down-volumes: ## Servisleri ve volume'leri sil (DİKKAT: veri kaybı!)
	@read -p "TÜM VERİLERİ silecek. Emin misin? (evet/hayır): " confirm; \
	if [ "$$confirm" != "evet" ]; then echo "İptal"; exit 1; fi
	docker compose down -v

logs: ## Logları takip et
	docker compose logs -f --tail=100

logs-agent: ## Belirli bir agent'ın loglarını izle (AGENT=name)
	docker compose logs -f --tail=200 agent-$(AGENT)

ps: ## Servis durumlarını göster
	docker compose ps

# -----------------------------------------------------------------------------
# Verification
# -----------------------------------------------------------------------------

verify-testnet: ## Testnet criteria'larını kontrol et
	@echo "TODO: Testnet verification (uptime, error rate, vs.)"
	docker compose exec agent-monitoring python -m src.cli health

verify-paper: ## Paper trading criteria'larını kontrol et
	@echo "TODO: Paper verification"
	docker compose exec agent-monitoring python -m src.cli backtest verify-paper

verify-live: ## Live trading durumunu kontrol et
	@echo "TODO: Live verification"
	docker compose exec agent-monitoring python -m src.cli health

# -----------------------------------------------------------------------------
# Promotion
# -----------------------------------------------------------------------------

promote-paper: ## Testnet → Paper geçiş
	./scripts/promote_to_next_stage.sh testnet paper

promote-live: ## Paper → Live geçiş (DİKKAT!)
	./scripts/promote_to_next_stage.sh paper live

# -----------------------------------------------------------------------------
# Maintenance
# -----------------------------------------------------------------------------

clean: ## Geçici dosyaları temizle
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml .coverage

backup-db: ## PostgreSQL'i yedekle
	mkdir -p backups
	docker compose exec -T postgres pg_dump -U macts_user macts | gzip > backups/postgres-$$(date +%Y%m%d-%H%M%S).sql.gz
	@echo "✓ Backup: backups/postgres-$$(date +%Y%m%d-%H%M%S).sql.gz"

# -----------------------------------------------------------------------------
# Misc
# -----------------------------------------------------------------------------

config-validate: ## Konfigürasyonun geçerliliğini kontrol et
	python -m src.cli config validate

version: ## Versiyonu göster
	@python -m src.cli version
