"""ACP worker: deploy one isolated container per owner/project/agent trust boundary."""

import asyncio
import base64
import hashlib
import json
import os
import sqlite3
import uuid
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

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
from pydantic import BaseModel, ConfigDict, Field

from aog.identity import verification_key
from aog.observability import configure_relay, relay_cursor, relay_summary

app = FastAPI(title="AgentPort ACP worker")
root = Path(os.environ.get("AOG_WORKER_ROOT", "data/worker"))
instance_id = uuid.uuid4().hex
tasks: dict[tuple[str, str], asyncio.Task] = {}
clients: dict[tuple[str, str], "Client"] = {}
TERMINAL = {"completed", "cancelled", "failed", "needs_attention"}


class ProviderConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: str = Field(pattern=r"^(responses|messages|chat_completions)$")
    base_url: str
    model: str | None = None


# Native provider schema inspected at anomalyco/opencode v1.19.5 (810c0eba).
OPENCODE_PACKAGES = {
    "chat_completions": "@ai-sdk/openai-compatible",
    "responses": "@ai-sdk/openai",
    "messages": "@ai-sdk/anthropic",
}


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
    harness_key: str | None = None
    provider_connection: ProviderConnection | None = None
    native_login_ref: str | None = None
    relay_mode: Literal["disabled", "observe", "enforce"] | None = None


class PermissionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_id: str


class JobResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["completed", "failed"]
    output: dict = Field(default_factory=dict)
    external_execution_stopped: Literal[True]


class JobControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["status", "cancel", "approve", "resolve"]
    job_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    permission_id: str | None = None
    option_id: str | None = None
    outcome: Literal["completed", "failed"] | None = None
    output: dict = Field(default_factory=dict)
    external_execution_stopped: bool | None = None


def job_database():
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / "jobs.sqlite"
    db = sqlite3.connect(path, timeout=30)
    path.chmod(0o600)
    db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request TEXT, result TEXT)")
    columns = {r[1] for r in db.execute("PRAGMA table_info(jobs)")}
    for name, definition in {
        "status": "TEXT",
        "details": "TEXT",
        "session_key": "TEXT",
        "instance": "TEXT",
    }.items():
        if name not in columns:
            db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
    # A previous process may have dispatched an external side effect. Never rerun it.
    db.execute(
        "UPDATE jobs SET status=CASE WHEN result IS NULL THEN 'needs_attention' "
        "ELSE 'completed' END WHERE status IS NULL"
    )
    db.execute(
        "UPDATE jobs SET status='needs_attention' WHERE status NOT IN "
        "('completed','cancelled','failed','needs_attention') AND (instance IS NULL OR instance != ?)",
        (instance_id,),
    )
    db.commit()
    return db


def job_identity(key: str):
    return hashlib.sha256(key.encode()).hexdigest()


def job_state(key: str):
    with job_database() as db:
        row = db.execute("SELECT status, details, result FROM jobs WHERE id=?", (key,)).fetchone()
    if row is None:
        raise HTTPException(404, "Worker job not found")
    value = json.loads(row[1] or "{}")
    value.update(job_id=job_identity(key), status=row[0])
    if row[2] is not None:
        value["result"] = json.loads(row[2])
    if row[0] == "needs_attention":
        value["error"] = "Execution requires reconciliation; it has not been replayed"
        value["permissions"] = []
    return value


def update_job(key: str, *, status: str | None = None, **changes):
    with job_database() as db:
        row = db.execute("SELECT details FROM jobs WHERE id=?", (key,)).fetchone()
        details = json.loads(row[0] or "{}")
        details.update(changes)
        db.execute("UPDATE jobs SET details=? WHERE id=?", (json.dumps(details), key))
        if status:
            db.execute("UPDATE jobs SET status=? WHERE id=?", (status, key))


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
    def __init__(self, key: str | None = None):
        self.key = key
        self.chunks = []
        self.permission_requests = 0
        self.pending: dict[str, asyncio.Future] = {}
        self.connection = None
        self.native_id = None
        self.cancel_requested = False

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.permission_requests += 1
        # No semantic guessing about commands. An operator may authorize the entire
        # isolated container boundary; otherwise all native permission requests are denied.
        option = next((o for o in options if o.kind == "allow_once"), None)
        if os.environ.get("AOG_AGENT_APPROVAL_POLICY") == "container" and option:
            return RequestPermissionResponse(outcome={"outcome": "selected", "optionId": option.option_id})
        if os.environ.get("AOG_AGENT_APPROVAL_POLICY", "interactive") == "interactive" and self.key:
            permission_id = uuid.uuid4().hex
            permission = {
                "id": permission_id,
                "session_id": session_id,
                "tool_call": tool_call.model_dump(mode="json", by_alias=True),
                "options": [o.model_dump(mode="json", by_alias=True) for o in options],
                "status": "pending",
            }
            if len(json.dumps(permission).encode()) > 64_000:
                return RequestPermissionResponse(outcome={"outcome": "cancelled"})
            future = asyncio.get_running_loop().create_future()
            self.pending[permission_id] = future
            permissions = job_state(self.key).get("permissions", [])
            permissions.append(permission)
            update_job(self.key, status="waiting_approval", permissions=permissions)
            try:
                choice = await future
                if choice is None:
                    return RequestPermissionResponse(outcome={"outcome": "cancelled"})
                return RequestPermissionResponse(outcome={"outcome": "selected", "optionId": choice})
            finally:
                self.pending.pop(permission_id, None)
                if job_state(self.key)["status"] == "waiting_approval":
                    update_job(self.key, status="running")
        return RequestPermissionResponse(outcome={"outcome": "cancelled"})

    async def session_update(self, session_id, update, **kwargs):
        data = update.model_dump(mode="json", by_alias=True)
        if (
            data.get("sessionUpdate") == "agent_message_chunk"
            and data.get("content", {}).get("type") == "text"
        ):
            self.chunks.append(data["content"]["text"])
            if self.key:
                update_job(self.key, text="".join(self.chunks)[-100_000:])


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
        "job_api": 1,
        "approval_policy": os.environ.get("AOG_AGENT_APPROVAL_POLICY", "interactive"),
    }


def verify_invocation(value: Prompt, context: str, key: str):
    verify_context(context, key)
    if value.local_only and os.environ.get("AOG_WORKER_EGRESS") != "isolated":
        raise HTTPException(403, "Worker network is not isolated")


def verify_context(context: str, key: str | None = None):
    try:
        actor = jwt.decode(
            context,
            verification_key(),
            algorithms=["EdDSA"],
            issuer="agentport-worker",
            audience=os.environ["AOG_WORKER_AUDIENCE"],
            options={"require": ["exp", "iss", "aud", "sub", "run_step"]},
        )
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid worker delegation") from None
    if actor.get("sub") != os.environ.get("AOG_WORKER_OWNER"):
        raise HTTPException(403, "Worker belongs to another user")
    if (actor.get("project_id") or "") != os.environ.get("AOG_WORKER_PROJECT", ""):
        raise HTTPException(403, "Worker belongs to another project")
    if not isinstance(actor.get("run_step"), str) or not actor["run_step"]:
        raise HTTPException(403, "Delegation has no invocation")
    if key is not None and actor.get("run_step") != key:
        raise HTTPException(403, "Delegation is bound to another invocation")
    return actor


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
    commands = {
        "codex": "codex-acp",
        "claude": "claude-agent-acp",
        "hermes": "hermes-acp",
        "kimi": "kimi",
        "opencode": "opencode",
    }
    if provider not in commands:
        raise HTTPException(503, "Unsupported ACP worker template")
    if value.harness_key and value.harness_key != provider:
        raise HTTPException(409, "Requested harness does not match the installed worker template")
    if authorization and (not authorization.startswith("Bearer ") or not authorization[7:].strip()):
        raise HTTPException(422, "Provider authentication requires an explicit API bearer credential")
    if value.native_login_ref and (authorization or value.provider_connection):
        raise HTTPException(422, "Native login and API provider configuration are separate profiles")
    if value.provider_connection:
        protocols = {
            "codex": {"responses"},
            "claude": {"messages"},
            "opencode": set(OPENCODE_PACKAGES),
        }.get(provider, set())
        if value.provider_connection.protocol not in protocols:
            raise HTTPException(422, "This harness requires a compatible native provider profile")
        if provider == "opencode" and not (value.model or value.provider_connection.model):
            raise HTTPException(422, "An OpenCode provider connection requires an explicit model ID")
        url = urlsplit(value.provider_connection.base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise HTTPException(422, "Provider base URL must be an HTTP origin/path without credentials")
    if provider in {"kimi", "opencode"} and not (value.native_login_ref or value.provider_connection):
        raise HTTPException(422, "This harness requires an operator-mounted native profile")
    relay_enabled = os.environ.get("AOG_RELAY_ENABLED") == "1"
    relay_mode = value.relay_mode or os.environ.get("AOG_RELAY_MODE", "observe")
    if relay_mode == "enforce":
        raise HTTPException(422, "This worker profile only supports Relay observation")
    if relay_mode == "disabled":
        relay_enabled = False
    elif value.relay_mode == "observe" and not relay_enabled:
        raise HTTPException(422, "Relay observation is not configured in this worker")
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


def agent_environment(
    home: Path,
    provider: str,
    authorization: str | None,
    connection: ProviderConnection | None = None,
    model: str | None = None,
):
    # Do not inherit gateway/worker signing keys or operator credentials into agents.
    env = {k: os.environ[k] for k in ("PATH", "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR") if k in os.environ}
    env.update(
        {"HOME": str(home.resolve()), "CODEX_HOME": str((home / ".codex").resolve()), "NO_BROWSER": "1"}
    )
    if provider == "hermes":
        hermes_home = home / ".hermes"
        env["HERMES_HOME"] = str(hermes_home.resolve())
    if provider == "kimi":
        env["KIMI_CODE_HOME"] = str((home / ".kimi-code").resolve())
    if provider == "opencode":
        env["XDG_CONFIG_HOME"] = str((home / ".config").resolve())
        env["XDG_DATA_HOME"] = str((home / ".local/share").resolve())
        env["XDG_STATE_HOME"] = str((home / ".local/state").resolve())
    if connection:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(422, "An API credential is required for the provider profile")
        if provider == "codex":
            env["AOG_MODEL_API_KEY"] = authorization[7:]
        elif provider == "claude":
            env["ANTHROPIC_BASE_URL"] = connection.base_url
            env["ANTHROPIC_AUTH_TOKEN"] = authorization[7:]
        elif provider == "opencode":
            env["AOG_MODEL_API_KEY"] = authorization[7:]
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(opencode_configuration(connection, model))
        return env
    if authorization and authorization.startswith("Bearer "):
        env["OPENAI_API_KEY" if provider == "codex" else "ANTHROPIC_API_KEY"] = authorization[7:]
    return env


def opencode_configuration(connection: ProviderConnection, model: str | None = None):
    model = model or connection.model
    if not model:
        raise HTTPException(422, "An OpenCode provider connection requires an explicit model ID")
    return {
        "$schema": "https://opencode.ai/config.json",
        "enabled_providers": ["agentport"],
        "model": "agentport/" + model,
        "autoupdate": False,
        "provider": {
            "agentport": {
                "name": "AgentPort provider",
                "npm": OPENCODE_PACKAGES[connection.protocol],
                "options": {"baseURL": connection.base_url, "apiKey": "{env:AOG_MODEL_API_KEY}"},
                "models": {model: {"name": model}},
            }
        },
    }


def native_home(value: Prompt, provider: str):
    if not value.native_login_ref:
        return root / "home"
    try:
        profiles = json.loads(Path(os.environ["AOG_NATIVE_LOGIN_PROFILES"]).read_text())
        profile = profiles[value.native_login_ref]
    except (KeyError, OSError, ValueError):
        raise HTTPException(422, "Native login profile is not provisioned") from None
    if (
        profile.get("owner") != os.environ.get("AOG_WORKER_OWNER")
        or (profile.get("project_id") or "") != os.environ.get("AOG_WORKER_PROJECT", "")
        or profile.get("harness_key") != provider
    ):
        raise HTTPException(403, "Native login profile belongs to another runtime principal")
    if not isinstance(profile.get("home"), str):
        raise HTTPException(422, "Native login profile requires a mounted home")
    home = Path(profile["home"])
    if not home.is_absolute() or not home.is_dir() or home.is_symlink():
        raise HTTPException(422, "Native login profile requires an existing mounted home")
    return home


def configure_provider(home: Path, value: Prompt, provider: str):
    connection = value.provider_connection
    if connection and provider == "codex":
        config = home / ".codex/config.toml"
        # Only the worker-owned profile is written. Native login homes are never modified here.
        content = 'model_provider = "agentport"\n'
        if connection.model or value.model:
            content += "model = " + json.dumps(connection.model or value.model) + "\n"
        content += (
            '[model_providers.agentport]\nname = "AgentPort provider"\nwire_api = "responses"\n'
            'env_key = "AOG_MODEL_API_KEY"\nbase_url = ' + json.dumps(connection.base_url) + "\n"
        )
        temporary = config.with_name(uuid.uuid4().hex + ".toml")
        temporary.write_text(content)
        temporary.chmod(0o600)
        temporary.replace(config)
    elif not connection and not value.native_login_ref and provider == "codex":
        # Do not retain a previous request's provider routing for a legacy official-key call.
        config = home / ".codex/config.toml"
        if config.exists() and 'name = "AgentPort provider"' in config.read_text():
            config.unlink()


async def run_agent(
    value: Prompt, command: str, workspace: Path, env: dict, mcp_servers: list, client: Client | None = None
):
    client = client or Client()
    args = ("acp",) if command in {"kimi", "opencode"} else ()
    async with asyncio.timeout(int(os.environ.get("AOG_AGENT_TIMEOUT", "900"))):
        async with spawn_agent_process(client, command, *args, cwd=workspace.resolve(), env=env) as (
            conn,
            process,
        ):
            initialized = await conn.initialize(
                protocol_version=PROTOCOL_VERSION, client_capabilities=ClientCapabilities()
            )
            client.connection = conn
            if client.key:
                update_job(
                    client.key,
                    capabilities={
                        "resume": bool(initialized.agent_capabilities.load_session),
                        "cancel": True,
                        "approval": True,
                        "approval_scope": "native_requests_only",
                        "fork": False,
                    },
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
            client.native_id = native_id
            if client.key:
                update_job(client.key, native_session_id=native_id)
            model = value.model or (value.provider_connection.model if value.provider_connection else None)
            if model:
                if command == "opencode" and value.provider_connection:
                    model = "agentport/" + model
                await conn.set_config_option(session_id=native_id, config_id="model", value=model)
            if client.cancel_requested:
                return {
                    "native_session_id": native_id,
                    "text": "",
                    "stop_reason": "cancelled",
                    "permission_requests": 0,
                }
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


@app.post("/jobs", status_code=202)
async def submit_job(
    value: Prompt,
    x_aog_context: str = Header(),
    idempotency_key: str = Header(),
    authorization: str | None = Header(default=None),
):
    verify_invocation(value, x_aog_context, idempotency_key)
    mcp_servers = delegated_mcp_servers(value)
    command, provider, relay_enabled = select_provider(value, authorization)
    home = native_home(value, provider)
    if value.provider_connection:
        profile_key = hashlib.sha256(value.provider_connection.model_dump_json().encode()).hexdigest()
        home = root / "provider-homes" / profile_key
    # Validate explicit authentication before recording an accepted invocation.
    env = agent_environment(home, provider, authorization, value.provider_connection, value.model)
    with job_database() as db:
        db.execute("BEGIN IMMEDIATE")
        checksum = hashlib.sha256(value.model_dump_json().encode()).hexdigest()
        old = db.execute("SELECT request, result FROM jobs WHERE id=?", (idempotency_key,)).fetchone()
        if old:
            if old[0] != checksum:
                raise HTTPException(409, "Idempotency key input mismatch")
        else:
            active = db.execute(
                "SELECT 1 FROM jobs WHERE session_key=? AND status NOT IN ('completed','cancelled','failed')",
                (value.session_key,),
            ).fetchone()
            if active:
                raise HTTPException(409, "This session already has an active turn")
            db.execute(
                "INSERT INTO jobs (id,request,result,status,details,session_key,instance) "
                "VALUES (?,?,NULL,'queued','{}',?,?)",
                (idempotency_key, checksum, value.session_key, instance_id),
            )
    if old:
        return job_state(idempotency_key)
    client = Client(idempotency_key)
    registry_key = (str(root.resolve()), idempotency_key)
    clients[registry_key] = client
    task = asyncio.create_task(
        execute_job(value, command, provider, relay_enabled, home, env, mcp_servers, client)
    )
    tasks[registry_key] = task
    task.add_done_callback(lambda _: tasks.pop(registry_key, None))
    return job_state(idempotency_key)


async def execute_job(value, command, provider, relay_enabled, home, env, mcp_servers, client):
    key = client.key
    workspace = root / "workspaces" / value.session_key
    registry_key = (str(root.resolve()), key)
    try:
        if client.cancel_requested:
            update_job(key, status="cancelled")
            return
        update_job(key, status="running", permissions=[])
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        (home / ".codex").mkdir(exist_ok=True, mode=0o700)
        if provider == "hermes":
            (home / ".hermes").mkdir(exist_ok=True, mode=0o700)
        import_files(workspace, value.workspace_files)
        configure_provider(home, value, provider)
        if relay_enabled:
            config, relay_file = configure_relay(root)
            env["HERMES_NEMO_RELAY_PLUGINS_TOML"] = str(config)
            cursor = relay_cursor(relay_file)
        output = await run_agent(value, command, workspace, env, mcp_servers, client)
        output["files"] = export_files(workspace, value.export_paths)
        if relay_enabled:
            output["observability"] = relay_summary(relay_file, cursor)
            output["observability"]["correlation"] = {
                "job_id": job_identity(key),
                "native_session_id": output["native_session_id"],
                "trace_verified": False,
            }
        status = "cancelled" if output["stop_reason"] == "cancelled" else "completed"
        with job_database() as db:
            db.execute("UPDATE jobs SET result=?,status=? WHERE id=?", (json.dumps(output), status, key))
    except asyncio.CancelledError:
        update_job(key, status="needs_attention")
        raise
    except Exception:
        # Native exceptions may contain model credentials or private provider payloads.
        update_job(key, status="needs_attention", error="Native execution requires reconciliation")
    finally:
        clients.pop(registry_key, None)


def authorized_job(job_id: str, context: str):
    actor = verify_context(context)
    key = actor["run_step"]
    if job_identity(key) != job_id:
        raise HTTPException(403, "Delegation is bound to another invocation")
    return key, job_state(key)


@app.get("/jobs/{job_id}")
async def get_job(job_id: str, x_aog_context: str = Header()):
    return authorized_job(job_id, x_aog_context)[1]


@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, x_aog_context: str = Header()):
    key, state = authorized_job(job_id, x_aog_context)
    if state["status"] in TERMINAL:
        return state
    client = clients.get((str(root.resolve()), key))
    if client is None:
        update_job(key, status="needs_attention")
        return job_state(key)
    client.cancel_requested = True
    for permission in state.get("permissions", []):
        if permission["status"] == "pending":
            permission["status"] = "cancelled"
    update_job(key, status="cancelling", permissions=state.get("permissions", []))
    for future in client.pending.values():
        if not future.done():
            future.set_result(None)
    if client.connection and client.native_id:
        try:
            await client.connection.cancel(session_id=client.native_id)
        except Exception:
            update_job(key, status="needs_attention")
    return job_state(key)


@app.post("/jobs/{job_id}/permissions/{permission_id}")
async def decide_permission(
    job_id: str, permission_id: str, value: PermissionDecision, x_aog_context: str = Header()
):
    key, state = authorized_job(job_id, x_aog_context)
    client = clients.get((str(root.resolve()), key))
    if client is None or state["status"] != "waiting_approval":
        raise HTTPException(409, "Native permission request is no longer live")
    permission = next((p for p in state.get("permissions", []) if p["id"] == permission_id), None)
    if not permission or permission["status"] != "pending":
        raise HTTPException(409, "Native permission request is no longer pending")
    option_ids = {o["optionId"] for o in permission["options"]}
    if value.option_id not in option_ids:
        raise HTTPException(422, "Select an option advertised by the native harness")
    future = client.pending.get(permission_id)
    if future is None or future.done():
        raise HTTPException(409, "Native permission request is no longer live")
    permission.update(status="resolved", option_id=value.option_id)
    update_job(key, permissions=state["permissions"])
    future.set_result(value.option_id)
    return job_state(key)


@app.post("/jobs/{job_id}/resolve")
async def resolve_job(job_id: str, value: JobResolution, x_aog_context: str = Header()):
    actor = verify_context(x_aog_context)
    if actor.get("operator") is not True:
        raise HTTPException(403, "Operator delegation is required for native reconciliation")
    key, state = authorized_job(job_id, x_aog_context)
    if state["status"] in {"completed", "cancelled", "failed"}:
        raise HTTPException(409, "Worker job is already terminal")
    registry_key = (str(root.resolve()), key)
    task = tasks.get(registry_key)
    if task and not task.done():
        # This is an explicit operator assertion that external execution has stopped,
        # not an inferred consequence of cancelling the local ACP process.
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=10)
        except asyncio.CancelledError:
            if not task.cancelled():
                raise
        except TimeoutError:
            raise HTTPException(409, "Local native execution is still stopping") from None
    with job_database() as db:
        db.execute(
            "UPDATE jobs SET result=?, status=? WHERE id=?",
            (
                json.dumps(value.output) if value.outcome == "completed" else None,
                value.outcome,
                key,
            ),
        )
    update_job(
        key,
        permissions=[],
        error=None,
        resolution={
            "source": "operator_confirmation",
            "external_execution_stopped": True,
        },
    )
    return job_state(key)


@app.post("/invoke")
async def invoke(
    value: Prompt,
    x_aog_context: str = Header(),
    idempotency_key: str = Header(),
    authorization: str | None = Header(default=None),
):
    state = await submit_job(value, x_aog_context, idempotency_key, authorization)
    task = tasks.get((str(root.resolve()), idempotency_key))
    if task:
        await asyncio.shield(task)
    state = job_state(idempotency_key)
    if state["status"] in {"completed", "cancelled"} and "result" in state:
        return state["result"]
    raise HTTPException(409, "Prior execution needs reconciliation; not replayed")


@app.post("/job-control")
async def control_job(value: JobControl, x_aog_context: str = Header(), idempotency_key: str = Header()):
    """Fixed REST tool target for the native gateway; no client-selected URLs."""
    verify_context(x_aog_context, idempotency_key)
    if value.operation == "status":
        return await get_job(value.job_id, x_aog_context)
    if value.operation == "cancel":
        return await cancel_job(value.job_id, x_aog_context)
    if value.operation == "approve":
        if not value.permission_id or not value.option_id:
            raise HTTPException(422, "Native approval requires permission_id and option_id")
        return await decide_permission(
            value.job_id, value.permission_id, PermissionDecision(option_id=value.option_id), x_aog_context
        )
    if not value.outcome or value.external_execution_stopped is not True:
        raise HTTPException(422, "Resolution requires outcome and explicit external stop confirmation")
    return await resolve_job(
        value.job_id,
        JobResolution(outcome=value.outcome, output=value.output, external_execution_stopped=True),
        x_aog_context,
    )
