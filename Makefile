# =============================================================================
# TradingAgents — top-level commands
#
# Everything to do with running the stack lives in infrastructure/local; this
# file is a short way to reach it from the repo root, plus the two things you
# do before deploying anything.
#
#   make test              the full suite
#   make stack-build       build the application image
#   make stack-up          start the stack
#   make stack-logs        follow it
#   make stack-down        stop it
#
# The switches pass straight through:
#   POSTGRES_MODE=local make stack-up
#   AUTONOMOUS=1 make stack-up
#
# See infrastructure/proxmox/README.md for deploying this to a Proxmox node.
# =============================================================================

PYTHON ?= .venv/bin/python
STACK  := $(MAKE) -C infrastructure/local

.DEFAULT_GOAL := help
.PHONY: help test coverage stack-build stack-up stack-down stack-restart \
        stack-logs stack-status stack-shell stack-psql

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ── Checks ───────────────────────────────────────────────────────────────────

test: ## Run the full test suite
	$(PYTHON) -m pytest -q

coverage: ## Run the suite under coverage and report on the source packages
	$(PYTHON) -m coverage run -m pytest -q
	$(PYTHON) -m coverage report --include="tradingagents/*,webui/*" --precision=2

# ── The stack ────────────────────────────────────────────────────────────────

stack-build: ## Build the application image
	$(STACK) build

stack-up: ## Start the stack (migrating to head first)
	$(STACK) up

stack-down: ## Stop the stack, keep the volumes
	$(STACK) down

stack-restart: ## Restart every running service
	$(STACK) restart

stack-logs: ## Follow the logs
	$(STACK) logs

stack-status: ## Show what is running and how healthy it is
	$(STACK) status

stack-shell: ## Open a shell in the web container
	$(STACK) shell

stack-psql: ## Open psql against the stack's database
	$(STACK) psql
