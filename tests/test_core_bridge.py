import json

import httpx
import pytest
from fastapi import HTTPException

from aog import bridge


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("AOG_GATEWAY_BRIDGE_SECRET", "test-only-" + "x" * 48)
    bridge._seen.clear()


def test_signed_bridge_binds_direction_body_and_one_use():
    body, headers = bridge.encode_message("authorize", {"actor": "alice"}, "core")
    with pytest.raises(HTTPException):
        bridge.verify_message(body.replace(b"alice", b"other"), headers, "contextforge")
    with pytest.raises(HTTPException):
        bridge.verify_message(body, headers, "core")
    assert bridge.verify_message(body, headers, "contextforge") == ("authorize", {"actor": "alice"})
    with pytest.raises(HTTPException):
        bridge.verify_message(body, headers, "contextforge")


@pytest.mark.parametrize("reply", ["unsigned", "wrong_request", "valid"])
async def test_connector_response_is_authenticated_and_correlated(monkeypatch, reply):
    calls = []

    async def server(request):
        body = request.content
        assert bridge.verify_message(body, request.headers, "contextforge")[0] == "capabilities"
        calls.append(body)
        if reply == "unsigned":
            return httpx.Response(200, json={"is_admin": True})
        if reply == "wrong_request":
            body = json.dumps({**json.loads(body), "id": "unrelated"}).encode()
        response = bridge.rpc_response(body, {"contract_version": 1}, "contextforge")
        return httpx.Response(response.status_code, content=response.body, headers=response.headers)

    client = httpx.AsyncClient
    monkeypatch.setattr(bridge.httpx, "AsyncClient", lambda **kwargs: client(
        **kwargs, transport=httpx.MockTransport(server)))
    if reply == "valid":
        assert await bridge.rpc_call("http://gateway", "capabilities", {}, "core") == {"contract_version": 1}
    else:
        with pytest.raises(HTTPException):
            await bridge.rpc_call("http://gateway", "capabilities", {}, "core")
    assert len(calls) == 1


async def test_unavailable_mutation_is_not_automatically_retried(monkeypatch):
    calls = []

    async def server(request):
        calls.append(request)
        raise httpx.ReadError("response lost")

    client = httpx.AsyncClient
    monkeypatch.setattr(bridge.httpx, "AsyncClient", lambda **kwargs: client(
        **kwargs, transport=httpx.MockTransport(server)))
    with pytest.raises(HTTPException) as error:
        await bridge.rpc_call("http://gateway", "catalog_invoke", {}, "core")
    assert error.value.status_code == 503 and len(calls) == 1


async def test_public_core_upload_limit_precedes_job_handler_for_chunked_bodies():
    from fastapi import FastAPI

    from aog.http_limits import BodyLimit

    application = FastAPI()
    application.add_middleware(BodyLimit, limit=32)
    calls = []

    @application.post("/invoke")
    async def invoke(value: dict):
        calls.append(value)
        return {"ok": True}

    async def chunks():
        yield b'{"image":"'
        yield b"x" * 64
        yield b'"}'

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://core") as client:
        response = await client.post("/invoke", content=chunks(), headers={"content-type": "application/json"})
        assert response.status_code == 413 and not calls
