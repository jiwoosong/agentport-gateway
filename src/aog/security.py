"""Core delegates identity to its gateway, and owns its agent-run constraints."""
from fastapi import HTTPException

from aog.backends import get_backend
from aog.models import Principal


async def authenticate(request):
    return await get_backend().authenticate(request)


async def authorize(actor: Principal, permission="tools.execute"):
    fresh = await get_backend().authorize(actor, permission)
    if fresh.agent_id:
        from aog.engine import current
        store = current().store
        parent = store.run(fresh.parent_run_id, fresh)
        if parent.status not in {"running", "waiting_approval", "waiting_agent_approval"}:
            raise HTTPException(403, "Parent agent run is no longer active")
        await authorize(Principal.model_validate(parent.principal), permission)
        agent = store.resource(fresh.agent_id, fresh, "agent")
        allowed = agent.document.get("allowed_tool_ids", agent.document.get("allowed_service_ids", []))
        fresh.allowed_service_ids = sorted(set(fresh.allowed_service_ids or []).intersection(allowed))
    return fresh


def require_admin(actor):
    if not actor.is_admin or not actor.scope_allows("admin.system_config"):
        raise HTTPException(403, "Platform administrator scope required for deployment changes")
