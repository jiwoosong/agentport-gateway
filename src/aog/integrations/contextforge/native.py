"""ContextForge-owned authentication, catalog and backend execution boundary."""

import hashlib
import json
import os
import time
import uuid
from types import SimpleNamespace

import jwt
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select

from aog.models import Principal, RuntimeDefinition, Service

LOCAL_METADATA = "io.agentport/local"
SERVICE_METADATA = "io.agentport/service"


def principal(caller) -> Principal:
    return Principal(
        subject=caller.subject,
        teams=list(caller.teams or ()),
        is_admin=caller.admin_bypass,
        scopes=list(caller.permissions or ()),
        server_id=caller.server_id,
        token_server_id=caller.token_server_id,
        token_id=caller.token_id,
        team_scope_restricted=caller.teams is not None,
        token_use=getattr(caller, "token_use", None),
        auth_method=caller.auth_method,
        token_expires_at=getattr(caller, "expires_at", None),
    )


async def authenticate(request: Request) -> Principal:
    from mcpgateway.agentport import caller_from_request
    from mcpgateway.middleware.rbac import get_current_user_with_permissions
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    user = await get_current_user_with_permissions(
        request, HTTPAuthorizationCredentials(scheme="Bearer", credentials=header[7:]), None
    )
    caller = caller_from_request(request, user)
    if caller is None:
        raise HTTPException(401, "Authenticated caller required")
    if caller.token_server_id:
        raise HTTPException(403, "Server-scoped tokens must use native virtual-server MCP or RPC")
    return principal(caller)



async def authorize(actor: Principal, permission="tools.execute") -> Principal:
    from mcpgateway.db import EmailTeamMember, EmailUser, SessionLocal
    from mcpgateway.middleware.rbac import token_scope_grants
    from mcpgateway.services.permission_service import PermissionService

    if actor.token_expires_at and actor.token_expires_at <= time.time():
        raise HTTPException(401, "Execution authorization expired; sign in and authorize again")
    if not token_scope_grants(actor.scopes, permission):
        raise HTTPException(403, "Token scope does not permit this action")
    with SessionLocal() as db:
        user = db.scalar(select(EmailUser).where(EmailUser.email == actor.subject))
        if not user or not user.is_active:
            raise HTTPException(403, "Account unavailable")
        memberships = set(db.scalars(select(EmailTeamMember.team_id).where(
            EmailTeamMember.user_email == actor.subject, EmailTeamMember.is_active.is_(True)
        )))
        fresh = actor.model_copy(update={
            "teams": sorted(memberships if actor.is_admin else memberships.intersection(actor.teams)),
            "is_admin": actor.is_admin and user.is_admin,
        })
        if not await PermissionService(db).check_permission(
            user_email=actor.subject, permission=permission,
            token_teams=None if fresh.is_admin else fresh.teams,
            check_any_team=True, allow_admin_bypass=fresh.is_admin,
        ):
            raise HTTPException(403, "Action not permitted")
        if fresh.token_id:
            from mcpgateway.db import EmailApiToken
            token = db.scalar(select(EmailApiToken).where(EmailApiToken.jti == fresh.token_id))
            if (fresh.token_use == "api" and token is None) or (token and not token.is_active):
                raise HTTPException(403, "API token has been revoked")
    if fresh.token_use == "api" and not fresh.token_id:
        raise HTTPException(403, "API execution token has no native token binding")
    return fresh


def require_admin(actor):
    """Require native administrator status and the token's configuration permission."""
    from mcpgateway.middleware.rbac import token_scope_grants
    if not actor.is_admin or not token_scope_grants(actor.scopes, "admin.system_config"):
        raise HTTPException(403, "Platform administrator required for deployment changes")


def encrypt_secret(value):
    from mcpgateway.utils.services_auth import encode_auth
    return encode_auth(value)


def decrypt_secret(value):
    from mcpgateway.utils.services_auth import decode_auth
    return decode_auth(value)


def _tool_service():
    from mcpgateway.services.tool_service import ToolService
    return ToolService()


def _scope(actor):
    return None if actor.is_admin else actor.teams


def _json_result(result):
    if result.is_error:
        raise RuntimeError("Gateway upstream reported an error")
    if result.structured_content is not None:
        return result.structured_content
    for item in result.content or []:
        text = getattr(item, "text", None)
        if text:
            try:
                return json.loads(text)
            except ValueError:
                pass
    return result.model_dump(mode="json", by_alias=True)


class Catalog:
    """Native gateway tools are the only active service catalog."""

    def __init__(self, store):
        self.store = store
        self.tools = _tool_service()

    async def resource(self, rid, actor, write=False):
        from mcpgateway.db import SessionLocal, Tool
        from mcpgateway.services.tool_service import ToolNotFoundError

        from aog.store import Resource
        with self.store.tx() as own_db:
            alias = own_db.get(Resource, rid)
            is_alias = alias is not None and alias.kind == "service_alias"
        if is_alias:
            alias = self.store.resource(rid, actor, "service_alias", write=write)
            if alias.document.get("phase") != "completed":
                raise HTTPException(409, "Service migration needs operator reconciliation")
            rid = alias.document["tool_id"]
        with SessionLocal() as db:
            try:
                await self.tools.get_tool(db, rid, requesting_user_email=actor.subject,
                    token_teams=_scope(actor))
            except ToolNotFoundError:
                raise HTTPException(404, "Tool not found") from None
            tool = db.get(Tool, rid)
            metadata = dict(tool.extension_metadata or {})
            options = metadata.get(SERVICE_METADATA)
            if not options or options.get("internal"):
                raise HTTPException(404, "Managed tool not found")
            if write and not (actor.is_admin or tool.owner_email == actor.subject):
                raise HTTPException(403, "Owner permission required")
            if actor.agent_id and rid not in (actor.allowed_service_ids or []):
                raise HTTPException(404, "Tool is outside the agent grant")
            server_id = actor.token_server_id or actor.server_id
            if server_id:
                from mcpgateway.db import Server
                server = db.get(Server, server_id)
                if not server or rid not in {t.id for t in server.tools}:
                    raise HTTPException(404, "Tool is outside the virtual server")
            backend = db.get(Tool, options["backend_tool_id"])
            if not backend:
                raise HTTPException(409, "Tool backend is missing")
            values = {k: v for k, v in options.items() if k not in {"backend_tool_id", "control_tool_id", "revision", "result_schema", "migration_source_id"}}
            endpoint = backend.url
            if options.get("transport") == "acp" and endpoint.endswith("/jobs"):
                endpoint = endpoint[:-5] + "/invoke"
            values.update(name=tool.display_name or tool.name, endpoint=endpoint,
                input_schema=tool.input_schema["properties"]["inputs"],
                output_schema=options.get("result_schema", backend.output_schema or {}),
                project_id=tool.team_id, visibility="project" if tool.visibility == "team" else "private",
                enabled=tool.enabled)
            values.pop("runtime_id", None)
            service = self.store.service_projection(rid, Service.model_validate(values))
            from aog.store import Resource
            with self.store.tx() as own_db:
                runtime_row = own_db.get(Resource, self.store.runtime_id(rid))
                runtime_revision = runtime_row.revision if runtime_row else 0
            return SimpleNamespace(id=rid, kind="service", owner=tool.owner_email,
                project_id=tool.team_id, visibility=values["visibility"],
                revision=options.get("revision", 1) * 1000000 + runtime_revision, document=service.model_dump())

    async def list_resources(self, actor):
        from mcpgateway.db import SessionLocal, Tool
        with SessionLocal() as db:
            ids = list(db.scalars(select(Tool.id).where(Tool.integration_type == "AGENTPORT_LOCAL")))
        rows = []
        for rid in ids:
            try:
                rows.append(await self.resource(rid, actor))
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
        return rows

    async def publish_resource(self, row, actor):
        from mcpgateway.db import SessionLocal, Tool
        from mcpgateway.schemas import ToolCreate, ToolUpdate

        from aog.models import Invocation
        if row.kind not in {"agent", "workflow"} or row.owner != actor.subject:
            raise HTTPException(403, "Only the resource owner can publish its fixed invocation tool")
        with SessionLocal() as db:
            candidates = list(db.scalars(select(Tool).where(Tool.integration_type == "AGENTPORT_LOCAL",
                Tool.owner_email == actor.subject)))
            old = next((t for t in candidates if (t.extension_metadata or {}).get(LOCAL_METADATA, {}).get("resource_id") == row.id), None)
            if old and old.team_id != row.project_id:
                raise HTTPException(409, "Publish a new resource to change its native gateway team")
            values = dict(displayName=row.document["name"], integration_type="AGENTPORT_LOCAL",
                request_type="POST", inputSchema=Invocation.model_json_schema(),
                visibility="team" if row.visibility == "project" else "private", team_id=row.project_id,
                extensionMetadata={LOCAL_METADATA: {"operation": row.kind, "resource_id": row.id}})
            if old:
                await self.tools.update_tool(db, old.id, ToolUpdate(**values), user_email=actor.subject,
                    local_registration_authorized=True)
            else:
                await self.tools.register_tool(db, ToolCreate(name=f"ap_{row.kind}_{row.id}", **values),
                    owner_email=actor.subject, created_by=actor.subject, team_id=row.project_id,
                    visibility=values["visibility"], local_registration_authorized=True)

    async def migrated_tool(self, source_id, owner):
        from mcpgateway.db import SessionLocal, Tool
        name = "ap_migrated_" + hashlib.sha256(source_id.encode()).hexdigest()[:32]
        with SessionLocal() as db:
            tool = db.scalar(select(Tool).where(Tool.original_name == name, Tool.owner_email == owner))
            if tool is None:
                return None
            tool_id = tool.id
        return await self.resource(tool_id, Principal(subject=owner, is_admin=True))

    async def submit(self, kind, rid, request, actor):
        """Submit through the same native local-tool policy pipeline as MCP and RPC."""
        from mcpgateway.agentport import LOCAL_INTEGRATION
        from mcpgateway.db import SessionLocal, Tool

        if kind == "service":
            resource = await self.resource(rid, actor)
        elif kind in {"agent", "workflow"}:
            resource = self.store.resource(rid, actor, kind)
        else:
            raise HTTPException(404, "Invocable resource not found")
        with SessionLocal() as db:
            if kind == "service":
                tool = db.get(Tool, resource.id)
            else:
                candidates = db.scalars(select(Tool).where(Tool.integration_type == LOCAL_INTEGRATION,
                    Tool.owner_email == resource.owner))
                matches = [candidate for candidate in candidates if
                    (candidate.extension_metadata or {}).get(LOCAL_METADATA, {}).get("operation") == kind
                    and (candidate.extension_metadata or {}).get(LOCAL_METADATA, {}).get("resource_id") == resource.id]
                if len(matches) != 1:
                    raise HTTPException(409, "Resource needs a single published native invocation tool")
                tool = matches[0]
            operation = "invoke" if kind == "service" else kind
            if not tool or tool.integration_type != LOCAL_INTEGRATION or (tool.extension_metadata or {}).get(LOCAL_METADATA, {}).get("operation") != operation:
                raise HTTPException(409, "Resource has no native invocation tool")
            if tool.team_id != resource.project_id or tool.visibility != ("team" if resource.visibility == "project" else "private"):
                raise HTTPException(409, "Resource publication needs reconciliation")
            tool_id = tool.id
        value = await self._invoke_local(tool_id, operation, request.model_dump(mode="json"), actor)
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise HTTPException(502, "Native gateway did not return a job acknowledgement")
        return self.store.run(value["id"], actor)

    async def control(self, operation, arguments, actor):
        """Apply native tool policy before the fixed job-control handler."""
        from aog.integrations.contextforge.delegation import ensure_controls

        if operation not in {"cancel", "approve", "agent_permission", "resolve"}:
            raise HTTPException(403, "Job control operation is not registered")
        controls = await ensure_controls(actor, self.store)
        return await self._invoke_local(controls[operation], operation, arguments, actor)

    async def _invoke_local(self, tool_id, operation, arguments, actor):
        from mcpgateway.agentport import LOCAL_INTEGRATION, AuthorizedInvocation
        from mcpgateway.db import SessionLocal, Tool
        from mcpgateway.services.tool_service import ToolInvocationError, ToolNotFoundError

        with SessionLocal() as db:
            tool = db.get(Tool, tool_id)
            if not tool or tool.integration_type != LOCAL_INTEGRATION or (tool.extension_metadata or {}).get(LOCAL_METADATA, {}).get("operation") != operation:
                raise HTTPException(409, "Native operation binding needs reconciliation")
            caller = AuthorizedInvocation(
                subject=actor.subject, teams=None if actor.is_admin else tuple(actor.teams),
                permissions=tuple(actor.scopes), server_id=actor.server_id,
                token_server_id=actor.token_server_id, token_id=actor.token_id, token_use=actor.token_use,
                expires_at=actor.token_expires_at, auth_method=actor.auth_method, admin_bypass=actor.is_admin,
            )
            try:
                result = await self.tools.invoke_tool(db, tool.name, arguments,
                    user_email=actor.subject, app_user_email=actor.subject, token_teams=_scope(actor),
                    server_id=actor.server_id, caller_context=caller)
            except ToolNotFoundError:
                raise HTTPException(404, "Invocation tool not found") from None
            except ToolInvocationError:
                raise HTTPException(409, "Native gateway rejected the invocation") from None
        return _json_result(result)

    async def save(self, document, actor, rid=None, migration_source_id=None):
        require_admin(actor)
        if not rid:
            return await self._save(document, actor, migration_source_id=migration_source_id)
        if not actor.is_admin:
            raise HTTPException(403, "Platform administrator required for tool registration")
        previous = await self.resource(rid, actor, write=True)
        values = dict(document)
        runtime_id = values.pop("runtime_id", None) or self.store.runtime_id(previous.id)
        values["schema_version"] = 1
        definition = Service.model_validate(values)
        if previous.project_id != definition.project_id:
            raise HTTPException(409, "Register a new tool to change its native gateway team")
        if previous.document["transport"] != definition.transport or previous.document.get("tool_name") != definition.tool_name:
            raise HTTPException(409, "Register a new tool to change its protocol or remote MCP tool name")
        if definition.transport == "acp" and not definition.endpoint.endswith("/invoke"):
            raise HTTPException(422, "ACP worker endpoint must end with /invoke")
        self.store.resource(runtime_id, actor, "runtime")
        source_runtime = self.store.runtime_id(previous.id)
        previous_phase = self.store.phase(source_runtime)
        if not self.store.claim_stop(source_runtime, 0, "updating"):
            raise HTTPException(409, "Tool runtime is busy; drain jobs and deployment operations first")
        try:
            result = await self._save(
                {**document, "runtime_id": runtime_id}, actor, previous.id,
                migration_source_id=migration_source_id, claimed_runtime_id=source_runtime,
            )
        except BaseException:
            self.store.phase(source_runtime, "error")
            raise
        self.store.phase(source_runtime, previous_phase)
        return result

    async def _save(self, document, actor, rid=None, migration_source_id=None, *, claimed_runtime_id=None):
        from mcpgateway.db import SessionLocal, Tool
        from mcpgateway.schemas import ToolCreate, ToolUpdate

        require_admin(actor)
        document = dict(document)
        runtime_id = document.pop("runtime_id", None)
        document["schema_version"] = 1
        definition = Service.model_validate(document)
        if definition.project_id and definition.project_id not in actor.teams and not actor.is_admin:
            raise HTTPException(403, "Project membership required")
        if rid:
            previous_row = await self.resource(rid, actor, write=True)
            rid = previous_row.id
            if previous_row.project_id != definition.project_id:
                raise HTTPException(409, "Register a new tool to change its native gateway team")
            if previous_row.document["transport"] != definition.transport or previous_row.document.get("tool_name") != definition.tool_name:
                raise HTTPException(409, "Register a new tool to change its protocol or remote MCP tool name")
            from aog.store import Lease
            with self.store.tx() as own_db:
                busy = own_db.scalar(select(Lease.id).where(
                    Lease.service_id == self.store.runtime_id(rid)).limit(1))
            if busy:
                raise HTTPException(409, "Tool runtime has active jobs")
        if runtime_id:
            self.store.resource(runtime_id, actor, "runtime")
        else:
            runtime = RuntimeDefinition(
                name=definition.name, project_id=definition.project_id, visibility=definition.visibility,
                backend="openshell" if definition.openshell else "komodo" if definition.stack_id else "external",
                target_id=definition.stack_id, openshell=definition.openshell,
                lifecycle=definition.lifecycle, health_url=definition.health_url,
                idle_seconds=definition.idle_seconds, timeout_seconds=definition.timeout_seconds,
                concurrency=definition.concurrency, location=definition.location,
                egress_policy=definition.egress_policy, releases=definition.releases,
                current_release=definition.current_release, rollback_safe=definition.rollback_safe,
            )
            runtime_id = self.store.register_runtime(runtime, actor).id
        visibility = "team" if definition.visibility == "project" else "private"
        options = definition.model_dump(exclude={"name", "endpoint", "input_schema", "output_schema",
            "project_id", "visibility", "enabled", "lifecycle", "stack_id", "openshell", "health_url",
            "idle_seconds", "timeout_seconds", "concurrency", "location", "egress_policy", "releases",
            "current_release", "rollback_safe"})
        with SessionLocal() as db:
            old = db.get(Tool, rid) if rid else None
            previous = (old.extension_metadata or {}).get(SERVICE_METADATA, {}) if old else {}
            backend_id = previous.get("backend_tool_id")
            migration_suffix = hashlib.sha256(migration_source_id.encode()).hexdigest()[:32] if migration_source_id else None
            backend_name = f"ap_migrated_backend_{migration_suffix}" if migration_suffix else f"ap_backend_{backend_id or uuid.uuid4().hex}"
            if migration_suffix and not backend_id:
                existing_backend = db.scalar(select(Tool).where(Tool.owner_email == actor.subject,
                    (Tool.original_name == backend_name) | (Tool.custom_name == backend_name)))
                backend_id = existing_backend.id if existing_backend else None
            backend_values = dict(
                name=backend_name,
                url=definition.endpoint, integration_type="MCP" if definition.transport == "mcp" else "REST",
                request_type="STREAMABLEHTTP" if definition.transport == "mcp" else definition.http_method,
                inputSchema=definition.input_schema, outputSchema=definition.output_schema or None,
                visibility="private", annotations={"agentport_internal": True},
                extensionMetadata={SERVICE_METADATA: {"internal": True}},
            )
            if definition.transport != "mcp":
                backend_values.update(expose_passthrough=False, timeout_ms=definition.timeout_seconds * 1000)
            if definition.transport == "acp":
                from aog.worker import Prompt
                if not definition.endpoint.endswith("/invoke"):
                    raise HTTPException(422, "ACP worker endpoint must end with /invoke")
                backend_values.update(url=definition.endpoint[:-7] + "/jobs",
                    inputSchema=Prompt.model_json_schema(), outputSchema=None)
            if definition.transport == "mcp":
                backend_values["name"] = definition.tool_name
            if backend_id:
                backend_values.pop("name")
                if definition.transport == "mcp":
                    backend_values.pop("integration_type")
                    backend_values.pop("request_type")
                await self.tools.update_tool(db, backend_id, ToolUpdate(**backend_values),
                    user_email=actor.subject, modified_by=actor.subject)
            else:
                backend_definition = ToolCreate.model_validate(backend_values,
                    context={"agentport_internal_mcp": True})
                backend = await self.tools.register_tool(db, backend_definition,
                    owner_email=actor.subject, created_by=actor.subject, visibility="private")
                if definition.transport == "mcp":
                    backend = await self.tools.update_tool(db, backend.id, ToolUpdate(custom_name=backend_name),
                        user_email=actor.subject, modified_by=actor.subject)
                backend_id = backend.id
            options.update(backend_tool_id=backend_id, result_schema=definition.output_schema, revision=previous.get("revision", 0) + 1)
            if migration_source_id:
                options["migration_source_id"] = migration_source_id
            if definition.transport == "acp":
                control_id = previous.get("control_tool_id")
                control_name = f"ap_migrated_control_{migration_suffix}" if migration_suffix else f"ap_control_{uuid.uuid4().hex}"
                if migration_suffix and not control_id:
                    existing_control = db.scalar(select(Tool).where(Tool.owner_email == actor.subject,
                        Tool.original_name == control_name))
                    control_id = existing_control.id if existing_control else None
                control_values = dict(url=definition.endpoint[:-7] + "/job-control", integration_type="REST",
                    request_type="POST", inputSchema={"type": "object"}, visibility="private", expose_passthrough=False,
                    extensionMetadata={SERVICE_METADATA: {"internal": True}})
                if control_id:
                    await self.tools.update_tool(db, control_id, ToolUpdate(**control_values), user_email=actor.subject)
                else:
                    control = await self.tools.register_tool(db,
                        ToolCreate(name=control_name, **control_values),
                        owner_email=actor.subject, created_by=actor.subject, visibility="private")
                    control_id = control.id
                options["control_tool_id"] = control_id
            local_values = dict(name=old.original_name if old else f"ap_{uuid.uuid4().hex}",
                displayName=definition.name, integration_type="AGENTPORT_LOCAL", request_type="POST",
                inputSchema={"type": "object", "properties": {"inputs": definition.input_schema,
                    "session_id": {"type": ["string", "null"]}, "local_only": {"type": "boolean"},
                    "allow_cloud": {"type": "boolean"}, "idempotency_key": {"type": ["string", "null"]}},
                    "required": ["inputs"], "additionalProperties": False},
                visibility=visibility, team_id=definition.project_id,
                extensionMetadata={LOCAL_METADATA: {"operation": "invoke"}, SERVICE_METADATA: options})
            if migration_source_id and not old:
                local_values["name"] = "ap_migrated_" + hashlib.sha256(migration_source_id.encode()).hexdigest()[:32]
            if old:
                local_values.pop("name")
                tool = await self.tools.update_tool(db, rid, ToolUpdate(**local_values),
                    user_email=actor.subject, modified_by=actor.subject, local_registration_authorized=True)
            else:
                tool = await self.tools.register_tool(db, ToolCreate(**local_values),
                    owner_email=actor.subject, created_by=actor.subject, team_id=definition.project_id,
                    visibility=visibility, local_registration_authorized=True)
            if tool.enabled != definition.enabled:
                tool = await self.tools.set_tool_state(db, tool.id, definition.enabled, True,
                    user_email=actor.subject)
        self.store.bind_runtime(tool.id, runtime_id, actor, claimed_runtime_id=claimed_runtime_id)
        return await self.resource(tool.id, actor)

    async def invoke(self, service_id, service, inputs, key, actor, credential=None):
        from mcpgateway.agentport import backend_invocation
        from mcpgateway.db import SessionLocal, Tool

        from aog.adapters import UncertainExecution
        from aog.identity import signing_key
        service_id = (await self.resource(service_id, Principal.model_validate(actor))).id
        with SessionLocal() as db:
            public = db.get(Tool, service_id)
            options = public.extension_metadata[SERVICE_METADATA]
            backend = db.get(Tool, options["backend_tool_id"])
            headers = {"Idempotency-Key": key}
            if service.transport == "acp" or service.accepts_identity:
                headers["X-AOG-Context"] = jwt.encode({"iss": "agentport-worker", "sub": actor["subject"],
                    "teams": actor.get("teams", []), "aud": service.endpoint,
                    "project_id": actor.get("project_id"), "run_step": key,
                    "exp": int(time.time()) + service.timeout_seconds + 60}, signing_key(), algorithm="EdDSA")
            if credential:
                headers["X-Upstream-Authorization"] = f"Bearer {credential}"
            try:
                # Only a previously authorized job can select this registered private backend.
                with backend_invocation(backend.id):
                    result = await self.tools.invoke_tool(db, backend.name, inputs,
                        request_headers=headers, user_email=backend.owner_email, app_user_email=backend.owner_email,
                        token_teams=None)
                value = _json_result(result)
            except Exception as exc:
                raise UncertainExecution("Native gateway call requires reconciliation") from exc
        if service.transport != "acp":
            return value
        import asyncio
        run_id, node_id = key.split(":", 1)
        deadline = time.monotonic() + service.timeout_seconds
        while True:
            if not isinstance(value, dict) or not value.get("job_id"):
                raise UncertainExecution("Worker did not return a durable job ID")
            previous = self.store.step(run_id, node_id)
            detail = {**(previous.output if previous else {}), "worker": value, "service_id": service_id}
            self.store.step(run_id, node_id, "running", detail)
            if value["status"] == "completed":
                return value["result"]
            if value["status"] == "cancelled":
                return {"native_session_id": value.get("native_session_id"), "stop_reason": "cancelled", "files": []}
            if value["status"] in {"needs_attention", "failed"}:
                raise UncertainExecution("Worker execution needs reconciliation")
            if time.monotonic() > deadline:
                raise UncertainExecution("Worker job is still active; reconcile its status")
            self.store.status(run_id, "waiting_agent_approval" if value.get("permissions") else "running")
            with self.store.tx() as own_db:
                from aog.store import Run
                cancelled = own_db.get(Run, run_id).cancel_requested
            operation = "cancel" if cancelled and value["status"] != "cancelling" else "status"
            await asyncio.sleep(0.25)
            value = await self.worker_control(service_id, service, key, actor,
                {"operation": operation, "job_id": value["job_id"]}, credential)

    async def worker_control(self, service_id, service, key, actor, payload, credential=None, operator=False):
        from mcpgateway.agentport import backend_invocation
        from mcpgateway.db import SessionLocal, Tool

        from aog.adapters import UncertainExecution
        from aog.identity import signing_key
        headers = {"Idempotency-Key": key, "X-AOG-Context": jwt.encode({
            "iss": "agentport-worker", "sub": actor["subject"], "teams": actor.get("teams", []),
            "aud": service.endpoint, "project_id": actor.get("project_id"), "run_step": key,
            "exp": int(time.time()) + 120, "operator": operator,
        }, signing_key(), algorithm="EdDSA")}
        if credential:
            headers["X-Upstream-Authorization"] = f"Bearer {credential}"
        with SessionLocal() as db:
            public = db.get(Tool, service_id)
            options = public.extension_metadata[SERVICE_METADATA]
            control = db.get(Tool, options["control_tool_id"])
            try:
                with backend_invocation(control.id):
                    result = await self.tools.invoke_tool(db, control.name, payload, request_headers=headers,
                        user_email=control.owner_email, app_user_email=control.owner_email, token_teams=None)
                return _json_result(result)
            except Exception as exc:
                raise UncertainExecution("Worker control requires reconciliation") from exc


_active_catalog: Catalog | None = None


def bind_catalog(catalog: Catalog | None):
    """Bind only the gateway component's catalog during its explicit lifespan."""
    global _active_catalog
    _active_catalog = catalog


async def local_dispatch(caller, tool, arguments):
    """Forward a native-authorized local call to the separate Core job controller."""
    from aog.bridge import rpc_call
    from aog.integrations.contextforge.delegation import resolve

    if _active_catalog is None:
        raise HTTPException(503, "ContextForge integration is not ready")
    actor = await authorize(await resolve(caller, _active_catalog.store))
    payload = {"actor": actor.model_dump(mode="json"), "tool": {
        "id": tool.id, "name": tool.name, "team_id": tool.team_id,
        "owner_email": tool.owner_email, "visibility": tool.visibility,
        "extension_metadata": dict(tool.extension_metadata)}, "arguments": arguments}
    return await rpc_call(os.environ.get("AOG_CORE_URL", "http://core:4455"), "dispatch", payload,
        "contextforge", timeout=3600, path="/internal/agentport/v1/dispatch")


async def prepare_gateway(catalog):
    from mcpgateway.cache.global_config_cache import global_config_cache
    from mcpgateway.db import GlobalConfig, SessionLocal

    from aog.store import Resource, Run
    with catalog.store.tx() as db:
        legacy = db.scalar(select(Resource.id).where(Resource.kind == "service").limit(1))
        if not legacy:
            aliases = db.scalars(select(Resource).where(Resource.kind == "service_alias"))
            legacy = next((r.id for r in aliases if r.document.get("phase") != "completed"), None)
        unsafe = db.scalar(select(Run.id).where(Run.status.not_in(["completed", "failed", "cancelled"])).limit(1))
        if legacy and unsafe:
            raise RuntimeError("Drain or reconcile legacy jobs before running aog migrate")
        if legacy and os.environ.get("AOG_MIGRATING") != "1":
            raise RuntimeError("Legacy service definitions need migration: back up data and run aog migrate")
    with SessionLocal() as db:
        config = db.scalar(select(GlobalConfig))
        if config is None:
            config = GlobalConfig(passthrough_headers=[])
            db.add(config)
        config.passthrough_headers = sorted(set(config.passthrough_headers or []) |
            {"X-AOG-Context", "Idempotency-Key"})
        db.commit()
    global_config_cache.invalidate()
