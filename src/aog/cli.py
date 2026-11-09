import argparse
import os
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="AgentPort independent gateway")
    parser.add_argument("command", choices=["init", "serve"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4444)
    args = parser.parse_args()
    if args.command == "init":
        env = Path(".env")
        if env.exists():
            parser.error(".env already exists; refusing to overwrite credentials")
        Path("data").mkdir(mode=0o700, exist_ok=True)
        lines = {
            "JWT_SECRET_KEY": secrets.token_urlsafe(48),
            "AUTH_ENCRYPTION_SECRET": secrets.token_urlsafe(48),
            "AOG_DELEGATION_SECRET": secrets.token_urlsafe(48),
            "PLATFORM_ADMIN_EMAIL": "admin@example.com",
            "PLATFORM_ADMIN_PASSWORD": "Aa1!" + secrets.token_urlsafe(24),
            "DEFAULT_USER_PASSWORD": "Aa1!" + secrets.token_urlsafe(24),
            "BASIC_AUTH_PASSWORD": secrets.token_urlsafe(24),
            "DATABASE_URL": "sqlite:///data/contextforge.db",
            "AOG_DATABASE_URL": "sqlite:///data/orchestration.db",
            "DBOS_SYSTEM_DATABASE_URL": "sqlite:///data/dbos.db",
            "AUTH_REQUIRED": "true",
            "MCP_CLIENT_AUTH_ENABLED": "true",
            "EMAIL_AUTH_ENABLED": "true",
            "MCPGATEWAY_UI_ENABLED": "true",
            "MCPGATEWAY_ADMIN_API_ENABLED": "true",
            "LOG_LEVEL": "ERROR",
        }
        with os.fdopen(os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
            output.write("\n".join(f"{k}={v}" for k, v in lines.items()) + "\n")
        print("Created .env with random secrets. Read it locally for the initial administrator credentials.")
        return
    from dotenv import load_dotenv

    load_dotenv()
    Path("data").mkdir(mode=0o700, exist_ok=True)
    import uvicorn

    uvicorn.run("aog.app:app", host=args.host, port=args.port, workers=1, log_level="warning")


if __name__ == "__main__":
    main()
