"""Secure external-service integration configuration."""

from .vault import (
    CredentialStatus,
    IntegrationCredentialVault,
    get_configured_credential,
    get_integration_vault,
    reset_integration_vault,
)

__all__ = [
    "CredentialStatus",
    "IntegrationCredentialVault",
    "get_configured_credential",
    "get_integration_vault",
    "reset_integration_vault",
]
