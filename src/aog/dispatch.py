"""Fixed domain operations admitted by a gateway; no framework objects cross here."""
from fastapi import HTTPException

from aog import api
from aog.engine import current
from aog.models import Decision, Invocation, Principal, Resolution
from aog.security import authorize


async def dispatch(actor, tool, arguments):
    actor = await authorize(Principal.model_validate(actor))
    binding = (tool.get("extension_metadata") or {}).get("io.agentport/local", {})
    operation = binding.get("operation")
    if operation == "invoke":
        return api.run_view(await current().submit("service", tool["id"], Invocation.model_validate(arguments), actor))
    if operation == "get_run":
        if actor.agent_id and arguments["run_id"] == actor.parent_run_id:
            raise HTTPException(403, "Parent workflow history is not delegated")
        return await api.run_detail(arguments["run_id"], actor)
    if operation == "cancel":
        return await api.cancel(arguments["run_id"], actor)
    if operation == "approve":
        return await api.approve(arguments["run_id"], arguments["node_id"], Decision(approved=arguments["approved"]), actor)
    if operation == "agent_permission":
        return await api.native_permission(arguments["run_id"], arguments["permission_id"],
            api.NativePermission(option_id=arguments["option_id"]), actor)
    if operation == "resolve":
        return await api.resolve_step(arguments["run_id"], arguments["node_id"],
            Resolution(**{key: arguments[key] for key in ("outcome", "output", "external_execution_stopped")}), actor)
    if operation in {"agent", "workflow"}:
        if actor.agent_id:
            raise HTTPException(403, "Nested agent and workflow execution is not delegated")
        return api.run_view(await current().submit(operation, binding["resource_id"], Invocation.model_validate(arguments), actor))
    raise HTTPException(403, "Gateway operation is not registered")
