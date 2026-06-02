# Instant Transcript — common tasks.
# Run `make` (or `make help`) to see what's available.

.DEFAULT_GOAL := help
.PHONY: help run docker setup dev clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

run: ## One-command local setup + run (venv, deps, .env, serve)
	@./run.sh

docker: ## Build and run with Docker Compose
	@docker compose up --build

setup: ## Create venv + install deps + seed .env (no server start)
	@python3 -m venv .venv
	@. .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt
	@test -f .env || cp .env.example .env
	@echo "Setup done. Start with: make run"

dev: ## Run locally with autoreload (assumes deps already installed)
	@. .venv/bin/activate && uvicorn app.main:app --reload

clean: ## Remove venv, caches, and local data (DB + temp audio)
	@rm -rf .venv app/__pycache__ data
	@echo "Cleaned."
