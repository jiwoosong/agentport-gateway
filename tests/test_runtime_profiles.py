import asyncio
import time

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update

from aog.lifecycle import Lifecycle
from aog.models import AgentProfile, Principal, ProviderConnection, RuntimeDefinition, Service
from aog.store import Lease, Resource, Run, Runtime, RuntimeBinding, RuntimeTarget, SchemaMigration, Store


@pytest.fixture
def store(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/state.db")
    store.init()
    return store


@pytest.fixture
def admin():
    return Principal(subject="operator", is_admin=True, teams=["team"])


async def test_two_tools_share_startup_capacity_and_busy_protection(store, admin):
    runtime = store.register_runtime(
        RuntimeDefinition(
            name="GPU service", backend="komodo", target_id="stack", lifecycle="on_demand",
            health_url="http://gpu/health", concurrency=2, idle_seconds=10,
        ), admin,
    )
    for tool in ("cf-tool-a", "cf-tool-b"):
        store.bind_runtime(tool, runtime.id, admin)
    calls = []

    class Backend:
        async def start(self, target):
            calls.append(("start", target))
            await asyncio.sleep(0.01)

        async def stop(self, target):
            calls.append(("stop", target))

    async def healthy(*_):
        pass

    lifecycle = Lifecycle(store, Backend(), healthy)
    service = Service(name="Native tool", endpoint="http://gpu/run")
    await asyncio.gather(
        lifecycle.acquire("cf-tool-a", service, "a", "run-a"),
        lifecycle.acquire("cf-tool-b", service, "b", "run-b"),
    )
    assert calls == [("start", "stack")]
    assert not store.acquire("cf-tool-a", "third", "run-c", 99)
    store.release("a")
    assert not store.claim_stop("cf-tool-a", 0)
    with store.tx() as db:
        db.execute(update(Runtime).values(last_used=time.time() - 100))
        assert {lease.service_id for lease in db.scalars(select(Lease))} == {runtime.id}
        assert db.get(Resource, "cf-tool-a") is None
    await lifecycle.sweep()
    assert calls == [("start", "stack")]
    store.release("b")
    with store.tx() as db:
        db.execute(update(Runtime).values(last_used=time.time() - 100))
    await lifecycle.sweep()
    await lifecycle.sweep()
    assert calls == [("start", "stack"), ("stop", "stack")]
    assert store.phase("cf-tool-a") == store.phase("cf-tool-b") == "stopped"


@pytest.mark.parametrize("backend", ["komodo", "openshell"])
def test_duplicate_native_runtime_targets_rejected(store, admin, backend):
    target = (
        {"target_id": "stack"} if backend == "komodo" else
        {"openshell": {"gateway": "pc", "workspace": "team", "sandbox": "alice", "sandbox_id": "one"}}
    )
    definition = RuntimeDefinition(name="Runtime", backend=backend, **target)
    original = store.register_runtime(definition, admin)
    with pytest.raises(HTTPException) as error:
        store.register_runtime(definition, admin)
    assert error.value.status_code == 409
    assert store.resource(original.id, admin, "runtime").id == original.id


def test_runtime_rebinding_requires_draining_and_operator(store, admin):
    old = store.register_runtime(RuntimeDefinition(name="Old"), admin)
    new = store.register_runtime(RuntimeDefinition(name="New"), admin)
    store.bind_runtime("cf-tool", old.id, admin)
    assert store.acquire("cf-tool", "active", "run-a", 1)
    with pytest.raises(HTTPException) as error:
        store.bind_runtime("cf-tool", new.id, admin)
    assert error.value.status_code == 409
    assert store.runtime_id("cf-tool") == old.id
    store.release("active")
    store.bind_runtime("cf-tool", new.id, admin)
    assert store.runtime_id("cf-tool") == new.id
    with pytest.raises(HTTPException) as error:
        store.bind_runtime("another-tool", new.id, Principal(subject="user"))
    assert error.value.status_code == 403


def test_provider_and_native_login_do_not_cross_owners(store, admin):
    runtime = store.register_runtime(
        RuntimeDefinition(name="Shared", project_id="team", visibility="project"), admin,
    )
    alice = Principal(subject="alice", teams=["team"])
    bob = Principal(subject="bob", teams=["team"])
    credential = store.save("credential", {"name": "Key", "encrypted": "opaque"}, alice)
    provider = store.save("provider", ProviderConnection(
        name="API", protocol="responses", base_url="https://provider.example/v1",
        credential_ref=credential.id,
    ).model_dump(), alice)
    profile = AgentProfile(name="Coder", harness_key="codex", runtime_id=runtime.id,
                           provider_connection_id=provider.id)
    assert store.save("agent", profile.model_dump(), alice).document["schema_version"] == 2
    for kind, value in [
        ("provider", {**provider.document, "credential_ref": credential.id}),
        ("agent", profile.model_dump()),
        ("agent", {**profile.model_dump(), "provider_connection_id": None, "native_login_ref": credential.id}),
    ]:
        with pytest.raises(HTTPException):
            store.save(kind, value, bob)
    with pytest.raises(ValidationError):
        AgentProfile(**{**profile.model_dump(), "native_login_ref": credential.id})
    with pytest.raises(ValidationError):
        ProviderConnection(**{**provider.document, "protocol": "openai"})


def test_additive_migration_preserves_legacy_work_and_identifiers(store, admin):
    service = Service(name="Legacy", endpoint="http://worker/run", stack_id="old-stack")
    with store.tx() as db:
        db.add(Resource(id="old-service", kind="service", owner=admin.subject, document=service.model_dump()))
        db.add(Runtime(service_id="old-service", phase="running"))
        db.add(Run(id="old-run", owner=admin.subject, principal=admin.model_dump(), inputs={}, spec={}))
        db.add(Lease(id="old-lease", service_id="old-service", run_id="old-run"))
    for table in (RuntimeBinding.__table__, RuntimeTarget.__table__, SchemaMigration.__table__):
        table.drop(store.engine)
    store.init()
    store.init()
    assert store.runtime_id("old-service") == "old-service"
    assert store.phase("old-service") == "running"
    assert not store.claim_stop("old-service", 0)
    assert store.run("old-run", admin).id == "old-run"
    with store.tx() as db:
        assert list(db.scalars(select(SchemaMigration.version))) == [2]
        assert db.scalar(select(RuntimeTarget.runtime_id)) == "old-service"
