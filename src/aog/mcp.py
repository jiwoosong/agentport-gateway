"""SDK-managed MCP transport with the same identity and resource checks as REST."""

from contextvars import ContextVar

from fastapi import HTTPException, Request
from mcp.server.mcpserver import MCPServer
from starlette.responses import JSONResponse

from aog.api import resource_view, run_detail, run_view
from aog.engine import current
from aog.models import Invocation, Principal
from aog.security import authenticate, authorize

principal: ContextVar[Principal] = ContextVar("aog_mcp_principal")
server = MCPServer("AgentPort", version="0.1.0")


@server.tool()
async def list_capabilities() -> dict:
    """List only services, agents and workflows accessible to the authenticated caller."""
    actor = await authorize(principal.get(), "tools.read")
    return {
        kind: [
            resource_view(r)
            for r in current().store.list_resources(actor, kind)
            if not actor.agent_id or r.id in (actor.allowed_service_ids or [])
        ]
        for kind in (["service"] if actor.agent_id else ["service", "agent", "workflow"])
    }


@server.tool()
async def invoke(
    kind: str,
    resource_id: str,
    inputs: dict,
    local_only: bool = True,
    allow_cloud: bool = False,
    session_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Submit an asynchronous job. Poll get_run using the returned ID."""
    if kind not in {"service", "agent", "workflow"}:
        raise ValueError("Invalid resource kind")
    request = Invocation(
        inputs=inputs,
        local_only=local_only,
        allow_cloud=allow_cloud,
        session_id=session_id,
        idempotency_key=idempotency_key,
    )
    return run_view(await current().submit(kind, resource_id, request, principal.get()))


@server.tool()
async def get_run(run_id: str) -> dict:
    """Retrieve an authorized job's progress and result."""
    actor = await authorize(principal.get(), "tools.read")
    if actor.agent_id and run_id == actor.parent_run_id:
        raise ValueError("Parent workflow history is not exposed to delegated agents")
    return await run_detail(run_id, actor)


class AuthenticatedMCP:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.application(scope, receive, send)
            return
        try:
            actor = await authenticate(Request(scope, receive))
        except HTTPException as exc:
            await JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)(
                scope, receive, send
            )
            return
        token = principal.set(actor)
        try:
            await self.application(scope, receive, send)
        finally:
            principal.reset(token)
