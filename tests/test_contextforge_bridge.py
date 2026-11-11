"""The optional gateway component authenticates both directions and owns no job engine."""

import base64
import hashlib
import json
import secrets
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from mcpgateway.agentport import AuthorizedInvocation, LocalTool

from aog import bridge
from aog.integrations.contextforge import app as component
from aog.integrations.contextforge import delegation, native
from aog.models import Principal, Service
from aog.store import Resource, Run, Step, Store


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("AOG_GATEWAY_BRIDGE_SECRET", "disposable-component-test-secret-" + "9" * 32)
    bridge._seen.clear()
    store = Store(f"sqlite:///{tmp_path}/core.db")
    store.init()
    catalog = SimpleNamespace(store=store, save=AsyncMock(), worker_control=AsyncMock(return_value={"status": "completed"}))
    application = FastAPI()
    application.state.catalog = catalog
    application.include_router(component.build_router())
    yield application, catalog
    native.bind_catalog(None)
    store.engine.dispose()
    bridge._seen.clear()


async def request_rpc(application, operation, arguments):
    body, headers = bridge.encode_message(operation, arguments, "core")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://gateway") as client:
        response = await client.post(component.RPC_PATH, content=body, headers=headers)
    reply_operation, reply = bridge.verify_message(response.content, response.headers, "core")
    assert reply_operation == "response" and reply["request_id"] == json.loads(body)["id"]
    assert reply["status"] == response.status_code
    return response.status_code, reply["value"]


async def test_component_requires_signed_calls_and_returns_correlated_signed_results(boundary):
    application, _ = boundary
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://gateway") as client:
        assert (await client.post(component.RPC_PATH, json={"operation": "capabilities", "arguments": {}})).status_code == 401
        body, headers = bridge.encode_message("capabilities", {}, "core")
        assert (await client.post(component.RPC_PATH, content=body + b" ", headers=headers)).status_code == 401
    status, capabilities = await request_rpc(application, "capabilities", {})
    assert status == 200 and capabilities["contract_version"] == 1 and capabilities["patched"]
    assert capabilities["source_commit"] == component.SOURCE_COMMIT
    status, result = await request_rpc(application, "__import__", {})
    assert status == 404 and result == {"detail": "Gateway bridge operation is not registered"}


async def test_authentication_reconstructs_fresh_native_request_not_supplied_identity(boundary, monkeypatch):
    application, _ = boundary
    verified = Principal(subject="alice@example.com", teams=["project"], scopes=["tools.execute"],
                         token_id="native-jti", token_use="api", token_expires_at=4102444800)
    authenticate = AsyncMock(return_value=verified)
    monkeypatch.setattr(native, "authenticate", authenticate)
    status, result = await request_rpc(application, "authenticate", {"authorization": "Bearer original-user-token",
        "method": "POST", "path": "/orchestration/v1/services/tool/invoke", "client_ip": "198.51.100.42",
        "actor": {"subject": "mallory"}})
    assert status == 200 and result == verified.model_dump(mode="json")
    request = authenticate.await_args.args[0]
    assert request.app is application and request.method == "POST"
    assert request.url.path == "/orchestration/v1/services/tool/invoke"
    assert dict(request.headers) == {"authorization": "Bearer original-user-token"}
    assert request.client.host == "198.51.100.42"


async def test_native_scope_cap_denies_install_before_calling_catalog(boundary):
    application, catalog = boundary
    actor = Principal(subject="operator@example.com", is_admin=True, scopes=["tools.execute"])
    status, result = await request_rpc(application, "catalog_save", {"document": {}, "actor": actor.model_dump()})
    assert status == 403 and "scope" in result["detail"]
    catalog.save.assert_not_awaited()


async def test_native_local_callback_carries_verified_caller_over_signed_core_channel(boundary, monkeypatch):
    _, catalog = boundary
    actor = Principal(subject="alice@example.com", teams=["project"], scopes=["tools.execute"],
        token_id="native-jti", token_use="api", token_expires_at=4102444800)
    monkeypatch.setattr(delegation, "resolve", AsyncMock(return_value=actor))
    monkeypatch.setattr(native, "authorize", AsyncMock(return_value=actor))
    native.bind_catalog(catalog)
    core = FastAPI()
    captured = {}

    @core.post("/internal/agentport/v1/dispatch")
    async def dispatch(request: component.Request):
        body = await bridge.read_message(request)
        operation, arguments = bridge.verify_message(body, request.headers, "core")
        assert operation == "dispatch"
        captured.update(arguments)
        return bridge.rpc_response(body, {"id": "accepted-run"}, "core")

    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.ASGITransport(app=core), **kwargs))
    caller = AuthorizedInvocation(subject=actor.subject, teams=("project",), permissions=("tools.execute",),
        token_id=actor.token_id, token_use="api", expires_at=actor.token_expires_at)
    tool = LocalTool(id="tool", name="submit", team_id="project", owner_email=actor.subject,
        visibility="team", extension_metadata={native.LOCAL_METADATA: {"operation": "invoke"}})
    result = await native.local_dispatch(caller, tool, {"inputs": {"actor": "untrusted input"}})
    assert result == {"id": "accepted-run"}
    assert captured["actor"] == actor.model_dump(mode="json")
    assert captured["tool"]["id"] == "tool" and captured["arguments"]["inputs"]["actor"] == "untrusted input"


async def test_expired_job_owner_can_cleanup_only_recorded_worker_binding(boundary, monkeypatch):
    application, catalog = boundary
    actor = Principal(subject="alice@example.com", token_use="api", token_id="expired", token_expires_at=1)
    with catalog.store.tx() as db:
        db.add(Run(id="run", owner=actor.subject, status="needs_attention", inputs={}, principal=actor.model_dump(),
                   spec={"snapshots": {"tool": {"kind": "service"}}}))
        db.flush()
        db.add(Step(id="run:node", run_id="run", node_id="node", status="needs_attention",
                    output={"service_id": "tool", "worker": {"job_id": "native-job"}}))
    authorize = AsyncMock(side_effect=AssertionError("Cleanup must not require the expired owner token"))
    monkeypatch.setattr(native, "authorize", authorize)
    args = {"actor": actor.model_dump(), "service_id": "tool", "key": "run:node", "operator": True,
            "service": Service(name="Worker", endpoint="http://worker/invoke").model_dump(),
            "payload": {"operation": "resolve", "job_id": "native-job"}}
    status, result = await request_rpc(application, "catalog_worker_control", args)
    assert status == 200 and result == {"status": "completed"}
    status, _ = await request_rpc(application, "catalog_worker_control", {**args,
        "payload": {"operation": "cancel", "job_id": "another-job"}})
    assert status == 403
    status, _ = await request_rpc(application, "catalog_worker_control", {**args,
        "actor": actor.model_copy(update={"subject": "other@example.com"}).model_dump()})
    assert status == 403
    catalog.worker_control.assert_awaited_once()
    authorize.assert_not_awaited()


async def test_cleanup_resolves_completed_service_aliases_without_replaying_work(boundary):
    application, catalog = boundary
    actor = Principal(subject="alice@example.com", token_expires_at=1)
    with catalog.store.tx() as db:
        db.add(Resource(id="legacy-service", kind="service_alias", owner=actor.subject,
            document={"phase": "completed", "tool_id": "native-tool"}))
        db.add(Run(id="legacy-run", owner=actor.subject, status="needs_attention", inputs={}, principal=actor.model_dump(),
            spec={"snapshots": {"legacy-service": {"kind": "service"}}}))
        db.flush()
        db.add(Step(id="legacy-run:node", run_id="legacy-run", node_id="node", status="needs_attention",
            output={"service_id": "legacy-service", "worker": {"job_id": "old-job"}}))
    status, _ = await request_rpc(application, "catalog_worker_control", {"actor": actor.model_dump(),
        "service_id": "native-tool", "key": "legacy-run:node", "operator": True,
        "service": Service(name="Worker", endpoint="http://worker/invoke").model_dump(),
        "payload": {"operation": "resolve", "job_id": "old-job"}})
    assert status == 200 and catalog.worker_control.await_args.args[0] == "native-tool"


async def test_worker_public_key_is_gateway_owned_and_contains_no_private_material(boundary, monkeypatch):
    from aog import identity

    application, _ = boundary
    monkeypatch.setattr(identity, "public_key_base64", lambda: "public-key-only")
    status, result = await request_rpc(application, "worker_public_key", {})
    assert status == 200 and result == {"algorithm": "EdDSA", "public_key": "public-key-only"}


async def test_component_lifespan_starts_only_catalog_state(boundary, monkeypatch):
    application, catalog = boundary
    store = SimpleNamespace(init=MagicMock(), engine=SimpleNamespace(dispose=MagicMock()))
    monkeypatch.setattr(component, "Store", MagicMock(return_value=store))
    monkeypatch.setattr(native, "Catalog", MagicMock(return_value=catalog))
    async with component.lifespan(application):
        assert application.state.catalog is catalog
        assert native._active_catalog is catalog
        store.init.assert_called_once()
    assert native._active_catalog is None
    store.engine.dispose.assert_called_once()


async def test_compatibility_proxy_forwards_only_original_auth_and_content_type(boundary, monkeypatch):
    application, _ = boundary
    core = FastAPI()
    actor = Principal(subject="alice@example.com", scopes=["tools.read"])
    monkeypatch.setattr(native, "authenticate", AsyncMock(return_value=actor))

    @application.middleware("http")
    async def native_scoped(request, call_next):
        request.state._token_scoping_done = True
        return await call_next(request)

    @core.post("/orchestration/v1/services/tool/invoke")
    async def invoke(request: component.Request):
        assert request.headers["authorization"] == "Bearer original-user-token"
        assert request.headers["content-type"] == "application/json"
        assert "x-actor-admin" not in request.headers and "cookie" not in request.headers
        assert request.query_params["mode"] == "one"
        body = await request.body()
        envelope = base64.b64decode(request.headers["x-agentport-proxy-context"], validate=True)
        operation, claims = bridge.verify_message(envelope,
            {"X-AgentPort-Bridge": request.headers["x-agentport-proxy-signature"]}, "core")
        assert operation == "proxy_auth" and claims["actor"] == actor.model_dump(mode="json")
        assert claims["method"] == request.method and claims["path"] == request.url.path
        assert claims["query"] == [list(pair) for pair in request.query_params.multi_items()]
        assert claims["body_sha256"] == hashlib.sha256(body).hexdigest()
        return {"received": await request.json()}

    client_type = httpx.AsyncClient
    async with client_type(transport=httpx.ASGITransport(app=application), base_url="http://gateway") as external:
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
            transport=httpx.ASGITransport(app=core), **kwargs))
        response = await external.post("/orchestration/v1/services/tool/invoke?mode=one",
            headers={"Authorization": "Bearer original-user-token", "X-Actor-Admin": "true", "Cookie": "not-forwarded=1",
                "X-AgentPort-Proxy-Context": "forged", "X-AgentPort-Proxy-Signature": "forged"},
            json={"inputs": {"text": "original"}})
    assert response.status_code == 200 and response.json() == {"received": {"inputs": {"text": "original"}}}


async def test_proxy_does_not_mint_identity_without_native_scope_middleware(boundary, monkeypatch):
    application, _ = boundary
    authenticate = AsyncMock()
    monkeypatch.setattr(native, "authenticate", authenticate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://gateway") as client:
        response = await client.get("/orchestration/v1/me", headers={"Authorization": "Bearer token",
            "X-Token-Scoping-Done": "true", "X-AgentPort-Proxy-Context": "forged"})
    assert response.status_code == 503
    authenticate.assert_not_awaited()


@pytest.fixture
def native_signing(monkeypatch):
    from mcpgateway.config import settings
    from mcpgateway.utils.jwt_config_helper import clear_jwt_caches
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "jwt_secret_key", SecretStr(secrets.token_urlsafe(48)))
    monkeypatch.setattr(settings, "jwt_algorithm", "HS256")
    monkeypatch.setattr(settings, "derive_key_per_environment", False)
    clear_jwt_caches()
    yield
    clear_jwt_caches()


@pytest.mark.parametrize("address,expected", [("198.51.100.42", 200), ("203.0.113.11", 403), (None, 403)])
async def test_direct_core_authentication_applies_native_ip_constraints(boundary, native_signing, monkeypatch, address, expected):
    from mcpgateway.utils.create_jwt_token import create_jwt_token

    application, _ = boundary
    actor = Principal(subject="alice@example.com", scopes=["tools.execute"])
    authenticate = AsyncMock(return_value=actor)
    monkeypatch.setattr(native, "authenticate", authenticate)
    token = await create_jwt_token({"sub": actor.subject, "jti": "restricted-jti", "token_use": "api", "is_admin": True},
        user_data={"email": actor.subject, "is_admin": True}, teams=None,
        scopes={"permissions": ["tools.execute"], "ip_restrictions": ["198.51.100.0/24"]})
    status, _ = await request_rpc(application, "authenticate", {"authorization": "Bearer " + token,
        "method": "POST", "path": "/orchestration/v1/services/tool/invoke", "client_ip": address})
    assert status == expected
    assert authenticate.await_count == (1 if expected == 200 else 0)


@pytest.mark.parametrize("scopes,path", [
    ({"permissions": ["tools.read"]}, "/orchestration/v1/services/tool/invoke"),
    ({"permissions": ["tools.execute"]}, "/orchestration/v1/future-unregistered"),
    ({"permissions": ["*"], "server_id": "one-server"}, "/orchestration/v1/services/tool/invoke"),
    ({"permissions": ["tools.execute"], "time_restrictions": {"weekdays_only": True}}, "/orchestration/v1/services/tool/invoke"),
])
async def test_direct_core_authentication_keeps_native_scope_server_time_and_default_denials(boundary, native_signing, monkeypatch, scopes, path):
    from datetime import datetime, timezone

    from mcpgateway.middleware import token_scoping
    from mcpgateway.utils.create_jwt_token import create_jwt_token

    application, _ = boundary
    authenticate = AsyncMock(return_value=Principal(subject="alice@example.com"))
    monkeypatch.setattr(native, "authenticate", authenticate)

    class Sunday(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 15, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(token_scoping, "datetime", Sunday)
    token = await create_jwt_token({"sub": "alice@example.com", "jti": "scope-jti", "token_use": "api", "is_admin": True},
        user_data={"email": "alice@example.com", "is_admin": True}, teams=None, scopes=scopes)
    status, _ = await request_rpc(application, "authenticate", {"authorization": "Bearer " + token,
        "method": "POST", "path": path, "client_ip": "198.51.100.42"})
    assert status == 403
    authenticate.assert_not_awaited()


async def test_direct_core_authentication_records_one_native_usage_attempt(boundary, native_signing, monkeypatch):
    from contextlib import nullcontext

    from mcpgateway.middleware import token_usage_middleware
    from mcpgateway.utils.create_jwt_token import create_jwt_token

    application, _ = boundary
    actor = Principal(subject="alice@example.com", scopes=["tools.execute"], token_id="usage-jti", token_use="api")

    async def authenticate(request):
        request.state.auth_method = "api_token"
        request.state.jti = actor.token_id
        request.state.user_email = actor.subject
        return actor

    service = SimpleNamespace(log_token_usage=AsyncMock())
    monkeypatch.setattr(native, "authenticate", authenticate)
    monkeypatch.setattr(token_usage_middleware, "fresh_db_session", lambda: nullcontext(MagicMock()))
    monkeypatch.setattr(token_usage_middleware, "TokenCatalogService", lambda db: service)
    token = await create_jwt_token({"sub": actor.subject, "jti": actor.token_id, "token_use": "api", "is_admin": True},
        user_data={"email": actor.subject, "is_admin": True}, teams=None, scopes={"permissions": ["tools.execute"]})
    status, _ = await request_rpc(application, "authenticate", {"authorization": "Bearer " + token,
        "method": "POST", "path": "/orchestration/v1/services/tool/invoke", "client_ip": "198.51.100.42"})
    assert status == 200
    service.log_token_usage.assert_awaited_once()
    usage = service.log_token_usage.await_args.kwargs
    assert usage["jti"] == actor.token_id and usage["ip_address"] == "198.51.100.42"
    assert usage["method"] == "POST" and usage["endpoint"] == "/orchestration/v1/services/tool/invoke"
