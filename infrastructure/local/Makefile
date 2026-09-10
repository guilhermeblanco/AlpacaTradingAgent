# =============================================================================
# TradingAgents — podman stack commands
#
#   make build                      build localhost/tradingagents:latest
#   make up                         migrate to head, then start the stack
#   POSTGRES_MODE=local make up     ... with PostgreSQL in the stack too
#   AUTONOMOUS=1 make up            ... and the autonomous worker
#   make logs / status / down
#
# Two switches, both defaulting to the cautious value:
#
#   POSTGRES_MODE  external (default) | local
#   AUTONOMOUS     unset (default)    | 1
#
# The Makefile owns three things the compose files deliberately do not: which
# overlay files apply, the image build, and the value of DATABASE_URL.
# =============================================================================

# ── Podman socket auto-detection (macOS) ────────────────────────────────────
# On a Mac, podman runs in a VM and compose providers speak to it over the
# socket named by DOCKER_HOST. On Linux this whole block finds nothing and the
# native socket is used. Set DOCKER_HOST_SKIP=1 to opt out.
ifndef DOCKER_HOST_SKIP
  _PM_NAME := $(shell podman machine list --format '{{.Name}}' --noheading 2>/dev/null | sed 's/\*$$//' | head -1)
  ifneq ($(_PM_NAME),)
    _PM_SOCK := $(shell podman machine inspect $(_PM_NAME) --format '{{.ConnectionInfo.PodmanSocket.Path}}' 2>/dev/null)
    ifneq ($(_PM_SOCK),)
      export DOCKER_HOST := unix://$(_PM_SOCK)
    endif
  endif
endif

# ── Compose provider ─────────────────────────────────────────────────────────
# `podman compose` is a thin delegator that needs an external provider
# installed; `podman-compose` is that provider, and calling it directly works
# whether or not the delegator is present. Prefer the delegator, fall back.
# `--in-pod=false` matters. podman-compose puts every service in one pod by
# default, and containers in a pod share a network namespace — so they reach
# each other on localhost and service-name DNS does not resolve. That breaks
# `postgres` as a hostname the moment POSTGRES_MODE=local. One netns per
# container on a user-defined network is what the compose files assume.
COMPOSE_BIN := $(shell command -v podman-compose 2>/dev/null)
ifeq ($(COMPOSE_BIN),)
  COMPOSE_CMD := podman compose
else
  _IN_POD := $(shell $(COMPOSE_BIN) --help 2>/dev/null | grep -c -- '--in-pod')
  ifeq ($(_IN_POD),0)
    COMPOSE_CMD := $(COMPOSE_BIN)
  else
    COMPOSE_CMD := $(COMPOSE_BIN) --in-pod=false
  endif
endif

# ── Image ────────────────────────────────────────────────────────────────────
# Built here rather than through compose `build:`. Compose providers disagree
# about when a build is implied and how many times it runs for services that
# share an image; `podman build` does not.
REPO_ROOT := $(abspath ../..)
IMAGE     ?= localhost/tradingagents
TAG       ?= latest
export IMAGE
export TAG

# ── .env ─────────────────────────────────────────────────────────────────────
# Read a single key out of .env, tolerating surrounding quotes. Used only for
# the few values the Makefile itself has to reason about; everything else is
# handed to the containers verbatim by compose.
_env = $(strip $(shell [ -f .env ] && sed -n 's/^$(1)=//p' .env | tail -1 | sed "s/^[\"']//; s/[\"']$$//"))

POSTGRES_DB       ?= $(or $(call _env,POSTGRES_DB),tradingagents)
POSTGRES_USER     ?= $(or $(call _env,POSTGRES_USER),tradingagents)
POSTGRES_PASSWORD ?= $(or $(call _env,POSTGRES_PASSWORD),tradingagents)
export POSTGRES_DB
export POSTGRES_USER
export POSTGRES_PASSWORD

# ── Topology ─────────────────────────────────────────────────────────────────
POSTGRES_MODE ?= external
COMPOSE_FILES := -f podman-compose.yml

ifeq ($(POSTGRES_MODE),local)
  COMPOSE_FILES += -f podman-compose.postgres.yml
  # A shell variable beats .env for compose interpolation, which is exactly
  # what we want: in local mode the host is decided by the topology, not by a
  # file somebody edited three weeks ago.
  export DATABASE_URL := postgresql+psycopg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@postgres:5432/$(POSTGRES_DB)
endif

ifdef AUTONOMOUS
  COMPOSE_FILES += -f podman-compose.autonomous.yml
endif

COMPOSE := $(COMPOSE_CMD) $(COMPOSE_FILES)

.DEFAULT_GOAL := help
.PHONY: help debug preflight build up down restart reset logs status \
        migrate shell psql pull-images

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  POSTGRES_MODE=$(POSTGRES_MODE)   AUTONOMOUS=$(if $(AUTONOMOUS),$(AUTONOMOUS),unset)"

debug: ## Show the resolved provider, files, and image
	@echo "provider     : $(COMPOSE_CMD)"
	@echo "DOCKER_HOST  : $(DOCKER_HOST)"
	@echo "compose files: $(COMPOSE_FILES)"
	@echo "image        : $(IMAGE):$(TAG)"
	@echo "repo root    : $(REPO_ROOT)"
	@echo "postgres mode: $(POSTGRES_MODE)"
	@echo "autonomous   : $(if $(AUTONOMOUS),yes,no)"
	@echo "DATABASE_URL : $(if $(DATABASE_URL),<set by Makefile>,<from .env>)"

# ── Preflight ────────────────────────────────────────────────────────────────
# Both of these are silent-wrong-answer failures rather than loud ones, which
# is why they are checked before anything starts. An absent .env means the
# containers come up with no API keys and fail one layer down; an empty
# DATABASE_URL means the app falls back to its localhost default and creates a
# second, empty history nobody is looking at.
preflight: ## Check .env and DATABASE_URL before starting anything
	@test -f .env || { \
		echo "✗ no .env here. Start from the template:"; \
		echo "    cp .env.example .env && \$$EDITOR .env"; \
		exit 1; }
	@if [ "$(POSTGRES_MODE)" != "local" ] && [ -z "$(call _env,DATABASE_URL)" ]; then \
		echo "✗ DATABASE_URL is not set in .env, and POSTGRES_MODE is '$(POSTGRES_MODE)'."; \
		echo "  Either point it at your server:"; \
		echo "    DATABASE_URL=postgresql+psycopg://tradingagents:<pw>@10.64.8.78:5432/tradingagents"; \
		echo "  or run a database in the stack:"; \
		echo "    POSTGRES_MODE=local make up"; \
		exit 1; fi
	@echo "✔ preflight ok ($(POSTGRES_MODE) postgres$(if $(AUTONOMOUS), + autonomous worker,))"

# ── Lifecycle ────────────────────────────────────────────────────────────────

build: ## Build the application image from the repo root
	podman build --tag $(IMAGE):$(TAG) --file $(REPO_ROOT)/Containerfile $(REPO_ROOT)

pull-images: ## Pull the third-party images (postgres) ahead of time
	podman pull docker.io/library/postgres:17-alpine

up: preflight ## Start the stack (migrating to head first)
	$(COMPOSE) up -d
	@echo ""
	@echo "  Workbench → http://localhost:$${HOST_PORT:-7860}"
	@echo "  Logs      → make logs"
	@echo ""

down: ## Stop the stack, keep the volumes
	$(COMPOSE) down

restart: ## Restart every running service
	$(COMPOSE) restart

reset: ## Destroy the volumes and start again — deletes decision history
	@printf 'This deletes the decision history, gate ledgers, and evaluation outcomes in this stack. Type "destroy" to continue: '; \
	read reply; [ "$$reply" = "destroy" ] || { echo "aborted."; exit 1; }
	$(COMPOSE) down -v
	$(MAKE) up

migrate: ## Run alembic upgrade head on its own
	$(COMPOSE) run --rm migrate

# ── Diagnostics ──────────────────────────────────────────────────────────────

logs: ## Follow the logs for every service
	$(COMPOSE) logs -f --tail=50

status: ## Show what is running and how healthy it is
	$(COMPOSE) ps

shell: ## Open a shell in the web container
	podman exec -it tradingagents-web /bin/bash

psql: ## Open psql against the stack's database
ifeq ($(POSTGRES_MODE),local)
	podman exec -it tradingagents-postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)
else
	@echo "POSTGRES_MODE=$(POSTGRES_MODE): the database is not in this stack."
	@echo "Connect to your server directly — the URL, password and all, is the"
	@echo "DATABASE_URL line in ./.env, which is not printed here on purpose."
endif
