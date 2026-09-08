"""Encrypted, server-side storage for external-service credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Mapping, Optional
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from tradingagents.persistence.postgres.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from tradingagents.persistence.postgres.models import (
    IntegrationCredentialAuditRow,
    IntegrationCredentialRow,
)


DEFAULT_SCOPE = "default"


@dataclass(frozen=True)
class CredentialStatus:
    name: str
    configured: bool
    source: str
    updated_at: Optional[datetime] = None


class IntegrationCredentialVault:
    """Encrypt credentials before they cross the persistence boundary."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        encryption_key: str | bytes,
        *,
        scope: str = DEFAULT_SCOPE,
    ):
        key = (
            encryption_key.encode("ascii")
            if isinstance(encryption_key, str)
            else encryption_key
        )
        try:
            self._cipher = Fernet(key)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "INTEGRATION_VAULT_KEY must be a URL-safe base64 Fernet key"
            ) from exc
        self._session_factory = session_factory
        self.scope = scope.strip() or DEFAULT_SCOPE

    def get(self, name: str) -> Optional[str]:
        with self._session_factory() as session:
            row = session.get(IntegrationCredentialRow, (self.scope, name))
            if row is None:
                return None
            try:
                return self._cipher.decrypt(row.ciphertext.encode("ascii")).decode(
                    "utf-8"
                )
            except InvalidToken as exc:
                raise ValueError(
                    f"Credential {name!r} cannot be decrypted with the configured vault key"
                ) from exc

    def set_many(self, values: Mapping[str, str], *, actor: str = "system") -> int:
        cleaned = {
            str(name).strip(): str(value).strip()
            for name, value in values.items()
            if str(name).strip() and str(value).strip()
        }
        if not cleaned:
            return 0
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            for name, value in cleaned.items():
                row = session.scalar(
                    select(IntegrationCredentialRow)
                    .where(
                        IntegrationCredentialRow.scope == self.scope,
                        IntegrationCredentialRow.name == name,
                    )
                    .with_for_update()
                )
                action = "rotated" if row is not None else "created"
                ciphertext = self._cipher.encrypt(value.encode("utf-8")).decode(
                    "ascii"
                )
                if row is None:
                    row = IntegrationCredentialRow(
                        scope=self.scope,
                        name=name,
                        ciphertext=ciphertext,
                        version=1,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                else:
                    row.ciphertext = ciphertext
                    row.version += 1
                    row.updated_at = now
                session.add(
                    IntegrationCredentialAuditRow(
                        event_id=str(uuid4()),
                        scope=self.scope,
                        credential_name=name,
                        action=action,
                        actor=actor,
                        occurred_at=now,
                    )
                )
            session.commit()
        return len(cleaned)

    def delete_many(self, names: list[str], *, actor: str = "system") -> int:
        normalized = sorted(
            {str(name).strip() for name in names if str(name).strip()}
        )
        if not normalized:
            return 0
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            existing = session.scalars(
                select(IntegrationCredentialRow.name).where(
                    IntegrationCredentialRow.scope == self.scope,
                    IntegrationCredentialRow.name.in_(normalized),
                )
            ).all()
            session.execute(
                delete(IntegrationCredentialRow).where(
                    IntegrationCredentialRow.scope == self.scope,
                    IntegrationCredentialRow.name.in_(normalized),
                )
            )
            for name in existing:
                session.add(
                    IntegrationCredentialAuditRow(
                        event_id=str(uuid4()),
                        scope=self.scope,
                        credential_name=name,
                        action="deleted",
                        actor=actor,
                        occurred_at=now,
                    )
                )
            session.commit()
        return len(existing)

    def statuses(self, names: list[str]) -> dict[str, CredentialStatus]:
        normalized = sorted(
            {str(name).strip() for name in names if str(name).strip()}
        )
        if not normalized:
            return {}
        with self._session_factory() as session:
            rows = session.scalars(
                select(IntegrationCredentialRow).where(
                    IntegrationCredentialRow.scope == self.scope,
                    IntegrationCredentialRow.name.in_(normalized),
                )
            ).all()
        by_name = {row.name: row for row in rows}
        return {
            name: CredentialStatus(
                name=name,
                configured=name in by_name,
                source="vault" if name in by_name else "missing",
                updated_at=by_name[name].updated_at if name in by_name else None,
            )
            for name in normalized
        }


_vault: Optional[IntegrationCredentialVault] = None
_vault_initialized = False
_vault_lock = Lock()
_vault_engine = None


def get_integration_vault() -> Optional[IntegrationCredentialVault]:
    """Build the process vault lazily when its root key is configured."""
    global _vault, _vault_initialized, _vault_engine
    if _vault_initialized:
        return _vault
    with _vault_lock:
        if _vault_initialized:
            return _vault
        encryption_key = os.getenv("INTEGRATION_VAULT_KEY", "").strip()
        if encryption_key:
            settings = DatabaseSettings.from_env()
            _vault_engine = create_database_engine(settings)
            _vault = IntegrationCredentialVault(
                create_session_factory(_vault_engine),
                encryption_key,
                scope=os.getenv("INTEGRATION_VAULT_SCOPE", DEFAULT_SCOPE),
            )
        _vault_initialized = True
        return _vault


def get_configured_credential(name: str) -> Optional[str]:
    vault = get_integration_vault()
    return vault.get(name) if vault is not None else None


def reset_integration_vault() -> None:
    """Release cached runtime resources; primarily useful for tests."""
    global _vault, _vault_initialized, _vault_engine
    with _vault_lock:
        if _vault_engine is not None:
            _vault_engine.dispose()
        _vault = None
        _vault_engine = None
        _vault_initialized = False
