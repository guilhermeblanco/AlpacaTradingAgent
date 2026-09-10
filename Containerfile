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
RUN python -m pip install --upgrade pip setuptools wheel

# Dependencies before source, and this ordering is the whole point.
#
# `COPY . .` followed by `pip install ".[app]"` puts every dependency
# behind a layer that any source change invalidates — so editing one Python
# file recompiled chromadb, backtrader and the rest, and a redeploy that
# touched nothing but a callback took the same forty minutes as the first
# build.
#
# pyproject.toml alone changes rarely, so the expensive layer survives
# almost every deploy. The specs are read out of it rather than duplicated
# into a requirements file, which would be a second list to keep in step
# with the first.
COPY pyproject.toml ./
RUN python -c "import tomllib, sys; project = tomllib.load(open('pyproject.toml', 'rb'))['project']; sys.stdout.write('\n'.join(project.get('dependencies', []) + project['optional-dependencies']['app']))" > /tmp/requirements.txt \
    && python -m pip install -r /tmp/requirements.txt

# Now the source, which changes on every commit. `--no-deps` because the
# line above already resolved them; without it pip re-checks the whole
# graph against the index on every build.
COPY . .
RUN python -m pip install --no-deps .


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

# No HEALTHCHECK here, deliberately.
#
# podman builds OCI-format images by default, and OCI has no healthcheck
# field — a HEALTHCHECK instruction is dropped with a warning that scrolls
# past in the middle of a forty-minute build. An instruction that looks
# like it works and does not is worse than its absence.
#
# The real probes are in infrastructure/local/podman-compose.yml, where
# every service has one: `/healthz` for the web process, and the control
# plane's heartbeat for the workers, which have no HTTP surface. Those are
# applied by the runtime rather than baked into the image, so they take
# effect without rebuilding and they are visible in the file that decides
# what runs.

CMD ["sh", "-c", "exec python run_webui_dash.py --server-name \"${SERVER_NAME:-0.0.0.0}\" --port \"${PORT:-7860}\""]

