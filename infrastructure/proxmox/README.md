# Proxmox deployment — one LXC, podman inside

Community-scripts-style: each script is self-contained, **run on the Proxmox
node as root**, and prints how to wire what comes next.

```bash
# on the Proxmox node, as root — database first, app second:
PG_CTID=<postgres ctid> bash -c "$(curl -fsSL https://raw.githubusercontent.com/guilhermeblanco/AlpacaTradingAgent/main/infrastructure/proxmox/postgres-database.sh)"

DATABASE_URL='postgresql+psycopg://tradingagents:<pw>@10.64.8.78:5432/tradingagents' \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/guilhermeblanco/AlpacaTradingAgent/main/infrastructure/proxmox/tradingagents.sh)"
```

Override any CONFIG var inline:

```bash
CTID=194 IP_CIDR=10.64.8.94/24 STORAGE=local-zfs CORES=6 RAM_MB=8192 \
  bash -c "$(curl -fsSL .../tradingagents.sh)"
```

| script | what it does | base | default CT / IP / port |
|---|---|---|---|
| `postgres-database.sh` | role + database on a PostgreSQL you already run | — | reaches `10.64.8.78:5432`, or `PG_CTID` via `pct exec` |
| `tradingagents.sh` | LXC + podman + the app stack, under systemd | Debian 13 | 194 · 10.64.8.94 · 7860 |

**Idempotent:** re-running `tradingagents.sh` creates the LXC if it is missing,
otherwise updates it in place — resets the checkout to `REPO_REF`, rebuilds the
image, rewrites the unit, restarts. It does *not* re-apply the LXC's network or
resources on an existing CT (`pct set <ctid> …` for that), and it **never
overwrites an existing `.env`**. `postgres-database.sh` never drops anything and
leaves an existing role's password alone unless you pass `APP_PASSWORD`.

## Why this one runs containers when the others install natively

The dev-support scripts in a sibling project install their service straight
into the LXC, no container runtime involved, which is the right shape for a
single binary under systemd.

This one is four processes — the web UI, the evaluation worker, the
reconciliation worker, and optionally the autonomous worker — that must run
**byte-identical code**. A decision's record is written by the web process,
resolved into outcomes by the evaluation worker, and reconciled against the
broker by a third; if they drift apart by a deploy, the record stops joining
up. One image, four entry points, is the cheapest way to make that impossible.

Podman rather than Docker: no daemon to run as root inside the container, the
LXC stays **unprivileged**, and `systemd` supervises the stack directly. The
LXC needs `nesting=1,keyctl=1,fuse=1` for this, which the script sets on
create and warns about on an existing CT that lacks them.

Storage driver is **fuse-overlayfs**, not the kernel's overlay: nested
overlayfs in an unprivileged LXC is refused on most Proxmox kernels. Set
`STORAGE_DRIVER=vfs` if fuse is unavailable — correct, much slower, much
larger on disk.

## The first run is slow

The image installs the full `[app]` extra: chromadb, backtrader, akshare, the
langchain stack, and everything they pull in. On Python 3.14 a number of those
have no published wheel and are compiled. **Budget 15–40 minutes**, and leave
the defaults alone (`CORES=4 RAM_MB=6144 DISK_GB=32`) — a 2 GB container fails
partway through the build and the error does not say why.

Re-runs reuse the layer cache and take a minute or two unless a dependency
changed.

## Database: two shapes, one flag

- **`POSTGRES_MODE=external`** (default) — the app uses a PostgreSQL you
  already run. `postgres-database.sh` creates the role and database on it and
  prints the `DATABASE_URL` to pass to `tradingagents.sh`. One server to patch,
  monitor and back up.
- **`POSTGRES_MODE=local`** — `postgres:17-alpine` runs inside the stack on a
  named volume. The LXC then snapshots and restores as one thing, at the cost
  of a second PostgreSQL to look after.

Either way the `migrate` service runs `alembic upgrade head` before anything
reads the schema, on every start.

## Can it trade?

Not after running these scripts, and not by accident. Three independent things
must change:

1. The autonomous worker is not started at all. `AUTONOMOUS=1` on the script,
   or `AUTONOMOUS=1 make up` in the LXC.
2. `AUTONOMOUS_ENABLED=false` in `.env` — it scans and decides but dispatches
   nothing.
3. `EXECUTION_GATEWAY=dry-run` in `.env` — intents are priced and taken through
   every gate, and then not sent.

The useful place to sit for a while is (1) and (2) on, (3) still `dry-run`: the
whole pipeline runs and journals what it *would* have done, and you can read
the gate ledgers in the workbench for a week before deciding. When you do move
to `broker`, check `ALPACA_USE_PAPER` on the same trip.

## Access & credentials

- **Shell in the container** (you are root on the node): `pct enter 194`. No
  password. `pct exec 194 -- <cmd>` for one-offs.
- **Container OS login** (console / web console / SSH): `root` / `CT_PASSWORD`
  (default `localdev123`, reset on every run). SSH additionally needs
  `openssh-server` + `PermitRootLogin`, so `pct enter` is simplest.
- **The app has no login at all.** There is no authentication in front of the
  workbench, which is why it binds `0.0.0.0` *inside the LXC* and why the LXC
  is the access control. Do not forward 7860 past your LAN.
- **Broker and model credentials** live in
  `/opt/tradingagents/infrastructure/local/.env`, mode 0600, seeded from
  `.env.example` on first run and never touched again.

## Day two

```bash
pct exec 194 -- systemctl status tradingagents      # the stack as one unit
pct exec 194 -- podman ps                           # the containers
pct exec 194 -- make -C /opt/tradingagents/infrastructure/local logs
pct exec 194 -- make -C /opt/tradingagents/infrastructure/local status

# change settings
pct exec 194 -- vi /opt/tradingagents/infrastructure/local/.env
pct exec 194 -- systemctl restart tradingagents

# upgrade to the current main
bash -c "$(curl -fsSL .../tradingagents.sh)"        # reset, rebuild, restart
```

Backups: the LXC holds the image, the checkout, and `.env`. In external mode
the decision history is on the other server and needs its own backup — a
`vzdump` of 194 alone would restore an app with no memory. In local mode a
`vzdump` covers everything.

## Caveats

- **Written against the standard Proxmox layout but not tested on your node.**
  Confirm the CONFIG block, the template name, and the storage id before the
  first run.
- **Debian 13, not 12.** Debian 12's podman is 4.3 and its podman-compose
  predates dependable `depends_on: service_completed_successfully` — which is
  exactly what stops a worker starting against a schema older than its code.
- **The web UI runs on Werkzeug**, not a production WSGI server. That is a
  deliberate consequence of the server-sent pulse stream, which wants a
  long-lived threaded connection per tab; it is fine for the handful of
  operators this is built for and would not survive being public.
- **The repo must be reachable from the LXC.** If it is private, put a deploy
  key at `/root/.ssh/id_ed25519` inside the CT and set `REPO_URL` to the SSH
  form.
