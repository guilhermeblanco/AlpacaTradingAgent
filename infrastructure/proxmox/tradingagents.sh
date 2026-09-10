#!/usr/bin/env bash
# =============================================================================
# TradingAgents — Proxmox LXC running the podman stack.
#
# WHAT   : creates an unprivileged Debian LXC, installs podman +
#          podman-compose, clones this repo, builds the application image, and
#          runs the stack under a systemd unit so it comes back after a reboot.
#
#          Unlike the dev-support scripts next to this one, this service is
#          NOT installed natively — it is four containers sharing one image,
#          and keeping the workers on byte-identical code to the web UI is the
#          point. Podman, not Docker: no daemon, and the LXC stays unprivileged.
#
# RUN    : on the PROXMOX NODE, as root:
#            bash -c "$(curl -fsSL https://raw.githubusercontent.com/guilhermeblanco/AlpacaTradingAgent/main/infrastructure/proxmox/tradingagents.sh)"
#          override any CONFIG var inline, e.g.:
#            CTID=194 IP_CIDR=10.64.8.94/24 STORAGE=local-zfs bash -c "$(curl -fsSL .../tradingagents.sh)"
#
# IDEMPOTENT: safe to re-run. CT missing → create; CT exists → update in place
#          (fetch/reset the repo to REPO_REF, rebuild the image, rewrite the
#          unit, restart). It does NOT re-apply the LXC's network or resources
#          on an existing CT, and it NEVER overwrites an existing .env.
#
# FIRST RUN IS SLOW. The image installs the full `[app]` extra — chromadb,
#          backtrader, akshare, the langchain stack — from source wheels where
#          none are published for 3.14. Budget 15-40 minutes and the CORES/
#          RAM_MB/DISK_GB below; a 2 GB container will fail partway through
#          and leave you guessing.
#
# ACCESS THE CONTAINER (you're already root on the Proxmox host):
#          pct enter 194                 # root shell inside the CT, no password
#          pct exec  194 -- <command>    # run one command
#          Console / web-console / SSH login: root / $CT_PASSWORD (set below).
#
# THE APP HAS NO LOGIN. There is no authentication in front of the workbench.
#          The LXC boundary is the access control, which is why it binds
#          0.0.0.0 inside the CT and why you should not forward it past your
#          LAN.
#
# DATABASE : POSTGRES_MODE=external (default) points the stack at a PostgreSQL
#          you already run — provision the role and database there first with
#          `postgres-database.sh`, then pass the URL it prints:
#            DATABASE_URL='postgresql+psycopg://tradingagents:pw@10.64.8.78:5432/tradingagents' \
#              bash -c "$(curl -fsSL .../tradingagents.sh)"
#          POSTGRES_MODE=local instead runs postgres:17 inside the stack on a
#          named volume, so the LXC snapshots and restores as one thing.
#
# CAN IT TRADE? Not after this script. The autonomous worker is not started
#          (AUTONOMOUS=0), and even when it is, AUTONOMOUS_ENABLED=false and
#          EXECUTION_GATEWAY=dry-run each independently stop an order. Three
#          deliberate edits stand between a fresh LXC and a live order.
#
# VERIFY : on the node:   pct exec 194 -- systemctl is-active tradingagents
#                         pct exec 194 -- podman ps
#          from the LAN:  curl -fsS http://<ip>:7860/healthz    # → ok
# =============================================================================
set -euo pipefail
APP="TradingAgents"

# ── CONFIG (env-overridable) ─────────────────────────────────────────────────
CTID="${CTID:-194}"
CT_HOSTNAME="${CT_HOSTNAME:-tradingagents}"
CT_PASSWORD="${CT_PASSWORD:-localdev123}"      # root password for console/SSH login
STORAGE="${STORAGE:-local-lvm}"
BRIDGE="${BRIDGE:-vmbr0}"
IP_CIDR="${IP_CIDR:-10.64.8.94/24}"            # or: NET=dhcp
GATEWAY="${GATEWAY:-10.64.8.1}"
# Debian 13. Not 12: its podman is 4.3 and its podman-compose predates
# reliable `depends_on: service_completed_successfully`, which is what keeps a
# worker from starting against a schema older than its code. If this filename
# is stale the script finds the newest debian-13-standard by itself.
TEMPLATE_FILE="${TEMPLATE_FILE:-debian-13-standard_13.1-2_amd64.tar.zst}"
CORES="${CORES:-4}"; RAM_MB="${RAM_MB:-6144}"; SWAP_MB="${SWAP_MB:-2048}"; DISK_GB="${DISK_GB:-32}"

REPO_URL="${REPO_URL:-https://github.com/guilhermeblanco/AlpacaTradingAgent}"
REPO_REF="${REPO_REF:-main}"
APP_DIR="${APP_DIR:-/opt/tradingagents}"

HOST_PORT="${HOST_PORT:-7860}"                 # port on the CT's own address
POSTGRES_MODE="${POSTGRES_MODE:-external}"     # external | local
AUTONOMOUS="${AUTONOMOUS:-0}"                  # 1 to also run the autonomous worker
DATABASE_URL="${DATABASE_URL:-}"               # required for POSTGRES_MODE=external
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"     # used for POSTGRES_MODE=local
TZ_NAME="${TZ_NAME:-America/New_York}"
# fuse-overlayfs, not the kernel's overlay: nested overlayfs in an
# unprivileged LXC is refused on most Proxmox kernels. Set vfs if fuse is
# unavailable — correct, much slower, and much larger on disk.
STORAGE_DRIVER="${STORAGE_DRIVER:-fuse-overlayfs}"
# ─────────────────────────────────────────────────────────────────────────────

BL="\e[36m"; GN="\e[1;92m"; YW="\e[33m"; RD="\e[01;31m"; CL="\e[m"
msg_info(){ echo -e " ${BL}‣${CL} $1"; }
msg_ok(){ echo -e " ${GN}✔${CL} $1"; }
msg_warn(){ echo -e " ${YW}!${CL} $1"; }
msg_err(){ echo -e " ${RD}✗${CL} $1" >&2; }
trap 'msg_err "$APP failed (line $LINENO)"' ERR
[ "$(id -u)" -eq 0 ] || { msg_err "run as root on the Proxmox node"; exit 1; }
command -v pct >/dev/null || { msg_err "pct not found — this must run on a Proxmox VE host"; exit 1; }
[ "${NET:-}" = "dhcp" ] && NETCFG="ip=dhcp" || NETCFG="ip=${IP_CIDR},gw=${GATEWAY}"

case "$POSTGRES_MODE" in
  external|local) ;;
  *) msg_err "POSTGRES_MODE must be 'external' or 'local', got '$POSTGRES_MODE'"; exit 1 ;;
esac

# ── The LXC ──────────────────────────────────────────────────────────────────
if pct status "$CTID" >/dev/null 2>&1; then
  msg_info "CT $CTID exists — updating in place"
else
  msg_info "ensuring template $TEMPLATE_FILE"
  if ! pveam list local 2>/dev/null | grep -q "$TEMPLATE_FILE"; then
    pveam update >/dev/null 2>&1 || true
    if ! pveam available --section system 2>/dev/null | grep -q "$TEMPLATE_FILE"; then
      DISCOVERED="$(pveam available --section system 2>/dev/null | awk '{print $2}' \
                    | grep -E '^debian-13-standard' | sort -V | tail -1 || true)"
      [ -n "$DISCOVERED" ] || { msg_err "no debian-13-standard template offered by this node; set TEMPLATE_FILE"; exit 1; }
      msg_warn "template $TEMPLATE_FILE not offered; using $DISCOVERED"
      TEMPLATE_FILE="$DISCOVERED"
    fi
    pveam download local "$TEMPLATE_FILE"
  fi
  msg_info "creating LXC $CTID ($CT_HOSTNAME) — ${CORES} cores, ${RAM_MB} MB, ${DISK_GB} GB"
  # nesting: podman runs containers inside this one.
  # keyctl:  podman/crun need their own keyring in an unprivileged CT.
  # fuse:    /dev/fuse for fuse-overlayfs, the storage driver below.
  pct create "$CTID" "local:vztmpl/${TEMPLATE_FILE}" \
    --hostname "$CT_HOSTNAME" --password "$CT_PASSWORD" \
    --cores "$CORES" --memory "$RAM_MB" --swap "$SWAP_MB" \
    --rootfs "${STORAGE}:${DISK_GB}" --net0 "name=eth0,bridge=${BRIDGE},${NETCFG}" \
    --unprivileged 1 --features nesting=1,keyctl=1,fuse=1 \
    --onboot 1 --description "$APP" >/dev/null
fi
pct start "$CTID" 2>/dev/null || true
for _ in $(seq 1 30); do pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1 && break; sleep 2; done
pct exec "$CTID" -- bash -c "echo 'root:${CT_PASSWORD}' | chpasswd" 2>/dev/null || true
msg_ok "container up (root password: ${CT_PASSWORD})"

# An existing CT created before this script grew the feature list will not have
# nesting/keyctl/fuse, and podman will fail in ways that do not name the cause.
CT_FEATURES="$(pct config "$CTID" | sed -n 's/^features: //p' || true)"
for feature in nesting=1 keyctl=1 fuse=1; do
  case "$CT_FEATURES" in
    *"$feature"*) ;;
    *) msg_warn "CT $CTID is missing '$feature' — podman will misbehave. Fix with:"
       msg_warn "    pct set $CTID --features nesting=1,keyctl=1,fuse=1 && pct reboot $CTID" ;;
  esac
done

# ── Podman ───────────────────────────────────────────────────────────────────
msg_info "installing podman + podman-compose"
pct exec "$CTID" -- bash -c "
  set -e; export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends \
    podman podman-compose buildah fuse-overlayfs uidmap slirp4netns \
    git make python3 ca-certificates curl tzdata >/dev/null
  ln -sf /usr/share/zoneinfo/${TZ_NAME} /etc/localtime || true
  echo '${TZ_NAME}' >/etc/timezone
"

msg_info "configuring the ${STORAGE_DRIVER} storage driver"
if [ "$STORAGE_DRIVER" = "fuse-overlayfs" ]; then
  pct exec "$CTID" -- bash -c "
    mkdir -p /etc/containers
    cat >/etc/containers/storage.conf <<'CONF'
# Nested unprivileged LXC: the kernel refuses overlayfs-on-overlayfs here, so
# podman goes through fuse-overlayfs instead. vfs also works and needs no
# fuse device, at a large cost in build time and disk.
[storage]
driver = \"overlay\"
runroot = \"/run/containers/storage\"
graphroot = \"/var/lib/containers/storage\"

[storage.options.overlay]
mount_program = \"/usr/bin/fuse-overlayfs\"
mountopt = \"nodev,metacopy=on\"
CONF
  "
else
  pct exec "$CTID" -- bash -c "
    mkdir -p /etc/containers
    printf '[storage]\ndriver = \"%s\"\n' '${STORAGE_DRIVER}' >/etc/containers/storage.conf
  "
fi
pct exec "$CTID" -- podman info --format '{{.Store.GraphDriverName}}' >/dev/null 2>&1 \
  || { msg_err "podman cannot initialise its store — check nesting/keyctl/fuse on CT $CTID"; exit 1; }
msg_ok "podman $(pct exec "$CTID" -- podman --version | awk '{print $3}') ready"

# ── The repo ─────────────────────────────────────────────────────────────────
msg_info "fetching ${REPO_URL} @ ${REPO_REF}"
pct exec "$CTID" -- bash -c "
  set -e
  if [ -d '${APP_DIR}/.git' ]; then
    git -C '${APP_DIR}' remote set-url origin '${REPO_URL}'
    git -C '${APP_DIR}' fetch --depth 1 origin '${REPO_REF}'
    git -C '${APP_DIR}' reset --hard FETCH_HEAD
  else
    git clone --depth 1 --branch '${REPO_REF}' '${REPO_URL}' '${APP_DIR}'
  fi
" || { msg_err "clone failed. Private repo? Put a deploy key at /root/.ssh/id_ed25519 in CT ${CTID} and set REPO_URL to the SSH form."; exit 1; }

# ── .env ─────────────────────────────────────────────────────────────────────
# Seeded once from the template and never overwritten: it holds live broker
# credentials, and a re-run that quietly reset them to blanks would be the
# worst possible behaviour for a script advertised as idempotent.
ENV_FILE="${APP_DIR}/infrastructure/local/.env"
if pct exec "$CTID" -- test -f "$ENV_FILE"; then
  msg_ok ".env already present — left untouched"
  ENV_IS_NEW=0
else
  msg_info "seeding .env from the template"
  pct exec "$CTID" -- bash -c "install -m 600 '${APP_DIR}/infrastructure/local/.env.example' '${ENV_FILE}'"
  ENV_IS_NEW=1
fi

# Only topology keys are ever set from here; the secrets are the operator's.
# The rewriter is installed into the CT rather than piped to it, because `pct
# exec` forwarding stdin is not something to bet a provisioning script on.
pct exec "$CTID" -- bash -c "
cat >/usr/local/sbin/ta-set-env <<'HELPER'
#!/usr/bin/env python3
'''Set one KEY=VALUE in an env file, in place, preserving everything else.'''
import pathlib, sys

path, key, value = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = path.read_text().splitlines()
out, replaced = [], False
for line in lines:
    if not line.lstrip().startswith('#') and line.split('=', 1)[0].strip() == key:
        out.append(f'{key}={value}')
        replaced = True
    else:
        out.append(line)
if not replaced:
    out.append(f'{key}={value}')
path.write_text('\n'.join(out) + '\n')
HELPER
chmod 755 /usr/local/sbin/ta-set-env
"

set_env_key() {
  pct exec "$CTID" -- /usr/local/sbin/ta-set-env "$ENV_FILE" "$1" "$2"
}

set_env_key HOST_BIND 0.0.0.0        # the LXC boundary is the access control
set_env_key HOST_PORT "$HOST_PORT"
set_env_key TZ "$TZ_NAME"
set_env_key PERSISTENCE_BACKEND postgres

if [ -n "$DATABASE_URL" ]; then
  set_env_key DATABASE_URL "$DATABASE_URL"
  msg_ok "DATABASE_URL written"
elif [ "$POSTGRES_MODE" = "external" ] && [ "$ENV_IS_NEW" = "1" ]; then
  msg_warn "POSTGRES_MODE=external and no DATABASE_URL given — the stack will refuse to start."
  msg_warn "Provision the database first:"
  msg_warn "    bash -c \"\$(curl -fsSL .../infrastructure/proxmox/postgres-database.sh)\""
  msg_warn "then put the URL it prints into ${ENV_FILE} (or re-run this script with DATABASE_URL=...)."
fi
if [ "$POSTGRES_MODE" = "local" ] && [ -n "$POSTGRES_PASSWORD" ]; then
  set_env_key POSTGRES_PASSWORD "$POSTGRES_PASSWORD"
fi
pct exec "$CTID" -- chmod 600 "$ENV_FILE"

# ── The image ────────────────────────────────────────────────────────────────
msg_info "building the application image (slow on a cold cache — see the header)"
pct exec "$CTID" -- make -C "${APP_DIR}/infrastructure/local" build
msg_ok "image built: $(pct exec "$CTID" -- podman images --format '{{.Repository}}:{{.Tag}} {{.Size}}' localhost/tradingagents | head -1)"

# ── The unit ─────────────────────────────────────────────────────────────────
# systemd owns the stack rather than podman-compose owning itself: one unit to
# enable, and `make down` on stop so the containers are not left behind for the
# next boot to trip over.
msg_info "installing the systemd unit"
# `x && y` as a bare statement would end the script under `set -e` whenever
# the test is false, which is the common case.
AUTONOMOUS_LINE=""
if [ "$AUTONOMOUS" = "1" ]; then AUTONOMOUS_LINE="Environment=AUTONOMOUS=1"; fi
pct exec "$CTID" -- bash -c "
cat >/etc/systemd/system/tradingagents.service <<UNIT
[Unit]
Description=TradingAgents stack (podman-compose)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${APP_DIR}/infrastructure/local
Environment=POSTGRES_MODE=${POSTGRES_MODE}
${AUTONOMOUS_LINE}
ExecStart=/usr/bin/make up
ExecStop=/usr/bin/make down
# The first start migrates and may pull images; do not shoot it partway.
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable tradingagents >/dev/null 2>&1
systemctl restart tradingagents || true
"

# ── Report ───────────────────────────────────────────────────────────────────
CT_IP="$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}' || true)"
[ -n "$CT_IP" ] || CT_IP="${IP_CIDR%%/*}"

echo
if pct exec "$CTID" -- systemctl is-active --quiet tradingagents; then
  msg_ok "$APP is running"
else
  msg_warn "the unit did not come up. Almost always .env — read the reason with:"
  msg_warn "    pct exec $CTID -- journalctl -u tradingagents -n 40 --no-pager"
fi
cat <<EOF

  Workbench     http://${CT_IP}:${HOST_PORT}          (no login — LAN only)
  Health        curl -fsS http://${CT_IP}:${HOST_PORT}/healthz

  Shell         pct enter ${CTID}
  Settings      ${ENV_FILE}     (mode 0600, never overwritten by a re-run)
  Containers    pct exec ${CTID} -- podman ps
  Logs          pct exec ${CTID} -- make -C ${APP_DIR}/infrastructure/local logs
  Restart       pct exec ${CTID} -- systemctl restart tradingagents

  Database      POSTGRES_MODE=${POSTGRES_MODE}
  Autonomous    $([ "$AUTONOMOUS" = "1" ] && echo "worker STARTED — AUTONOMOUS_ENABLED and EXECUTION_GATEWAY in .env still gate every order" || echo "worker not started (re-run with AUTONOMOUS=1)")

  Upgrade       re-run this script; it resets to ${REPO_REF}, rebuilds, restarts.

EOF
