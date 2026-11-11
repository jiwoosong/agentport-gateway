"""Native API token scopes cover only the known AgentPort REST routes."""

from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from mcpgateway.middleware import token_scoping
from starlette.middleware.base import BaseHTTPMiddleware

from aog import api
from aog.models import Principal


@pytest.mark.parametrize("method,path,permission", [
    *[("GET", path, "tools.read") for path in (
        "/me", "/worker-public-key", "/harnesses", "/services", "/agents", "/workflows",
        "/runtimes", "/runtimes/runtime-a", "/providers", "/providers/provider-a",
        "/runs", "/runs/run-a", "/sessions", "/credentials", "/artifacts/artifact-a",
        "/services/service-a/export", "/agents/agent-a/export", "/workflows/workflow-a/export",
        "/runtimes/runtime-a/export", "/services/service-a/runtime",
    )],
    *[("POST", path, "tools.execute") for path in (
        "/services/service-a/invoke", "/agents/agent-a/invoke", "/workflows/workflow-a/invoke",
        "/runs/run-a/cancel", "/runs/run-a/permissions/permission-a",
        "/runs/run-a/steps/step-a/approve", "/runs/run-a/steps/step-a/resolve",
        "/agents", "/workflows", "/providers", "/credentials", "/credentials/native", "/artifacts",
    )],
    *[("PUT", path, "tools.execute") for path in (
        "/agents/agent-a", "/workflows/workflow-a", "/providers/provider-a",
    )],
    *[("POST", path, "admin.system_config") for path in (
        "/services", "/runtimes", "/services/service-a/operations/start",
        "/services/service-a/operations/stop", "/services/service-a/operations/update",
        "/runtimes/runtime-a/operations/start", "/runtimes/runtime-a/operations/stop",
        "/runtimes/runtime-a/operations/update", "/services/service-a/releases/release-a/deploy",
        "/runtimes/runtime-a/releases/release-a/deploy", "/services/service-a/reconcile",
        "/runtimes/runtime-a/reconcile",
    )],
    ("PUT", "/services/service-a", "admin.system_config"),
    ("PUT", "/runtimes/runtime-a", "admin.system_config"),
])
def test_known_rest_routes_require_their_native_permission(method, path, permission):
    middleware = token_scoping.TokenScopingMiddleware()
    path = "/orchestration/v1" + path
    for candidate in (path, path + "/"):
        assert middleware._check_permission_restrictions(candidate, method, [permission])
        assert not middleware._check_permission_restrictions(candidate, method, ["tokens.read"])


@pytest.mark.parametrize("method,path", [
    ("GET", "/future"),
    ("GET", "/services/service-a"),
    ("GET", "/services/service-a/export/future"),
    ("GET", "/runs/run-a/future"),
    ("POST", "/services/service-a/invoke/future"),
    ("POST", "/services/service-a/operations/install"),
    ("POST", "/runtimes/runtime-a/operations/install"),
    ("POST", "/runs/run-a/steps/step-a/future"),
    ("POST", "/providers/provider-a/invoke"),
    ("PUT", "/credentials/credential-a"),
    ("DELETE", "/services/service-a"),
])
def test_unknown_rest_routes_stay_denied(method, path):
    middleware = token_scoping.TokenScopingMiddleware()
    assert not middleware._check_permission_restrictions(
        "/orchestration/v1" + path, method, ["tools.*", "admin.*"]
    )


@pytest.mark.parametrize("permissions,server_id,path,expected", [
    (["tools.read", "tools.execute"], None, "/services/service-a/invoke", 202),
    (["tools.read", "tools.execute"], None, "/agents/agent-a/invoke", 202),
    (["tools.read", "tools.execute"], None, "/workflows/workflow-a/invoke", 202),
    (["tools.read"], None, "/services/service-a/invoke", 403),
    (["tools.execute"], None, "/services", 403),
    (["tools.execute"], None, "/runtimes", 403),
    (["tools.execute"], None, "/services/service-a/operations/start", 403),
    (["tools.read", "tools.execute"], "server-a", "/services/service-a/invoke", 403),
    (["*"], "server-a", "/agents/agent-a/invoke", 403),
    (["tools.*", "admin.*"], None, "/future/resource/invoke", 403),
])
async def test_native_api_token_middleware_gates_rest_invocation(
    monkeypatch, permissions, server_id, path, expected
):
    payload = {
        "sub": "alice@example.com", "token_use": "api", "teams": [],
        "scopes": {"permissions": permissions, "server_id": server_id},
    }
    verify = AsyncMock(return_value=payload)
    monkeypatch.setattr(token_scoping, "verify_jwt_token_cached", verify)
    run = SimpleNamespace(
        id="run-a", status="queued", created_at="2026-11-11T00:00:00Z", result={},
        spec={"name": "Invocation", "definition_revision": "revision-a"},
    )
    submit = AsyncMock(return_value=run)
    monkeypatch.setattr(api, "current", lambda: SimpleNamespace(catalog=SimpleNamespace(submit=submit)))

    async def principal():
        return Principal(subject="alice@example.com", scopes=permissions)

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.operator] = principal
    app.add_middleware(BaseHTTPMiddleware, dispatch=token_scoping.TokenScopingMiddleware())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        response = await client.post(
            "/orchestration/v1" + path, json={}, headers={"Authorization": "Bearer scoped-api-token"}
        )
    assert response.status_code == expected, response.text
    verify.assert_awaited_once_with("scoped-api-token", ANY)
    if expected == 202:
        kind, resource_id, invocation, actor = submit.await_args.args
        assert kind == {"services": "service", "agents": "agent", "workflows": "workflow"}[path.split("/")[1]]
        assert resource_id == path.split("/")[2] and invocation.inputs == {}
        assert actor.subject == "alice@example.com"
        assert response.json()["id"] == "run-a"
    else:
        submit.assert_not_awaited()


def test_upstream_scope_mappings_and_inheritance_stay_intact():
    middleware = token_scoping.TokenScopingMiddleware()
    assert middleware._check_permission_restrictions("/tools", "GET", ["tools.read"])
    assert not middleware._check_permission_restrictions("/tools", "POST", ["tools.execute"])
    assert middleware._check_permission_restrictions("/orchestration/v1/future", "GET", [])
    assert middleware._check_permission_restrictions("/orchestration/v1/future", "GET", ["*"])
