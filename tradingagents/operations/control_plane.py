"""Durable service controls and health projections."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ServiceControl(BaseModel):
    service: str
    paused: bool = False
    reason: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None


class ServiceHeartbeat(BaseModel):
    service: str
    instance_id: str
    status: str
    last_seen_at: datetime
    stale: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class OperationalHealth(BaseModel):
    observed_at: datetime
    controls: list[ServiceControl] = Field(default_factory=list)
    heartbeats: list[ServiceHeartbeat] = Field(default_factory=list)
    reconciliation_pending: int = 0
    reconciliation_oldest_lag_seconds: float = 0.0
    outbox_pending: int = 0
    outbox_dead_lettered: int = 0
    analyses_in_flight: int = 0
    active_reservation_allocations: int = 0
    active_executions: int = 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or update worker controls")
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--stale-after-seconds", type=float, default=600)
    check = subparsers.add_parser("check")
    check.add_argument("service")
    check.add_argument("--stale-after-seconds", type=float, default=600)
    for command in ("pause", "resume"):
        action = subparsers.add_parser(command)
        action.add_argument("service")
        action.add_argument("--reason")
        action.add_argument("--updated-by", default=os.getenv("USER", "operator"))
    args = parser.parse_args()

    from tradingagents.persistence import build_persistence_runtime

    runtime = build_persistence_runtime(
        {
            "persistence_backend": os.getenv("PERSISTENCE_BACKEND", "postgres"),
            "database_url": os.getenv("DATABASE_URL"),
        }
    )
    if runtime.unit_of_work_factory is None:
        runtime.close()
        raise ValueError("operational control plane requires PostgreSQL")
    try:
        with runtime.unit_of_work_factory() as uow:
            if args.command in {"status", "check"}:
                health = uow.operations.health(
                    stale_after_seconds=args.stale_after_seconds
                )
                output = health.model_dump(mode="json")
            else:
                output = uow.operations.set_paused(
                    args.service,
                    paused=args.command == "pause",
                    reason=args.reason,
                    updated_by=args.updated_by,
                ).model_dump(mode="json")
            uow.commit()
        print(json.dumps(output, indent=2, sort_keys=True))
        if args.command == "check":
            fresh = [
                heartbeat
                for heartbeat in health.heartbeats
                if heartbeat.service == args.service.lower().strip()
                and not heartbeat.stale
                and heartbeat.status in {"healthy", "running", "paused"}
            ]
            if not fresh:
                raise SystemExit(1)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
