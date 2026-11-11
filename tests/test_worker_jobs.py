import asyncio
import base64
import json
import sqlite3
import sys
import time
from contextlib import asynccontextmanager

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from aog import worker

FIXTURE = r"""
import json, pathlib, sys
pending = None
def send(value):
    print(json.dumps({"jsonrpc":"2.0", **value}), flush=True)
for line in sys.stdin:
    m = json.loads(line)
    method, p = m.get("method"), m.get("params", {})
    if method == "initialize":
        send({"id":m["id"], "result":{"protocolVersion":1,"agentCapabilities":{"loadSession":True},"authMethods":[]}})
    elif method == "session/new":
        send({"id":m["id"], "result":{"sessionId":"native-control"}})
    elif method == "session/set_config_option":
        send({"id":m["id"], "result":{"configOptions":[]}})
    elif method == "session/prompt":
        pending = m["id"]
        if '"wait"' in p["prompt"][0]["text"]:
            continue
        send({"id":900,"method":"session/request_permission","params":{
            "sessionId":"native-control", "toolCall":{"toolCallId":"write","title":"Write result", "kind":"edit", "rawInput":{"path":"result.txt","text":"approved"}},
            "options":[{"optionId":"allow","name":"Allow once","kind":"allow_once"},{"optionId":"deny","name":"Reject once","kind":"reject_once"}]}})
    elif method == "session/cancel":
        if pending is not None:
            send({"id":pending,"result":{"stopReason":"cancelled"}})
            pending = None
    elif m.get("id") == 900 and "result" in m:
        allowed = m["result"]["outcome"].get("optionId") == "allow"
        if allowed:
            pathlib.Path("result.txt").write_text("approved")
        send({"method":"session/update","params":{"sessionId":"native-control","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"allowed" if allowed else "denied"}}}})
        send({"id":pending,"result":{"stopReason":"end_turn"}})
        pending = None
    elif "id" in m:
        send({"id":m["id"],"result":{}})
"""


@pytest.fixture
def worker_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "root", tmp_path / "worker")
    key = Ed25519PrivateKey.generate()
    for name, value in {
        "AOG_AGENT_PROVIDER": "codex",
        "AOG_AGENT_APPROVAL_POLICY": "interactive",
        "AOG_RELAY_ENABLED": "0",
        "AOG_WORKER_OWNER": "alice",
        "AOG_WORKER_PROJECT": "project",
        "AOG_WORKER_AUDIENCE": "http://worker/invoke",
        "AOG_WORKER_EGRESS": "isolated",
        "AOG_WORKER_PUBLIC_KEY": base64.b64encode(key.public_key().public_bytes_raw()).decode(),
    }.items():
        monkeypatch.setenv(name, value)
    script = tmp_path / "control.py"
    script.write_text(FIXTURE)
    original = worker.spawn_agent_process
    calls = []

    @asynccontextmanager
    async def spawn(client, command, *args, **kwargs):
        calls.append(command)
        async with original(client, sys.executable, str(script), **kwargs) as pair:
            yield pair

    monkeypatch.setattr(worker, "spawn_agent_process", spawn)

    def headers(step, subject="alice", operator=False):
        return {
            "X-AOG-Context": jwt.encode(
                {
                    "sub": subject,
                    "project_id": "project",
                    "aud": "http://worker/invoke",
                    "iss": "agentport-worker",
                    "run_step": step,
                    "exp": int(time.time()) + 60,
                    "operator": operator,
                },
                key,
                algorithm="EdDSA",
            ),
            "Idempotency-Key": step,
        }

    return headers, calls


async def poll(client, handle, headers, status):
    async with asyncio.timeout(10):
        while True:
            response = await client.get("/jobs/" + handle, headers=headers)
            assert response.status_code == 200, response.text
            value = response.json()
            if value["status"] == status:
                return value
            assert value["status"] not in worker.TERMINAL, value
            await asyncio.sleep(0.01)


async def test_persisted_native_approval_does_not_relaunch_and_is_owner_bound(worker_setup):
    headers, calls = worker_setup
    body = {"prompt": {"instruction": "write"}, "session_key": "a" * 32, "export_paths": ["result.txt"]}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=worker.app), base_url="http://worker"
    ) as c:
        result = await c.post("/jobs", json=body, headers=headers("approval"))
        assert result.status_code == 202, result.text
        handle = result.json()["job_id"]
        waiting = await poll(c, handle, headers("approval"), "waiting_approval")
        assert waiting["native_session_id"] == "native-control"
        permission = waiting["permissions"][0]
        assert permission["tool_call"]["rawInput"] == {"path": "result.txt", "text": "approved"}
        with worker.job_database() as db:
            assert json.loads(db.execute("SELECT details FROM jobs WHERE id='approval'").fetchone()[0])[
                "permissions"
            ]
        assert (await c.get("/jobs/" + handle, headers=headers("approval", "bob"))).status_code == 403
        assert (await c.get("/jobs/" + handle, headers=headers("other"))).status_code == 403
        assert (await c.post("/jobs", json=body, headers=headers("approval"))).json()["job_id"] == handle
        assert (await c.post("/jobs", json=body, headers=headers("duplicate-turn"))).status_code == 409
        url = f"/jobs/{handle}/permissions/{permission['id']}"
        assert (
            await c.post(url, json={"option_id": "invented"}, headers=headers("approval"))
        ).status_code == 422
        assert (
            await c.post(url, json={"option_id": "allow"}, headers=headers("approval"))
        ).status_code == 200
        done = await poll(c, handle, headers("approval"), "completed")
        assert done["result"]["text"] == "allowed"
        assert base64.b64decode(done["result"]["files"][0]["content_base64"]) == b"approved"
        assert (
            await c.post(url, json={"option_id": "allow"}, headers=headers("approval"))
        ).status_code == 409
    assert calls == ["codex-acp"]


async def test_native_cancel_waits_for_cancelled_prompt_response(worker_setup):
    headers, calls = worker_setup
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=worker.app), base_url="http://worker"
    ) as c:
        result = await c.post(
            "/jobs",
            json={"prompt": {"instruction": "wait"}, "session_key": "b" * 32},
            headers=headers("cancel"),
        )
        handle = result.json()["job_id"]
        async with asyncio.timeout(10):
            while not worker.clients[(str(worker.root.resolve()), "cancel")].native_id:
                await asyncio.sleep(0.01)
        state = (await c.post(f"/jobs/{handle}/cancel", headers=headers("cancel"))).json()
        assert state["status"] in {"cancelling", "cancelled"}
        done = await poll(c, handle, headers("cancel"), "cancelled")
        assert done["result"]["stop_reason"] == "cancelled"
        assert calls == ["codex-acp"]


async def test_previous_worker_job_is_never_replayed(worker_setup):
    headers, calls = worker_setup
    value = worker.Prompt(prompt={"instruction": "wait"}, session_key="c" * 32)
    checksum = worker.hashlib.sha256(value.model_dump_json().encode()).hexdigest()
    with worker.job_database() as db:
        db.execute(
            "INSERT INTO jobs(id,request,result,status,instance,session_key) "
            "VALUES(?,?,NULL,'running','old-process',?)",
            ("lost", checksum, value.session_key),
        )
    response = await worker.submit_job(value, headers("lost")["X-AOG-Context"], "lost", None)
    assert response["status"] == "needs_attention"
    assert not calls
    with sqlite3.connect(worker.root / "jobs.sqlite") as db:
        assert db.execute("SELECT status FROM jobs WHERE id='lost'").fetchone()[0] == "needs_attention"
    with pytest.raises(HTTPException) as conflict:
        await worker.submit_job(value, headers("next")["X-AOG-Context"], "next", None)
    assert conflict.value.status_code == 409
    handle = response["job_id"]
    resolution = worker.JobResolution(
        outcome="completed", output={"text": "externally verified"}, external_execution_stopped=True
    )
    with pytest.raises(HTTPException) as denied:
        await worker.resolve_job(handle, resolution, headers("lost")["X-AOG-Context"])
    assert denied.value.status_code == 403
    resolved = await worker.resolve_job(handle, resolution, headers("lost", operator=True)["X-AOG-Context"])
    assert resolved["status"] == "completed"
    assert resolved["resolution"]["source"] == "operator_confirmation"
    assert (await worker.submit_job(value, headers("lost")["X-AOG-Context"], "lost", None))["result"] == {
        "text": "externally verified"
    }
    assert not calls


def test_provider_protocol_and_native_login_are_explicit(worker_setup, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    value = worker.Prompt(
        prompt={},
        session_key="d" * 32,
        provider_connection={
            "protocol": "responses",
            "base_url": "https://provider.example/v1",
            "model": "custom-model",
        },
    )
    worker.select_provider(value, "Bearer secret")
    env = worker.agent_environment(home, "codex", "Bearer secret", value.provider_connection)
    worker.configure_provider(home, value, "codex")
    assert env["AOG_MODEL_API_KEY"] == "secret" and "OPENAI_API_KEY" not in env
    assert "secret" not in (home / ".codex/config.toml").read_text()
    monkeypatch.setenv("AOG_AGENT_PROVIDER", "claude")
    with pytest.raises(HTTPException):
        worker.select_provider(value, "Bearer secret")
    monkeypatch.setenv("AOG_AGENT_PROVIDER", "kimi")
    native = worker.Prompt(prompt={}, session_key="e" * 32, harness_key="kimi", native_login_ref="personal")
    config = tmp_path / "native-profiles.json"
    config.write_text(
        json.dumps(
            {
                "personal": {
                    "owner": "alice",
                    "project_id": "project",
                    "harness_key": "kimi",
                    "home": str(home),
                }
            }
        )
    )
    monkeypatch.setenv("AOG_NATIVE_LOGIN_PROFILES", str(config))
    assert worker.native_home(native, "kimi") == home
    assert worker.select_provider(native, None)[0] == "kimi"
    with pytest.raises(HTTPException):
        worker.select_provider(native, "Bearer not-an-oauth-cache")
    config.write_text(config.read_text().replace('"alice"', '"bob"'))
    with pytest.raises(HTTPException) as denied:
        worker.native_home(native, "kimi")
    assert denied.value.status_code == 403


def test_openshell_uses_sdk_authentication_without_exporting_credentials(tmp_path, monkeypatch):
    openshell = pytest.importorskip("openshell")
    from aog.openshell import connect

    captured = {}
    monkeypatch.setattr(
        openshell, "SandboxClient", lambda endpoint, **kwargs: captured.update(endpoint=endpoint, **kwargs)
    )
    path = tmp_path / "gateways.json"
    path.write_text(
        json.dumps(
            {
                "dgx": {
                    "endpoint": "dgx.private:443",
                    "bearer_token_env": "TEST_AOG_ACCESS_TOKEN",
                }
            }
        )
    )
    monkeypatch.setenv("AOG_OPENSHELL_CONFIG", str(path))
    monkeypatch.setenv("TEST_AOG_ACCESS_TOKEN", "first")
    connect("dgx")
    assert captured["bearer_token"]() == "first"
    monkeypatch.setenv("TEST_AOG_ACCESS_TOKEN", "rotated")
    assert captured["bearer_token"]() == "rotated"
    path.write_text(
        json.dumps(
            {
                "dgx": {
                    "endpoint": "dgx.private:443",
                    "client_credentials": {
                        "issuer": "https://identity.private",
                        "client_id": "agentport",
                        "client_secret_env": "TEST_AOG_SECRET",
                        "audience": "openshell",
                        "scopes": ["sandbox:read"],
                    },
                }
            }
        )
    )
    monkeypatch.setenv("TEST_AOG_SECRET", "private-secret")
    connect("dgx")
    assert isinstance(captured["client_credentials"], openshell.ClientCredentialsAuth)
    assert "private-secret" not in repr(captured["client_credentials"])


def test_relay_enforcement_is_not_inferred_from_observation(worker_setup, monkeypatch):
    monkeypatch.setenv("AOG_AGENT_PROVIDER", "hermes")
    monkeypatch.setenv("AOG_RELAY_ENABLED", "1")
    value = worker.Prompt(prompt={}, session_key="f" * 32, relay_mode="enforce")
    with pytest.raises(HTTPException) as unsupported:
        worker.select_provider(value, None)
    assert unsupported.value.status_code == 422
    assert "observation" in unsupported.value.detail
    value.relay_mode = "disabled"
    assert worker.select_provider(value, None)[2] is False


@pytest.mark.parametrize(
    "protocol,package",
    [
        ("chat_completions", "@ai-sdk/openai-compatible"),
        ("responses", "@ai-sdk/openai"),
        ("messages", "@ai-sdk/anthropic"),
    ],
)
def test_opencode_provider_configuration_uses_native_protocol_and_secret_reference(
    worker_setup, tmp_path, monkeypatch, protocol, package
):
    monkeypatch.setenv("AOG_AGENT_PROVIDER", "opencode")
    connection = worker.ProviderConnection(protocol=protocol, base_url="https://provider.example/v1")
    value = worker.Prompt(
        prompt={},
        session_key="a" * 32,
        model="model/with-prefix",
        provider_connection=connection,
        harness_key="opencode",
    )
    assert worker.select_provider(value, "Bearer private-provider-key")[0] == "opencode"
    before = set(tmp_path.rglob("*"))
    env = worker.agent_environment(
        tmp_path, "opencode", "Bearer private-provider-key", connection, value.model
    )
    config_text = env["OPENCODE_CONFIG_CONTENT"]
    config = json.loads(config_text)
    assert config["provider"]["agentport"]["npm"] == package
    assert config["provider"]["agentport"]["options"] == {
        "baseURL": "https://provider.example/v1",
        "apiKey": "{env:AOG_MODEL_API_KEY}",
    }
    assert config["model"] == "agentport/model/with-prefix"
    assert config["enabled_providers"] == ["agentport"]
    assert config["autoupdate"] is False
    assert env["AOG_MODEL_API_KEY"] == "private-provider-key"
    assert "private-provider-key" not in config_text
    assert set(tmp_path.rglob("*")) == before
    monkeypatch.setenv("AOG_AGENT_PROVIDER", "codex")
    value.harness_key = "codex"
    if protocol != "responses":
        with pytest.raises(HTTPException) as mismatch:
            worker.select_provider(value, "Bearer private-provider-key")
        assert mismatch.value.status_code == 422


async def test_opencode_chat_provider_submits_without_native_login_profile(worker_setup, monkeypatch):
    monkeypatch.setenv("AOG_AGENT_PROVIDER", "opencode")
    headers, calls = worker_setup
    value = worker.Prompt(
        prompt={"instruction": "wait"},
        session_key="b" * 32,
        harness_key="opencode",
        provider_connection={
            "protocol": "chat_completions",
            "base_url": "https://openrouter.ai/api/v1",
            "model": "provider/model",
        },
    )
    context = headers("opencode")["X-AOG-Context"]
    submitted = await worker.submit_job(value, context, "opencode", "Bearer private-provider-key")
    async with asyncio.timeout(10):
        while not worker.clients[(str(worker.root.resolve()), "opencode")].native_id:
            await asyncio.sleep(0.01)
    await worker.cancel_job(submitted["job_id"], context)
    task = worker.tasks.get((str(worker.root.resolve()), "opencode"))
    if task:
        await task
    done = worker.job_state("opencode")
    assert done["status"] == "cancelled"
    assert calls == ["opencode"]
    assert "private-provider-key" not in json.dumps(done)


async def test_fixed_control_rpc_preserves_scope_and_operator_resolution(worker_setup):
    headers, _ = worker_setup
    with worker.job_database() as db:
        db.execute(
            "INSERT INTO jobs(id,request,result,status,instance) "
            "VALUES('rpc-job','checksum',NULL,'needs_attention','old-process')"
        )
    body = {"operation": "status", "job_id": worker.job_identity("rpc-job")}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=worker.app), base_url="http://worker"
    ) as c:
        response = await c.post("/job-control", json=body, headers=headers("rpc-job"))
        assert response.status_code == 200
        assert response.json()["status"] == "needs_attention"
        assert (await c.post("/job-control", json=body, headers=headers("other-job"))).status_code == 403
        mismatched = {**headers("rpc-job"), "Idempotency-Key": "other-job"}
        assert (await c.post("/job-control", json=body, headers=mismatched)).status_code == 403
        resolution = {
            **body,
            "operation": "resolve",
            "outcome": "completed",
            "output": {"verified": True},
            "external_execution_stopped": True,
        }
        assert (await c.post("/job-control", json=resolution, headers=headers("rpc-job"))).status_code == 403
        resolved = await c.post("/job-control", json=resolution, headers=headers("rpc-job", operator=True))
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "completed"
        assert resolved.json()["result"] == {"verified": True}
