"""Configured integrations, as things rather than as slots.

The old model had one slot per provider: there was an "Alpaca broker" and
you either filled it in or you did not. That cannot hold a paper account
and a live one at the same time, cannot hold a production model key and
an evaluation one, and gives you nowhere to stand while migrating from
one to the other.

An integration here is an *instance*: a kind, a provider, a name you
chose, and its own credentials. Several can exist per kind and exactly
one is active — except for the kinds whose sources contribute together
rather than compete, where several can be.

Credentials live in the vault as before, namespaced per instance, so two
Alpaca instances do not overwrite each other. What makes the whole thing
work without rewriting every caller is that `get_api_key` consults the
*active* instance first: code that asks for `alpaca_api_key` gets
whichever Alpaca instance is currently active, and never learns that
instances exist.

Plain vault entries still resolve underneath, so a deployment configured
before any of this keeps working and can be migrated an instance at a
time rather than all at once.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

#: How an instance's credentials are named in the vault. The instance id
#: rather than the provider, because two instances of one provider are
#: the entire point.
CREDENTIAL_PREFIX = "integration"


def credential_key(instance_id: str, field_key: str) -> str:
    return f"{CREDENTIAL_PREFIX}:{instance_id}:{field_key}"


@dataclass
class Integration:
    """One configured third party."""

    id: str
    kind: str  #: a role id from tradingagents.setup.providers
    provider: str
    name: str
    active: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    updated_by: str = ""
    #: Which of its fields have a stored value. Never the values
    #: themselves — this object is rendered into a browser.
    configured_fields: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        """How it reads in a list: provider first, then what you called it."""
        from tradingagents.setup.providers import role as get_role

        role = get_role(self.kind)
        spec = role.provider(self.provider) if role else None
        provider_label = spec.label if spec else self.provider
        return f"{provider_label} · {self.name}" if self.name else provider_label


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class IntegrationStore:
    """Reads and writes configured integrations."""

    def __init__(self, session_factory, *, vault=None):
        self._session_factory = session_factory
        self._vault = vault

    # ── Reading ─────────────────────────────────────────────────────────
    def list(self, kind: str = "") -> list[Integration]:
        from tradingagents.persistence.postgres.models import IntegrationRow

        with self._session_factory() as session:
            query = session.query(IntegrationRow)
            if kind:
                query = query.filter(IntegrationRow.kind == kind)
            rows = query.order_by(
                IntegrationRow.kind, IntegrationRow.created_at
            ).all()
            return [self._to_integration(row) for row in rows]

    def get(self, instance_id: str) -> Optional[Integration]:
        from tradingagents.persistence.postgres.models import IntegrationRow

        with self._session_factory() as session:
            row = session.get(IntegrationRow, instance_id)
            return self._to_integration(row) if row is not None else None

    def active(self, kind: str) -> Optional[Integration]:
        return next((item for item in self.list(kind) if item.active), None)

    def _to_integration(self, row) -> Integration:
        configured: tuple[str, ...] = ()
        if self._vault is not None:
            from tradingagents.setup.providers import role as get_role

            role = get_role(row.kind)
            spec = role.provider(row.provider) if role else None
            names = [
                credential_key(row.id, item.key) for item in (spec.fields if spec else ())
            ]
            if names:
                try:
                    statuses = self._vault.statuses(names)
                    configured = tuple(
                        name.split(":", 2)[2]
                        for name, status in statuses.items()
                        if status.configured
                    )
                except Exception:
                    configured = ()
        return Integration(
            id=row.id,
            kind=row.kind,
            provider=row.provider,
            name=row.name,
            active=row.active,
            created_at=row.created_at,
            updated_at=row.updated_at,
            updated_by=row.updated_by or "",
            configured_fields=configured,
        )

    # ── Writing ─────────────────────────────────────────────────────────
    def add(
        self,
        kind: str,
        provider: str,
        name: str,
        *,
        credentials: Optional[dict[str, str]] = None,
        activate: bool = False,
        actor: str = "webui",
    ) -> Integration:
        from tradingagents.persistence.postgres.models import IntegrationRow

        instance_id = new_id()
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            session.add(
                IntegrationRow(
                    id=instance_id,
                    kind=kind,
                    provider=provider,
                    name=(name or "").strip(),
                    active=False,
                    created_at=now,
                    updated_at=now,
                    updated_by=actor,
                )
            )
            session.commit()

        if credentials:
            self.set_credentials(instance_id, credentials, actor=actor)
        # The first of its kind activates itself. Adding your only broker
        # and then having to notice it is inactive is a pointless step.
        if activate or not any(
            item.active for item in self.list(kind) if item.id != instance_id
        ):
            self.activate(instance_id, actor=actor)
        return self.get(instance_id)

    def rename(self, instance_id: str, name: str, *, actor: str = "webui") -> None:
        self._update(instance_id, actor=actor, name=(name or "").strip())

    def activate(self, instance_id: str, *, actor: str = "webui") -> None:
        """Make this the one that answers for its kind.

        Deactivating the others is part of activating this one, in one
        transaction. Two active brokers is not a state anything
        downstream knows how to read.
        """
        from tradingagents.persistence.postgres.models import IntegrationRow
        from tradingagents.setup.providers import role as get_role

        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            row = session.get(IntegrationRow, instance_id)
            if row is None:
                raise ValueError(f"no integration {instance_id!r}")
            role = get_role(row.kind)
            exclusive = role is None or role.selection == "one"
            if exclusive:
                for other in (
                    session.query(IntegrationRow)
                    .filter(IntegrationRow.kind == row.kind)
                    .all()
                ):
                    other.active = other.id == instance_id
                    other.updated_at = now
                    other.updated_by = actor
            else:
                row.active = True
                row.updated_at = now
                row.updated_by = actor
            session.commit()

    def deactivate(self, instance_id: str, *, actor: str = "webui") -> None:
        self._update(instance_id, actor=actor, active=False)

    def remove(self, instance_id: str, *, actor: str = "webui") -> None:
        """Delete an instance and the credentials that belonged to it.

        Leaving the credentials behind would be a secret nothing points
        at and nothing can reach to rotate.
        """
        from tradingagents.persistence.postgres.models import IntegrationRow

        instance = self.get(instance_id)
        with self._session_factory() as session:
            row = session.get(IntegrationRow, instance_id)
            if row is None:
                return
            session.delete(row)
            session.commit()

        if instance is not None and self._vault is not None:
            names = [
                credential_key(instance_id, key)
                for key in instance.configured_fields
            ]
            if names:
                try:
                    self._vault.delete_many(names, actor=actor)
                except Exception:
                    pass

    def set_credentials(
        self, instance_id: str, values: dict[str, str], *, actor: str = "webui"
    ) -> int:
        """Store this instance's own credentials.

        Blank means keep, as everywhere else in this interface.
        """
        if self._vault is None:
            raise ValueError("the encrypted vault is not configured")
        entered = {
            credential_key(instance_id, key): str(value).strip()
            for key, value in (values or {}).items()
            if str(value or "").strip()
        }
        if not entered:
            return 0
        return self._vault.set_many(entered, actor=actor)

    def _update(self, instance_id: str, *, actor: str, **fields) -> None:
        from tradingagents.persistence.postgres.models import IntegrationRow

        with self._session_factory() as session:
            row = session.get(IntegrationRow, instance_id)
            if row is None:
                raise ValueError(f"no integration {instance_id!r}")
            for key, value in fields.items():
                setattr(row, key, value)
            row.updated_at = datetime.now(timezone.utc)
            row.updated_by = actor
            session.commit()


# ── Process-wide access ──────────────────────────────────────────────────────
_store: Optional[IntegrationStore] = None
_initialised = False


def get_integration_store() -> Optional[IntegrationStore]:
    """The process's integration store, or None with no database."""
    global _store, _initialised
    if _initialised:
        return _store
    try:
        import os

        from tradingagents.integrations.vault import get_integration_vault
        from tradingagents.persistence.postgres.database import (
            DatabaseSettings,
            create_database_engine,
            create_session_factory,
        )

        if os.getenv("DATABASE_URL"):
            engine = create_database_engine(DatabaseSettings.from_env())
            _store = IntegrationStore(
                create_session_factory(engine), vault=get_integration_vault()
            )
    except Exception:
        _store = None
    _initialised = True
    return _store


def reset_integration_store() -> None:
    global _store, _initialised
    _store = None
    _initialised = False


def active_credential(field_key: str) -> Optional[str]:
    """The active instance's value for a field, if any instance owns it.

    This is what lets every existing caller keep asking for
    `alpaca_api_key` and get whichever Alpaca instance is active. It
    returns None rather than raising for every reason it can fail —
    no database, no vault, no instance — because those are all normal
    states and the layers underneath still have answers.
    """
    store = get_integration_store()
    if store is None:
        return None
    try:
        from tradingagents.setup.providers import ROLES

        for role in ROLES:
            for spec in role.providers:
                if not any(item.key == field_key for item in spec.fields):
                    continue
                instance = store.active(role.id)
                if instance is None or instance.provider != spec.id:
                    continue
                vault = store._vault
                if vault is None:
                    return None
                return vault.get(credential_key(instance.id, field_key))
    except Exception:
        return None
    return None
