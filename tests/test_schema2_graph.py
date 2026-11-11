from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from aog import engine
from aog.models import (
    AgentProfile,
    Invocation,
    Node,
    Principal,
    ProviderConnection,
    RuntimeDefinition,
    Service,
)
from aog.store import Run, Store


@pytest.fixture
def release_graph(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path}/state.db")
    store.init()
    monkeypatch.setattr(engine, "current", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(engine, "SetWorkflowID", lambda _: nullcontext())
    completed = AsyncMock()
    start = AsyncMock(return_value=SimpleNamespace(get_result=completed))
    monkeypatch.setattr(engine.DBOS, "start_workflow_async", start)

    def graph(*, auth="provider", auth_location="cloud", worker_location="local", local_only=False,
              resolved=True, legacy=False):
        profile = {
            "schema_version": 2, "name": "Writer", "harness_key": "codex", "runtime_id": "runtime",
            "provider_connection_id": "authentication" if auth == "provider" else None,
            "native_login_ref": "authentication" if auth == "native" else None,
        }
        if legacy:
            profile = {"schema_version": 1, "name": "Writer", "service_id": "worker"}
        snapshots = {
            "agent": {"kind": "agent", "revision": 1, "document": profile},
            "worker": {"kind": "service", "revision": 1,
                       "document": {"location": worker_location}},
            "authentication": {"kind": "provider" if auth == "provider" else "credential", "revision": 1,
                               "document": {"location": auth_location}},
        }
        if resolved:
            snapshots["agent"]["resolved_service_id"] = "worker"
        with store.tx() as db:
            db.add(Run(id="run", owner="alice", principal={"subject": "alice"}, inputs={},
                       spec={"local_only": local_only, "snapshots": snapshots}))
        return Node(id="review", type="review", target="agent", reviewer="reviewer", release=True)

    return graph, start, completed


@pytest.mark.parametrize("auth", ["provider", "native"])
async def test_schema2_review_approves_exact_cloud_payload_from_frozen_authentication(release_graph, auth):
    graph, start, completed = release_graph
    node = graph(auth=auth)
    values = {"instruction": "Review only the selected excerpt", "excerpt": "approved fields"}
    await engine.review_release("run", node, "release-author-0", "agent", values)
    raw = start.call_args.args[2]
    assert raw["type"] == "approval" and raw["release"] is True
    assert start.call_args.args[3] == values
    completed.assert_awaited_once()


async def test_review_never_relabels_cloud_worker_as_local_provider(release_graph):
    graph, start, _ = release_graph
    node = graph(auth_location="local", worker_location="cloud", local_only=True)
    with pytest.raises(HTTPException) as error:
        await engine.review_release("run", node, "release-author-0", "agent", {})
    assert error.value.status_code == 403
    start.assert_not_awaited()


async def test_schema2_review_without_frozen_worker_fails_closed(release_graph):
    graph, start, _ = release_graph
    node = graph(resolved=False)
    with pytest.raises(HTTPException) as error:
        await engine.review_release("run", node, "release-author-0", "agent", {})
    assert error.value.status_code == 409
    start.assert_not_awaited()


async def test_legacy_local_review_needs_no_cloud_release(release_graph):
    graph, start, _ = release_graph
    node = graph(legacy=True)
    await engine.review_release("run", node, "release-author-0", "agent", {})
    start.assert_not_awaited()


async def test_submission_freezes_resolved_schema2_worker_separately_from_profile_contract(release_graph):
    store = engine.current().store
    alice = Principal(subject="alice", is_admin=True)
    runtime = store.register_runtime(RuntimeDefinition(name="Worker"), alice)
    secret = store.save("credential", {"name": "API", "encrypted": "not-needed-for-submission"}, alice)
    provider = store.save("provider", ProviderConnection(name="API", protocol="responses",
        base_url="https://provider.example/v1", credential_ref=secret.id).model_dump(), alice)
    profile = store.save("agent", AgentProfile(name="Writer", runtime_id=runtime.id,
        harness_key="codex", provider_connection_id=provider.id).model_dump(), alice)
    service = SimpleNamespace(id="native-worker-tool", kind="service", owner=alice.subject,
        project_id=None, revision=1,
        document=Service(name="ACP", kind="agent", transport="acp",
                         endpoint="http://worker/invoke").model_dump())
    store.bind_runtime(service.id, runtime.id, alice)
    catalog = SimpleNamespace(resource=AsyncMock(return_value=service),
                              list_resources=AsyncMock(return_value=[service]))
    application = engine.Engine(store, permission_check=AsyncMock(return_value=alice), catalog=catalog)
    run = await application.submit("agent", profile.id,
                                   Invocation(inputs={"instruction": "write"}), alice)
    frozen = run.spec["snapshots"][profile.id]
    assert frozen["resolved_service_id"] == service.id
    assert "service_id" not in frozen["document"]
    assert run.spec["snapshots"][service.id]["document"] == service.document
    assert run.spec["snapshots"][provider.id]["document"] == provider.document
