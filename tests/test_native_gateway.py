"""Regression checks for the controlled fork's native local-tool boundary."""

from dataclasses import FrozenInstanceError
from types import MappingProxyType
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from cpex.framework import PluginResult, PluginViolationError, ToolHookType, ToolPreInvokePayload
from mcpgateway import agentport
from mcpgateway.schemas import ToolCreate
from mcpgateway.services.tool_service import ResolvedTool, ToolInvocationError, ToolService
from pydantic import ValidationError


def local_payload():
    return {
        "id": "local-tool",
        "name": "submit",
        "integration_type": agentport.LOCAL_INTEGRATION,
        "request_type": "POST",
        "url": None,
        "owner_email": "alice@example.com",
        "team_id": "team-a",
        "visibility": "team",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        "output_schema": {"type": "object", "required": ["job_id"]},
        "extension_metadata": {agentport.LOCAL_NAMESPACE: {"operation": "invoke"}},
    }


def caller(**overrides):
    return agentport.AuthorizedInvocation(
        **{
            "subject": "alice@example.com",
            "teams": ("team-a",),
            "permissions": ("tools.execute",),
            "server_id": "server-a",
            "token_server_id": "server-a",
            **overrides,
        }
    )


@pytest.fixture
def handlers(monkeypatch):
    handler = AsyncMock(return_value={"job_id": "job-1"})
    monkeypatch.setattr(agentport, "_handlers", MappingProxyType({"invoke": handler}))
    return handler


def test_local_schema_has_no_network_endpoint():
    definition = {"name": "submit", "integration_type": agentport.LOCAL_INTEGRATION,
                  "extensionMetadata": {agentport.LOCAL_NAMESPACE: {"operation": "invoke"}}}
    parsed = ToolCreate.model_validate(definition)
    assert parsed.url is None and parsed.request_type == "POST"
    with pytest.raises(ValidationError):
        ToolCreate.model_validate({**definition, "url": "https://example.com"})


def test_mcp_creation_context_is_process_owned_and_requires_internal_metadata(monkeypatch):
    from mcpgateway.config import settings

    definition = {"name": "remote_echo", "integration_type": "MCP", "request_type": "STREAMABLEHTTP",
                  "url": "https://example.com/mcp", "allow_auto": True,
                  "extensionMetadata": {agentport.SERVICE_NAMESPACE: {"internal": True}},
                  "context": {"agentport_internal_mcp": True}, "agentport_internal_mcp": True}
    with pytest.raises(ValidationError, match="Cannot manually create MCP tools"):
        ToolCreate.model_validate(definition)
    monkeypatch.setattr(settings, "ssrf_allow_localhost", True)
    monkeypatch.setattr(settings, "ssrf_allow_private_networks", True)
    parsed = ToolCreate.model_validate({**definition, "url": "http://127.0.0.1:8000/mcp"},
                                     context={"agentport_internal_mcp": True})
    assert parsed.integration_type == "MCP" and parsed.request_type == "STREAMABLEHTTP"
    definition.pop("extensionMetadata")
    with pytest.raises(ValidationError, match="Cannot manually create MCP tools"):
        ToolCreate.model_validate(definition, context={"agentport_internal_mcp": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,metadata", [
    (agentport.LOCAL_INTEGRATION, {}),
    ("REST", {agentport.SERVICE_NAMESPACE: {"internal": True}}),
    ("MCP", {agentport.SERVICE_NAMESPACE: {"internal": True}}),
])
async def test_optional_toolops_cannot_invoke_managed_backend_directly(monkeypatch, kind, metadata):
    import mcpgateway.toolops.toolops_altk_service as module

    tools = MagicMock()
    tools.get_tool = AsyncMock(return_value=MagicMock(integration_type=kind, extension_metadata=metadata))
    auth = MagicMock()
    chat = MagicMock()
    monkeypatch.setattr(module, "query_tool_auth", auth)
    monkeypatch.setattr(module, "MCPChatService", chat)
    with pytest.raises(PermissionError):
        await module.execute_tool_nl_test_cases("managed-tool", ["run the tool"], tools, MagicMock())
    auth.assert_not_called()
    chat.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("path_server", ["server-a", None])
async def test_native_mcp_scoped_caller_cannot_route_around_server_catalog(monkeypatch, path_server):
    from contextlib import asynccontextmanager

    import mcpgateway.transports.streamablehttp_transport as module
    from mcpgateway.db import Gateway

    gateway = Gateway(id="public-proxy", name="Public proxy", url="https://backend.example/mcp",
                      gateway_mode="direct_proxy", visibility="public", owner_email="alice@example.com")
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = gateway
    assert await module.check_gateway_access(db, gateway, "alice@example.com", ["team-a"])

    @asynccontextmanager
    async def database():
        yield db

    user = {"is_authenticated": True, "email": "alice@example.com", "teams": ["team-a"],
            "scoped_permissions": ["tools.execute"], "scoped_server_id": "server-a"}
    monkeypatch.setattr(module, "_get_request_context_or_default", AsyncMock(return_value=(
        path_server, {"X-Context-Forge-Gateway-Id": gateway.id}, user)))
    monkeypatch.setattr(module, "get_db", database)
    monkeypatch.setattr(module, "_check_streamable_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(module.settings, "mcpgateway_direct_proxy_enabled", True)
    direct = AsyncMock()
    monkeypatch.setattr(module.tool_service, "invoke_tool_direct", direct)
    with pytest.raises(PermissionError, match="cannot override gateway routing"):
        await module.call_tool("arbitrary_remote_tool", {})
    direct.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_rpc_resolver_rejects_server_scoped_gateway_hint():
    from mcpgateway.services.tool_service import ToolNotFoundError

    db = MagicMock()
    with pytest.raises(ToolNotFoundError):
        await ToolService()._resolve_tool_for_invocation(db, "arbitrary_remote_tool",
            {"X-Context-Forge-Gateway-Id": "public-proxy"}, "alice@example.com", ["team-a"],
            "server-a", False, False)
    db.execute.assert_not_called()


def test_native_mcp_scope_remains_narrow_and_immutable():
    actor = agentport.caller_from_mcp({"is_authenticated": True, "email": "alice@example.com",
                                      "teams": ["team-a"], "is_admin": True,
                                      "scoped_permissions": ["tools.execute"],
                                      "token_id": "child-token", "exp": 4102444800,
                                      "scoped_server_id": "server-a"}, "server-a")
    assert actor.teams == ("team-a",) and not actor.admin_bypass
    assert actor.permissions == ("tools.execute",) and actor.token_server_id == "server-a"
    assert actor.token_id == "child-token" and actor.expires_at == 4102444800
    with pytest.raises(FrozenInstanceError):
        actor.subject = "bob@example.com"
    assert agentport.caller_from_mcp({"is_authenticated": False}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [None, caller(permissions=("tools.read",)), caller(server_id="server-b"), caller(expires_at=1)])
async def test_local_dispatch_denies_missing_or_narrowed_authority(handlers, actor):
    with pytest.raises(PermissionError):
        await agentport.dispatch(actor, local_payload(), {"text": "hello"})
    handlers.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_dispatch_disabled_or_unknown_operation(monkeypatch, handlers):
    payload = local_payload()
    payload["extension_metadata"][agentport.LOCAL_NAMESPACE]["operation"] = "python.import"
    with pytest.raises(PermissionError):
        await agentport.dispatch(caller(), payload, {})
    monkeypatch.setattr(agentport, "_handlers", MappingProxyType({}))
    with pytest.raises(PermissionError):
        await agentport.dispatch(caller(), local_payload(), {})
    handlers.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_registration_cannot_bypass_operator_gate():
    parsed = ToolCreate(name="submit", integration_type=agentport.LOCAL_INTEGRATION,
                        extensionMetadata={agentport.LOCAL_NAMESPACE: {"operation": "invoke"}})
    with pytest.raises(PermissionError):
        await ToolService().register_tool(MagicMock(), parsed)
    result = ToolService()._process_single_tool_for_bulk(
        parsed, {}, "update", "private", None, "alice@example.com", "alice@example.com",
        None, "import", None, None, None,
    )
    assert result["status"] == "fail"


@pytest.fixture
def pipeline(monkeypatch, handlers):
    import mcpgateway.services.tool_service as module

    service = ToolService()
    payload = local_payload()
    resolved = ResolvedTool(is_direct_proxy=False, tool=None, gateway=None,
                            tool_payload=payload, gateway_payload=None)
    monkeypatch.setattr(service, "_resolve_tool_for_invocation", AsyncMock(return_value=resolved))
    monkeypatch.setattr(module.global_config_cache, "get_passthrough_headers", lambda *a: [])
    manager = MagicMock()
    manager.has_hooks_for.return_value = True
    manager.invoke_hook = AsyncMock(return_value=(PluginResult(), {}))
    monkeypatch.setattr(service, "_get_plugin_manager", AsyncMock(return_value=manager))
    monkeypatch.setattr(module, "is_input_capture_enabled", lambda *a: False)
    monkeypatch.setattr(module, "is_output_capture_enabled", lambda *a: False)
    return service, manager, payload


@pytest.mark.asyncio
async def test_local_native_pipeline_keeps_hooks_and_structured_result(pipeline, handlers):
    service, manager, _ = pipeline
    result = await service.invoke_tool(MagicMock(), "submit", {"text": "hello"},
                                       user_email="alice@example.com", token_teams=["team-a"],
                                       server_id="server-a", caller_context=caller())
    assert result.structured_content == {"job_id": "job-1"}
    assert not result.is_error
    handlers.assert_awaited_once()
    assert [c.args[0] for c in manager.invoke_hook.await_args_list] == [
        ToolHookType.TOOL_PRE_INVOKE, ToolHookType.TOOL_POST_INVOKE]
    assert handlers.await_args.args[0].token_server_id == "server-a"
    assert handlers.await_args.args[1].id == "local-tool"


@pytest.mark.asyncio
async def test_local_schema_is_rechecked_after_plugin_transform(pipeline, handlers):
    service, manager, _ = pipeline
    transformed = ToolPreInvokePayload(name="submit", args={"text": 123})
    manager.invoke_hook.return_value = (PluginResult(modified_payload=transformed), {})
    with pytest.raises(ToolInvocationError):
        await service.invoke_tool(MagicMock(), "submit", {"text": "hello"}, caller_context=caller())
    handlers.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_jobs_do_not_use_generic_proxy_retries(pipeline, handlers):
    service, manager, _ = pipeline
    manager.invoke_hook.return_value = (PluginResult(retry_delay_ms=10), {})
    await service.invoke_tool(MagicMock(), "submit", {"text": "hello"}, caller_context=caller())
    handlers.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_rejects_policy_skip_and_missing_native_context(pipeline, handlers):
    service, _, _ = pipeline
    for options in ({"skip_pre_invoke": True, "caller_context": caller()}, {}):
        with pytest.raises(ToolInvocationError):
            await service.invoke_tool(MagicMock(), "submit", {"text": "hello"}, **options)
    handlers.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_policy_denial_does_not_submit_a_job(pipeline, handlers):
    service, manager, _ = pipeline
    manager.invoke_hook.side_effect = PluginViolationError("Operation denied")
    with pytest.raises(PluginViolationError):
        await service.invoke_tool(MagicMock(), "submit", {"text": "hello"}, caller_context=caller())
    handlers.assert_not_awaited()


@pytest.fixture
def rest_submission(tmp_path, monkeypatch, pipeline):
    from types import SimpleNamespace

    import mcpgateway.db as native_db

    import aog.engine as engine
    from aog.gateway import Catalog
    from aog.models import Invocation, Principal
    from aog.store import Store

    service, manager, payload = pipeline
    payload["input_schema"] = Invocation.model_json_schema()
    payload["output_schema"] = {"type": "object", "required": ["id"]}
    row = SimpleNamespace(id="local-tool", project_id="team-a", visibility="project")
    tool = SimpleNamespace(id=row.id, name=payload["name"], team_id=row.project_id, visibility="team",
                           integration_type=agentport.LOCAL_INTEGRATION, extension_metadata=payload["extension_metadata"])
    native = MagicMock()
    native.__enter__.return_value = native
    native.get.return_value = tool
    monkeypatch.setattr(native_db, "SessionLocal", lambda: native)
    store = Store(f"sqlite:///{tmp_path}/owned.db")
    store.init()
    catalog = Catalog(store)
    catalog.tools = service
    catalog.resource = AsyncMock(return_value=row)
    application = engine.Engine(store, catalog=catalog)
    monkeypatch.setattr(engine, "engine", application)
    actor = Principal(subject="alice@example.com", teams=["team-a"], scopes=["tools.execute"],
                      server_id="server-a", token_server_id="server-a", token_id="native-token",
                      token_use="api", token_expires_at=4102444800, auth_method="jwt")
    yield store, actor, manager
    store.engine.dispose()


@pytest.mark.asyncio
async def test_rest_submission_runs_native_facade_policy_before_creating_job(rest_submission, handlers):
    from sqlalchemy import func, select

    from aog import api
    from aog.models import Invocation
    from aog.store import Run

    store, actor, manager = rest_submission
    manager.invoke_hook.side_effect = PluginViolationError("Facade policy denied")
    with pytest.raises(PluginViolationError):
        await api.invoke_resource("services", "legacy-alias", Invocation(inputs={"text": "request"}), actor)
    handlers.assert_not_awaited()
    with store.tx() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 0


@pytest.mark.asyncio
async def test_rest_submission_preserves_verified_native_caller_scope(rest_submission, handlers):
    from aog import api
    from aog.models import Invocation
    from aog.store import Run

    store, actor, manager = rest_submission

    async def submit(caller, tool, arguments):
        with store.tx() as db:
            db.add(Run(id="submitted-run", owner=caller.subject, project_id="team-a", status="queued",
                       spec={"name": "Accepted", "definition_revision": 1}, inputs=arguments["inputs"], principal=actor.model_dump()))
        return {"id": "submitted-run"}

    handlers.side_effect = submit
    result = await api.invoke_resource("services", "legacy-alias", Invocation(inputs={"text": "request"}), actor)
    assert result["id"] == "submitted-run"
    captured = handlers.await_args.args[0]
    assert captured.subject == actor.subject and captured.teams == ("team-a",)
    assert captured.permissions == ("tools.execute",) and not captured.admin_bypass
    assert (captured.server_id, captured.token_server_id, captured.token_id) == ("server-a", "server-a", "native-token")
    assert captured.token_use == "api" and captured.expires_at == actor.token_expires_at and captured.auth_method == "jwt"
    assert [call.args[0] for call in manager.invoke_hook.await_args_list] == [
        ToolHookType.TOOL_PRE_INVOKE, ToolHookType.TOOL_POST_INVOKE]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cancel", "approve", "agent_permission", "resolve"])
async def test_rest_controls_run_native_policy_before_mutating_jobs(rest_submission, pipeline, handlers, monkeypatch, operation):
    from aog import api
    from aog.integrations.contextforge import delegation
    from aog.models import Decision, Resolution
    from aog.store import Run

    store, actor, manager = rest_submission
    if operation == "resolve":
        actor = actor.model_copy(update={"is_admin": True, "scopes": ["tools.execute", "admin.system_config"]})
    _, _, payload = pipeline
    payload["extension_metadata"][agentport.LOCAL_NAMESPACE]["operation"] = operation
    monkeypatch.setattr(agentport, "_handlers", MappingProxyType({operation: handlers}))
    monkeypatch.setattr(delegation, "ensure_controls", AsyncMock(return_value={operation: "local-tool"}))
    manager.invoke_hook.side_effect = PluginViolationError("Control policy denied")
    with store.tx() as db:
        db.add(Run(id="active-run", owner=actor.subject, status="running", spec={}, inputs={}, principal=actor.model_dump()))
    with pytest.raises(PluginViolationError):
        if operation == "cancel":
            await api.cancel_route("active-run", actor)
        elif operation == "approve":
            await api.approve_route("active-run", "node", Decision(approved=True), actor)
        elif operation == "agent_permission":
            await api.native_permission_route("active-run", "permission", api.NativePermission(option_id="allow"), actor)
        else:
            await api.resolve_step_route("active-run", "node", Resolution(outcome="completed", output={}, external_execution_stopped=True), actor)
    handlers.assert_not_awaited()
    assert store.run("active-run", actor).status == "running"


@pytest.mark.asyncio
async def test_internal_backend_requires_exact_process_grant_and_keeps_hooks(pipeline, handlers):
    service, manager, payload = pipeline
    payload["extension_metadata"][agentport.SERVICE_NAMESPACE] = {"internal": True}
    for grant in (None, "another-tool"):
        with pytest.raises(PermissionError):
            if grant is None:
                await service.invoke_tool(MagicMock(), "submit", {"text": "hello"}, caller_context=caller())
            else:
                with agentport.backend_invocation(grant):
                    await service.invoke_tool(MagicMock(), "submit", {"text": "hello"}, caller_context=caller())
    handlers.assert_not_awaited()
    manager.invoke_hook.assert_not_awaited()
    with agentport.backend_invocation(payload["id"]):
        result = await service.invoke_tool(MagicMock(), "submit", {"text": "hello"}, caller_context=caller())
    assert result.structured_content == {"job_id": "job-1"}
    assert [call.args[0] for call in manager.invoke_hook.await_args_list] == [
        ToolHookType.TOOL_PRE_INVOKE, ToolHookType.TOOL_POST_INVOKE]
    with pytest.raises(PermissionError):
        await service.invoke_tool(MagicMock(), "submit", {"text": "hello"}, caller_context=caller())


@pytest.mark.asyncio
async def test_internal_rest_backend_does_not_repeat_on_plugin_retry(pipeline, monkeypatch):
    import mcpgateway.services.tool_service as module

    service, manager, payload = pipeline
    payload.update(integration_type="REST", url="https://backend.example/convert")
    payload["extension_metadata"][agentport.SERVICE_NAMESPACE] = {"internal": True}
    manager.invoke_hook.return_value = (PluginResult(retry_delay_ms=1), {})
    http = MagicMock()
    http.request = AsyncMock(return_value=httpx.Response(200, json={"job_id": "job-1"},
                                                       request=httpx.Request("POST", payload["url"])))
    http.aclose = AsyncMock()
    factory = MagicMock(return_value=http)
    monkeypatch.setattr(module, "_build_pinned_rest_http_client", factory)
    monkeypatch.setattr(module.SecurityValidator, "validate_url_for_connection_pinning", AsyncMock(return_value={}))
    monkeypatch.setattr(module.settings, "ssrf_protection_enabled", False)
    monkeypatch.setattr(module.settings, "max_tool_retries", 1)
    with agentport.backend_invocation(payload["id"]):
        result = await service.invoke_tool(MagicMock(), "submit", {"text": "hello"})
    assert not result.is_error
    http.request.assert_awaited_once()
    factory.assert_called_once_with(max_attempts=1)
    assert [call.args[0] for call in manager.invoke_hook.await_args_list] == [
        ToolHookType.TOOL_PRE_INVOKE, ToolHookType.TOOL_POST_INVOKE]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["read_error", "http_503"])
async def test_internal_rest_transport_never_replays_lost_or_failed_response(pipeline, monkeypatch, failure):
    import mcpgateway.services.tool_service as module

    service, manager, payload = pipeline
    payload.update(integration_type="REST", url="https://backend.example/convert")
    payload["extension_metadata"][agentport.SERVICE_NAMESPACE] = {"internal": True}
    manager.invoke_hook.return_value = (PluginResult(retry_delay_ms=1), {})
    requests = []

    def response(request):
        requests.append(request)
        if failure == "read_error":
            raise httpx.ReadError("Response lost after accepting the operation", request=request)
        return httpx.Response(503, json={"error": "Operation outcome is unknown"}, request=request)

    def build(*, max_attempts):
        return module.ResilientHttpClient(max_retries=max_attempts, base_backoff=0, jitter_max=0,
                                          client_args={"transport": httpx.MockTransport(response)})

    monkeypatch.setattr(module, "_build_pinned_rest_http_client", build)
    monkeypatch.setattr(module.SecurityValidator, "validate_url_for_connection_pinning", AsyncMock(return_value={}))
    monkeypatch.setattr(module.settings, "ssrf_protection_enabled", False)
    monkeypatch.setattr(module.settings, "max_tool_retries", 1)
    with agentport.backend_invocation(payload["id"]):
        if failure == "read_error":
            with pytest.raises(ToolInvocationError):
                await service.invoke_tool(MagicMock(), "submit", {"text": "hello"})
        else:
            result = await service.invoke_tool(MagicMock(), "submit", {"text": "hello"})
            assert result.is_error
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_native_restart_preserves_owned_private_tools_without_team(tmp_path, monkeypatch):
    from mcpgateway.bootstrap_db import bootstrap_resource_assignments
    from mcpgateway.config import settings
    from mcpgateway.db import Base, EmailTeam, EmailUser, Tool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(f"sqlite:///{tmp_path}/native.db")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "email_auth_enabled", True)
    monkeypatch.setattr(settings, "platform_admin_email", "admin@example.com")
    with Session(engine) as db:
        db.add_all([EmailUser(email="admin@example.com", is_admin=True, is_active=True),
                    EmailUser(email="alice@example.com", is_active=True)])
        db.flush()
        db.add(EmailTeam(id="admin-team", name="Admin", slug="admin-team",
                         created_by="admin@example.com", is_personal=True, is_active=True))
        for identifier, owner in (("owned", "alice@example.com"), ("legacy", None)):
            db.add(Tool(id=identifier, name=identifier, original_name=identifier, custom_name=identifier,
                        custom_name_slug=identifier, integration_type="REST", request_type="POST",
                        url="https://backend.example/convert", owner_email=owner, team_id=None,
                        visibility="private", input_schema={"type": "object"}))
        db.commit()
    with engine.connect() as connection:
        await bootstrap_resource_assignments(connection)
        await bootstrap_resource_assignments(connection)
    with Session(engine) as db:
        owned = db.get(Tool, "owned")
        assert (owned.owner_email, owned.team_id, owned.visibility) == ("alice@example.com", None, "private")
        legacy = db.get(Tool, "legacy")
        assert (legacy.owner_email, legacy.team_id, legacy.visibility) == ("admin@example.com", "admin-team", "public")
    engine.dispose()
