"""Configuration and operations for extension-owned execution resources."""

from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update

from aog.api import operator, reader, resource_view
from aog.engine import current
from aog.models import Principal, ProviderConnection, RuntimeDefinition
from aog.security import require_admin
from aog.store import Lease, Resource, Runtime

router = APIRouter(tags=["AgentPort management"])


def runtime_view(row: Resource, actor: Principal):
    definition = RuntimeDefinition.model_validate(row.document)
    store = current().store
    with store.tx() as db:
        active = db.scalar(select(func.count(func.distinct(Lease.run_id))).where(Lease.service_id == row.id))
    managed = definition.backend != "external" and actor.is_admin
    return {
        **resource_view(row),
        "runtime_phase": store.phase(row.id),
        "active_jobs": active,
        "capabilities": {
            "start": managed,
            "stop": managed,
            "update": managed and definition.backend == "komodo",
            "reconcile": actor.is_admin,
        },
    }


@router.get("/runtimes")
async def runtimes(actor: Principal = Depends(reader)):
    return [runtime_view(row, actor) for row in current().store.list_resources(actor, "runtime")]


@router.get("/runtimes/{rid}")
async def runtime(rid: str, actor: Principal = Depends(reader)):
    return runtime_view(current().store.resource(rid, actor, "runtime"), actor)


@router.post("/runtimes", status_code=201)
async def create_runtime(definition: RuntimeDefinition, actor: Principal = Depends(operator)):
    require_admin(actor)
    return runtime_view(current().store.register_runtime(definition, actor), actor)


@router.put("/runtimes/{rid}")
async def update_runtime(rid: str, definition: RuntimeDefinition, actor: Principal = Depends(operator)):
    require_admin(actor)
    return runtime_view(current().store.register_runtime(definition, actor, rid), actor)


@router.get("/runtimes/{rid}/export")
async def export_runtime(rid: str, actor: Principal = Depends(reader)):
    row = current().store.resource(rid, actor, "runtime")
    return {"kind": row.kind, "revision": row.revision, "definition": row.document}


@router.post("/runtimes/{rid}/operations/{operation}")
async def runtime_operation(
    rid: str, operation: Literal["start", "stop", "update"], actor: Principal = Depends(operator)
):
    require_admin(actor)
    engine = current()
    row = engine.store.resource(rid, actor, "runtime")
    definition = RuntimeDefinition.model_validate(row.document)
    if definition.backend == "external":
        raise HTTPException(409, "This runtime is started and stopped by its operator")
    if definition.backend == "openshell" and operation == "update":
        raise HTTPException(409, "Provision and verify a replacement OpenShell sandbox before rebinding")
    if operation != "stop" and definition.backend == "komodo" and not definition.health_url:
        raise HTTPException(422, "Register a readiness URL before starting or updating this runtime")
    phase = engine.store.phase(rid)
    if operation == "start":
        if phase == "running":
            return {"phase": phase}
        claimed = engine.store.claim_start(rid)
    else:
        claimed = engine.store.claim_stop(rid, 0, "stopping" if operation == "stop" else "updating")
    if not claimed:
        raise HTTPException(409, "Runtime is busy; reconcile operations and drain active jobs first")
    if operation == "stop" and phase == "stopped":
        engine.store.phase(rid, "stopped")
        return {"phase": "stopped"}
    engine.store.audit(actor.subject, f"runtime.{operation}.begin", rid)
    try:
        await engine.lifecycle.control(definition, operation)
        if operation != "stop" and definition.health_url:
            await engine.lifecycle.health(definition.health_url, definition.timeout_seconds)
        engine.store.phase(rid, "stopped" if operation == "stop" else "running")
    except Exception:
        engine.store.phase(rid, "error")
        engine.store.audit(actor.subject, f"runtime.{operation}.uncertain", rid)
        raise HTTPException(502, "Verify the external deployment before reconciling this runtime") from None
    engine.store.audit(actor.subject, f"runtime.{operation}.completed", rid)
    return {"phase": engine.store.phase(rid)}


@router.post("/runtimes/{rid}/releases/{release}/deploy")
async def deploy_runtime_release(rid: str, release: str, actor: Principal = Depends(operator)):
    require_admin(actor)
    row = current().store.resource(rid, actor, "runtime")
    return await current().lifecycle.deploy_release(row, release, actor)


class RuntimeResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: Literal["running", "stopped"]
    deployment_verified: Literal[True]


@router.post("/runtimes/{rid}/reconcile")
async def reconcile_runtime(rid: str, resolution: RuntimeResolution, actor: Principal = Depends(operator)):
    require_admin(actor)
    engine = current()
    row = engine.store.resource(rid, actor, "runtime")
    definition = RuntimeDefinition.model_validate(row.document)
    # Serialize against acquisitions before issuing any external readiness/status request.
    with engine.store.tx() as db:
        db.execute(update(Runtime).where(Runtime.service_id == rid).values(last_used=Runtime.last_used))
        if db.scalar(select(Lease.id).where(Lease.service_id == rid).limit(1)):
            raise HTTPException(409, "Reconcile active executions before changing the runtime")
        db.get(Runtime, rid).phase = "updating"
    try:
        if definition.backend == "openshell":
            remote = await engine.lifecycle.control(definition, "status")
            expected = "SANDBOX_PHASE_READY" if resolution.phase == "running" else "SANDBOX_PHASE_STOPPED"
            if remote["phase"] != expected:
                raise HTTPException(409, "OpenShell state does not match the reconciliation")
        if resolution.phase == "running":
            if not definition.health_url:
                raise HTTPException(422, "Register a readiness URL before reconciling a running runtime")
            await engine.lifecycle.health(definition.health_url, definition.timeout_seconds)
        engine.store.phase(rid, resolution.phase)
    except HTTPException:
        engine.store.phase(rid, "error")
        raise
    except Exception:
        engine.store.phase(rid, "error")
        raise HTTPException(502, "External runtime verification failed") from None
    engine.store.audit(actor.subject, "runtime.reconcile", rid, resolution.model_dump())
    return {"phase": resolution.phase}


@router.get("/providers")
async def providers(actor: Principal = Depends(reader)):
    return [resource_view(row) for row in current().store.list_resources(actor, "provider")
            if row.owner == actor.subject]


@router.get("/providers/{rid}")
async def provider(rid: str, actor: Principal = Depends(reader)):
    return resource_view(current().store.resource(rid, actor, "provider"))


def save_provider(definition: ProviderConnection, actor: Principal, rid: str | None = None):
    if definition.location == "local":
        require_admin(actor)
    if urlsplit(definition.base_url).query:
        raise HTTPException(422, "Provider base URLs cannot contain query parameters")
    credential = current().store.resource(definition.credential_ref, actor, "credential")
    if credential.document.get("kind") == "native_login":
        raise HTTPException(422, "Native logins are not provider API credentials")
    return resource_view(current().store.save("provider", definition.model_dump(), actor, rid))


@router.post("/providers", status_code=201)
async def create_provider(definition: ProviderConnection, actor: Principal = Depends(operator)):
    return save_provider(definition, actor)


@router.put("/providers/{rid}")
async def update_provider(rid: str, definition: ProviderConnection, actor: Principal = Depends(operator)):
    return save_provider(definition, actor, rid)


class NativeLoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    profile_ref: str = Field(pattern=r"^[A-Za-z0-9_-]{1,120}$")
    location: Literal["local", "cloud"] = "cloud"


@router.post("/credentials/native", status_code=201)
async def enroll_native_login(value: NativeLoginInput, actor: Principal = Depends(operator)):
    if value.location == "local":
        require_admin(actor)
    row = current().store.save("credential", {
        "name": value.name, "kind": "native_login", "native_profile_ref": value.profile_ref,
        "location": value.location,
    }, actor)
    return {"id": row.id, "name": row.document["name"], "kind": "native_login", "location": value.location}


@router.get("/harnesses")
async def harnesses(actor: Principal = Depends(reader)):
    # Worker adapters are shipped templates; availability is verified on the selected runtime.
    return [
        {"key": key, "name": name, "provider_protocols": protocols,
         "native_profile_required": native_only, "verification": "adapter_template", "live_verified": False}
        for key, name, protocols, native_only in (
            ("codex", "Codex", ["responses"], False),
            ("claude", "Claude Code", ["messages"], False),
            ("hermes", "Hermes", [], True),
            ("kimi", "Kimi Code", [], True),
            ("opencode", "OpenCode", ["chat_completions", "responses", "messages"], False),
        )
    ]
