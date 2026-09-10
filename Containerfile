# =============================================================================
# TradingAgents — OCI image (built with podman/buildah).
#
#   podman build -t localhost/tradingagents:latest -f Containerfile .
#
# Base images are fully qualified. A short name resolves through whatever
# shortnames.conf the host happens to ship, which is a different registry on
# a different machine and a prompt on a machine with nobody to answer it.
#
# One image, several entry points: the web UI is the default CMD, the workers
# and `alembic upgrade head` override it. That keeps the workers on exactly the
# code the UI is showing, which matters because a decision's record is written
# by the web process and resolved by the evaluation worker.
#
# No BuildKit directive: podman/buildah ignore `# syntax=`, and nothing here
# needs it.
# =============================================================================

FROM docker.io/library/python:3.14-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        gcc \
        g++ \
        libffi-dev \
        libxml2-dev \
        libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv

COPY . .
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install ".[app]"


FROM docker.io/library/python:3.14-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PORT=7860 \
    SERVER_NAME=0.0.0.0 \
    TRADINGAGENTS_CACHE_DIR=/app/tradingagents/dataflows/data_cache \
    TRADINGAGENTS_RESULTS_DIR=/app/eval_results \
    TRADINGAGENTS_MEMORY_LOG_PATH=/app/.tradingagents/memory/trading_memory.md \
    MPLCONFIGDIR=/tmp/matplotlib \
    TRADINGAGENTS_STRICT_PORT=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libgomp1 \
        libxml2 \
        libxslt1.1 \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY . .

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p \
        /app/tradingagents/dataflows/data_cache \
        /app/eval_results \
        /app/.tradingagents/memory \
        /tmp/matplotlib \
    && chown -R app:app /app /tmp/matplotlib

USER app

EXPOSE 7860

# `/healthz` is a plain 200 from the Flask server; hitting `/` would render the
# whole Dash layout every 30 seconds for no reason.
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"7860\")}/healthz', timeout=3).read(1)"

CMD ["sh", "-c", "exec python run_webui_dash.py --server-name \"${SERVER_NAME:-0.0.0.0}\" --port \"${PORT:-7860}\""]

