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
from sqlalchemy import select, update

from aog.engine import current, digest
from aog.models import Agent, Decision, Invocation, Principal, Resolution, Service, Workflow
from aog.security import authenticate, authorize, require_admin
from aog.store import Lease, Run, Runtime, Step

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
    return actor


@router.get("/worker-public-key")
async def worker_public_key(actor: Principal = Depends(reader)):
    require_admin(actor)
    from aog.identity import public_key_base64

    return {"algorithm": "EdDSA", "public_key": public_key_base64()}


@router.get("/runs")
async def runs(actor: Principal = Depends(reader)):
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
    return {**run_view(run), "steps": steps}


@router.post("/runs/{run_id}/cancel")
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


@router.post("/runs/{run_id}/steps/{node_id}/approve")
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


@router.post("/runs/{run_id}/steps/{node_id}/resolve")
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
        {"id": r.id, "name": r.document["name"]}
        for r in current().store.list_resources(actor, "credential")
        if r.owner == actor.subject
    ]


@router.post("/credentials", status_code=201)
async def credential(value: SecretInput, actor: Principal = Depends(operator)):
    from mcpgateway.utils.services_auth import encode_auth

    row = current().store.save(
        "credential", {"name": value.name, "encrypted": encode_auth({"token": value.token})}, actor
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
    row = current().store.resource(artifact_id, actor, "artifact")
    path = Path(os.environ.get("AOG_ARTIFACT_ROOT", "data/artifacts")) / row.document["key"]
    return {"name": row.document["name"], "content_base64": base64.b64encode(path.read_bytes()).decode()}


@router.get("/{collection}")
async def resources(
    collection: Literal["services", "agents", "workflows"], actor: Principal = Depends(reader)
):
    rows = current().store.list_resources(actor, KINDS[collection][0])
    result = []
    for row in rows:
        item = resource_view(row)
        if collection == "services":
            item["runtime_phase"] = current().store.phase(row.id)
        result.append(item)
    return result


async def save_resource(collection, document, actor, rid=None):
    kind, model = KINDS[collection]
    if kind == "service":
        require_admin(actor)
    try:
        parsed = model.model_validate(document)
    except ValidationError as exc:
        raise HTTPException(422, json.loads(exc.json(include_input=False))) from None
    if kind == "agent":
        srv = current().store.resource(parsed.service_id, actor, "service")
        if srv.document["kind"] != "agent":
            raise HTTPException(422, "Agent requires an agent service")
        if parsed.project_id is None:
            parsed.project_id = srv.project_id
        if parsed.project_id != srv.project_id:
            raise HTTPException(422, "Agent project must match its worker service")
        for service_id in parsed.allowed_service_ids:
            current().store.resource(service_id, actor, "service")
        if parsed.credential_ref:
            secret = current().store.resource(parsed.credential_ref, actor, "credential")
            if secret.owner != actor.subject:
                raise HTTPException(403, "Personal credential belongs to another user")
    return resource_view(current().store.save(kind, parsed.model_dump(), actor, rid))


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
    row = current().store.resource(rid, actor, KINDS[collection][0])
    value = {"kind": row.kind, "revision": row.revision, "definition": row.document}
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
    return run_view(await current().submit(KINDS[collection][0], rid, request, actor))


@router.post("/services/{rid}/operations/{operation}")
async def operation(
    rid: str, operation: Literal["start", "stop", "update"], actor: Principal = Depends(operator)
):
    require_admin(actor)
    row = current().store.resource(rid, actor, "service")
    service = Service.model_validate(row.document)
    if not service.stack_id:
        raise HTTPException(409, "Service has no managed deployment")
    if not current().store.claim_stop(rid, 0, phase="updating"):
        raise HTTPException(409, "Service is busy; wait for active jobs to drain")
    current().store.audit(actor.subject, f"service.{operation}", rid)
    try:
        backend = current().lifecycle.backend
        if operation == "stop":
            await backend.stop(service.stack_id)
        elif operation == "start":
            await backend.start(service.stack_id)
        else:
            await backend.update(service.stack_id)
        if operation != "stop" and service.health_url:
            await current().lifecycle.health(service.health_url, service.timeout_seconds)
        current().store.phase(rid, "stopped" if operation == "stop" else "running")
    except Exception:
        current().store.phase(rid, "error")
        raise HTTPException(502, "Deployment needs operator attention; inspect Komodo") from None
    return {"phase": current().store.phase(rid)}


@router.post("/services/{rid}/releases/{release}/deploy")
async def deploy_release(rid: str, release: str, actor: Principal = Depends(operator)):
    require_admin(actor)
    row = current().store.resource(rid, actor, "service")
    return await current().lifecycle.deploy_release(row, release, actor)


class RuntimeResolution(BaseModel):
    phase: Literal["running", "stopped"]
    deployment_verified: Literal[True]


@router.post("/services/{rid}/reconcile")
async def reconcile_runtime(rid: str, resolution: RuntimeResolution, actor: Principal = Depends(operator)):
    require_admin(actor)
    row = current().store.resource(rid, actor, "service")
    if resolution.phase == "running":
        service = Service.model_validate(row.document)
        if not service.health_url:
            raise HTTPException(422, "A readiness URL is required to reconcile a running service")
        await current().lifecycle.health(service.health_url, service.timeout_seconds)
    with current().store.tx() as db:
        db.execute(update(Runtime).where(Runtime.service_id == rid).values(last_used=Runtime.last_used))
        if db.scalar(select(Lease.id).where(Lease.service_id == rid).limit(1)):
            raise HTTPException(409, "Reconcile active executions before changing the runtime")
        db.get(Runtime, rid).phase = resolution.phase
    current().store.audit(actor.subject, "service.reconcile", rid, resolution.model_dump())
    return {"phase": resolution.phase}
