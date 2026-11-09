"""Generate credentials without printing them. Never overwrite an existing .env."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
values = {
    "JWT_SECRET_KEY": secrets.token_urlsafe(48),
    "AUTH_ENCRYPTION_SECRET": secrets.token_urlsafe(48),
    "AOG_DELEGATION_SECRET": secrets.token_urlsafe(48),
    "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
    "KOMODO_JWT_SECRET": secrets.token_urlsafe(48),
    "PLATFORM_ADMIN_EMAIL": "admin@example.com",
    "KOMODO_DATABASE_PASSWORD": secrets.token_urlsafe(32),
    "KOMODO_ADMIN_PASSWORD": secrets.token_urlsafe(32),
    "KOMODO_WEBHOOK_SECRET": secrets.token_urlsafe(48),
    "PLATFORM_ADMIN_PASSWORD": "Aa1!" + secrets.token_urlsafe(24),
    "DEFAULT_USER_PASSWORD": "Aa1!" + secrets.token_urlsafe(24),
    "BASIC_AUTH_PASSWORD": secrets.token_urlsafe(24),
    "AUTH_REQUIRED": "true",
    "MCP_CLIENT_AUTH_ENABLED": "true",
    "EMAIL_AUTH_ENABLED": "true",
    "MCPGATEWAY_UI_ENABLED": "true",
    "MCPGATEWAY_ADMIN_API_ENABLED": "true",
    "LOG_LEVEL": "ERROR",
}
path = root / ".env"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.write("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
print(
    "Created owner-readable .env. Read administrator credentials locally; keep the encryption keys in backups."
)
