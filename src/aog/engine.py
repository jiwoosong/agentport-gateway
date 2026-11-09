import asyncio
import hashlib
import json
import os
import time
import uuid

import jwt
from dbos import DBOS, SetWorkflowID
from fastapi import HTTPException
from jsonschema import validate
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from aog import adapters
from aog.graph import condition, layers, resolve
from aog.lifecycle import Lifecycle
from aog.models import Agent, Invocation, Node, Principal, Service, Workflow
from aog.security import authorize
from aog.store import Lease, Run, Step, Store


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Engine:
    def __init__(self, store: Store, permission_check=authorize, caller=adapters.invoke, lifecycle=None):
        self.store = store
        self.authorize = permission_check
        self.caller = caller
        self.lifecycle = lifecycle or Lifecycle(store)

    async def submit(self, kind: str, rid: str, request: Invocation, actor: Principal):
        actor = await self.authorize(actor)
        if actor.agent_id:
            if kind != "service" or rid not in (actor.allowed_service_ids or []):
                raise HTTPException(403, "Service is outside the agent grant")
            parent = self.store.run(actor.parent_run_id, actor)
            request.local_only = request.local_only or parent.spec["local_only"]
            if request.allow_cloud and digest(request.inputs) not in parent.spec["released"]:
                raise HTTPException(403, "Agent cannot release new cloud inputs")
        resource = self.store.resource(rid, actor, kind)
        if kind == "workflow":
            workflow = Workflow.model_validate(resource.document)
            spec = workflow.model_dump()
            spec["local_only"] = workflow.local_only or request.local_only
        else:
            node_type = (
                "agent"
                if kind == "agent"
                else ("model" if resource.document.get("kind") == "model" else "tool")
            )
            spec = Workflow(
                name=resource.document["name"],
                local_only=request.local_only,
                nodes=[
                    Node(
                        id="invoke",
                        type=node_type,
                        target=rid,
                        inputs={"$ref": "input"},
                        session_id=request.session_id,
                    )
                ],
            ).model_dump()
        snapshots = {}
        for node in Workflow.model_validate(spec).nodes:
            for target, target_kind in [
                (node.target, "agent" if node.type in {"agent", "review"} else "service"),
                (node.reviewer, "agent"),
            ]:
                if target:
                    row = self.store.resource(target, actor, target_kind)
                    snapshots[target] = {"kind": row.kind, "revision": row.revision, "document": row.document}
                    if row.kind == "agent":
                        agent = Agent.model_validate(row.document)
                        srv = self.store.resource(agent.service_id, actor, "service")
                        snapshots[srv.id] = {
                            "kind": "service",
                            "revision": srv.revision,
                            "document": srv.document,
                        }
            if node.session_id:
                session = self.store.resource(node.session_id, actor, "session", write=True)
                if session.owner != actor.subject or session.document["agent_id"] != node.target:
                    raise HTTPException(403, "Session belongs to another actor or agent")
        spec["snapshots"] = snapshots
        spec["definition_revision"] = resource.revision
        # Direct calls can explicitly release their exact input; graphs use approval nodes.
        spec["released"] = [digest(request.inputs)] if request.allow_cloud and kind != "workflow" else []
        rid_run = (
            digest([actor.subject, request.idempotency_key]) if request.idempotency_key else uuid.uuid4().hex
        )
        try:
            with self.store.tx() as db:
                old = db.get(Run, rid_run)
                if old:
                    if old.owner != actor.subject or old.spec != spec or old.inputs != request.inputs:
                        raise HTTPException(409, "Idempotency key already used for different input")
                    return self.store.run(rid_run, actor)
                if actor.agent_id:
                    parent = db.get(Run, actor.parent_run_id)
                    db.execute(update(Run).where(Run.id == parent.id).values(status=Run.status))
                    db.refresh(parent)
                    if parent.status not in {"running", "waiting_approval"}:
                        raise HTTPException(403, "Parent agent run is no longer active")
                    count = db.scalar(
                        select(func.count())
                        .select_from(Run)
                        .where(
                            Run.principal["parent_run_id"].as_string() == parent.id,
                            Run.principal["agent_id"].as_string() == actor.agent_id,
                        )
                    )
                    limit = self.store.resource(actor.agent_id, actor, "agent").document.get(
                        "max_tool_calls", 20
                    )
                    if count >= limit:
                        raise HTTPException(429, "Agent tool-call budget exhausted")
                db.add(
                    Run(
                        id=rid_run,
                        owner=actor.subject,
                        project_id=resource.project_id,
                        spec=spec,
                        inputs=request.inputs,
                        principal=actor.model_dump(),
                    )
                )
        except IntegrityError:
            old = self.store.run(rid_run, actor)
            if old.spec != spec or old.inputs != request.inputs:
                raise HTTPException(409, "Idempotency key already used for different input") from None
        # Reconciliation on startup covers a crash between the commit and this enqueue.
        with SetWorkflowID(rid_run):
            await DBOS.start_workflow_async(run_graph, rid_run, spec, request.inputs, actor.model_dump())
        self.store.audit(actor.subject, "run.submit", rid_run)
        return self.store.run(rid_run, actor)

    async def call(self, run_id: str, node_id: str, target: str, inputs: dict, session_id: str | None = None):
        with self.store.tx() as db:
            run = db.get(Run, run_id)
        if run.cancel_requested:
            raise RuntimeError("Run cancelled")
        actor = await self.authorize(Principal.model_validate(run.principal))
        snap = run.spec["snapshots"][target]
        row = self.store.resource(target, actor, snap["kind"])
        agent = Agent.model_validate(snap["document"]) if row.kind == "agent" else None
        service_id = agent.service_id if agent else target
        current = self.store.resource(service_id, actor, "service")
        if not current.document.get("enabled", True):
            raise HTTPException(403, "Service disabled")
        if current.revision != run.spec["snapshots"][service_id]["revision"]:
            raise HTTPException(409, "Service changed after submission; start a new run")
        service = Service.model_validate(run.spec["snapshots"][service_id]["document"])
        if run.spec["local_only"] and (service.location != "local" or service.egress_policy != "isolated"):
            raise HTTPException(403, "Local-only requires an operator-verified isolated service")
        if service.location == "cloud":
            with self.store.tx() as db:
                approved = [
                    s.output.get("release_digest")
                    for s in db.scalars(select(Step).where(Step.run_id == run_id, Step.status == "completed"))
                    if s.output.get("release_digest")
                ]
            if digest(inputs) not in run.spec["released"] + approved:
                raise HTTPException(403, "Exact cloud input has not been released")
        key = f"{run_id}:{node_id}"
        old = self.store.step(run_id, node_id)
        if old and old.status == "completed":
            return old.output["value"]
        if old and old.status in {"running", "needs_attention"}:
            self.store.step(run_id, node_id, "needs_attention", old.output)
            self.store.status(run_id, "needs_attention")
            raise adapters.UncertainExecution("External execution requires reconciliation")
        validate(inputs, service.input_schema)
        credential = None
        credential_ref = agent.credential_ref if agent and agent.credential_ref else service.credential_ref
        if credential_ref:
            from mcpgateway.utils.services_auth import decode_auth

            secret = self.store.resource(credential_ref, actor, "credential")
            if secret.owner != actor.subject:
                raise HTTPException(403, "Personal credential belongs to another user")
            credential = decode_auth(secret.document["encrypted"])["token"]
        session = None
        if agent:
            if agent.allowed_service_ids and not (
                os.environ.get("AOG_INTERNAL_MCP_URL") and os.environ.get("AOG_DELEGATION_SECRET")
            ):
                raise HTTPException(503, "Scoped MCP delegation is not configured")
            if session_id:
                session = self.store.resource(session_id, actor, "session", write=True)
                if session.owner != actor.subject or session.document["agent_id"] != target:
                    raise HTTPException(403, "Session ownership mismatch")
            else:
                session = self.store.save(
                    "session",
                    {
                        "agent_id": target,
                        "native_id": None,
                        "name": f"{agent.name} session",
                        "project_id": row.project_id,
                    },
                    actor,
                )
            # Serialize native session mutation, including runs through different services.
            try:
                with self.store.tx() as db:
                    existing = db.get(Lease, f"session:{session.id}")
                    if existing:
                        raise HTTPException(409, "Session is already in use")
                    db.add(Lease(id=f"session:{session.id}", service_id=service_id, run_id=run_id))
            except IntegrityError:
                raise HTTPException(409, "Session is already in use") from None
            inputs = {
                "prompt": inputs,
                "persona": agent.persona,
                "model": agent.model,
                "workspace_files": inputs.get("workspace_files", []),
                "export_paths": inputs.get("export_paths", []),
                "session_key": session.id,
                "native_session_id": session.document["native_id"],
                "local_only": run.spec["local_only"],
                "allowed_service_ids": agent.allowed_service_ids,
            }
            if agent.allowed_service_ids:
                grant = actor.model_copy(
                    update={
                        "is_admin": False,
                        "agent_id": target,
                        "parent_run_id": run_id,
                        "allowed_service_ids": agent.allowed_service_ids,
                        "scopes": ["tools.read", "tools.execute"],
                    }
                )
                inputs["gateway_mcp_url"] = os.environ["AOG_INTERNAL_MCP_URL"]
                inputs["gateway_token"] = jwt.encode(
                    {
                        "iss": "agentport",
                        "aud": "agentport-mcp",
                        "exp": int(time.time()) + service.timeout_seconds + 60,
                        "principal": grant.model_dump(),
                    },
                    os.environ["AOG_DELEGATION_SECRET"],
                    algorithm="HS256",
                )
        self.store.step(run_id, node_id, "starting")
        try:
            await self.lifecycle.acquire(service_id, service, key, run_id)
        except Exception:
            self.store.release(key)
            if session:
                self.store.release(f"session:{session.id}")
            raise
        self.store.step(run_id, node_id, "running", {"session_id": session.id if session else None})
        try:
            value = await self.caller(
                service, inputs, key, {**actor.model_dump(), "project_id": current.project_id}, credential
            )
            validate(value, service.output_schema)
            if session:
                native = value.get("native_session_id")
                session.document = {**session.document, "native_id": native}
                self.store.save("session", session.document, actor, session.id)
                value = {**value, "session_id": session.id}
            self.store.step(run_id, node_id, "completed", {"value": value})
            self.store.release(key)
            if session:
                self.store.release(f"session:{session.id}")
            return value
        except Exception as exc:
            # Even output validation errors occur AFTER the external side effect.
            self.store.step(
                run_id,
                node_id,
                "needs_attention",
                {"error_type": type(exc).__name__, "session_id": session.id if session else None},
            )
            self.store.status(run_id, "needs_attention")
            raise adapters.UncertainExecution("External execution needs reconciliation") from exc


engine: Engine | None = None


def current() -> Engine:
    if engine is None:
        raise RuntimeError("Orchestration engine not initialized")
    return engine


@DBOS.step()
async def external_call(run_id, node_id, target, inputs, session_id=None):
    return await current().call(run_id, node_id, target, inputs, session_id)


@DBOS.step()
async def mark_node(run_id, node_id, status, output):
    current().store.step(run_id, node_id, status, output)


@DBOS.step()
async def mark_run(run_id, status, result=None):
    current().store.status(run_id, status, result)


@DBOS.step()
async def cancelled(run_id):
    with current().store.tx() as db:
        return db.get(Run, run_id).cancel_requested


async def reconciled_call(run_id, node_id, target, inputs, session_id=None):
    try:
        return await external_call(run_id, node_id, target, inputs, session_id)
    except adapters.UncertainExecution:
        # No expiry, retry or inferred "probably failed": operator supplies verified outcome.
        while True:
            resolution = await DBOS.recv_async(topic=f"resolve:{node_id}", timeout_seconds=3600)
            if resolution:
                if resolution.get("outcome") != "completed":
                    raise RuntimeError("Operator marked external execution failed")
                return resolution["output"]


async def review_release(run_id: str, node: Node, suffix: str, target: str, values: dict):
    if not node.release:
        return
    with current().store.tx() as db:
        spec = db.get(Run, run_id).spec
    agent = spec["snapshots"][target]["document"]
    service = spec["snapshots"][agent["service_id"]]["document"]
    if service["location"] == "cloud":
        if spec["local_only"]:
            raise HTTPException(403, "Local-only graph cannot release cloud input")
        approval = Node(id=f"{node.id[:32]}-{suffix}", type="approval", release=True)
        with SetWorkflowID(f"{run_id}:{approval.id}"):
            handle = await DBOS.start_workflow_async(run_node, run_id, approval.model_dump(), values)
        await handle.get_result()


@DBOS.workflow()
async def run_node(run_id: str, raw: dict, values: dict):
    node = Node.model_validate(raw)
    if node.type == "approval":
        await mark_node(run_id, node.id, "waiting_approval", {"payload": values, "release": node.release})
        await mark_run(run_id, "waiting_approval")
        while True:
            decision = await DBOS.recv_async(topic=f"approve:{node.id}", timeout_seconds=3600)
            if decision:
                break
        if not decision.get("approved"):
            raise RuntimeError("Approval declined")
        output = {"approved": True, "payload": values}
        if node.release:
            output["release_digest"] = digest(values)
        await mark_node(run_id, node.id, "completed", output)
        await mark_run(run_id, "running")
        return output
    if node.type == "condition":
        output = {"matched": condition(values)}
    elif node.type == "join":
        output = values
    elif node.type == "review":
        session_id = node.session_id
        request = values
        for turn in range(node.max_rounds):
            await review_release(run_id, node, f"release-author-{turn}", node.target, request)
            produced = await reconciled_call(
                run_id, f"{node.id}-author-{turn}", node.target, request, session_id
            )
            session_id = produced.get("session_id", session_id)
            review_input = {
                "instruction": "Review the supplied artifact. Return structured approved:boolean and feedback.",
                "artifact": produced,
                "workspace_files": produced.get("files", []),
            }
            await review_release(run_id, node, f"release-review-{turn}", node.reviewer, review_input)
            verdict = await reconciled_call(run_id, f"{node.id}-review-{turn}", node.reviewer, review_input)
            decision = verdict.get("structured", verdict)
            if not isinstance(decision.get("approved"), bool):
                raise ValueError("Reviewer must return structured approved:boolean; verdict is not inferred")
            if decision["approved"]:
                output = {"artifact": produced, "review": verdict, "rounds": turn + 1}
                break
            request = {**values, "review": decision}
        else:
            raise RuntimeError("Review limit reached; responsible user must decide next action")
    else:
        output = await reconciled_call(run_id, node.id, node.target, values, node.session_id)
    await mark_node(run_id, node.id, "completed", {"value": output})
    return output


@DBOS.workflow()
async def run_graph(run_id: str, spec: dict, inputs: dict, principal: dict):
    outputs = {}
    await mark_run(run_id, "running")
    try:
        for group in layers([Node.model_validate(n) for n in spec["nodes"]]):
            if await cancelled(run_id):
                await mark_run(run_id, "cancelled")
                return
            handles = []
            for node in group:
                if node.when and not condition(resolve(node.when, inputs, outputs)):
                    await mark_node(run_id, node.id, "skipped", {})
                    outputs[node.id] = {"skipped": True}
                    continue
                values = resolve(node.inputs, inputs, outputs)
                with SetWorkflowID(f"{run_id}:{node.id}"):
                    handle = await DBOS.start_workflow_async(run_node, run_id, node.model_dump(), values)
                handles.append((node.id, handle))
            # Children run concurrently; collect every outcome before allowing dependents.
            results = await asyncio.gather(*(h.get_result() for _, h in handles), return_exceptions=True)
            failures = [r for r in results if isinstance(r, BaseException)]
            if failures:
                raise RuntimeError(type(failures[0]).__name__)
            outputs.update({nid: result for (nid, _), result in zip(handles, results)})
        await mark_run(run_id, "cancelled" if await cancelled(run_id) else "completed", outputs)
    except Exception as exc:
        await mark_run(
            run_id,
            "cancelled" if await cancelled(run_id) else "failed",
            {"error_type": type(exc).__name__, "completed_nodes": list(outputs)},
        )
