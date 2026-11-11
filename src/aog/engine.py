import asyncio
import hashlib
import json
import uuid
from types import SimpleNamespace

from dbos import DBOS, SetWorkflowID
from fastapi import HTTPException
from jsonschema import validate
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from aog import adapters
from aog.backends import get_backend
from aog.graph import condition, layers, resolve
from aog.lifecycle import Lifecycle
from aog.models import Agent, AgentProfile, Invocation, Node, Principal, Service, Workflow
from aog.security import authorize
from aog.store import Lease, Run, Step, Store


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Engine:
    def __init__(self, store: Store, permission_check=authorize, caller=None, lifecycle=None, catalog=None, backend=None):
        self.store = store
        self.catalog = catalog
        self.backend = backend or get_backend()
        self.authorize = permission_check
        self.caller = caller
        self.lifecycle = lifecycle or Lifecycle(store)

    async def resource(self, rid, actor, kind=None, write=False):
        if kind == "service" and self.catalog:
            return await self.catalog.resource(rid, actor, write=write)
        return self.store.resource(rid, actor, kind, write=write)

    async def agent_definition(self, row, actor):
        if row.document.get("schema_version", 1) == 1:
            return Agent.model_validate(row.document)
        profile = AgentProfile.model_validate(row.document)
        if not self.catalog:
            raise HTTPException(503, "Native gateway catalog is required for schema 2 profiles")
        services = await self.catalog.list_resources(actor)
        workers = [s for s in services if s.document.get("transport") == "acp"
            and self.store.runtime_id(s.id) == profile.runtime_id]
        if len(workers) != 1:
            raise HTTPException(409, "Select a runtime with exactly one registered ACP worker tool")
        return Agent(name=profile.name, project_id=profile.project_id, visibility=profile.visibility,
            service_id=workers[0].id, persona=profile.persona, model=profile.model,
            allowed_service_ids=profile.allowed_tool_ids, max_tool_calls=profile.max_tool_calls)

    async def submit(self, kind: str, rid: str, request: Invocation, actor: Principal):
        actor = await self.authorize(actor)
        if actor.public_only:
            raise HTTPException(403, "Private jobs require a personal or project token scope")
        if actor.agent_id:
            if kind != "service" or rid not in (actor.allowed_service_ids or []):
                raise HTTPException(403, "Service is outside the agent grant")
            parent = self.store.run(actor.parent_run_id, actor)
            request.local_only = request.local_only or parent.spec["local_only"]
            if request.allow_cloud and digest(request.inputs) not in parent.spec["released"]:
                raise HTTPException(403, "Agent cannot release new cloud inputs")
        resource = await self.resource(rid, actor, kind)
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
                    row = await self.resource(target, actor, target_kind)
                    snapshots[target] = {"kind": row.kind, "revision": row.revision, "document": row.document}
                    if row.kind == "agent":
                        agent = await self.agent_definition(row, actor)
                        srv = await self.resource(agent.service_id, actor, "service")
                        snapshots[target]["resolved_service_id"] = srv.id
                        snapshots[srv.id] = {
                            "kind": "service",
                            "revision": srv.revision,
                            "document": srv.document,
                        }
                        if row.document.get("schema_version") == 2:
                            for field, kind in (("runtime_id", "runtime"),
                                ("provider_connection_id", "provider"), ("native_login_ref", "credential")):
                                reference = row.document.get(field)
                                if reference:
                                    linked = self.store.resource(reference, actor, kind)
                                    snapshots[reference] = {"kind": kind, "revision": linked.revision,
                                        "document": linked.document}
            if node.session_id:
                session = await self.resource(node.session_id, actor, "session", write=True)
                if session.owner != actor.subject or session.document["agent_id"] != node.target:
                    raise HTTPException(403, "Session belongs to another actor or agent")
        spec["catalog_version"] = 2 if self.catalog else 1
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
                    if parent.status not in {"running", "waiting_approval", "waiting_agent_approval"}:
                        raise HTTPException(403, "Parent agent run is no longer active")
                    count = db.scalar(
                        select(func.count())
                        .select_from(Run)
                        .where(
                            Run.principal["parent_run_id"].as_string() == parent.id,
                            Run.principal["agent_id"].as_string() == actor.agent_id,
                        )
                    )
                    profile = await self.resource(actor.agent_id, actor, "agent")
                    limit = profile.document.get("max_tool_calls", 20)
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
        row = await self.resource(target, actor, snap["kind"])
        if row.revision != snap["revision"]:
            raise HTTPException(409, "Definition changed after submission; start a new run")
        row = SimpleNamespace(**{**vars(row), "document": snap["document"]})
        agent = await self.agent_definition(row, actor) if row.kind == "agent" else None
        service_id = agent.service_id if agent else target
        current = await self.resource(service_id, actor, "service")
        if not current.document.get("enabled", True):
            raise HTTPException(403, "Service disabled")
        if current.revision != run.spec["snapshots"][service_id]["revision"]:
            raise HTTPException(409, "Service changed after submission; start a new run")
        service = Service.model_validate(run.spec["snapshots"][service_id]["document"])
        service_id = current.id  # Legacy aliases keep snapshot keys, never execute as native tool IDs.
        profile = AgentProfile.model_validate(row.document) if agent and row.document.get("schema_version") == 2 else None
        provider = None
        login = None
        if profile:
            reference = profile.provider_connection_id or profile.native_login_ref
            linked = self.store.resource(reference, actor, "provider" if profile.provider_connection_id else "credential")
            if linked.revision != run.spec["snapshots"][reference]["revision"] or linked.owner != actor.subject:
                raise HTTPException(409, "Authentication profile changed after submission")
            if profile.provider_connection_id:
                provider = linked.document
                service = service.model_copy(update={"location": "cloud" if service.location == "cloud"
                    or provider.get("location", "cloud") == "cloud" else "local"})
            else:
                login = linked.document
                if login.get("kind") != "native_login":
                    raise HTTPException(422, "Select an enrolled native login reference")
                service = service.model_copy(update={"location": "cloud" if service.location == "cloud"
                    or login.get("location", "cloud") == "cloud" else "local"})
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
        if provider:
            credential_ref = provider["credential_ref"]
        elif login:
            credential_ref = None  # Native subscription login must not inherit a worker's legacy API key.
        if credential_ref:
            secret = await self.resource(credential_ref, actor, "credential")
            if secret.owner != actor.subject:
                raise HTTPException(403, "Personal credential belongs to another user")
            credential = (await self.backend.decrypt_secret(secret.document["encrypted"]))["token"]
        session = None
        session_locked = False
        try:
            if agent:
                if agent.allowed_service_ids and not self.catalog:
                    raise HTTPException(503, "Scoped MCP delegation is not configured")
                if session_id:
                    session = await self.resource(session_id, actor, "session", write=True)
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
                        db.add(Lease(id=f"session:{session.id}", service_id=self.store.runtime_id(service_id), run_id=run_id))
                except IntegrityError:
                    raise HTTPException(409, "Session is already in use") from None
                session_locked = True
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
                if profile:
                    inputs["harness_key"] = profile.harness_key
                    if provider:
                        inputs["provider_connection"] = {k: provider[k] for k in ("protocol", "base_url", "model") if k in provider}
                    if login:
                        inputs["native_login_ref"] = login["native_profile_ref"]
                if agent.allowed_service_ids and self.catalog:
                    inputs.update(await self.backend.delegate(agent_id=target,
                        allowed_tool_ids=agent.allowed_service_ids, run_id=run_id, actor=actor,
                        timeout_seconds=service.timeout_seconds))
            self.store.step(run_id, node_id, "starting")
            if self.catalog:
                async def validate_locked_definition():
                    await self.authorize(actor)
                    locked = await self.resource(service_id, actor, "service")
                    if locked.revision != current.revision:
                        raise HTTPException(409, "Definition changed while waiting for its runtime; start a new run")

                await self.lifecycle.acquire(service_id, service, key, run_id,
                    validate=validate_locked_definition)
            else:
                await self.lifecycle.acquire(service_id, service, key, run_id)
        except BaseException:
            self.store.release(key)
            if session_locked:
                self.store.release(f"session:{session.id}")
            raise
        self.store.step(run_id, node_id, "running", {"session_id": session.id if session else None})
        try:
            if self.catalog:
                value = await self.catalog.invoke(service_id, service, inputs, key,
                    {**actor.model_dump(), "project_id": current.project_id}, credential)
            else:
                value = await self.caller(service, inputs, key,
                    {**actor.model_dump(), "project_id": current.project_id}, credential)
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
            previous = self.store.step(run_id, node_id)
            self.store.step(
                run_id,
                node_id,
                "needs_attention",
                {**(previous.output if previous else {}), "error_type": type(exc).__name__,
                 "session_id": session.id if session else None},
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
    snapshots = spec["snapshots"]
    snapshot = snapshots[target]
    agent = snapshot["document"]
    service_id = agent.get("service_id")
    authentication = None
    if agent.get("schema_version", 1) == 2:
        profile = AgentProfile.model_validate(agent)
        service_id = snapshot.get("resolved_service_id")
        reference = profile.provider_connection_id or profile.native_login_ref
        linked = snapshots.get(reference)
        if not linked:
            raise HTTPException(409, "Authentication snapshot is missing; start a new run")
        authentication = linked["document"]
    if not service_id or service_id not in snapshots:
        raise HTTPException(409, "Resolved worker snapshot is missing; start a new run")
    service = snapshots[service_id]["document"]
    location = service["location"]
    if authentication and authentication.get("location", "cloud") == "cloud":
        location = "cloud"
    if location == "cloud":
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
