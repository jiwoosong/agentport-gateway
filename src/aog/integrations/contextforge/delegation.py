"""Native gateway credentials narrowed to one agent run and virtual server."""

import hashlib
import math
import os
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import select

from aog.models import Principal
from aog.store import Resource

TAG = "agentport-delegation"
LOCAL = "AGENTPORT_LOCAL"
NAMESPACE = "io.agentport/local"


async def ensure_controls(actor: Principal, store) -> dict[str, str]:
    """Install fixed private control tools; users cannot select arbitrary handlers."""
    from mcpgateway.db import SessionLocal, Tool
    from mcpgateway.schemas import ToolCreate
    from mcpgateway.services.tool_service import ToolService

    schemas = {
        "get_run": {"run_id": {"type": "string"}},
        "cancel": {"run_id": {"type": "string"}},
        "approve": {"run_id": {"type": "string"}, "node_id": {"type": "string"}, "approved": {"type": "boolean"}},
        "agent_permission": {"run_id": {"type": "string"}, "permission_id": {"type": "string"},
            "option_id": {"type": "string"}},
        "resolve": {"run_id": {"type": "string"}, "node_id": {"type": "string"},
            "outcome": {"enum": ["completed", "failed"]}, "output": {},
            "external_execution_stopped": {"const": True}},
    }
    result = {}
    suffix = hashlib.sha256(actor.subject.encode()).hexdigest()[:16]
    service = ToolService()
    with SessionLocal() as db:
        for operation, properties in schemas.items():
            name = f"ap_control_{operation}_{suffix}"
            tool = db.scalar(select(Tool).where(Tool.original_name == name, Tool.owner_email == actor.subject))
            if tool:
                binding = (tool.extension_metadata or {}).get(NAMESPACE, {})
                if tool.integration_type != LOCAL or binding.get("operation") != operation or tool.visibility != "private":
                    raise HTTPException(409, "Control tool registration requires operator reconciliation")
                result[operation] = tool.id
                continue
            definition = ToolCreate(
                name=name, integration_type=LOCAL, request_type="POST", visibility="private",
                inputSchema={"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False},
                extensionMetadata={NAMESPACE: {"operation": operation}},
            )
            created = await service.register_tool(
                db, definition, owner_email=actor.subject, created_by=actor.subject,
                visibility="private", local_registration_authorized=True,
            )
            result[operation] = created.id
    return result


async def issue(agent_id: str, allowed_tool_ids: list[str], run_id: str,
                actor: Principal, timeout_seconds: int, store) -> dict:
    """Issue native credentials from a validated job owner, never from a public minting API."""
    from mcpgateway.db import EmailUser, Server, SessionLocal, Tool
    from mcpgateway.middleware.rbac import token_scope_grants
    from mcpgateway.schemas import ServerCreate
    from mcpgateway.services.permission_service import PermissionService
    from mcpgateway.services.server_service import ServerService
    from mcpgateway.services.token_catalog_service import TokenCatalogService, TokenScope
    from mcpgateway.services.tool_service import ToolService

    if actor.agent_id:
        raise HTTPException(403, "Delegated agents cannot mint further gateway credentials")
    run = store.run(run_id, actor)
    if run.owner != actor.subject or run.status not in {"running", "waiting_approval"}:
        raise HTTPException(403, "Delegation requires an active job owned by the caller")
    profile = store.resource(agent_id, actor, "agent")
    declared = profile.document.get("allowed_tool_ids", profile.document.get("allowed_service_ids", []))
    if not set(allowed_tool_ids).issubset(declared):
        raise HTTPException(403, "Requested tools exceed the current agent profile")
    deadline = int(time.time()) + max(1, timeout_seconds) + 60
    if actor.token_expires_at is not None:
        deadline = min(deadline, actor.token_expires_at)
    if deadline <= time.time():
        raise HTTPException(401, "Parent execution authorization has expired")
    controls = await ensure_controls(actor, store)
    with SessionLocal() as db:
        user = db.scalar(select(EmailUser).where(EmailUser.email == actor.subject))
        if not user or not user.is_active:
            raise HTTPException(403, "Delegating account is unavailable")
        tokens = TokenCatalogService(db)
        team_id = profile.project_id or await tokens.get_default_team_id(actor.subject)
        if not team_id or (not actor.is_admin and team_id not in actor.teams):
            raise HTTPException(403, "Select an agent project available to the caller")
        caller_teams = None if actor.is_admin else actor.teams
        effective = await PermissionService(db).get_user_permissions(
            actor.subject, team_id=team_id, token_teams=caller_teams,
        )
        if not effective:
            raise HTTPException(403, "Caller has no effective gateway permissions")
        permissions = [p for p in ("tools.read", "tools.execute")
                       if token_scope_grants(list(effective), p) and token_scope_grants(actor.scopes, p)]
        if set(permissions) != {"tools.read", "tools.execute"}:
            raise HTTPException(403, "Caller cannot delegate tool reading and execution")
        native_tools = ToolService()
        parent_server_id = actor.token_server_id or actor.server_id
        parent_server = db.get(Server, parent_server_id) if parent_server_id else None
        if parent_server_id and not parent_server:
            raise HTTPException(403, "Parent virtual server is unavailable")
        for tool_id in set(allowed_tool_ids):
            await native_tools.get_tool(db, tool_id, requesting_user_email=actor.subject, token_teams=caller_teams)
            tool = db.get(Tool, tool_id)
            if not tool or tool.integration_type != LOCAL or not tool.enabled or tool.deprecated:
                raise HTTPException(403, "Only enabled managed tools can be delegated")
            if (tool.extension_metadata or {}).get(NAMESPACE, {}).get("operation") != "invoke":
                raise HTTPException(403, "Control or management operations cannot be delegated as tools")
            if tool.team_id and tool.team_id != team_id:
                raise HTTPException(403, "One agent grant cannot span multiple native gateway teams")
            if parent_server and tool_id not in {t.id for t in parent_server.tools}:
                raise HTTPException(403, "Agent tools exceed the caller virtual server")
        server = await ServerService().register_server(
            db, ServerCreate(name=f"ap_run_{uuid.uuid4().hex}",
                             associated_tools=sorted(set(allowed_tool_ids) | {controls["get_run"]})),
            created_by=actor.subject, owner_email=actor.subject, team_id=team_id, visibility="private",
        )
        token = None
        try:
            token, raw = await tokens.create_token(
                user_email=actor.subject, name=f"ap_run_{uuid.uuid4().hex}",
                scope=TokenScope(server_id=server.id, permissions=permissions),
                expires_in_days=max(1, math.ceil((deadline - time.time()) / 86400)),
                tags=[TAG], team_id=team_id, caller_permissions=permissions,
                is_admin=False, caller_token_teams=caller_teams, caller_token_teams_provided=True,
                caller_email=actor.subject,
            )
            grant = actor.model_copy(update={
                "is_admin": False, "teams": [team_id], "scopes": permissions,
                "agent_id": agent_id, "parent_run_id": run_id,
                "allowed_service_ids": sorted(set(allowed_tool_ids)),
                "server_id": server.id, "token_server_id": server.id,
                "token_id": token.jti, "token_expires_at": deadline,
                "token_use": "api",
                "team_scope_restricted": True,
            })
            with store.tx() as owned:
                owned.add(Resource(
                    id=token.jti, kind="delegation", owner=actor.subject, project_id=team_id,
                    visibility="private", document={**grant.model_dump(), "native_token_id": token.id},
                ))
            base = os.environ.get("AOG_INTERNAL_GATEWAY_URL", "http://gateway:4444").rstrip("/")
            return {"gateway_mcp_url": f"{base}/servers/{server.id}/mcp", "gateway_token": raw}
        except Exception:
            if token is not None:
                token.is_active = False
            native_server = db.get(Server, server.id)
            if native_server is not None:
                native_server.enabled = False
            db.commit()
            raise


async def resolve(caller, store) -> Principal:
    """Resolve a native token's persisted run grant without trusting caller-supplied metadata."""
    from mcpgateway.db import EmailApiToken, SessionLocal

    from aog.integrations.contextforge.native import principal

    actor = principal(caller)
    if not caller.token_id:
        return actor
    with store.tx() as db:
        row = db.get(Resource, caller.token_id)
        if row and row.kind == "delegation":
            owner, document = row.owner, dict(row.document)
        else:
            owner, document = None, None
    if document is None:
        with SessionLocal() as db:
            token = db.scalar(select(EmailApiToken).where(EmailApiToken.jti == caller.token_id))
            if token and TAG in (token.tags or []):
                raise HTTPException(403, "Native delegation has no active run binding")
        return actor
    grant = Principal.model_validate(document)
    if owner != caller.subject or grant.subject != caller.subject or grant.is_admin:
        raise HTTPException(403, "Native delegation identity does not match")
    if not grant.agent_id or not grant.parent_run_id or not grant.token_server_id:
        raise HTTPException(403, "Native delegation binding is incomplete")
    if caller.server_id != grant.token_server_id or caller.token_server_id != grant.token_server_id:
        raise HTTPException(403, "Native delegation virtual server does not match")
    if grant.token_expires_at is None or grant.token_expires_at <= time.time():
        raise HTTPException(403, "Native delegation has expired")
    if caller.teams is None or not set(grant.teams).issubset(caller.teams):
        raise HTTPException(403, "Native delegation team scope does not match")
    from mcpgateway.middleware.rbac import token_scope_grants

    grant.scopes = [scope for scope in grant.scopes
                    if token_scope_grants(list(caller.permissions) if caller.permissions is not None else None, scope)]
    if "tools.execute" not in grant.scopes:
        raise HTTPException(403, "Native token no longer permits delegated execution")
    return grant
