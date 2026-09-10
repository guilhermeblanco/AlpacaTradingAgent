"""What each background service is, and how often it should be heard from.

Two bugs came out of not having this written down anywhere.

*Every restart left a tombstone.* Worker identities were
`f"evaluation-{uuid4()}"` — a fresh identity per process — and the
heartbeat table is keyed `(service, instance_id)` with nothing ever
deleting a row. So four redeploys left eight rows, and the vitals strip,
which counts rows, reported "1/8 live". Nothing was broken; the counter
was measuring deployment history.

*A worker could never look alive.* The UI judged staleness at a
hardcoded 120 seconds while the evaluation worker heartbeats once per
cycle, default 300. It was therefore permanently stale in the UI and
permanently healthy to its own container healthcheck, which allows 900.
The two disagreed and the UI was wrong.

Both are the same root cause: how often a service speaks is a property
of that service, and it was written down in three places that did not
have to agree. Here it is written down once.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ServiceProfile:
    """One long-lived process, and what "healthy" means for it."""

    name: str
    label: str
    #: How often it is expected to heartbeat, at its default settings.
    beat_seconds: float
    #: The setting that changes that cadence, when there is one. Named so
    #: the tolerance below can be recomputed rather than guessed at.
    cadence_setting: str = ""
    #: Whether the deployment is expected to be running one at all. The
    #: autonomous worker is opt-in, so its absence is not a fault.
    expected: bool = True

    @property
    def stale_after_seconds(self) -> float:
        """Silence long enough to mean something is wrong.

        Three missed beats plus a minute. Generous on purpose: a worker
        reported dead while it is merely between cycles trains an
        operator to ignore the indicator, and then it is worth nothing
        on the day it is right.
        """
        return self.beat_seconds * 3 + 60


PROFILES: tuple[ServiceProfile, ...] = (
    ServiceProfile(
        "evaluation-worker", "Evaluation", 300.0,
        cadence_setting="evaluation_worker_interval_seconds",
    ),
    ServiceProfile(
        "reconciliation-worker", "Reconciliation", 5.0,
        cadence_setting="reconciliation_worker_interval_seconds",
    ),
    ServiceProfile(
        "autonomous-worker", "Autonomous", 1800.0,
        cadence_setting="autonomous_interval_seconds",
        expected=False,
    ),
)

PROFILES_BY_NAME = {item.name: item for item in PROFILES}

#: Anything heartbeating under a name nobody registered still gets a
#: tolerance, rather than being judged by whichever number was nearest.
DEFAULT_STALE_AFTER_SECONDS = 900.0


def profile(name: str) -> Optional[ServiceProfile]:
    return PROFILES_BY_NAME.get(str(name or "").strip().lower())


def stale_after(name: str, config=None) -> float:
    """How long this service may be silent before it is a problem."""
    item = profile(name)
    if item is None:
        return DEFAULT_STALE_AFTER_SECONDS
    if config and item.cadence_setting:
        try:
            beat = float(config.get(item.cadence_setting) or item.beat_seconds)
        except (TypeError, ValueError):
            beat = item.beat_seconds
        return beat * 3 + 60
    return item.stale_after_seconds


def instance_id(service: str) -> str:
    """A stable identity for this process's heartbeat row.

    Stable is the whole point. A uuid per process means a new row per
    restart and a table that only grows; the hostname means a container
    reuses its own row and a redeploy leaves nothing behind.

    Two of the same service on one host — which is what scaling the
    compose service does — need distinguishing, so the replica index is
    honoured when the runtime supplies one.
    """
    explicit = os.getenv(f"{service.upper().replace('-', '_')}_INSTANCE_ID")
    if explicit:
        return explicit.strip()
    host = os.getenv("HOSTNAME") or socket.gethostname()
    replica = os.getenv("REPLICA_ID") or os.getenv("PODMAN_REPLICA")
    return f"{host}-{replica}" if replica else host


def restart_requested(unit_of_work_factory, service: str, started_at) -> bool:
    """Whether somebody asked this service to restart since it started.

    A worker cannot be restarted from the web process — different
    container, no signal, and handing the UI a podman socket to fix that
    would be a much worse trade than this. So a restart is a row: the
    worker notices, exits cleanly between units of work, and the
    container's `restart: unless-stopped` starts it again.

    Never raises. An unreachable database is a reason to keep working,
    not to stop.
    """
    if unit_of_work_factory is None:
        return False
    try:
        with unit_of_work_factory() as uow:
            operations = getattr(uow, "operations", None)
            if operations is None:
                return False
            requested = operations.restart_requested_at(service)
    except Exception:
        return False
    if requested is None or started_at is None:
        return False
    if requested.tzinfo is None:
        from datetime import timezone

        requested = requested.replace(tzinfo=timezone.utc)
    return requested > started_at

