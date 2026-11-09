import asyncio

from dbos import DBOS

from aog import engine as module
from aog.engine import Engine
from aog.models import Agent, Invocation, Node, Principal, Service, Workflow
from aog.store import Store


async def test_native_session_review_loop_and_finite_limit(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path}/app.db")
    store.init()
    actor = Principal(subject="owner@example.com")
    runtime = store.save(
        "service",
        Service(
            name="ACP fixture",
            kind="agent",
            transport="acp",
            endpoint="http://fixture/invoke",
            egress_policy="isolated",
        ).model_dump(),
        actor,
    )
    author = store.save(
        "agent", Agent(name="Author", service_id=runtime.id, persona="author").model_dump(), actor
    )
    reviewer = store.save(
        "agent", Agent(name="Reviewer", service_id=runtime.id, persona="reviewer").model_dump(), actor
    )
    calls = []

    async def permit(value):
        return value

    async def invoke(service, values, key, *_):
        calls.append(values)
        if values["persona"] == "author":
            return {"native_session_id": "author-native", "text": "explicit artifact", "files": []}
        reviews = sum(v["persona"] == "reviewer" for v in calls)
        return {
            "native_session_id": f"review-{reviews}",
            "structured": {"approved": reviews == 3, "feedback": "revise"},
        }

    engine = Engine(store, permission_check=permit, caller=invoke)
    monkeypatch.setattr(module, "engine", engine)
    graph = store.save(
        "workflow",
        Workflow(
            name="Three reviews",
            nodes=[
                Node(
                    id="write",
                    type="review",
                    target=author.id,
                    reviewer=reviewer.id,
                    inputs={"instruction": "Write", "export_paths": []},
                    max_rounds=3,
                )
            ],
        ).model_dump(),
        actor,
    )
    DBOS(config={"name": "agentport-test", "system_database_url": f"sqlite:///{tmp_path}/dbos.db"})
    DBOS.launch()
    try:
        run = await engine.submit("workflow", graph.id, Invocation(), actor)
        for _ in range(200):
            result = store.run(run.id, actor)
            if result.status in {"completed", "failed"}:
                break
            await asyncio.sleep(0.02)
        assert result.status == "completed"
        assert result.result["write"]["rounds"] == 3
        assert len(calls) == 6
        authors = [v for v in calls if v["persona"] == "author"]
        assert authors[0]["native_session_id"] is None
        assert authors[1]["native_session_id"] == "author-native"
        assert len({v["session_key"] for v in authors}) == 1
        # A permanently rejecting reviewer reaches a terminal failure, not an unbounded loop.
        run2 = await engine.submit("workflow", graph.id, Invocation(), actor)
        for _ in range(200):
            result2 = store.run(run2.id, actor)
            if result2.status in {"completed", "failed"}:
                break
            await asyncio.sleep(0.02)
        assert result2.status == "failed"
        assert len(calls) == 12
    finally:
        DBOS.destroy(workflow_completion_timeout_sec=2)
