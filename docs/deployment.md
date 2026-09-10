# Deployment

One image, four roles, podman. This describes what runs, why it is arranged
this way, and what has to change before the system can trade.

For the Proxmox LXC script that builds all of it on a node, see
[infrastructure/proxmox/README.md](../infrastructure/proxmox/README.md).

## What runs

| service | command | why it is separate |
|---|---|---|
| `migrate` | `alembic upgrade head` | Runs to completion before anything else starts. A worker on a schema older than its code writes rows nothing can read back. |
| `web` | `run_webui_dash.py` | The workbench. Port 7860. |
| `evaluation-worker` | `tradingagents.evaluation.worker` | Resolves decisions into outcomes at each horizon, long after the decision was made. |
| `reconciliation-worker` | `tradingagents.execution.reconciliation_worker` | Keeps the broker's view and ours in agreement, on a much tighter loop than the others. |
| `autonomous-worker` | `tradingagents.orchestration.autonomous_worker` | Opt-in. The only service that can place an order with nobody watching. |
| `postgres` | — | Opt-in. Only when the app should own its database. |

They share **one image**, built once from `Containerfile`. That is the point
rather than an economy: a decision's record is written by the web process,
resolved by the evaluation worker, and reconciled by a third. If those drift
apart by a deploy, the record stops joining up. One image with several entry
points makes the drift impossible rather than unlikely.

## Files

```
Containerfile                                  the image
.containerignore                               what stays out of the build context
infrastructure/local/
  podman-compose.yml                           migrate, web, and the two always-on workers
  podman-compose.postgres.yml                  overlay: PostgreSQL in the stack
  podman-compose.autonomous.yml                overlay: the autonomous worker
  Makefile                                     owns the build, the overlays, and DATABASE_URL
  .env.example                                 the deployment's own settings
infrastructure/proxmox/
  tradingagents.sh                             LXC + podman + the stack, under systemd
  postgres-database.sh                         role and database on a server you already run
```

## Running it

```bash
cd infrastructure/local
cp .env.example .env && $EDITOR .env
make build
make up
```

`make` is the interface, not a convenience wrapper. It owns three things the
compose files deliberately do not:

- **The image build.** Compose providers disagree about when a build is
  implied and how many times it runs for services sharing an image. `podman
  build` does not, so the Makefile calls it directly and the compose files
  carry no `build:` key at all.
- **Which overlays apply.** `POSTGRES_MODE=local` and `AUTONOMOUS=1` add a
  file each.
- **`DATABASE_URL`.** One mechanism: compose interpolates `${DATABASE_URL}`
  into every service. In external mode the value comes from `.env`, which
  compose reads for interpolation by itself. In local mode the Makefile
  exports the in-stack URL, and a shell variable beats `.env`. So the answer
  to "which database is it talking to" is always `podman inspect
  tradingagents-web`, never an archaeology of overlays.

`make preflight` runs before `up` and refuses to start on the two mistakes
that fail quietly rather than loudly: a missing `.env` (the containers come up
with no API keys and fail a layer down) and an empty `DATABASE_URL` in
external mode (the app falls back to its localhost default and starts a
second, empty history nobody is looking at).

Other targets: `logs`, `status`, `shell`, `psql`, `restart`, `down`, `reset`
(which asks you to type `destroy`, because it deletes the decision history).

## Podman, specifically

- **No daemon.** Nothing runs as a root daemon, which is what lets the Proxmox
  LXC stay unprivileged.
- **`--in-pod=false`.** podman-compose puts every service in one pod by
  default, and containers in a pod share a network namespace — they reach each
  other on localhost and service-name DNS does not resolve. That breaks
  `postgres` as a hostname the moment `POSTGRES_MODE=local`. The Makefile
  passes the flag when the installed podman-compose understands it.
- **Fully-qualified image names.** `docker.io/library/postgres:17-alpine`, not
  `postgres:17-alpine`. Podman refuses to guess a registry for a short name
  non-interactively, and a provisioning script has no one to ask.
- **`.containerignore`, `Containerfile`.** Podman prefers both names; there is
  no Docker in this repo.
- **In a nested unprivileged LXC**, podman needs `nesting=1,keyctl=1,fuse=1`
  on the container and the `fuse-overlayfs` storage driver, because the kernel
  refuses overlayfs-on-overlayfs there. The Proxmox script sets both.

## Health

`/healthz` on the web service is a plain 200 from the Flask server behind
Dash. It is **liveness, not readiness**: it does not touch PostgreSQL, the
broker, or any model provider.

That restraint is deliberate. A probe that checked the database would restart
the web UI every time PostgreSQL blipped — and the web UI is exactly where an
operator goes to find out that PostgreSQL has blipped. Dependency health is
already reported per dependency in the vitals strip, which is the right place
for it.

The workers have no HTTP surface, so their probes read the control plane's
heartbeat instead: `control_plane check <service> --stale-after-seconds N`. A
loop that is wedged but alive therefore reads as unhealthy, which is the whole
point of checking a heartbeat rather than a process.

`TRADINGAGENTS_STRICT_PORT=1` is set in the image. On a laptop the web entry
point hunts for a free port when 7860 is taken, which is a convenience,
because the browser follows what the console prints. Under a container runtime
it is a trap: the port is published by the pod, so moving to 7861 does not
relocate the mapping — it makes the service unreachable while the process
reports that it started fine.

## Can it trade?

Three independent things must change, and each defaults to no:

1. **The autonomous worker is not started.** It is in its own overlay file, so
   running it is `AUTONOMOUS=1 make up`. A compose `profile` would have done
   the same job, but a provider that quietly ignores an unknown key would then
   start it, and that is not a mistake this stack should be able to make.
2. **`AUTONOMOUS_ENABLED=false`.** The worker runs, scans, and dispatches
   nothing.
3. **`EXECUTION_GATEWAY=dry-run`.** Intents are priced and taken through every
   gate, and then not sent.

The useful place to sit for a while is (1) and (2) on with (3) still
`dry-run`: the whole pipeline runs and journals what it *would* have done, and
you can read the gate ledgers in the workbench for a week before deciding.
When you do move to `broker`, check `ALPACA_USE_PAPER` on the same trip.

`SAFETY_STATE_SCOPE` deserves a moment too. It names the broker account whose
kill switch, high-water mark and rejection streak this deployment shares. Two
deployments pointed at one database with the **same** scope will lift each
other's quarantines; with different scopes they will not see each other's
halts. Neither is wrong, but pick on purpose.

## There is no login

Nothing authenticates in front of the workbench. In the Proxmox deployment the
LXC boundary is the access control, which is why the container binds `0.0.0.0`
*inside* the LXC and why `HOST_BIND` defaults to `127.0.0.1` everywhere else.
Do not forward 7860 past a network you trust.

The web UI also runs on Werkzeug rather than a production WSGI server. That
follows from the server-sent pulse stream, which wants a long-lived threaded
connection per tab; it suits the handful of operators this is built for and
would not survive being public.

## Secrets

`infrastructure/local/.env`, mode 0600, holds live broker and model
credentials. It is git-ignored, seeded once from `.env.example`, and the
Proxmox script never overwrites an existing one — a re-run that quietly reset
credentials to blanks is the worst possible behaviour for something advertised
as idempotent.

If a password contains `@ : / ?` it must be percent-encoded in `DATABASE_URL`.
That is the main reason the database is configured as one URL rather than four
parts.

## Backups

In **external** mode the decision history, gate ledgers, and evaluation
outcomes live on the other PostgreSQL. A `vzdump` of the app LXC alone would
restore an application with no memory. Back up the database separately.

In **local** mode a `vzdump` of the LXC covers everything, which is the main
argument for that shape.

Either way, `.env` is the one thing in the LXC that cannot be rebuilt from the
repository.
