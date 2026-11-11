"""Compatibility imports for earlier extensions; Core uses aog.backends instead."""
from aog.integrations.contextforge.native import (
    LOCAL_METADATA,
    SERVICE_METADATA,
    Catalog,
    authenticate,
    authorize,
    decrypt_secret,
    encrypt_secret,
    local_dispatch,
    prepare_gateway,
    principal,
)

__all__ = ["LOCAL_METADATA", "SERVICE_METADATA", "Catalog", "authenticate", "authorize",
           "decrypt_secret", "encrypt_secret", "local_dispatch", "prepare_gateway", "principal"]
