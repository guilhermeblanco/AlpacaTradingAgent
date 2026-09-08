from __future__ import annotations

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import create_engine, select

from tradingagents.integrations import IntegrationCredentialVault
from tradingagents.persistence.postgres import Base, create_session_factory
from tradingagents.persistence.postgres.models import (
    IntegrationCredentialAuditRow,
    IntegrationCredentialRow,
)


@pytest.fixture
def vault():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return IntegrationCredentialVault(
        create_session_factory(engine), Fernet.generate_key(), scope="test"
    )


def test_vault_encrypts_round_trips_and_rotates(vault) -> None:
    assert vault.set_many({"openai_api_key": "sk-first"}, actor="test") == 1
    assert vault.get("openai_api_key") == "sk-first"

    with vault._session_factory() as session:
        row = session.get(IntegrationCredentialRow, ("test", "openai_api_key"))
        assert row is not None
        assert "sk-first" not in row.ciphertext
        assert row.version == 1

    vault.set_many({"openai_api_key": "sk-second"}, actor="test")
    assert vault.get("openai_api_key") == "sk-second"
    with vault._session_factory() as session:
        row = session.get(IntegrationCredentialRow, ("test", "openai_api_key"))
        events = session.scalars(
            select(IntegrationCredentialAuditRow).order_by(
                IntegrationCredentialAuditRow.occurred_at
            )
        ).all()
        assert row.version == 2
        assert [event.action for event in events] == ["created", "rotated"]


def test_vault_status_and_delete_do_not_disclose_values(vault) -> None:
    vault.set_many({"alpaca_api_key": "PK-secret"}, actor="test")
    statuses = vault.statuses(["alpaca_api_key", "finnhub_api_key"])
    assert statuses["alpaca_api_key"].configured
    assert statuses["alpaca_api_key"].source == "vault"
    assert not statuses["finnhub_api_key"].configured

    assert vault.delete_many(["alpaca_api_key"], actor="test") == 1
    assert vault.get("alpaca_api_key") is None
    with vault._session_factory() as session:
        actions = session.scalars(
            select(IntegrationCredentialAuditRow.action)
        ).all()
        assert "deleted" in actions


def test_wrong_encryption_key_fails_closed(vault) -> None:
    vault.set_many({"fred_api_key": "fred-secret"})
    wrong_key_vault = IntegrationCredentialVault(
        vault._session_factory, Fernet.generate_key(), scope="test"
    )
    with pytest.raises(ValueError, match="cannot be decrypted"):
        wrong_key_vault.get("fred_api_key")


def test_dataflow_config_prefers_vault_over_environment(monkeypatch) -> None:
    from tradingagents.dataflows import config
    import tradingagents.integrations

    config.clear_runtime_api_keys()
    monkeypatch.setenv("FINNHUB_API_KEY", "environment-value")
    monkeypatch.setattr(
        tradingagents.integrations,
        "get_configured_credential",
        lambda name: "vault-value" if name == "finnhub_api_key" else None,
    )
    assert config.get_finnhub_api_key() == "vault-value"
