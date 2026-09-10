#!/usr/bin/env bash
# =============================================================================
# TradingAgents — Proxmox LXC running the podman stack.
#
# WHAT   : creates an unprivileged Ubuntu LXC, installs podman +
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
# IT EDITS THE CT CONFIG. Beyond `pct create`, the script appends two lines to
#          /etc/pve/lxc/<ctid>.conf to expose /dev/net/tun, which podman's
#          network helpers need and an unprivileged LXC does not get. Adding
#          them restarts the container.
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
# Ubuntu 26.04 LTS. Any apt-based template works: the script does not depend
# on the distribution's podman-compose being new enough, because it probes for
# the one capability it needs and installs a current podman-compose itself if
# the packaged one lacks it (see COMPOSE CAPABILITY below). So this is a
# preference, not a requirement — 24.04 LTS and Debian 13 both work, e.g.
#   TEMPLATE_FILE=ubuntu-24.04-standard_24.04-2_amd64.tar.zst TEMPLATE_MATCH='^ubuntu-24.04-standard'
#
# The exact point-release suffix moves; if this filename is not offered the
# script downloads the newest template matching TEMPLATE_MATCH instead and
# says which one it picked.
TEMPLATE_FILE="${TEMPLATE_FILE:-ubuntu-26.04-standard_26.04-1_amd64.tar.zst}"
TEMPLATE_MATCH="${TEMPLATE_MATCH:-^ubuntu-26.04-standard}"
CORES="${CORES:-4}"; RAM_MB="${RAM_MB:-6144}"; SWAP_MB="${SWAP_MB:-2048}"; DISK_GB="${DISK_GB:-32}"

REPO_URL="${REPO_URL:-https://github.com/guilhermeblanco/AlpacaTradingAgent}"
REPO_REF="${REPO_REF:-main}"
APP_DIR="${APP_DIR:-/opt/tradingagents}"

HOST_PORT="${HOST_PORT:-7860}"                 # port on the CT's own address
POSTGRES_MODE="${POSTGRES_MODE:-external}"     # external | local
AUTONOMOUS="${AUTONOMOUS:-0}"                  # 1 to also run the autonomous worker
DATABASE_URL="${DATABASE_URL:-}"               # required for POSTGRES_MODE=external
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"     # used for POSTGRES_MODE=local
BUILD_NETWORK="${BUILD_NETWORK:-}"             # empty: probe for it. 'host' to force.
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
                    | grep -E "$TEMPLATE_MATCH" | sort -V | tail -1 || true)"
      [ -n "$DISCOVERED" ] || { msg_err "no template matching ${TEMPLATE_MATCH} offered by this node; set TEMPLATE_FILE"; exit 1; }
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
for _ in $(seq 1 30); do pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1 && break; sleep 2; done
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

# ── /dev/net/tun ─────────────────────────────────────────────────────────────
# Both of podman's rootless network helpers — pasta and slirp4netns — set up
# a container's network by creating a tap device inside its namespace, and
# that needs /dev/net/tun. An unprivileged LXC is not given one, and the
# failure names nothing useful:
#
#     setup network: pasta failed with exit code -1:
#
# with an empty message after the colon. There is no fallback to reach for
# here: slirp4netns wants the same device, so the device is the fix.
#
# `pct set` has no option for this, so it goes into the CT's config file
# directly, and needs a restart to take effect.
[ -c /dev/net/tun ] || modprobe tun 2>/dev/null || true
CT_CONF="/etc/pve/lxc/${CTID}.conf"
TUN_ADDED=0
if [ -f "$CT_CONF" ]; then
  grep -q '^lxc.cgroup2.devices.allow: c 10:200 rwm' "$CT_CONF" \
    || { echo 'lxc.cgroup2.devices.allow: c 10:200 rwm' >>"$CT_CONF"; TUN_ADDED=1; }
  grep -q '^lxc.mount.entry: /dev/net/tun' "$CT_CONF" \
    || { echo 'lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file' >>"$CT_CONF"; TUN_ADDED=1; }
else
  msg_warn "no config at ${CT_CONF} — cannot add /dev/net/tun automatically"
fi
if [ "$TUN_ADDED" = "1" ]; then
  msg_info "added /dev/net/tun to CT ${CTID} — restarting to apply"
  pct stop "$CTID" >/dev/null 2>&1 || true
  pct start "$CTID"
  for _ in $(seq 1 30); do pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1 && break; sleep 2; done
fi
if ! pct exec "$CTID" -- test -c /dev/net/tun; then
  msg_err "/dev/net/tun is absent inside CT ${CTID}, so podman cannot give a"
  msg_err "container a network. These two lines belong in ${CT_CONF}:"
  msg_err "    lxc.cgroup2.devices.allow: c 10:200 rwm"
  msg_err "    lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file"
  msg_err "and the node itself needs the module: modprobe tun"
  exit 1
fi
msg_ok "/dev/net/tun available"

# ── Podman ───────────────────────────────────────────────────────────────────
# `--no-install-recommends` is a trap here. On Ubuntu the pieces podman
# actually needs at runtime — the network helper, the DNS server for
# user-defined networks, the init process — are Recommends rather than
# Depends, so podman installs cleanly and then fails at the moment of use,
# minutes later, with an error that names a binary and not a package:
#
#   pasta         podman 5 configures the build container's netns with it
#   netavark      the network backend
#   aardvark-dns  resolves container names on a user-defined network, which
#                 is how `postgres` resolves in POSTGRES_MODE=local
#   catatonit     what `init: true` in the compose files runs as pid 1
#   crun          the OCI runtime
#
# So they are named explicitly, and verified below rather than assumed.
#
# LC_ALL=C because pct exec forwards the node's LANG into a container that
# has no locales generated, and the resulting perl warnings bury real output.
msg_info "installing podman and its runtime helpers"
pct exec "$CTID" -- bash -c "
  set -e
  export DEBIAN_FRONTEND=noninteractive LC_ALL=C LANG=C
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends \
    podman buildah crun fuse-overlayfs uidmap \
    passt netavark aardvark-dns catatonit slirp4netns \
    git make python3 python3-venv ca-certificates curl tzdata >/dev/null
  # podman-compose is in universe on Ubuntu and may be absent or old; it is
  # dealt with separately below, so a failure here is not fatal.
  apt-get install -y -qq --no-install-recommends podman-compose >/dev/null 2>&1 || true
  ln -sf /usr/share/zoneinfo/${TZ_NAME} /etc/localtime || true
  echo '${TZ_NAME}' >/etc/timezone
"

# Fail here, naming what is missing, rather than twenty minutes into a build.
# netavark and aardvark-dns live under /usr/lib/podman rather than on PATH on
# Debian-family packagings, so they are looked for in both places.
if ! pct exec "$CTID" -- bash -c '
  missing=""
  for tool in pasta crun fuse-overlayfs catatonit; do
    command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
  done
  for tool in netavark aardvark-dns; do
    command -v "$tool" >/dev/null 2>&1 && continue
    [ -x "/usr/lib/podman/$tool" ] && continue
    [ -x "/usr/libexec/podman/$tool" ] && continue
    missing="$missing $tool"
  done
  [ -z "$missing" ] || { echo "missing:$missing"; exit 1; }
'; then
  msg_err "podman installed but cannot run containers — the tools listed above are absent."
  msg_err "On Ubuntu they live in these packages:"
  msg_err "    pct exec ${CTID} -- apt-get install -y passt netavark aardvark-dns catatonit crun"
  exit 1
fi
msg_ok "podman runtime helpers present"

# ── COMPOSE CAPABILITY ───────────────────────────────────────────────────────
# The stack relies on `depends_on: condition: service_completed_successfully`
# to keep a worker from ever starting against a schema older than its code.
# Older podman-compose accepts that key and ignores it, which is the worst
# outcome: it starts, it looks fine, and the ordering guarantee is gone.
#
# So probe for the capability rather than trusting a version number or a
# distribution. podman-compose is a single Python module, so asking whether it
# knows the string is both cheap and exact. If it does not — or if the distro
# had no package at all — install a current one into its own venv and put it
# ahead of /usr/bin on PATH.
msg_info "checking podman-compose understands the ordering conditions"
# The interpreter comes from the wrapper's own shebang rather than being
# assumed to be the system python3 — otherwise the venv installed below is
# invisible to the probe on the next run, and every re-run reinstalls it while
# reporting that the packaged one is inadequate.
if pct exec "$CTID" -- bash -c '
  bin=$(command -v podman-compose) || exit 1
  py=$(head -1 "$bin" | sed "s|^#!||; s|^ *||")
  case "$py" in */python*|python*) ;; *) py=python3 ;; esac
  module=$("$py" -c "import importlib.util
spec = importlib.util.find_spec(\"podman_compose\")
print(spec.origin if spec else \"\")" 2>/dev/null)
  [ -n "$module" ] || module="$bin"
  grep -q service_completed_successfully "$module"
'; then
  msg_ok "packaged podman-compose $(pct exec "$CTID" -- podman-compose --version 2>/dev/null | sed -n 's/.*version //p' | tail -1) is sufficient"
else
  msg_warn "the packaged podman-compose cannot enforce start ordering — installing a current one"
  pct exec "$CTID" -- bash -c "
    set -e
    python3 -m venv /opt/podman-compose
    /opt/podman-compose/bin/pip install --quiet --upgrade pip
    /opt/podman-compose/bin/pip install --quiet 'podman-compose>=1.2'
    ln -sf /opt/podman-compose/bin/podman-compose /usr/local/bin/podman-compose
  "
  pct exec "$CTID" -- bash -c 'command -v podman-compose >/dev/null' \
    || { msg_err "podman-compose is still not on PATH in CT $CTID"; exit 1; }
  msg_ok "podman-compose installed at /opt/podman-compose"
fi

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
  set -e; export LC_ALL=C LANG=C
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

# ── Can podman actually build something? ─────────────────────────────────────
# The smoke test this replaces started a container with `podman run`, which
# passed — and the build failed anyway, at the same step as before. They are
# different code paths: `podman run` took the default bridge, while a RUN step
# in a build asks buildah for a namespace and buildah reached for pasta, which
# cannot configure one in this LXC even with /dev/net/tun present. Testing the
# path that works tells you nothing about the path that does not.
#
# So the probe is a two-line build: the same base image the real one starts
# from, and a RUN step, which is precisely what breaks. It costs seconds
# against a warm cache and, on a cold one, pulls what STEP 1 needs anyway.
#
# It also decides. If podman's own choice works, nothing is overridden. If it
# does not, the build gets --network=host: the RUN steps then share the
# container's network namespace, which is all apt and pip need, and a build
# publishes nothing. The answer is recorded in .env so a later `make build`
# inside the LXC does not have to rediscover it.
if [ -n "$BUILD_NETWORK" ]; then
  msg_ok "build network forced to ${BUILD_NETWORK} — not probing"
else
  msg_info "probing how the build reaches the network"
  pct exec "$CTID" -- bash -c "
    set -e
    mkdir -p /var/tmp/ta-build-probe
    printf 'FROM docker.io/library/python:3.14-slim-bookworm\nRUN true\n' \
      >/var/tmp/ta-build-probe/Containerfile
  "
  if pct exec "$CTID" -- podman build -q --tag localhost/ta-build-probe:latest \
       /var/tmp/ta-build-probe >/dev/null 2>&1; then
    msg_ok "the build's default network works"
  elif pct exec "$CTID" -- podman build -q --network=host \
         --tag localhost/ta-build-probe:latest /var/tmp/ta-build-probe >/dev/null 2>&1; then
    BUILD_NETWORK="host"
    msg_warn "podman's default build network fails in this LXC — using --network=host."
    msg_warn "RUN steps share the container's own network; they need it only for"
    msg_warn "apt and pip, and a build publishes nothing."
  else
    msg_err "podman cannot build in CT ${CTID}, with or without host networking."
    msg_err "Get the reason with:"
    msg_err "    pct exec ${CTID} -- podman build --log-level=debug /var/tmp/ta-build-probe 2>&1 | tail -40"
    exit 1
  fi
  pct exec "$CTID" -- podman rmi -f localhost/ta-build-probe:latest >/dev/null 2>&1 || true
fi
[ -z "$BUILD_NETWORK" ] || set_env_key BUILD_NETWORK "$BUILD_NETWORK"

# ── The image ────────────────────────────────────────────────────────────────
msg_info "building the application image (slow on a cold cache — see the header)"
pct exec "$CTID" -- make -C "${APP_DIR}/infrastructure/local" BUILD_NETWORK="$BUILD_NETWORK" build
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
