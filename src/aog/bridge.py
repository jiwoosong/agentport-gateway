"""Authenticated, bounded messages between Core and a trusted gateway connector."""

import hashlib
import json
import os
import time
import uuid

import httpx
import jwt
from fastapi import HTTPException, Request, Response

MAX_BODY = 32 * 1024 * 1024
_seen: dict[tuple[str, str], int] = {}


def _secret():
    secret = os.environ.get("AOG_GATEWAY_BRIDGE_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("Set AOG_GATEWAY_BRIDGE_SECRET to an operator-generated random secret of at least 32 characters")
    return secret


def encode_message(operation, arguments, sender):
    if sender not in {"core", "contextforge"}:
        raise ValueError("Unknown bridge endpoint")
    body = json.dumps({"id": uuid.uuid4().hex, "operation": operation, "arguments": arguments},
        ensure_ascii=False, separators=(",", ":")).encode()
    if len(body) > MAX_BODY:
        raise HTTPException(413, "Bridge message exceeds 32 MiB")
    receiver = "contextforge" if sender == "core" else "core"
    now = int(time.time())
    token = jwt.encode({"iss": sender, "aud": receiver, "iat": now, "exp": now + 30,
        "jti": uuid.uuid4().hex, "sha256": hashlib.sha256(body).hexdigest()}, _secret(), algorithm="HS256")
    return body, {"Content-Type": "application/json", "X-AgentPort-Bridge": token}


def verify_message(body, headers, receiver):
    if receiver not in {"core", "contextforge"} or len(body) > MAX_BODY:
        raise HTTPException(401, "Invalid bridge message")
    sender = "contextforge" if receiver == "core" else "core"
    try:
        token = next(value for name, value in headers.items() if name.lower() == "x-agentport-bridge")
        claims = jwt.decode(token, _secret(), algorithms=["HS256"], issuer=sender, audience=receiver,
            options={"require": ["iss", "aud", "iat", "exp", "jti", "sha256"]})
        if claims["exp"] - claims["iat"] > 30 or claims["sha256"] != hashlib.sha256(body).hexdigest():
            raise ValueError("Invalid message binding")
        value = json.loads(body)
        if set(value) != {"id", "operation", "arguments"} or not isinstance(value["id"], str):
            raise ValueError("Invalid envelope")
        if not isinstance(value["operation"], str) or not isinstance(value["arguments"], dict):
            raise ValueError("Invalid operation")
        now = int(time.time())
        for key in [key for key, expiry in _seen.items() if expiry <= now]:
            del _seen[key]
        key = (receiver, claims["jti"])
        if key in _seen or len(_seen) >= 10000:
            raise ValueError("Replayed or overloaded bridge")
        _seen[key] = claims["exp"]
        return value["operation"], value["arguments"]
    except (StopIteration, ValueError, KeyError, TypeError, jwt.PyJWTError):
        raise HTTPException(401, "Invalid bridge message") from None


async def read_message(request: Request):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            raise HTTPException(413, "Bridge message exceeds 32 MiB")
    return bytes(body)


def rpc_response(request_body, value, sender, status=200):
    body, headers = encode_message("response", {
        "request_id": json.loads(request_body)["id"], "status": status, "value": value}, sender)
    return Response(body, status_code=status, headers=headers)


async def rpc_call(url, operation, args, sender, timeout=120, path="/internal/agentport/v1/rpc"):
    body, headers = encode_message(operation, args, sender)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        try:
            response = await client.post(url.rstrip("/") + path, content=body, headers=headers)
        except httpx.HTTPError:
            raise HTTPException(503, "Gateway connector response unavailable; no automatic retry was attempted") from None
    # An unauthenticated proxy/error page is never an authority on identity or results.
    received_operation, result = verify_message(response.content, response.headers, sender)
    if received_operation != "response" or result.get("request_id") != json.loads(body)["id"]:
        raise HTTPException(502, "Gateway response does not match the request")
    status = result.get("status", 502)
    if not isinstance(status, int) or status >= 400:
        value = result.get("value")
        detail = value.get("detail", "Gateway operation failed") if isinstance(value, dict) else "Gateway operation failed"
        raise HTTPException(status if isinstance(status, int) and 400 <= status <= 599 else 502, detail)
    return result.get("value")
