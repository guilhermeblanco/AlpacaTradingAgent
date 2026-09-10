# Proxmox deployment — one LXC, podman inside

Community-scripts-style: each script is self-contained, **run on the Proxmox
node as root**, and prints how to wire what comes next.

```bash
# on the Proxmox node, as root — database first, app second:
PG_CTID=<postgres ctid> bash -c "$(curl -fsSL https://raw.githubusercontent.com/guilhermeblanco/AlpacaTradingAgent/main/infrastructure/proxmox/postgres-database.sh)"

DATABASE_URL='postgresql+psycopg://tradingagents:<pw>@10.64.8.78:5432/tradingagents' \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/guilhermeblanco/AlpacaTradingAgent/main/infrastructure/proxmox/tradingagents.sh)"
```

Override any CONFIG var inline — on the **first** run:

```bash
CTID=194 IP_CIDR=10.64.8.94/24 STORAGE=local-zfs CORES=6 RAM_MB=8192 \
  bash -c "$(curl -fsSL .../tradingagents.sh)"
```

## Redeploying

```bash
./tradingagents.sh
```

That is the whole command. The first run saves the topology it resolved to
`/etc/tradingagents/deploy-<ctid>.conf` and later runs read it back, so
there is nothing to retype and nothing to get wrong. `DATABASE_URL` is not
needed either — it went into the CT's `.env` on the first run and is left
alone after that.

An environment variable still wins over the saved value, because each one
is stored as `${VAR:-saved}`. Delete a line to fall back to the built-in
default, or the file to forget everything. No credentials are saved there.

`CTID` is the exception: it names the file, so pass it if your deployment
is not 194. It is also the one worth getting right — with the wrong CTID
the script builds a second container rather than updating yours.

A redeploy resets the checkout to `REPO_REF`, rebuilds, and restarts. The
rebuild reuses the dependency layer, so a code-only change takes a minute
or two rather than the first run's forty.

| script | what it does | base | default CT / IP / port |
|---|---|---|---|
| `postgres-database.sh` | role + database on a PostgreSQL you already run | — | reaches `10.64.8.78:5432`, or `PG_CTID` via `pct exec` |
| `tradingagents.sh` | LXC + podman + the app stack, under systemd | Ubuntu 26.04 LTS | 194 · 10.64.8.94 · 7860 |

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

It also needs **`/dev/net/tun`**, which an unprivileged LXC is not given.
Podman's network helpers — pasta, and slirp4netns equally — set a container's
network up by creating a tap device inside its namespace, and without the
device that fails as:

```
setup network: pasta failed with exit code -1:
```

with nothing after the colon. `pct set` has no option for it, so the script
appends two lines to `/etc/pve/lxc/<ctid>.conf` and restarts the container:

```
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
```

If your node has no `/dev/net/tun` at all, `modprobe tun` on the node first.

Storage driver is **fuse-overlayfs**, not the kernel's overlay: nested
overlayfs in an unprivileged LXC is refused on most Proxmox kernels. Set
`STORAGE_DRIVER=vfs` if fuse is unavailable — correct, much slower, much
larger on disk.

The script installs podman's runtime helpers **by name**, and checks for them
before the build. On Ubuntu they are `Recommends` rather than `Depends`, so
with `--no-install-recommends` podman installs cleanly and then fails at the
moment of use, with an error that names a binary and not a package:

| tool | what breaks without it |
|---|---|
| `pasta` (`passt`) | `podman build` — "could not find pasta, the network namespace can't be configured" |
| `catatonit` | every service, since the compose files set `init: true` |
| `aardvark-dns` | name resolution on the user-defined network, so `postgres` in `POSTGRES_MODE=local` |
| `netavark` | the network backend |
| `crun` | the OCI runtime |

## Why the distribution stopped mattering

The stack relies on `depends_on: condition: service_completed_successfully`
to keep a worker from ever starting against a schema older than its code.
Older podman-compose accepts that key and **ignores** it, which is the worst
of the three possible outcomes: the stack starts, it looks fine, and the
ordering guarantee is quietly gone.

So the script probes for the capability instead of trusting a version number
or a distribution's packaging. podman-compose is a single Python module, so
asking whether it knows the string is cheap and exact. If the packaged one
cannot do it — or the distribution shipped none at all, which happens because
Ubuntu keeps it in `universe` — a current podman-compose goes into
`/opt/podman-compose` and is symlinked ahead of `/usr/bin` on PATH.

That is what makes the base image a preference. Pick whichever apt-based
template you already keep updated.

## It probes before it commits twenty minutes

Between installing podman and building the image, the script runs a two-line
build: the same base image the real one starts from, plus `RUN true`. On a
cold cache that is not extra work — it pulls what STEP 1 needs anyway — and
on a warm one it takes a second.

It is a *build* and not a `podman run` for a reason worth knowing. An earlier
version of this check started a container, passed, and the real build failed
at the same step as always: `podman run` takes the default bridge, while a
RUN step asks buildah for a namespace, and buildah reached for pasta, which
could not configure one in this LXC even with `/dev/net/tun` present.
Exercising the path that works tells you nothing about the path that does not.

The probe also decides. If podman's own choice works it is left alone; if it
does not, the build is given `--network=host` and the answer is written to
`.env`, so a `make build` run by hand in the LXC later does not rediscover
it. RUN steps then share the container's own network, which is all apt and
pip need, and a build publishes nothing. Force it yourself with
`BUILD_NETWORK=host`.

Three attempts at this deployment died at STEP 4 of 7, minutes in. All three
would have surfaced here in seconds.

## The first run is slow

The image installs the full `[app]` extra: chromadb, backtrader, akshare, the
langchain stack, and everything they pull in. On Python 3.14 a number of those
have no published wheel and are compiled. **Budget 15–40 minutes**, and leave
the defaults alone (`CORES=4 RAM_MB=6144 DISK_GB=32`) — a 2 GB container fails
partway through the build and the error does not say why.

**Re-runs are fast.** The image installs dependencies from `pyproject.toml`
before copying the source, so a deploy that changed only Python code reuses
the expensive layer and takes a minute or two. Only a change to
`pyproject.toml` pays the full cost again.

That ordering is load-bearing and easy to undo by accident — `COPY . .`
before the install puts every dependency behind a layer that any edit
invalidates, and a one-line change then recompiles chromadb. There is a
test.

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
- **Broker and model credentials** can live in either place. The setup
  wizard puts them in the **encrypted vault** — Fernet-encrypted rows in
  PostgreSQL, rotatable from the UI without a redeploy. The script
  generates `INTEGRATION_VAULT_KEY` on first run and never regenerates it,
  because replacing it orphans everything stored under the old one. **Back
  that key up with the database.**
- **`/opt/tradingagents/infrastructure/local/.env`**, mode 0600, is the
  other place: seeded from `.env.example` on first run and never touched
  again by a re-run. Anything set here is read if the vault has no value
  for it.

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
- **The base is a preference, not a requirement.** Ubuntu 26.04 LTS by
  default; 24.04 LTS and Debian 13 both work, e.g.
  `TEMPLATE_FILE=debian-13-standard_13.1-2_amd64.tar.zst TEMPLATE_MATCH='^debian-13-standard'`.
  Template point-release suffixes move, so if the named file is not offered
  the script downloads the newest one matching `TEMPLATE_MATCH` and says
  which it picked.
- **The web UI runs on Werkzeug**, not a production WSGI server. That is a
  deliberate consequence of the server-sent pulse stream, which wants a
  long-lived threaded connection per tab; it is fine for the handful of
  operators this is built for and would not survive being public.
- **The repo must be reachable from the LXC.** If it is private, put a deploy
  key at `/root/.ssh/id_ed25519` inside the CT and set `REPO_URL` to the SSH
  form.
