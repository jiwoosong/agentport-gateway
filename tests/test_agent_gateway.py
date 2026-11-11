"""Native CF catalog/HTTP dispatch → persisted worker → real local ACP subprocess.

Authentication middleware and a live model provider are outside this fixture; the
native caller scope, private-tool boundary, signed worker identity and job controls
are exercised. No ContextForge invoke method or worker request handler is mocked.
"""

import asyncio
import base64
import json
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from test_worker_jobs import FIXTURE

from aog import api, security, worker
from aog import engine as engine_module
from aog.adapters import UncertainExecution
from aog.dispatch import dispatch
from aog.engine import Engine
from aog.identity import public_key_base64
from aog.integrations.contextforge import delegation, native
from aog.integrations.contextforge.native import SERVICE_METADATA, Catalog, encrypt_secret, prepare_gateway
from aog.migration import perform_migration
from aog.models import AgentProfile, Principal, ProviderConnection, Service
from aog.store import Lease, Resource, Run, Runtime, Store


@pytest.fixture
async def gateway(tmp_path, monkeypatch):
    import mcpgateway.db as native_db
    import mcpgateway.services.tool_service as native_tools
    from mcpgateway import agentport
    from mcpgateway.cache.tool_lookup_cache import tool_lookup_cache
    from mcpgateway.config import settings
    from mcpgateway.services.permission_service import PermissionService
    from mcpgateway.services.tool_service import ToolService

    native_engine = create_engine(f"sqlite:///{tmp_path}/native.db")
    native_db.Base.metadata.create_all(native_engine)
    sessions = sessionmaker(bind=native_engine, expire_on_commit=False)
    monkeypatch.setattr(native_db, "SessionLocal", sessions)
    monkeypatch.setattr(tool_lookup_cache, "_enabled", False)
    monkeypatch.setattr(settings, "ssrf_protection_enabled", False)
    monkeypatch.setattr(settings, "enable_header_passthrough", True)
    monkeypatch.setattr(native_tools.SecurityValidator, "validate_url_for_connection_pinning", AsyncMock(return_value={}))
    monkeypatch.setattr(PermissionService, "check_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(ToolService, "_notify_tool_added", AsyncMock())
    monkeypatch.setattr(ToolService, "_get_plugin_manager", AsyncMock(return_value=None))
    monkeypatch.setattr(native_tools, "is_input_capture_enabled", lambda *_: False)
    monkeypatch.setattr(native_tools, "is_output_capture_enabled", lambda *_: False)
    with sessions() as db:
        db.add_all([native_db.EmailUser(email="alice@example.com", is_active=True, is_admin=True),
                    native_db.EmailUser(email="bob@example.com", is_active=True)])
        db.flush()
        db.add(native_db.EmailTeam(id="project", name="Project", slug="project",
                                  created_by="alice@example.com", is_active=True))
        db.flush()
        db.add(native_db.EmailTeamMember(team_id="project", user_email="alice@example.com", is_active=True))
        db.commit()
    store = Store(f"sqlite:///{tmp_path}/state.db")
    store.init()
    alice = Principal(subject="alice@example.com", teams=["project"], scopes=["tools.execute", "tools.read"])
    admin = alice.model_copy(update={"is_admin": True,
        "scopes": ["tools.execute", "tools.read", "admin.system_config"]})
    secret = store.save("credential", {"name": "Provider API key",
        "encrypted": encrypt_secret({"token": "fake-api-key"})}, alice)
    service = Service(name="ACP worker", kind="agent", transport="acp", endpoint="http://worker/invoke",
        project_id="project", visibility="private", egress_policy="isolated", timeout_seconds=10,
        credential_ref=secret.id)
    with store.tx() as db:
        db.add(Resource(id="legacy-worker", kind="service", owner=alice.subject,
            project_id="project", visibility="private", revision=1, document=service.model_dump()))
        db.flush()
        db.add(Runtime(service_id="legacy-worker", phase="running"))
    monkeypatch.setenv("AOG_WORKER_SIGNING_KEY_PATH", str(tmp_path / "signing.key"))
    monkeypatch.setattr(worker, "root", tmp_path / "worker")
    for key, value in {
        "AOG_AGENT_PROVIDER": "codex", "AOG_AGENT_APPROVAL_POLICY": "interactive", "AOG_RELAY_ENABLED": "0",
        "AOG_WORKER_OWNER": alice.subject, "AOG_WORKER_PROJECT": "project",
        "AOG_WORKER_AUDIENCE": service.endpoint, "AOG_WORKER_EGRESS": "isolated",
        "AOG_WORKER_PUBLIC_KEY": public_key_base64(),
    }.items():
        monkeypatch.setenv(key, value)
    script = tmp_path / "agent.py"
    script.write_text(FIXTURE)
    real_spawn = worker.spawn_agent_process
    spawns, outgoing, environments = [], [], []

    @asynccontextmanager
    async def spawn(client, command, *args, **kwargs):
        spawns.append(command)
        environment = kwargs["env"]
        environments.append(dict(environment))
        if "AOG_MODEL_API_KEY" in environment:
            assert environment["AOG_MODEL_API_KEY"] == "connection-api-key"
            assert "OPENAI_API_KEY" not in environment
        elif Path(environment["HOME"]) == worker.root / "home":
            assert environment["OPENAI_API_KEY"] == "fake-api-key"
        else:
            assert not {"OPENAI_API_KEY", "AOG_MODEL_API_KEY", "ANTHROPIC_API_KEY"}.intersection(environment)
        async with real_spawn(client, sys.executable, str(script), **kwargs) as pair:
            yield pair

    monkeypatch.setattr(worker, "spawn_agent_process", spawn)
    catalog = Catalog(store)
    monkeypatch.setenv("AOG_MIGRATING", "1")
    await prepare_gateway(catalog)
    report = await perform_migration(store, catalog)
    assert report["migrated_services"] == 1
    monkeypatch.delenv("AOG_MIGRATING")
    migrated = await catalog.resource("legacy-worker", alice)
    class InProcessTestBackend:
        async def authorize(self, actor, permission):
            return await native.authorize(actor, permission)

        async def encrypt_secret(self, value):
            return native.encrypt_secret(value)

        async def decrypt_secret(self, value):
            return native.decrypt_secret(value)

        async def delegate(self, **arguments):
            return await delegation.issue(**arguments, store=store)

        async def controls(self, actor, store):
            return await delegation.ensure_controls(actor, store)

    integration_backend = InProcessTestBackend()
    monkeypatch.setattr(security, "get_backend", lambda: integration_backend)
    engine = Engine(store, catalog=catalog, backend=integration_backend)
    monkeypatch.setattr(engine_module, "engine", engine)

    async def local_dispatch(caller, tool, arguments):
        actor = await native.authorize(await delegation.resolve(caller, store))
        return await dispatch(actor.model_dump(mode="json"), {
            "id": tool.id, "name": tool.name, "team_id": tool.team_id, "owner_email": tool.owner_email,
            "visibility": tool.visibility, "extension_metadata": dict(tool.extension_metadata)}, arguments)

    monkeypatch.setattr(agentport, "_handlers", MappingProxyType({
        operation: local_dispatch for operation in ("get_run", "cancel", "agent_permission", "resolve")}))
    controls = await integration_backend.controls(alice, store)
    users = {"active": alice}
    app = FastAPI()
    app.include_router(api.router)

    async def user():
        return users["active"]

    app.dependency_overrides[api.reader] = user
    app.dependency_overrides[api.operator] = user

    class Backend(httpx.AsyncClient):
        async def request(self, method, url, **kwargs):
            outgoing.append((str(url), kwargs.get("json"), kwargs.get("headers", {})))
            return await super().request(method, url, **kwargs)

    async with Backend(transport=httpx.ASGITransport(app=worker.app), base_url="http://worker") as backend:
        monkeypatch.setattr(catalog.tools, "_http_client", backend)

        def managed_http_client(**kwargs):
            assert kwargs == {"max_attempts": 1}
            return Backend(transport=httpx.ASGITransport(app=worker.app), base_url="http://worker")

        monkeypatch.setattr(native_tools, "_build_pinned_rest_http_client", managed_http_client)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://central") as client:
            def new_run(service=None, target=None):
                selected = service or migrated
                run_id = uuid.uuid4().hex
                with store.tx() as db:
                    db.add(Run(id=run_id, owner=alice.subject, project_id="project", status="running",
                        principal=alice.model_dump(), inputs={}, spec={"name": "Native ACP", "definition_revision": 1,
                            "local_only": True, "released": [], "nodes": [{"id": "invoke"}],
                            "snapshots": {target or selected.id: {"kind": "service", "revision": selected.revision,
                                "document": selected.document}}}))
                return run_id

            gateway_admin = admin

            async def mcp_control(operation, arguments, admin=False):
                with sessions() as db:
                    tool = db.get(native_db.Tool, controls[operation])
                    caller = agentport.caller_from_mcp({"is_authenticated": True, "email": alice.subject,
                        "teams": None if admin else ["project"], "is_admin": admin,
                        "scoped_permissions": gateway_admin.scopes if admin else alice.scopes})
                    result = await catalog.tools.invoke_tool(db, tool.name, arguments, user_email=alice.subject,
                        token_teams=None if admin else ["project"], caller_context=caller)
                    return result.structured_content

            try:
                yield SimpleNamespace(store=store, engine=engine, catalog=catalog, client=client, users=users,
                    alice=alice, admin=admin, service=migrated, new_run=new_run, spawns=spawns,
                    outgoing=outgoing, environments=environments, mcp_control=mcp_control, sessions=sessions)
            finally:
                active = [task for (directory, _), task in worker.tasks.items()
                          if directory == str(worker.root.resolve())]
                for task in active:
                    task.cancel()
                await asyncio.gather(*active, return_exceptions=True)
    store.engine.dispose()
    native_engine.dispose()


async def wait_for_step(gateway, run_id, predicate):
    async with asyncio.timeout(10):
        while True:
            step = gateway.store.step(run_id, "invoke")
            if step and predicate(step):
                return step
            assert not step or step.status != "needs_attention", step.output
            await asyncio.sleep(0.01)


def active_jobs(gateway):
    with gateway.store.tx() as db:
        return list(db.scalars(select(Lease).where(
            Lease.service_id == gateway.store.runtime_id(gateway.service.id))))


async def test_migrated_native_gateway_worker_approval_and_mcp_scope(gateway):
    run_id = gateway.new_run(target="legacy-worker")
    task = asyncio.create_task(gateway.engine.call(run_id, "invoke", "legacy-worker", {
        "prompt": {"instruction": "write"}, "session_key": "a" * 32, "export_paths": ["result.txt"]}))
    try:
        step = await wait_for_step(gateway, run_id, lambda s: s.output.get("worker", {}).get("permissions"))
        permission = step.output["worker"]["permissions"][0]
        assert len(active_jobs(gateway)) == 1 and not task.done()
        state = await gateway.mcp_control("get_run", {"run_id": run_id})
        assert state["status"] == "waiting_agent_approval" and state["permissions"][0]["id"] == permission["id"]
        option_id = state["permissions"][0]["options"][0]["option_id"]
        assert option_id == "allow"
        gateway.users["active"] = Principal(subject="bob@example.com")
        assert (await gateway.client.get(f"/orchestration/v1/runs/{run_id}")).status_code == 404
        denied = await gateway.client.post(f"/orchestration/v1/runs/{run_id}/permissions/{permission['id']}",
                                          json={"option_id": "allow"})
        assert denied.status_code == 404
        gateway.users["active"] = gateway.alice
        await gateway.mcp_control("agent_permission", {"run_id": run_id,
            "permission_id": permission["id"], "option_id": option_id})
        output = await asyncio.wait_for(task, 10)
        assert output["text"] == "allowed"
        assert base64.b64decode(output["files"][0]["content_base64"]) == b"approved"
        assert active_jobs(gateway) == []
        assert gateway.spawns == ["codex-acp"]
        assert all(headers.get("Authorization") == "Bearer fake-api-key"
                   for url, _, headers in gateway.outgoing if url.endswith("/jobs"))
        assert all("X-AOG-Context" in headers and "Idempotency-Key" in headers
                   for _, _, headers in gateway.outgoing)
        assert {url for url, _, _ in gateway.outgoing} == {"http://worker/jobs", "http://worker/job-control"}
        import mcpgateway.db as native_db
        with gateway.sessions() as db:
            public = db.get(native_db.Tool, gateway.service.id)
            backend = db.get(native_db.Tool, public.extension_metadata[SERVICE_METADATA]["backend_tool_id"])
            with pytest.raises(PermissionError, match="authorized AgentPort"):
                await gateway.catalog.tools.invoke_tool(db, backend.name,
                    {"prompt": {}, "session_key": "f" * 32}, user_email=gateway.alice.subject, token_teams=None)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_central_cancel_reaches_native_acp_before_releasing_lease(gateway):
    run_id = gateway.new_run()
    task = asyncio.create_task(gateway.engine.call(run_id, "invoke", gateway.service.id, {
        "prompt": {"instruction": "wait"}, "session_key": "b" * 32}))
    try:
        await wait_for_step(gateway, run_id, lambda s: s.output.get("worker", {}).get("native_session_id"))
        response = await gateway.mcp_control("cancel", {"run_id": run_id})
        assert response["status"] == "cancel_requested"
        assert (await asyncio.wait_for(task, 10))["stop_reason"] == "cancelled"
        assert active_jobs(gateway) == []
        assert any(payload.get("operation") == "cancel" for _, payload, _ in gateway.outgoing if payload)
        assert gateway.spawns == ["codex-acp"]
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_unknown_worker_effect_is_not_replayed_and_resolution_clears_both_guards(gateway, monkeypatch):
    run_id = gateway.new_run()
    key = run_id + ":invoke"
    body = {"prompt": {"instruction": "wait"}, "session_key": "c" * 32}
    checksum = worker.hashlib.sha256(worker.Prompt(**body).model_dump_json().encode()).hexdigest()
    with worker.job_database() as db:
        db.execute("INSERT INTO jobs(id,request,status,instance,session_key) VALUES(?,?,'running','lost-process',?)",
                   (key, checksum, body["session_key"]))
    with pytest.raises(UncertainExecution):
        await gateway.engine.call(run_id, "invoke", gateway.service.id, body)
    assert gateway.store.step(run_id, "invoke").status == "needs_attention"
    assert len(active_jobs(gateway)) == 1 and not gateway.spawns
    count = len(gateway.outgoing)
    with pytest.raises(UncertainExecution):
        await gateway.engine.call(run_id, "invoke", gateway.service.id, body)
    assert len(gateway.outgoing) == count
    payload = {"outcome": "completed", "output": {"text": "operator-verified"}, "external_execution_stopped": True}
    url = f"/orchestration/v1/runs/{run_id}/steps/invoke/resolve"
    assert (await gateway.client.post(url, json=payload)).status_code == 403
    gateway.users["active"] = gateway.admin
    monkeypatch.setattr(api.DBOS, "send_async", AsyncMock())
    assert await gateway.mcp_control("resolve", {"run_id": run_id, "node_id": "invoke", **payload},
                                     admin=True) == {"accepted": True}
    assert active_jobs(gateway) == []
    assert gateway.store.step(run_id, "invoke").output == {"value": {"text": "operator-verified"}}
    with worker.job_database() as db:
        record = db.execute("SELECT status,result FROM jobs WHERE id=?", (key,)).fetchone()
        assert record[0] == "completed" and json.loads(record[1])["text"] == "operator-verified"
    with gateway.store.tx() as db:
        assert not list(db.scalars(select(Lease)))
    assert not gateway.spawns


async def test_completed_native_job_can_be_reconciled_after_central_output_validation_failure(gateway, monkeypatch):
    runtime_id = gateway.store.runtime_id(gateway.service.id)
    updated = await gateway.catalog.save({**gateway.service.document, "runtime_id": runtime_id,
        "output_schema": {"type": "object", "required": ["verified_field"]}},
        gateway.admin, rid=gateway.service.id)
    run_id = gateway.new_run(updated)
    inputs = {"prompt": {"instruction": "write"}, "session_key": "d" * 32}
    task = asyncio.create_task(gateway.engine.call(run_id, "invoke", updated.id, inputs))
    try:
        step = await wait_for_step(gateway, run_id, lambda s: s.output.get("worker", {}).get("permissions"))
        await gateway.mcp_control("agent_permission", {"run_id": run_id,
            "permission_id": step.output["worker"]["permissions"][0]["id"], "option_id": "allow"})
        with pytest.raises(UncertainExecution):
            await asyncio.wait_for(task, 10)
        step = gateway.store.step(run_id, "invoke")
        assert step.status == "needs_attention" and step.output["worker"]["status"] == "completed"
        assert len(active_jobs(gateway)) == 1
        monkeypatch.setattr(api.DBOS, "send_async", AsyncMock())
        verified = {"verified_field": "operator checked the completed native result"}
        start = len(gateway.outgoing)
        result = await gateway.mcp_control("resolve", {"run_id": run_id, "node_id": "invoke",
            "outcome": "completed", "output": verified, "external_execution_stopped": True}, admin=True)
        assert result == {"accepted": True}
        assert [payload["operation"] for _, payload, _ in gateway.outgoing[start:]] == ["status"]
        assert active_jobs(gateway) == []
        count = len(gateway.outgoing)
        assert await gateway.engine.call(run_id, "invoke", updated.id, inputs) == verified
        assert len(gateway.outgoing) == count and gateway.spawns == ["codex-acp"]
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("authentication", ["responses-provider", "native-login"])
async def test_schema2_agent_profile_runs_native_acp_with_selected_authentication(
    gateway, tmp_path, monkeypatch, authentication
):
    """Local fake ACP validates routing/session control; no model inference or live login."""
    async with asyncio.timeout(15):
        runtime_id = gateway.store.runtime_id(gateway.service.id)
        if authentication == "responses-provider":
            credential = gateway.store.save("credential", {"name": "Selected provider key",
                "encrypted": encrypt_secret({"token": "connection-api-key"})}, gateway.alice)
            linked = gateway.store.save("provider", ProviderConnection(
                name="Fake Responses provider", project_id="project", protocol="responses",
                base_url="http://fake-provider/v1", credential_ref=credential.id,
                model="fixture-model", location="local").model_dump(), gateway.admin)
            authentication_fields = {"provider_connection_id": linked.id}
        else:
            home = tmp_path / "native-login-home"
            (home / ".codex").mkdir(parents=True)
            config = home / ".codex/config.toml"
            config.write_text("# operator-owned native profile\n")
            profiles = tmp_path / "native-profiles.json"
            profiles.write_text(json.dumps({"alice-native": {"owner": gateway.alice.subject,
                "project_id": "project", "harness_key": "codex", "home": str(home)}}))
            monkeypatch.setenv("AOG_NATIVE_LOGIN_PROFILES", str(profiles))
            linked = gateway.store.save("credential", {"name": "Native login reference",
                "kind": "native_login", "native_profile_ref": "alice-native", "location": "local"}, gateway.alice)
            authentication_fields = {"native_login_ref": linked.id}
        profile = gateway.store.save("agent", AgentProfile(
            name="Codex profile", project_id="project", harness_key="codex", runtime_id=runtime_id,
            **authentication_fields).model_dump(), gateway.alice)
        runtime = gateway.store.resource(runtime_id, gateway.admin, "runtime")
        run_id = gateway.new_run()
        with gateway.store.tx() as db:
            run = db.get(Run, run_id)
            run.spec = {**run.spec, "snapshots": {row.id: {
                "kind": row.kind, "revision": row.revision, "document": row.document}
                for row in (profile, gateway.service, linked, runtime)}}
        task = asyncio.create_task(gateway.engine.call(run_id, "invoke", profile.id,
            {"instruction": "write", "export_paths": ["result.txt"]}))
        try:
            step = await wait_for_step(gateway, run_id, lambda s: s.output.get("worker", {}).get("permissions"))
            assert len(active_jobs(gateway)) == 2  # Session mutation and execution both hold leases.
            permission = step.output["worker"]["permissions"][0]
            await gateway.mcp_control("agent_permission", {"run_id": run_id,
                "permission_id": permission["id"], "option_id": "allow"})
            output = await task
            assert output["text"] == "allowed" and output["native_session_id"] == "native-control"
            session = gateway.store.resource(output["session_id"], gateway.alice, "session")
            assert session.document["agent_id"] == profile.id and session.document["native_id"] == "native-control"
            assert active_jobs(gateway) == [] and gateway.spawns == ["codex-acp"]
            submits = [(payload, headers) for url, payload, headers in gateway.outgoing if url.endswith("/jobs")]
            assert len(submits) == 1
            payload, headers = submits[0]
            assert payload["session_key"] == session.id and payload["harness_key"] == "codex"
            if authentication == "responses-provider":
                assert payload["provider_connection"] == {"protocol": "responses",
                    "base_url": "http://fake-provider/v1", "model": "fixture-model"}
                assert headers["Authorization"] == "Bearer connection-api-key"
                assert gateway.environments[0]["AOG_MODEL_API_KEY"] == "connection-api-key"
                assert "connection-api-key" not in (Path(gateway.environments[0]["CODEX_HOME"]) / "config.toml").read_text()
            else:
                assert payload["native_login_ref"] == "alice-native" and "provider_connection" not in payload
                assert "Authorization" not in headers  # The worker's legacy API key must never leak into native login.
                assert Path(gateway.environments[0]["HOME"]) == home
                assert config.read_text() == "# operator-owned native profile\n"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
