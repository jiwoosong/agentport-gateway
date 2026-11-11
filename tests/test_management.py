from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from aog import management
from aog.api import operator, reader
from aog.lifecycle import Lifecycle
from aog.models import Principal
from aog.store import Store


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path}/state.db")
    store.init()
    calls = []

    class Backend:
        async def start(self, target):
            calls.append(("start", target))

        async def stop(self, target):
            calls.append(("stop", target))

        async def update(self, target):
            calls.append(("update", target))

    async def healthy(*_):
        pass

    engine = SimpleNamespace(store=store, lifecycle=Lifecycle(store, Backend(), healthy))
    monkeypatch.setattr(management, "current", lambda: engine)
    actor = {"value": Principal(subject="operator", is_admin=True)}

    async def principal():
        return actor["value"]

    app = FastAPI()
    app.include_router(management.router, prefix="/orchestration/v1")
    app.dependency_overrides[reader] = principal
    app.dependency_overrides[operator] = principal
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway")
    return client, store, calls, actor, engine


async def test_runtime_operations_drain_shared_jobs_before_stopping(setup):
    client, store, calls, actor, _ = setup
    async with client:
        response = await client.post("/orchestration/v1/runtimes", json={
            "schema_version": 2, "name": "GPU", "backend": "komodo", "target_id": "gpu-stack",
            "health_url": "http://gpu/health", "concurrency": 2,
        })
        assert response.status_code == 201, response.text
        runtime = response.json()
        rid = runtime["id"]
        assert runtime["capabilities"]["update"]
        assert (await client.post(f"/orchestration/v1/runtimes/{rid}/operations/start")).json() == {"phase": "running"}
        for tool in ("native-tool-a", "native-tool-b"):
            store.bind_runtime(tool, rid, actor["value"])
            assert store.acquire(tool, tool, "job-" + tool, 2)
        view = (await client.get(f"/orchestration/v1/runtimes/{rid}")).json()
        assert view["active_jobs"] == 2
        store.release("native-tool-a")
        assert (await client.post(f"/orchestration/v1/runtimes/{rid}/operations/stop")).status_code == 409
        assert calls == [("start", "gpu-stack")]
        store.release("native-tool-b")
        assert (await client.post(f"/orchestration/v1/runtimes/{rid}/operations/stop")).json() == {"phase": "stopped"}
        assert (await client.post(f"/orchestration/v1/runtimes/{rid}/operations/stop")).status_code == 200
        assert calls == [("start", "gpu-stack"), ("stop", "gpu-stack")]
        actor["value"] = Principal(subject="user")
        assert (await client.get("/orchestration/v1/runtimes")).json() == []
        assert (await client.post(f"/orchestration/v1/runtimes/{rid}/operations/start")).status_code == 403


async def test_openshell_updates_are_not_reported_as_supported(setup):
    client, _, calls, _, _ = setup
    async with client:
        response = await client.post("/orchestration/v1/runtimes", json={
            "schema_version": 2, "name": "Sandbox", "backend": "openshell",
            "openshell": {"gateway": "pc", "workspace": "team", "sandbox": "one", "sandbox_id": "id-1"},
        })
        assert response.status_code == 201
        runtime = response.json()
        assert not runtime["capabilities"]["update"]
        assert (await client.post(f"/orchestration/v1/runtimes/{runtime['id']}/operations/update")).status_code == 409
        assert calls == []


async def test_uncertain_operation_preserves_error_until_verified_reconciliation(setup):
    client, store, _, _, engine = setup

    async def uncertain(*_):
        raise TimeoutError("DO-NOT-DISCLOSE-PRIVATE-DEPLOYMENT-LOG")

    engine.lifecycle.backend.update = uncertain
    async with client:
        created = await client.post("/orchestration/v1/runtimes", json={
            "schema_version": 2, "name": "GPU", "backend": "komodo", "target_id": "stack",
            "health_url": "http://gpu/health",
        })
        rid = created.json()["id"]
        response = await client.post(f"/orchestration/v1/runtimes/{rid}/operations/update")
        assert response.status_code == 502
        assert "DO-NOT-DISCLOSE" not in response.text
        assert store.phase(rid) == "error"
        assert (await client.post(f"/orchestration/v1/runtimes/{rid}/reconcile", json={"phase": "stopped"})).status_code == 422
        reconciled = await client.post(f"/orchestration/v1/runtimes/{rid}/reconcile", json={
            "phase": "stopped", "deployment_verified": True,
        })
        assert reconciled.json() == {"phase": "stopped"}


async def test_provider_and_native_login_enrollment_keep_identity_and_protocol_separate(setup):
    client, store, _, actor, _ = setup
    actor["value"] = alice = Principal(subject="alice")
    key = store.save("credential", {"name": "API key", "encrypted": "PRIVATE-CIPHERTEXT"}, alice)
    body = {"schema_version": 2, "name": "API", "protocol": "responses",
            "base_url": "https://provider.example/v1", "credential_ref": key.id}
    async with client:
        created = await client.post("/orchestration/v1/providers", json=body)
        assert created.status_code == 201, created.text
        assert "PRIVATE-CIPHERTEXT" not in created.text
        assert created.json()["protocol"] == "responses"
        assert (await client.post("/orchestration/v1/providers", json={**body, "location": "local"})).status_code == 403
        native = await client.post("/orchestration/v1/credentials/native", json={"name": "Subscription", "profile_ref": "alice-codex"})
        assert native.status_code == 201
        login = store.resource(native.json()["id"], alice, "credential")
        assert login.document == {"name": "Subscription", "kind": "native_login", "native_profile_ref": "alice-codex", "location": "cloud"}
        assert (await client.post("/orchestration/v1/providers", json={**body, "credential_ref": login.id})).status_code == 422
        assert (await client.post("/orchestration/v1/credentials/native", json={"name": "Bad", "profile_ref": "/host/credentials"})).status_code == 422
        actor["value"] = Principal(subject="bob")
        assert (await client.get("/orchestration/v1/providers")).json() == []
        assert (await client.put(f"/orchestration/v1/providers/{created.json()['id']}", json=body)).status_code == 404
        templates = (await client.get("/orchestration/v1/harnesses")).json()
        assert {entry["key"] for entry in templates} == {"codex", "claude", "hermes", "kimi", "opencode"}
        assert all(not entry["live_verified"] for entry in templates)
        assert next(entry for entry in templates if entry["key"] == "kimi")["native_profile_required"]
