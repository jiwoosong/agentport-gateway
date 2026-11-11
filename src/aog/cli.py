import argparse
import asyncio
import json
import os
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="AgentPort Core")
    parser.add_argument("command", choices=["init", "serve", "backup", "migrate"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4455)
    parser.add_argument("--output", help="New private backup directory (backup/migrate)")
    parser.add_argument("--postgres-backup", help="Operator pg_dump custom archive for one configured PostgreSQL database")
    parser.add_argument("--offline", action="store_true", help="Confirm central and worker writers are stopped")
    args = parser.parse_args()
    if args.command == "init":
        env = Path(".env")
        if env.exists():
            parser.error(".env already exists; refusing to overwrite credentials")
        Path("data").mkdir(mode=0o700, exist_ok=True)
        lines = {
            "JWT_SECRET_KEY": secrets.token_urlsafe(48),
            "AUTH_ENCRYPTION_SECRET": secrets.token_urlsafe(48),
            "AOG_GATEWAY_BRIDGE_SECRET": secrets.token_urlsafe(48),
            "AOG_GATEWAY_BACKEND": "contextforge",
            "AOG_GATEWAY_URL": "http://127.0.0.1:4444",
            "AOG_CORE_URL": "http://127.0.0.1:4455",
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
            "MCPGATEWAY_DIRECT_PROXY_ENABLED": "false",
            "MCPGATEWAY_A2A_ENABLED": "false",
            "LLM_API_ENABLED": "false",
            "VALIDATION_MAX_RPC_PARAM_SIZE": "33554432",
            "LOG_LEVEL": "ERROR",
        }
        with os.fdopen(os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
            output.write("\n".join(f"{k}={v}" for k, v in lines.items()) + "\n")
        print("Created .env with random secrets. Read it locally for the initial administrator credentials.")
        return
    from dotenv import load_dotenv

    load_dotenv()
    if args.command in {"backup", "migrate"}:
        if args.command == "migrate" and not args.offline:
            parser.error("migrate requires --offline: stop central/worker writers and drain external jobs first")
        from aog.migration import create_backup

        backup = create_backup(args.output, postgres_backup=args.postgres_backup)
        print(f"Private backup: {backup}")
        if args.command == "migrate":
            previous_migrating = os.environ.get("AOG_MIGRATING")
            os.environ["AOG_MIGRATING"] = "1"

            async def migrate():
                from aog.app import app

                async with app.router.lifespan_context(app):
                    return app.state.migration_report

            try:
                print(json.dumps(asyncio.run(migrate()), indent=2))
            finally:
                if previous_migrating is None:
                    os.environ.pop("AOG_MIGRATING", None)
                else:
                    os.environ["AOG_MIGRATING"] = previous_migrating
        return
    Path("data").mkdir(mode=0o700, exist_ok=True)
    import uvicorn

    uvicorn.run("aog.app:app", host=args.host, port=args.port, workers=1, log_level="warning")


if __name__ == "__main__":
    main()
