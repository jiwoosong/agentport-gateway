import base64
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Literal

from dbos import DBOS
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from aog.engine import current, digest
from aog.models import Agent, AgentProfile, Decision, Invocation, Principal, Resolution, Service, Workflow
from aog.security import authenticate, authorize, require_admin
from aog.store import Run, Step

router = APIRouter(prefix="/orchestration/v1", tags=["AgentPort"])
KINDS = {"services": ("service", Service), "agents": ("agent", Agent), "workflows": ("workflow", Workflow)}


async def reader(request: Request):
    actor = await authorize(await authenticate(request), "tools.read")
    if actor.agent_id:
        raise HTTPException(403, "Agent grants are restricted to the MCP endpoint")
    return actor


async def operator(request: Request):
    actor = await authorize(await authenticate(request), "tools.execute")
    if actor.agent_id:
        raise HTTPException(403, "Agent grants are restricted to the MCP endpoint")
    return actor


def resource_view(row):
    return {"id": row.id, "kind": row.kind, "owner": row.owner, "revision": row.revision, **row.document}


def run_view(run):
    return {
        "id": run.id,
        "status": run.status,
        "created_at": run.created_at,
        "name": run.spec["name"],
        "result": run.result,
        "definition_revision": run.spec["definition_revision"],
    }


@router.get("/me")
async def me(actor: Principal = Depends(reader)):
    controls = {} if actor.public_only else await current().backend.controls(actor, current().store)
    return {**actor.model_dump(), "mcp_tools": controls, "mcp_url": "/mcp"}


@router.get("/worker-public-key")
async def worker_public_key(actor: Principal = Depends(reader)):
    require_admin(actor)
    return await current().backend.worker_public_key()


@router.get("/runs")
async def runs(actor: Principal = Depends(reader)):
    if actor.public_only:
        return []
    with current().store.tx() as db:
        query = select(Run).order_by(Run.created_at.desc()).limit(100)
        if not actor.is_admin:
            query = query.where(Run.owner == actor.subject)
        return [run_view(r) for r in db.scalars(query)]


@router.get("/runs/{run_id}")
async def run_detail(run_id: str, actor: Principal = Depends(reader)):
    run = current().store.run(run_id, actor)
    with current().store.tx() as db:
        steps = [
            {"id": s.node_id, "status": s.status, "output": s.output}
            for s in db.scalars(select(Step).where(Step.run_id == run_id))
        ]
    permissions = [permission for step in steps
        for permission in step["output"].get("worker", {}).get("permissions", [])]
    permissions = [{**permission, "options": [
        {"option_id": option.get("optionId", option.get("option_id")), "name": option["name"]}
        for option in permission.get("options", [])]} for permission in permissions]
    return {**run_view(run), "steps": steps, "permissions": permissions}


class NativePermission(BaseModel):
    option_id: str


async def native_permission(run_id: str, permission_id: str, value: NativePermission,
    actor: Principal = Depends(operator)):
    run = current().store.run(run_id, actor)
    with current().store.tx() as db:
        steps = list(db.scalars(select(Step).where(Step.run_id == run_id)))
    for step in steps:
        worker = step.output.get("worker", {})
        if any(p["id"] == permission_id for p in worker.get("permissions", [])):
            service_row = await current().resource(step.output["service_id"], actor, "service")
            service = Service.model_validate(service_row.document)
            response = await current().catalog.worker_control(service_row.id, service,
                step.id, {**run.principal, "project_id": service_row.project_id},
                {"operation": "approve", "job_id": worker["job_id"],
                    "permission_id": permission_id, "option_id": value.option_id})
            current().store.audit(actor.subject, "agent.permission", step.id,
                {"permission_id": permission_id, "option_id": value.option_id})
            return response
    raise HTTPException(409, "The native permission request is no longer pending")


async def cancel(run_id: str, actor: Principal = Depends(operator)):
    run = current().store.run(run_id, actor)
    if run.status in {"completed", "cancelled", "failed"}:
        raise HTTPException(409, "Run is already terminal")
    current().store.status(run_id, "cancel_requested")
    with current().store.tx() as db:
        waiting = list(
            db.scalars(select(Step).where(Step.run_id == run_id, Step.status == "waiting_approval"))
        )
    for node in waiting:
        await DBOS.send_async(
            f"{run_id}:{node.node_id}",
            {"approved": False},
            topic=f"approve:{node.node_id}",
            idempotency_key=f"cancel:{run_id}:{node.node_id}",
        )
    current().store.audit(actor.subject, "run.cancel", run_id)
    return {
        "status": "cancel_requested",
        "message": "Active external calls are reconciled before releasing resources",
    }


async def approve(run_id: str, node_id: str, decision: Decision, actor: Principal = Depends(operator)):
    current().store.run(run_id, actor)
    step = current().store.step(run_id, node_id)
    if not step or step.status != "waiting_approval":
        raise HTTPException(409, "Step is not waiting for approval")
    current().store.audit(
        actor.subject,
        "step.approve",
        step.id,
        {"approved": decision.approved, "payload_hash": digest(step.output["payload"])},
    )
    await DBOS.send_async(
        f"{run_id}:{node_id}",
        decision.model_dump(),
        topic=f"approve:{node_id}",
        idempotency_key=f"decision:{run_id}:{node_id}",
    )
    return {"accepted": True}


async def resolve_step(
    run_id: str, node_id: str, resolution: Resolution, actor: Principal = Depends(operator)
):
    run = current().store.run(run_id, actor)
    require_admin(actor)  # Releasing a possibly active external job is an operational decision.
    step = current().store.step(run_id, node_id)
    if not step or step.status not in {"running", "needs_attention"}:
        raise HTTPException(409, "Step does not need reconciliation")
    parents = [
        n["id"]
        for n in run.spec["nodes"]
        if node_id == n["id"]
        or node_id.startswith(n["id"] + "-author-")
        or node_id.startswith(n["id"] + "-review-")
    ]
    parent = max(parents, key=len)
    worker = step.output.get("worker", {})
    if worker.get("job_id") and current().catalog:
        service_row = await current().resource(step.output["service_id"], actor, "service")
        service = Service.model_validate(service_row.document)
        # The worker has a separate restart/session guard. Do not clear only the
        # central lease while the native job remains unresolved.
        worker_actor = {**run.principal, "project_id": service_row.project_id}
        state = await current().catalog.worker_control(service_row.id, service, step.id,
            worker_actor, {"operation": "status", "job_id": worker["job_id"]})
        if state.get("status") not in {"completed", "cancelled", "failed"}:
            await current().catalog.worker_control(service_row.id, service, step.id,
                worker_actor,
                {"operation": "resolve", "job_id": worker["job_id"], **resolution.model_dump()},
                operator=True)
    current().store.release(step.id)
    if step.output.get("session_id"):
        current().store.release(f"session:{step.output['session_id']}")
    current().store.step(run_id, node_id, resolution.outcome, {"value": resolution.output})
    current().store.audit(actor.subject, "step.resolve", step.id, {"outcome": resolution.outcome})
    await DBOS.send_async(
        f"{run_id}:{parent}",
        resolution.model_dump(),
        topic=f"resolve:{node_id}",
        idempotency_key=f"resolution:{run_id}:{node_id}",
    )
    return {"accepted": True}


@router.post("/runs/{run_id}/permissions/{permission_id}")
async def native_permission_route(run_id: str, permission_id: str, value: NativePermission,
    actor: Principal = Depends(operator)):
    if current().catalog:
        return await current().catalog.control("agent_permission", {"run_id": run_id,
            "permission_id": permission_id, "option_id": value.option_id}, actor)
    return await native_permission(run_id, permission_id, value, actor)


@router.post("/runs/{run_id}/cancel")
async def cancel_route(run_id: str, actor: Principal = Depends(operator)):
    if current().catalog:
        return await current().catalog.control("cancel", {"run_id": run_id}, actor)
    return await cancel(run_id, actor)


@router.post("/runs/{run_id}/steps/{node_id}/approve")
async def approve_route(run_id: str, node_id: str, decision: Decision, actor: Principal = Depends(operator)):
    if current().catalog:
        return await current().catalog.control("approve", {"run_id": run_id,
            "node_id": node_id, "approved": decision.approved}, actor)
    return await approve(run_id, node_id, decision, actor)


@router.post("/runs/{run_id}/steps/{node_id}/resolve")
async def resolve_step_route(run_id: str, node_id: str, resolution: Resolution,
    actor: Principal = Depends(operator)):
    require_admin(actor)
    if current().catalog:
        return await current().catalog.control("resolve", {"run_id": run_id,
            "node_id": node_id, **resolution.model_dump()}, actor)
    return await resolve_step(run_id, node_id, resolution, actor)


@router.get("/sessions")
async def sessions(actor: Principal = Depends(reader)):
    return [
        resource_view(r) for r in current().store.list_resources(actor, "session") if r.owner == actor.subject
    ]


class SecretInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    token: str = Field(min_length=1, max_length=16000)


@router.get("/credentials")
async def credentials(actor: Principal = Depends(reader)):
    return [
        {"id": r.id, "name": r.document["name"], "kind": r.document.get("kind", "api_key")}
        for r in current().store.list_resources(actor, "credential")
        if r.owner == actor.subject
    ]


@router.post("/credentials", status_code=201)
async def credential(value: SecretInput, actor: Principal = Depends(operator)):
    row = current().store.save(
        "credential", {"name": value.name, "kind": "api_key",
            "encrypted": await current().backend.encrypt_secret({"token": value.token})}, actor
    )
    return {"id": row.id, "name": value.name}  # Never expose ciphertext or plaintext.


class Upload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(max_length=700000)


@router.post("/artifacts", status_code=201)
async def upload(value: Upload, actor: Principal = Depends(operator)):
    try:
        data = base64.b64decode(value.content_base64, validate=True)
    except ValueError:
        raise HTTPException(422, "Invalid base64") from None
    root = Path(os.environ.get("AOG_ARTIFACT_ROOT", "data/artifacts"))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = uuid.uuid4().hex
    path = root / key
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
        output.write(data)
    row = current().store.save(
        "artifact",
        {
            "name": Path(value.name).name,
            "key": key,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        },
        actor,
    )
    return {"id": row.id, "name": row.document["name"], "size": len(data)}


@router.get("/artifacts/{artifact_id}")
async def artifact(artifact_id: str, actor: Principal = Depends(reader)):
    row = await current().resource(artifact_id, actor, "artifact")
    path = Path(os.environ.get("AOG_ARTIFACT_ROOT", "data/artifacts")) / row.document["key"]
    return {"name": row.document["name"], "content_base64": base64.b64encode(path.read_bytes()).decode()}


@router.get("/{collection}")
async def resources(
    collection: Literal["services", "agents", "workflows"], actor: Principal = Depends(reader)
):
    rows = (await current().catalog.list_resources(actor) if collection == "services" and current().catalog
        else current().store.list_resources(actor, KINDS[collection][0]))
    result = []
    for row in rows:
        item = resource_view(row)
        if collection == "services":
            item["runtime_phase"] = current().store.phase(row.id)
        result.append(item)
    return result


async def save_resource(collection, document, actor, rid=None):
    kind, model = KINDS[collection]
    if kind == "service" and current().catalog:
        try:
            return resource_view(await current().catalog.save(document, actor, rid))
        except ValidationError as exc:
            raise HTTPException(422, json.loads(exc.json(include_input=False))) from None
    if kind == "agent" and document.get("schema_version") == 2:
        model = AgentProfile
    if kind == "service":
        require_admin(actor)
    try:
        parsed = model.model_validate(document)
    except ValidationError as exc:
        raise HTTPException(422, json.loads(exc.json(include_input=False))) from None
    if kind == "agent" and parsed.schema_version == 1:
        srv = await current().resource(parsed.service_id, actor, "service")
        if srv.document["kind"] != "agent":
            raise HTTPException(422, "Agent requires an agent service")
        if parsed.project_id is None:
            parsed.project_id = srv.project_id
        if parsed.project_id != srv.project_id:
            raise HTTPException(422, "Agent project must match its worker service")
        for service_id in parsed.allowed_service_ids:
            await current().resource(service_id, actor, "service")
        if parsed.credential_ref:
            secret = await current().resource(parsed.credential_ref, actor, "credential")
            if secret.owner != actor.subject:
                raise HTTPException(403, "Personal credential belongs to another user")
    if kind == "agent" and parsed.schema_version == 2:
        runtime = current().store.resource(parsed.runtime_id, actor, "runtime")
        if parsed.project_id is None:
            parsed.project_id = runtime.project_id
        if parsed.project_id != runtime.project_id:
            raise HTTPException(422, "Agent project must match its runtime")
        for tool_id in parsed.allowed_tool_ids:
            tool = await current().resource(tool_id, actor, "service")
            if tool.project_id and tool.project_id != parsed.project_id:
                raise HTTPException(422, "Agent tools must belong to its project")
    if rid and current().catalog and kind in {"agent", "workflow"}:
        previous = current().store.resource(rid, actor, kind, write=True)
        if previous.project_id != parsed.project_id:
            raise HTTPException(409, "Create a new resource to change its native gateway team")
    row = current().store.save(kind, parsed.model_dump(), actor, rid)
    if current().catalog and kind in {"agent", "workflow"}:
        await current().catalog.publish_resource(row, actor)
    return resource_view(row)


@router.post("/{collection}", status_code=201)
async def create_resource(
    collection: Literal["services", "agents", "workflows"],
    document: dict,
    actor: Principal = Depends(operator),
):
    return await save_resource(collection, document, actor)


@router.put("/{collection}/{rid}")
async def update_resource(
    collection: Literal["services", "agents", "workflows"],
    rid: str,
    document: dict,
    actor: Principal = Depends(operator),
):
    return await save_resource(collection, document, actor, rid)


@router.get("/{collection}/{rid}/export")
async def export_resource(
    collection: Literal["services", "agents", "workflows"],
    rid: str,
    format: Literal["json", "yaml"] = "json",
    actor: Principal = Depends(reader),
):
    row = await current().resource(rid, actor, KINDS[collection][0])
    value = {"kind": row.kind, "revision": row.revision, "definition": row.document}
    if row.kind == "service" and current().catalog:
        value["definition"] = {**row.document, "schema_version": 2,
            "runtime_id": current().store.runtime_id(row.id)}
    if format == "yaml":
        import yaml

        return Response(
            yaml.safe_dump(value, allow_unicode=True, sort_keys=False), media_type="application/yaml"
        )
    return value


@router.post("/{collection}/{rid}/invoke", status_code=202)
async def invoke_resource(
    collection: Literal["services", "agents", "workflows"],
    rid: str,
    request: Invocation,
    actor: Principal = Depends(operator),
):
    application = current()
    submit = application.catalog.submit if application.catalog else application.submit
    return run_view(await submit(KINDS[collection][0], rid, request, actor))


@router.post("/services/{rid}/operations/{operation}")
async def operation(
    rid: str, operation: Literal["start", "stop", "update"], actor: Principal = Depends(operator)
):
    from aog.management import runtime_operation
    row = await current().resource(rid, actor, "service")
    return await runtime_operation(current().store.runtime_id(row.id), operation, actor)


@router.post("/services/{rid}/releases/{release}/deploy")
async def deploy_release(rid: str, release: str, actor: Principal = Depends(operator)):
    from aog.management import deploy_runtime_release
    row = await current().resource(rid, actor, "service")
    return await deploy_runtime_release(current().store.runtime_id(row.id), release, actor)


class RuntimeResolution(BaseModel):
    phase: Literal["running", "stopped"]
    deployment_verified: Literal[True]


@router.post("/services/{rid}/reconcile")
async def reconcile_runtime(rid: str, resolution: RuntimeResolution, actor: Principal = Depends(operator)):
    from aog.management import RuntimeResolution as TargetResolution
    from aog.management import reconcile_runtime as reconcile_target
    row = await current().resource(rid, actor, "service")
    return await reconcile_target(current().store.runtime_id(row.id),
        TargetResolution(**resolution.model_dump()), actor)


@router.get("/services/{rid}/runtime")
async def runtime_detail(rid: str, actor: Principal = Depends(reader)):
    require_admin(actor)
    row = await current().resource(rid, actor, "service")
    service = Service.model_validate(row.document)
    if not service.openshell:
        return {"backend": "komodo" if service.stack_id else "external", "phase": current().store.phase(rid)}
    try:
        return await current().lifecycle.control(service, "status")
    except Exception:
        raise HTTPException(502, "OpenShell status unavailable or sandbox identity changed") from None
