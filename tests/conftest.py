"""Disposable credentials for native imports; never read an operator's keys."""

import os
import secrets

# ContextForge validates secrets while importing its application. Set these before
# collection, including when no .env exists, without weakening its validation.
for name in (
    "JWT_SECRET_KEY",
    "AUTH_ENCRYPTION_SECRET",
    "BASIC_AUTH_PASSWORD",
    "PLATFORM_ADMIN_PASSWORD",
    "DEFAULT_USER_PASSWORD",
):
    os.environ[name] = secrets.token_urlsafe(48)
