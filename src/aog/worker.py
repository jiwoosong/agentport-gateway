"""ACP worker: deploy one isolated container per owner/project/agent trust boundary."""

import asyncio
import base64
import hashlib
import json
import os
import sqlite3
from pathlib import Path, PurePosixPath

import jwt
from acp import PROTOCOL_VERSION, spawn_agent_process
from acp.schema import (
    ClientCapabilities,
    HttpHeader,
    HttpMcpServer,
    RequestPermissionResponse,
    TextContentBlock,
)
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from aog.identity import verification_key
from aog.observability import configure_relay, relay_cursor, relay_summary

app = FastAPI(title="AgentPort ACP worker")
root = Path(os.environ.get("AOG_WORKER_ROOT", "data/worker"))


class Prompt(BaseModel):
    prompt: dict
    persona: str = ""
    model: str | None = None
    session_key: str = Field(pattern=r"^[a-f0-9]{32}$")
    native_session_id: str | None = None
    local_only: bool = True
    allowed_service_ids: list[str] = Field(default_factory=list)
    gateway_mcp_url: str | None = None
    gateway_token: str | None = None
    workspace_files: list[dict] = Field(default_factory=list, max_length=100)
    export_paths: list[str] = Field(default_factory=list, max_length=100)


def workspace_path(workspace: Path, name: str) -> Path:
    """Explicit relative files only; never follow a symlink out of the workspace."""
    relative = PurePosixPath(name)
    if not name or relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise HTTPException(422, "Invalid workspace file path")
    path = workspace.joinpath(*relative.parts)
    if path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve()):
        raise HTTPException(422, "Workspace file escapes its session")
    return path


def import_files(workspace: Path, files: list[dict]):
    total = 0
    for file in files:
        path = workspace_path(workspace, file["path"])
        data = base64.b64decode(file["content_base64"], validate=True)
        total += len(data)
        if total > 10_000_000 or len(data) > 1_000_000:
            raise HTTPException(413, "Workspace snapshot exceeds its size limit")
        if hashlib.sha256(data).hexdigest() != file["sha256"]:
            raise HTTPException(422, "Workspace snapshot checksum mismatch")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), "wb"
        ) as out:
            out.write(data)


def export_files(workspace: Path, names: list[str]):
    files, total = [], 0
    for name in names:
        path = workspace_path(workspace, name)
        with path.open("rb") as source:
            data = source.read(1_000_001)
        total += len(data)
        if total > 10_000_000 or len(data) > 1_000_000:
            raise HTTPException(413, "Workspace snapshot exceeds its size limit")
        files.append(
            {
                "path": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "content_base64": base64.b64encode(data).decode(),
            }
        )
    return files


class Client:
    def __init__(self):
        self.chunks = []
        self.permission_requests = 0

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.permission_requests += 1
        # No semantic guessing about commands. An operator may authorize the entire
        # isolated container boundary; otherwise all native permission requests are denied.
        option = next((o for o in options if o.kind == "allow_once"), None)
        if os.environ.get("AOG_AGENT_APPROVAL_POLICY") == "container" and option:
            return RequestPermissionResponse(outcome={"outcome": "selected", "optionId": option.option_id})
        return RequestPermissionResponse(outcome={"outcome": "cancelled"})

    async def session_update(self, session_id, update, **kwargs):
        data = update.model_dump(mode="json", by_alias=True)
        if (
            data.get("sessionUpdate") == "agent_message_chunk"
            and data.get("content", {}).get("type") == "text"
        ):
            self.chunks.append(data["content"]["text"])


@app.get("/health")
def health():
    return {
        "ok": True,
        "protocol": "acp",
        "fork": False,
        "owner_bound": bool(os.environ.get("AOG_WORKER_OWNER")),
        "egress_policy": os.environ.get("AOG_WORKER_EGRESS", "unrestricted"),
        "provider": os.environ.get("AOG_AGENT_PROVIDER", "codex"),
        "relay_configured": os.environ.get("AOG_RELAY_ENABLED") == "1",
    }


def verify_invocation(value: Prompt, context: str, key: str):
    try:
        actor = jwt.decode(
            context,
            verification_key(),
            algorithms=["EdDSA"],
            issuer="agentport-worker",
            audience=os.environ["AOG_WORKER_AUDIENCE"],
        )
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid worker delegation") from None
    if actor.get("sub") != os.environ.get("AOG_WORKER_OWNER"):
        raise HTTPException(403, "Worker belongs to another user")
    if (actor.get("project_id") or "") != os.environ.get("AOG_WORKER_PROJECT", ""):
        raise HTTPException(403, "Worker belongs to another project")
    if actor.get("run_step") != key:
        raise HTTPException(403, "Delegation is bound to another invocation")
    if value.local_only and os.environ.get("AOG_WORKER_EGRESS") != "isolated":
        raise HTTPException(403, "Worker network is not isolated")


def delegated_mcp_servers(value: Prompt):
    if not value.allowed_service_ids:
        return []
    if not value.gateway_mcp_url or not value.gateway_token:
        raise HTTPException(422, "Scoped gateway delegation is required")
    return [
        HttpMcpServer(
            type="http",
            name="agentport",
            url=value.gateway_mcp_url,
            headers=[HttpHeader(name="Authorization", value="Bearer " + value.gateway_token)],
        )
    ]


def select_provider(value: Prompt, authorization: str | None):
    provider = os.environ.get("AOG_AGENT_PROVIDER", "codex")
    commands = {"codex": "codex-acp", "claude": "claude-agent-acp", "hermes": "hermes-acp"}
    if provider not in commands:
        raise HTTPException(503, "Unsupported ACP worker template")
    relay_enabled = os.environ.get("AOG_RELAY_ENABLED") == "1"
    if relay_enabled and provider != "hermes":
        raise HTTPException(422, "Relay worker integration currently requires Hermes native support")
    if provider == "hermes":
        # Hermes owns provider selection and credentials in its dedicated profile.
        # A generic Bearer token must not silently become an Anthropic/OpenAI key.
        if authorization:
            raise HTTPException(422, "Configure Hermes credentials in its dedicated HERMES_HOME")
        # Hermes binds resumed sessions to the original MCP credential fingerprint.
        # AgentPort rotates delegation tokens; do not pretend this can reattach safely.
        if value.native_session_id and value.allowed_service_ids:
            raise HTTPException(409, "Hermes resume with delegated MCP tools is not supported yet")
    return commands[provider], provider, relay_enabled


def agent_environment(home: Path, provider: str, authorization: str | None):
    # Do not inherit gateway/worker signing keys or operator credentials into agents.
    env = {k: os.environ[k] for k in ("PATH", "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR") if k in os.environ}
    env.update(
        {"HOME": str(home.resolve()), "CODEX_HOME": str((home / ".codex").resolve()), "NO_BROWSER": "1"}
    )
    if provider == "hermes":
        hermes_home = home / ".hermes"
        hermes_home.mkdir(exist_ok=True, mode=0o700)
        env["HERMES_HOME"] = str(hermes_home.resolve())
    if authorization and authorization.startswith("Bearer "):
        env["OPENAI_API_KEY" if provider == "codex" else "ANTHROPIC_API_KEY"] = authorization[7:]
    return env


async def run_agent(value: Prompt, command: str, workspace: Path, env: dict, mcp_servers: list):
    client = Client()
    async with asyncio.timeout(int(os.environ.get("AOG_AGENT_TIMEOUT", "900"))):
        async with spawn_agent_process(client, command, cwd=workspace.resolve(), env=env) as (
            conn,
            process,
        ):
            initialized = await conn.initialize(
                protocol_version=PROTOCOL_VERSION, client_capabilities=ClientCapabilities()
            )
            if value.native_session_id:
                if not initialized.agent_capabilities.load_session:
                    raise HTTPException(409, "This adapter does not support session resume")
                await conn.load_session(
                    cwd=str(workspace.resolve()), session_id=value.native_session_id, mcp_servers=mcp_servers
                )
                native_id = value.native_session_id
            else:
                session = await conn.new_session(cwd=str(workspace.resolve()), mcp_servers=mcp_servers)
                native_id = session.session_id
            if value.model:
                await conn.set_config_option(session_id=native_id, config_id="model", value=value.model)
            # ACP load_session may replay previous messages. Return only this turn.
            client.chunks.clear()
            text = (value.persona + "\n\n" if value.persona else "") + json.dumps(
                value.prompt, ensure_ascii=False
            )
            result = await conn.prompt(
                session_id=native_id, prompt=[TextContentBlock(type="text", text=text)]
            )
            output = {
                "native_session_id": native_id,
                "text": "".join(client.chunks),
                "stop_reason": result.stop_reason,
                "permission_requests": client.permission_requests,
            }
            try:
                structured = json.loads(output["text"])
                if isinstance(structured, dict):
                    output["structured"] = structured
            except ValueError:
                pass
    return output


@app.post("/invoke")
async def invoke(
    value: Prompt,
    x_aog_context: str = Header(),
    idempotency_key: str = Header(),
    authorization: str | None = Header(default=None),
):
    verify_invocation(value, x_aog_context, idempotency_key)
    mcp_servers = delegated_mcp_servers(value)
    command, provider, relay_enabled = select_provider(value, authorization)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    database = root / "jobs.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request TEXT, result TEXT)")
        checksum = hashlib.sha256(value.model_dump_json().encode()).hexdigest()
        old = db.execute("SELECT request, result FROM jobs WHERE id=?", (idempotency_key,)).fetchone()
        if old:
            if old[0] != checksum:
                raise HTTPException(409, "Idempotency key input mismatch")
            if old[1] is not None:
                return json.loads(old[1])
            raise HTTPException(409, "Prior execution needs reconciliation; not replayed")
        db.execute("INSERT INTO jobs VALUES (?, ?, NULL)", (idempotency_key, checksum))
    workspace = root / "workspaces" / value.session_key
    home = root / "home"
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.mkdir(exist_ok=True, mode=0o700)
    (home / ".codex").mkdir(exist_ok=True, mode=0o700)
    import_files(workspace, value.workspace_files)
    env = agent_environment(home, provider, authorization)
    if relay_enabled:
        config, relay_file = configure_relay(root)
        env["HERMES_NEMO_RELAY_PLUGINS_TOML"] = str(config)
        cursor = relay_cursor(relay_file)
    output = await run_agent(value, command, workspace, env, mcp_servers)
    output["files"] = export_files(workspace, value.export_paths)
    if relay_enabled:
        output["observability"] = relay_summary(relay_file, cursor)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps(output), idempotency_key))
    return output
