"""Secure external-service integration configuration."""

from .instances import (
    Integration,
    IntegrationStore,
    active_credential,
    get_integration_store,
    reset_integration_store,
)
from .vault import (
    CredentialStatus,
    IntegrationCredentialVault,
    get_configured_credential,
    get_integration_vault,
    reset_integration_vault,
)

__all__ = [
    "CredentialStatus",
    "Integration",
    "IntegrationStore",
    "active_credential",
    "get_integration_store",
    "reset_integration_store",
    "IntegrationCredentialVault",
    "get_configured_credential",
    "get_integration_vault",
    "reset_integration_vault",
]
