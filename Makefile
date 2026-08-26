# =================================================================
# AI Opportunity Radar — Makefile
# =================================================================
# Convenience entrypoints. Real implementation lives in each service.
# =================================================================

PYTHON       ?= python3
PIP          ?= python3 -m pip
COMPOSE      ?= docker compose
BACKEND_DIR  := backend
FRONTEND_DIR := frontend

.DEFAULT_GOAL := help

.PHONY: help install dev backend frontend test test-backend test-frontend \
        lint format migrate seed docker-up docker-down docker-logs clean \
        n8n-sync n8n-validate

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

install: ## Install local dependencies (backend + frontend)
	$(MAKE) install-backend
	$(MAKE) install-frontend

install-backend: ## Install backend Python dependencies
	cd $(BACKEND_DIR) && $(PIP) install -e ".[dev]"

install-frontend: ## Install frontend Node dependencies
	cd $(FRONTEND_DIR) && npm install

dev: ## Run backend + frontend locally (no docker)
	$(MAKE) -j2 dev-backend dev-frontend

dev-backend: ## Run FastAPI dev server
	cd $(BACKEND_DIR) && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend: ## Run Next.js dev server
	cd $(FRONTEND_DIR) && npm run dev

test: ## Run all tests
	$(MAKE) test-backend
	$(MAKE) test-frontend

test-backend: ## Run backend pytest suite
	cd $(BACKEND_DIR) && pytest -q

test-frontend: ## Run frontend tests
	cd $(FRONTEND_DIR) && npm test --silent

lint: ## Lint everything
	cd $(BACKEND_DIR) && ruff check app tests
	cd $(FRONTEND_DIR) && npm run lint

format: ## Format everything
	cd $(BACKEND_DIR) && ruff format app tests
	cd $(FRONTEND_DIR) && npm run format 2>/dev/null || true

migrate: ## Run alembic migrations
	cd $(BACKEND_DIR) && alembic upgrade head

seed: ## Seed demo fixtures
	cd $(BACKEND_DIR) && python -m app.scripts.seed

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