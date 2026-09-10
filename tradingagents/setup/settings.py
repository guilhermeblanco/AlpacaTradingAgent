"""Settings an operator can change without a redeploy.

Precedence is **runtime setting > environment > built-in default**. The
environment supplies the first boot's answer and stops being authoritative
after that, so the UI toggles always work and there is one place to look.

That is a deliberate trade and it is worth writing down which way it cuts.
A redeployed `.env` no longer restrains anything: setting
`EXECUTION_GATEWAY=dry-run` in the deployment does not stop someone
switching to `broker` in the browser, and there is no login in front of
that browser. What the environment gives up in authority, this module
tries to give back in accountability:

  * every change is journalled with the previous value, the actor, and a
    reason, in a table you can read with psql;
  * the changes that increase what the system may do are marked
    `dangerous`, and the UI makes those a confirmation rather than a
    toggle;
  * nothing here is cached beyond a short TTL, so a worker picks up a
    change within a cycle rather than at the next restart.

Only settings named in `SETTINGS` can be changed this way. An operator
being able to edit arbitrary configuration keys from a browser is a
different and much worse idea than being able to turn the worker off.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional

SettingType = Literal["bool", "text", "choice"]

DEFAULT_SCOPE = "default"

#: How long a read is reused before going back to the database. Short
#: enough that a change lands within a worker cycle, long enough that a
#: tight loop does not hammer PostgreSQL.
CACHE_TTL_SECONDS = 5.0


@dataclass(frozen=True)
class Setting:
    """One thing an operator may change while the system is running."""

    key: str  #: the config key it overrides
    label: str
    blurb: str
    type: SettingType = "bool"
    default: Any = None
    choices: tuple[tuple[str, str], ...] = ()
    #: True when *some* value of this setting lets the system do more than
    #: it could before. Those transitions get a confirmation, not a toggle.
    dangerous: bool = False
    #: Which value is the cautious one. Moving away from it is what counts
    #: as dangerous; moving back towards it never does.
    safe_value: Any = None

    @property
    def env_var(self) -> str:
        return self.key.upper()

    def is_dangerous_change(self, value: Any) -> bool:
        if not self.dangerous:
            return False
        return _normalise(self, value) != _normalise(self, self.safe_value)


def _normalise(setting: Setting, value: Any) -> Any:
    if setting.type == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    return str(value).strip().lower()


#: The allow-list. Adding to it is a decision about what a browser with no
#: authentication in front of it is permitted to change.
SETTINGS: tuple[Setting, ...] = (
    Setting(
        "autonomous_enabled", "Autonomous worker",
        "Whether the worker scans and dispatches without anyone clicking. "
        "Off, it stays idle.",
        "bool", default=False, dangerous=True, safe_value=False,
    ),
    Setting(
        "execution_gateway", "Execution gateway",
        "dry-run prices an intent and takes it through every gate, then "
        "does not send it. broker sends it.",
        "choice", default="dry-run",
        # `alpaca` is the legacy spelling of "send it", still accepted by
        # the config model and still the built-in default, so it has to be
        # offered — dropping it from the list would silently rewrite an
        # existing deployment's setting the first time anyone saved.
        choices=(("dry-run", "Dry run — gate it, do not send"),
                 ("broker", "Broker — actually send"),
                 ("alpaca", "Alpaca — actually send (legacy spelling)")),
        dangerous=True, safe_value="dry-run",
    ),
    Setting(
        "alpaca_use_paper", "Alpaca account",
        "Which account an order would reach.",
        # Not a config-model field: it is read through get_api_key, whose
        # consumers all lowercase-and-compare, so "true"/"false" is the
        # right stored shape.
        "bool", default=True,
        dangerous=True, safe_value=True,
    ),
    Setting(
        "llm_provider", "Model provider",
        "Which provider every analyst reaches.",
        "choice", default="openai",
    ),
    Setting(
        "execution_broker", "Broker",
        "Where positions and orders come from and go to.",
        "choice", default="alpaca",
    ),
    Setting(
        "research_market_data_provider", "Market data",
        "Where bars and quotes come from.",
        "choice", default="alpaca",
    ),
    Setting(
        "autonomous_asset_filter", "Assets in scope",
        "What the worker is allowed to look at.",
        "choice", default="all",
        choices=(("all", "Stocks and crypto"), ("stock", "Stocks only"),
                 ("crypto", "Crypto only")),
    ),
    Setting(
        "autonomous_interval_seconds", "Cycle interval",
        "Seconds between autonomous cycles.",
        "text", default="1800",
    ),
    Setting(
        "autonomous_requested_notional_usd", "Requested size",
        "What each intent asks for before the gates clip it.",
        "text", default="1000",
    ),
    # ── Limits ───────────────────────────────────────────────────────
    # `daily_llm_token_budget` has existed as a validated config field
    # since the safety guard was written, with no environment variable
    # and no UI — so the only way to set it was to edit the source. It
    # is the one limit that stops a runaway loop costing real money, so
    # that is a poor place for it to live.
    Setting(
        "daily_llm_token_budget", "Daily token budget",
        "Refuse to start new analyses after this many LLM tokens in a "
        "day. 0 means no limit, which is the current default.",
        "text", default="0",
    ),
    Setting(
        "autonomous_max_concurrency", "Analyses in parallel",
        "How many symbols the autonomous worker analyses at once. More "
        "finishes a cycle sooner and spends the token budget faster.",
        "text", default="2",
    ),
    Setting(
        "autonomous_provider_concurrency", "Calls per provider",
        "Concurrent requests to one model provider. Raise it only as far "
        "as your rate limit allows; past that it buys retries.",
        "text", default="2",
    ),
    Setting(
        "autonomous_max_candidates", "Candidates per cycle",
        "How many symbols the screener may hand over in one cycle.",
        "text", default="3",
    ),
    Setting(
        "evaluation_worker_interval_seconds", "Evaluation cadence",
        "Seconds between evaluation passes. Also decides how long the "
        "worker may be silent before it is reported as stale.",
        "text", default="300",
    ),
    Setting(
        "require_point_in_time_web_search", "Point-in-time sourcing",
        "Stand the hosted web search down even for current dates, so a run "
        "is reproducible from dated sources alone.",
        "bool", default=False,
    ),
)

SETTINGS_BY_KEY = {item.key: item for item in SETTINGS}


def setting(key: str) -> Optional[Setting]:
    return SETTINGS_BY_KEY.get(key)


class RuntimeSettings:
    """Reads and writes the operator-changeable settings."""

    def __init__(self, session_factory, *, scope: str = DEFAULT_SCOPE):
        self._session_factory = session_factory
        self.scope = scope.strip() or DEFAULT_SCOPE
        self._cache: dict[str, str] = {}
        self._cache_at = 0.0
        self._lock = threading.Lock()

    # ── Reading ─────────────────────────────────────────────────────────
    def all(self, *, fresh: bool = False) -> dict[str, str]:
        """Every stored override, cached briefly."""
        with self._lock:
            if not fresh and self._cache_at and (
                time.monotonic() - self._cache_at
            ) < CACHE_TTL_SECONDS:
                return dict(self._cache)

        from tradingagents.persistence.postgres.models import RuntimeSettingRow

        with self._session_factory() as session:
            rows = (
                session.query(RuntimeSettingRow)
                .filter(RuntimeSettingRow.scope == self.scope)
                .all()
            )
            values = {row.name: row.value for row in rows}

        with self._lock:
            self._cache = values
            self._cache_at = time.monotonic()
        return dict(values)

    def invalidate(self) -> None:
        with self._lock:
            self._cache_at = 0.0

    # ── Writing ─────────────────────────────────────────────────────────
    def set(self, key: str, value: Any, *, actor: str, reason: str = "") -> None:
        """Store one override and journal the change.

        Refuses anything outside the allow-list. A browser being able to
        turn the worker off is reasonable; a browser being able to rewrite
        arbitrary configuration is not.
        """
        item = setting(key)
        if item is None:
            raise ValueError(f"{key!r} is not an operator-changeable setting")

        stored = _serialise(item, value)

        from tradingagents.persistence.postgres.models import (
            RuntimeSettingAuditRow,
            RuntimeSettingRow,
        )

        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            row = session.get(RuntimeSettingRow, (self.scope, key))
            previous = row.value if row is not None else None
            if row is None:
                session.add(
                    RuntimeSettingRow(
                        scope=self.scope, name=key, value=stored,
                        updated_at=now, updated_by=actor,
                    )
                )
            else:
                row.value = stored
                row.updated_at = now
                row.updated_by = actor
            session.add(
                RuntimeSettingAuditRow(
                    event_id=str(uuid.uuid4()),
                    scope=self.scope,
                    name=key,
                    previous_value=previous,
                    new_value=stored,
                    actor=actor,
                    reason=reason or None,
                    occurred_at=now,
                )
            )
            session.commit()
        self.invalidate()

    def clear(self, key: str, *, actor: str, reason: str = "") -> None:
        """Drop an override, so the environment answers again."""
        from tradingagents.persistence.postgres.models import (
            RuntimeSettingAuditRow,
            RuntimeSettingRow,
        )

        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            row = session.get(RuntimeSettingRow, (self.scope, key))
            if row is None:
                return
            session.delete(row)
            session.add(
                RuntimeSettingAuditRow(
                    event_id=str(uuid.uuid4()),
                    scope=self.scope,
                    name=key,
                    previous_value=row.value,
                    new_value=None,
                    actor=actor,
                    reason=reason or None,
                    occurred_at=now,
                )
            )
            session.commit()
        self.invalidate()

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Recent changes, newest first."""
        from tradingagents.persistence.postgres.models import RuntimeSettingAuditRow

        with self._session_factory() as session:
            rows = (
                session.query(RuntimeSettingAuditRow)
                .filter(RuntimeSettingAuditRow.scope == self.scope)
                .order_by(RuntimeSettingAuditRow.occurred_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "name": row.name,
                    "previous": row.previous_value,
                    "new": row.new_value,
                    "actor": row.actor,
                    "reason": row.reason,
                    "at": row.occurred_at,
                }
                for row in rows
            ]


def _serialise(item: Setting, value: Any) -> str:
    if item.type == "bool":
        return "true" if _normalise(item, value) else "false"
    return str(value).strip()


def deserialise(item: Setting, raw: str) -> Any:
    if item.type == "bool":
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(item.default, bool):
        # A choice whose options are booleans, like paper/live.
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    return raw


# ── Process-wide access ──────────────────────────────────────────────────────
_settings: Optional[RuntimeSettings] = None
_initialised = False
_init_lock = threading.Lock()


def get_runtime_settings() -> Optional[RuntimeSettings]:
    """The process's settings store, or None when there is no database."""
    global _settings, _initialised
    if _initialised:
        return _settings
    with _init_lock:
        if _initialised:
            return _settings
        try:
            from tradingagents.persistence.postgres.database import (
                DatabaseSettings,
                create_database_engine,
                create_session_factory,
            )

            if os.getenv("DATABASE_URL"):
                engine = create_database_engine(DatabaseSettings.from_env())
                _settings = RuntimeSettings(
                    create_session_factory(engine),
                    scope=os.getenv("RUNTIME_SETTINGS_SCOPE", DEFAULT_SCOPE),
                )
        except Exception:
            # No database, or an unreachable one. The environment still
            # answers; settings simply cannot be changed from the UI.
            _settings = None
        _initialised = True
        return _settings


def reset_runtime_settings() -> None:
    global _settings, _initialised
    with _init_lock:
        _settings = None
        _initialised = False


def apply_overrides(config: dict, *, store=None) -> dict:
    """Layer stored overrides on top of a configuration.

    Called wherever a configuration is assembled, so the precedence holds
    everywhere rather than only where somebody remembered.
    """
    store = store if store is not None else get_runtime_settings()
    if store is None:
        return config
    try:
        stored = store.all()
    except Exception:
        return config

    for key, raw in stored.items():
        item = setting(key)
        if item is None:
            # A setting removed from the allow-list since it was stored.
            # Ignored rather than applied: the list is what is permitted
            # now, not what was permitted then.
            continue
        config[key] = deserialise(item, raw)
    return config
