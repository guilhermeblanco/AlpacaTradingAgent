#!/usr/bin/env bash
# =============================================================================
# TradingAgents — role and database on a PostgreSQL server you already run.
#
# WHAT   : creates the `tradingagents` role and database on an existing
#          PostgreSQL, grants the role ownership of the public schema, and
#          prints the DATABASE_URL line to paste into the app's .env.
#
#          This is the `POSTGRES_MODE=external` half of the deployment. It
#          exists because a second PostgreSQL is a second thing to patch,
#          monitor and back up, and one server hosting two application
#          databases is the ordinary arrangement.
#
# RUN    : on the PROXMOX NODE, as root. Two ways to reach the server:
#
#          a) it is an LXC on this node — give its CTID and this goes in
#             through `pct exec`, so no password and no network exposure:
#               PG_CTID=178 bash -c "$(curl -fsSL .../postgres-database.sh)"
#
#          b) it is anywhere else — give host and superuser credentials:
#               PG_HOST=10.64.8.78 PG_SUPERUSER=postgres PGPASSWORD='...' \
#                 bash -c "$(curl -fsSL .../postgres-database.sh)"
#
#          Set APP_PASSWORD to choose the role's password; otherwise one is
#          generated and printed once.
#
# IDEMPOTENT: safe to re-run. An existing role keeps its password unless you
#          pass APP_PASSWORD, and an existing database is left alone — this
#          script never drops anything, so a re-run cannot cost you history.
#
# MIGRATIONS: not run here. The app's `migrate` service does that on every
#          start, so the schema always matches the code that is about to read
#          it. This script only has to produce an empty database the app can
#          own.
#
# VERIFY : psql "$DATABASE_URL" -c '\conninfo'
# =============================================================================
set -euo pipefail
APP="TradingAgents database"

# ── CONFIG (env-overridable) ─────────────────────────────────────────────────
PG_CTID="${PG_CTID:-}"                       # LXC id of the PostgreSQL container
PG_HOST="${PG_HOST:-10.64.8.78}"             # used when PG_CTID is empty
PG_PORT="${PG_PORT:-5432}"
PG_SUPERUSER="${PG_SUPERUSER:-postgres}"

APP_DB="${APP_DB:-tradingagents}"
APP_ROLE="${APP_ROLE:-tradingagents}"
APP_PASSWORD="${APP_PASSWORD:-}"             # generated if empty
# ─────────────────────────────────────────────────────────────────────────────

BL="\e[36m"; GN="\e[1;92m"; YW="\e[33m"; RD="\e[01;31m"; CL="\e[m"
msg_info(){ echo -e " ${BL}‣${CL} $1"; }
msg_ok(){ echo -e " ${GN}✔${CL} $1"; }
msg_warn(){ echo -e " ${YW}!${CL} $1"; }
msg_err(){ echo -e " ${RD}✗${CL} $1" >&2; }
trap 'msg_err "$APP failed (line $LINENO)"' ERR
[ "$(id -u)" -eq 0 ] || { msg_err "run as root on the Proxmox node"; exit 1; }

# A near miss on the variable name would otherwise fall through to the
# network path and fail several steps later complaining about psql, which
# points at the wrong problem entirely. Catch it here and say the real thing.
for _near_miss in PGCTID PG_CT_ID PGCT_ID CTID PG_CONTAINER PG_VMID; do
  if [ -n "$(eval "printf '%s' \"\${${_near_miss}:-}\"")" ] && [ -z "$PG_CTID" ]; then
    msg_err "found ${_near_miss} in the environment — the variable is PG_CTID."
    msg_err "    PG_CTID=$(eval "printf '%s' \"\${${_near_miss}}\"") $0"
    exit 1
  fi
done

GENERATED=0
if [ -z "$APP_PASSWORD" ]; then
  APP_PASSWORD="$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 24)"
  GENERATED=1
fi

# Identifiers are validated rather than quoted. A plain lowercase identifier
# needs no quoting in SQL at all, and requiring one removes the whole question
# of how many layers of escaping a name would have to survive on its way to
# the server.
for _pair in "role:${APP_ROLE}" "database:${APP_DB}"; do
  case "${_pair#*:}" in
    [a-z_]*) ;;
    *) msg_err "${_pair%%:*} name '${_pair#*:}' must start with a lowercase letter or underscore"; exit 1 ;;
  esac
  case "${_pair#*:}" in
    *[!a-z0-9_]*) msg_err "${_pair%%:*} name '${_pair#*:}' may contain only [a-z0-9_]"; exit 1 ;;
  esac
done

# The password is interpolated into a single-quoted SQL literal, so a quote or
# backslash in it would end the literal early.
case "$APP_PASSWORD" in
  *[\'\\]*) msg_err "APP_PASSWORD must not contain a quote or a backslash"; exit 1 ;;
esac
# It also ends up in a URL, where these are structural.
case "$APP_PASSWORD" in
  *[@:/?\#\[\]]*)
    msg_warn "the password contains a character that must be percent-encoded in a URL."
    msg_warn "The DATABASE_URL printed below will need fixing by hand, or pass a simpler APP_PASSWORD." ;;
esac

# CREATE/ALTER ROLE carries the password in the statement, so it is briefly
# visible in the process list on the database host — the same exposure as
# typing it into psql yourself. That is also why a generated password is
# printed once here and stored nowhere.
# `runuser -u postgres -- psql`, not `su - postgres -c "psql ..."`: pct exec
# hands argv to the container without a shell, so the statement travels as one
# argument and both branches below need exactly the same quoting. With `su -c`
# there would be an extra shell in the middle of one path and not the other,
# and every literal in every statement would have to be escaped twice on one
# side and once on the other.
run_sql() {
  local database="$1" sql="$2"
  if [ -n "$PG_CTID" ]; then
    pct exec "$PG_CTID" -- runuser -u postgres -- \
      psql --no-psqlrc --quiet --tuples-only --no-align -d "$database" -c "$sql"
  else
    psql --no-psqlrc --quiet --tuples-only --no-align \
      -h "$PG_HOST" -p "$PG_PORT" -U "$PG_SUPERUSER" -d "$database" -c "$sql"
  fi
}

if [ -n "$PG_CTID" ]; then
  command -v pct >/dev/null || { msg_err "pct not found — PG_CTID only works on a Proxmox VE host"; exit 1; }
  pct status "$PG_CTID" >/dev/null 2>&1 || { msg_err "no LXC $PG_CTID on this node"; exit 1; }
  msg_info "reaching PostgreSQL through pct exec on CT $PG_CTID"
else
  if ! command -v psql >/dev/null; then
    msg_err "psql is not installed on this node, and PG_CTID is not set."
    msg_err "If PostgreSQL is an LXC here, that is the easier path — no password,"
    msg_err "no network exposure, nothing to install:"
    msg_err "    PG_CTID=<its ctid> $0        # pct list, to find it"
    msg_err "Otherwise: apt install postgresql-client, then set PG_HOST and PGPASSWORD."
    exit 1
  fi
  [ -n "${PGPASSWORD:-}" ] || msg_warn "PGPASSWORD is not set; psql may prompt or fail"
  msg_info "reaching PostgreSQL at ${PG_HOST}:${PG_PORT} as ${PG_SUPERUSER}"
fi

run_sql postgres "SELECT 1" >/dev/null || { msg_err "cannot query the server"; exit 1; }
msg_ok "connected"

# ── Role ─────────────────────────────────────────────────────────────────────
# An existing role keeps its password unless one was passed explicitly:
# rotating it silently would leave every running deployment unable to connect,
# and there is no way for this script to know which those are.
if [ "$(run_sql postgres "SELECT 1 FROM pg_roles WHERE rolname = '${APP_ROLE}'")" = "1" ]; then
  if [ "$GENERATED" = "1" ]; then
    msg_ok "role ${APP_ROLE} exists — password left as it is"
    APP_PASSWORD="<unchanged — the existing one>"
  else
    run_sql postgres "ALTER ROLE ${APP_ROLE} WITH LOGIN PASSWORD '${APP_PASSWORD}'" >/dev/null
    msg_ok "role ${APP_ROLE} exists — password set to the one you passed"
  fi
else
  run_sql postgres "CREATE ROLE ${APP_ROLE} WITH LOGIN PASSWORD '${APP_PASSWORD}'" >/dev/null
  msg_ok "role ${APP_ROLE} created"
fi

# ── Database ─────────────────────────────────────────────────────────────────
if [ "$(run_sql postgres "SELECT 1 FROM pg_database WHERE datname = '${APP_DB}'")" = "1" ]; then
  msg_ok "database ${APP_DB} exists — left alone"
else
  run_sql postgres "CREATE DATABASE ${APP_DB} OWNER ${APP_ROLE}" >/dev/null
  msg_ok "database ${APP_DB} created, owned by ${APP_ROLE}"
fi

# Alembic creates tables in `public`, and from PostgreSQL 15 the public schema
# is no longer writable by every role. Without this the first migration fails
# on permission denied, which reads like a connection problem and is not one.
run_sql "$APP_DB" "ALTER SCHEMA public OWNER TO ${APP_ROLE}" >/dev/null
run_sql "$APP_DB" "GRANT ALL ON SCHEMA public TO ${APP_ROLE}" >/dev/null
msg_ok "schema public owned by ${APP_ROLE}"

# ── Report ───────────────────────────────────────────────────────────────────
URL_HOST="$PG_HOST"
if [ -n "$PG_CTID" ]; then
  DISCOVERED_IP="$(pct exec "$PG_CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || true)"
  [ -n "$DISCOVERED_IP" ] && URL_HOST="$DISCOVERED_IP"
fi

cat <<EOF

  Put this in the app's .env (infrastructure/local/.env in the app LXC),
  or pass it to tradingagents.sh as DATABASE_URL:

    DATABASE_URL=postgresql+psycopg://${APP_ROLE}:${APP_PASSWORD}@${URL_HOST}:${PG_PORT}/${APP_DB}

EOF
if [ "$GENERATED" = "1" ] && [ "$APP_PASSWORD" != "<unchanged — the existing one>" ]; then
  msg_warn "that password was generated just now and is not stored anywhere else. Copy it."
fi
msg_warn "the app connects over the LAN — check pg_hba.conf and listen_addresses on the server allow it."
