import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update

from aog import adapters
from aog.engine import Engine, digest
from aog.graph import condition, layers, resolve
from aog.lifecycle import Lifecycle
from aog.models import Node, Principal, Service, Workflow
from aog.store import Lease, Run, Runtime, Store


@pytest.fixture
def store(tmp_path):
    value = Store(f"sqlite:///{tmp_path}/test.db")
    value.init()
    return value


@pytest.fixture
def alice():
    return Principal(subject="alice@example.com", teams=["project-a"])


def registered(store, alice, **changes):
    definition = Service(
        name="Convert", endpoint="http://converter/convert", egress_policy="isolated", **changes
    )
    return store.save("service", definition.model_dump(), alice)


def queued(store, alice, row, local_only=True, released=None):
    with store.tx() as db:
        db.add(
            Run(
                id="run-a",
                owner=alice.subject,
                principal=alice.model_dump(),
                inputs={},
                spec={
                    "local_only": local_only,
                    "released": released or [],
                    "snapshots": {
                        row.id: {"kind": "service", "revision": row.revision, "document": row.document}
                    },
                },
            )
        )


async def permit(actor):
    return actor


def test_private_project_and_owner_isolation(store, alice):
    row = registered(store, alice)
    bob = Principal(subject="bob@example.com", teams=["project-a"])
    with pytest.raises(HTTPException) as exc:
        store.resource(row.id, bob)
    assert exc.value.status_code == 404
    row = store.save(
        "service", {**row.document, "project_id": "project-a", "visibility": "project"}, alice, row.id
    )
    assert store.resource(row.id, bob).id == row.id
    with pytest.raises(HTTPException):
        store.resource(row.id, bob, write=True)
    outsider = Principal(subject="mallory@example.com", teams=["project-b"])
    assert store.list_resources(outsider, "service") == []
    with pytest.raises(HTTPException):
        store.save("workflow", {"project_id": "project-b"}, alice)


def test_runs_remain_private_even_when_definition_shared(store, alice):
    row = registered(store, alice, project_id="project-a", visibility="project")
    queued(store, alice, row)
    with pytest.raises(HTTPException):
        store.run("run-a", Principal(subject="bob@example.com", teams=["project-a"]))


def test_delegated_agent_cannot_read_sibling_agent_output(store, alice):
    row = registered(store, alice)
    queued(store, alice, row)
    with store.tx() as db:
        run = db.get(Run, "run-a")
        run.principal = {**alice.model_dump(), "agent_id": "agent-a", "parent_run_id": "parent"}
    grant = alice.model_copy(update={"agent_id": "agent-b", "parent_run_id": "parent"})
    with pytest.raises(HTTPException):
        store.run("run-a", grant)


def test_cancellation_survives_uncertain_parallel_execution(store, alice):
    row = registered(store, alice)
    queued(store, alice, row)
    store.status("run-a", "cancel_requested")
    store.step("run-a", "external", "needs_attention", {})
    store.status("run-a", "needs_attention")
    store.status("run-a", "running")  # A sibling approval must not erase either state.
    assert store.run("run-a", alice).status == "needs_attention"
    assert store.run("run-a", alice).cancel_requested
    store.step("run-a", "external", "completed", {"value": {}})
    store.status("run-a", "running")
    assert store.run("run-a", alice).status == "cancel_requested"


def test_graph_validates_cycles_and_data_dependencies():
    nodes = [
        Node(id="source", type="join"),
        Node(id="left", type="join", depends_on=["source"]),
        Node(id="right", type="join", depends_on=["source"]),
        Node(id="done", type="join", depends_on=["left", "right"]),
    ]
    assert [[n.id for n in group] for group in layers(nodes)] == [["source"], ["left", "right"], ["done"]]
    nodes[0].depends_on = ["done"]
    with pytest.raises(ValueError, match="Cycles"):
        layers(nodes)
    with pytest.raises(ValidationError):
        Workflow(
            name="bad",
            nodes=[
                Node(id="a", type="join"),
                Node(id="b", type="join", inputs={"value": {"$ref": "nodes.a.secret"}}),
            ],
        )


def test_mapping_and_conditions_are_explicit():
    assert resolve({"text": {"$ref": "input.public"}}, {"public": "yes", "private": "no"}, {}) == {
        "text": "yes"
    }
    assert condition({"left": True, "op": "eq", "right": True})
    with pytest.raises(ValueError):
        condition({"left": 1, "op": "__import__", "right": 2})
    with pytest.raises(ValueError):
        resolve({"$ref": "input.missing"}, {}, {})


def test_single_capacity_across_concurrent_requests(store, alice):
    row = registered(store, alice)
    with ThreadPoolExecutor(max_workers=8) as pool:
        got = list(pool.map(lambda n: store.acquire(row.id, f"lease-{n}", f"run-{n}", 1), range(20)))
    assert sum(got) == 1
    assert not store.claim_stop(row.id, 0)
    with store.tx() as db:
        lease = db.scalar(select(Lease))
    store.release(lease.id)
    assert store.claim_stop(row.id, 0)
    assert not store.acquire(row.id, "new", "run-new", 1)


class Backend:
    starts = 0
    stops = 0

    async def start(self, _):
        self.starts += 1
        await asyncio.sleep(0.03)

    async def stop(self, _):
        self.stops += 1


async def healthy(*_):
    pass


async def test_one_cold_start_and_no_idle_stop_while_busy(store, alice):
    row = registered(
        store,
        alice,
        lifecycle="on_demand",
        stack_id="stack",
        health_url="http://converter/health",
        concurrency=10,
        idle_seconds=10,
    )
    backend = Backend()
    lifecycle = Lifecycle(store, backend, healthy)
    service = Service.model_validate(row.document)
    await asyncio.gather(*(lifecycle.acquire(row.id, service, f"lease-{n}", f"run-{n}") for n in range(5)))
    assert backend.starts == 1
    with store.tx() as db:
        db.execute(update(Runtime).values(last_used=0))
    await lifecycle.sweep()
    assert backend.stops == 0
    for n in range(5):
        store.release(f"lease-{n}")
    with store.tx() as db:
        db.execute(update(Runtime).values(last_used=0))
    await lifecycle.sweep()
    assert backend.stops == 1


async def test_authorization_precedes_wake_and_side_effect(store, alice):
    row = registered(store, alice)
    queued(store, alice, row)

    async def deny(_):
        raise HTTPException(403, "revoked")

    async def unexpected(*_):
        pytest.fail("Network must not be reached")

    engine = Engine(store, permission_check=deny, caller=unexpected)
    with pytest.raises(HTTPException):
        await engine.call("run-a", "call", row.id, {})
    with store.tx() as db:
        assert db.scalar(select(Lease)) is None


async def test_lost_response_is_not_replayed_and_lease_is_preserved(store, alice):
    row = registered(store, alice)
    queued(store, alice, row)
    calls = []

    async def lost(*_):
        calls.append(1)
        raise adapters.UncertainExecution("Lost response")

    engine = Engine(store, permission_check=permit, caller=lost)
    for _ in range(2):
        with pytest.raises(adapters.UncertainExecution):
            await engine.call("run-a", "call", row.id, {})
    assert len(calls) == 1
    assert not store.claim_stop(row.id, 0)
    assert store.run("run-a", alice).status == "needs_attention"


async def test_cloud_requires_release_of_exact_input(store, alice):
    row = registered(store, alice, location="cloud")
    values = {"public": "summary"}
    queued(store, alice, row, local_only=False, released=[digest(values)])
    calls = []

    async def send(_, inputs, *__):
        calls.append(inputs)
        return {"ok": True}

    engine = Engine(store, permission_check=permit, caller=send)
    with pytest.raises(HTTPException):
        await engine.call("run-a", "bad", row.id, {**values, "private": "raw"})
    assert await engine.call("run-a", "ok", row.id, values) == {"ok": True}
    assert calls == [values]


async def test_local_only_blocks_cloud_before_call(store, alice):
    row = registered(store, alice, location="cloud")
    queued(store, alice, row)
    engine = Engine(store, permission_check=permit)
    with pytest.raises(HTTPException) as exc:
        await engine.call("run-a", "call", row.id, {})
    assert exc.value.status_code == 403


async def test_failed_release_stops_new_stack_before_rollback(store, alice):
    row = registered(
        store,
        alice,
        stack_id="old",
        health_url="http://converter/health",
        current_release="v1",
        releases={"v2": "new"},
        rollback_safe=True,
    )
    events = []

    class Deployment:
        async def stop(self, stack):
            events.append(("stop", stack))

        async def start(self, stack):
            events.append(("start", stack))

        async def update(self, stack):
            events.append(("deploy", stack))
            raise RuntimeError("New image failed")

    result = await Lifecycle(store, Deployment(), healthy).deploy_release(row, "v2", alice)
    assert result == {"release": "v1", "rolled_back": True}
    assert events == [("stop", "old"), ("deploy", "new"), ("stop", "new"), ("start", "old")]
    assert store.phase(row.id) == "running"
    assert store.resource(row.id, alice).document["stack_id"] == "old"


async def test_stateful_failure_does_not_blindly_roll_back(store, alice):
    row = registered(
        store, alice, stack_id="old", health_url="http://converter/health", releases={"v2": "new"}
    )

    class Deployment:
        async def stop(self, _):
            pass

        async def update(self, _):
            raise RuntimeError("Migration failed")

        async def start(self, _):
            pytest.fail("Must not blindly restore old image over changed data")

    with pytest.raises(HTTPException):
        await Lifecycle(store, Deployment(), healthy).deploy_release(row, "v2", alice)
    assert store.phase(row.id) == "error"
