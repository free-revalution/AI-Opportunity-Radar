# =================================================================
# AI Opportunity Radar — Makefile (MVP)
# =================================================================
# Convenience entrypoints for the MVP surface.
#
# The legacy `frontend / lint / format / seed / backup / restore`
# targets were removed in the simplify refactor — frontend lives in
# `experimental/frontend/` and the backup / restore scripts were
# retired with their FREEZE consumers.
# =================================================================

PYTHON       ?= python3
PIP          ?= python3 -m pip
COMPOSE      ?= docker compose
BACKEND_DIR  := backend

.DEFAULT_GOAL := help

.PHONY: help install-backend dev-backend test-backend migrate \
        docker-up docker-down docker-logs clean \
        n8n-sync n8n-validate metrics-scrape

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

install-backend: ## Install backend Python dependencies
	cd $(BACKEND_DIR) && $(PIP) install -e ".[dev]"

dev-backend: ## Run FastAPI dev server
	cd $(BACKEND_DIR) && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test-backend: ## Run backend pytest suite
	cd $(BACKEND_DIR) && pytest -q

migrate: ## Run alembic migrations
	cd $(BACKEND_DIR) && alembic upgrade head

docker-up: ## Bring up full docker stack
	$(COMPOSE) up -d --build

docker-down: ## Tear down docker stack (keeps volumes)
	$(COMPOSE) down

docker-logs: ## Tail docker logs
	$(COMPOSE) logs -f --tail=200

clean: ## Remove caches and build artifacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .next -prune -exec rm -rf {} +
	rm -rf backend/.mypy_cache backend/.ruff_cache

n8n-validate: ## Validate every n8n/workflows/*.json (no network calls)
	cd $(BACKEND_DIR) && $(PYTHON) -m scripts.n8n_sync --dry-run

n8n-sync: ## Push n8n/workflows/*.json into the running n8n container (activate)
	cd $(BACKEND_DIR) && $(PYTHON) -m scripts.n8n_sync --activate

metrics-scrape: ## Curl the Prometheus metrics endpoint and grep for radar_*
	@curl -sf http://localhost:8000/api/metrics | grep '^radar_' | head -40 || echo "(backend not running on localhost:8000)"