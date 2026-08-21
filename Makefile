.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE ?= docker compose
BACKEND  := backend
FRONTEND := frontend

.PHONY: help doctor env up down logs ps migrate revision seed reset demo demo-reset \
        test test-backend test-frontend lint fmt typecheck api-shell db-shell \
        openapi verify-docs

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

doctor: ## Check the local toolchain and required files before anything else
	@bash scripts/doctor.sh

env: ## Create .env from .env.example if absent
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")
	@echo ".env present"

# ---------------------------------------------------------------- stack

up: env ## Build and start the full stack (loopback-only ports)
	$(COMPOSE) up --build -d
	@echo ""
	@echo "  API   http://127.0.0.1:8000/api/v1/health/ready"
	@echo "  Docs  http://127.0.0.1:8000/docs"
	@echo "  Web   http://127.0.0.1:5173"

down: ## Stop the stack (volumes preserved)
	$(COMPOSE) down

logs: ## Tail API logs
	$(COMPOSE) logs -f api

ps: ## Show container status
	$(COMPOSE) ps

# ---------------------------------------------------------------- database

migrate: ## Apply all migrations
	$(COMPOSE) run --rm api alembic upgrade head

revision: ## Autogenerate a migration. STREAM C ONLY. usage: make revision m="add x"
	$(COMPOSE) run --rm api alembic revision --autogenerate -m "$(m)"

seed: ## Seed the fixed-seed demo dataset
	$(COMPOSE) run --rm api python -m app.cli seed

reset: ## Remove demo-owned rows and re-seed. Leaves the schema in place
	$(COMPOSE) run --rm api python -m app.cli reset
	$(MAKE) seed

db-shell: ## psql into the database (not host-published; goes through the container)
	$(COMPOSE) exec postgres psql -U travelops -d travelops

# ---------------------------------------------------------------- demo

demo: ## Inject the bengaluru_storm scenario
	$(COMPOSE) run --rm api python -m app.cli inject --scenario bengaluru_storm

demo-reset: ## Reset demo-owned records only, then re-inject
	$(COMPOSE) run --rm api python -m app.cli demo-reset

# ---------------------------------------------------------------- quality

test: test-backend ## Run backend tests

test-backend: ## Backend unit + contract tests
	cd $(BACKEND) && uv run pytest -q

test-frontend: ## Frontend tests (added by streams E/F)
	cd $(FRONTEND) && npm run test --if-present

lint: ## Ruff check + format check, then frontend lint
	cd $(BACKEND) && uv run ruff check . && uv run ruff format --check .
	cd $(FRONTEND) && npm run lint --if-present

fmt: ## Auto-format backend and frontend
	cd $(BACKEND) && uv run ruff check --fix . && uv run ruff format .
	cd $(FRONTEND) && npm run format --if-present

typecheck: ## Frontend TypeScript typecheck
	cd $(FRONTEND) && npm run typecheck

openapi: ## Write the OpenAPI document to docs/openapi.json
	cd $(BACKEND) && uv run python -m app.cli openapi > ../docs/openapi.json
	@echo "wrote docs/openapi.json"

verify-docs: ## Check every relative markdown link resolves
	@python3 scripts/verify_docs.py
