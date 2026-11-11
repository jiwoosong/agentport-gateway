"""ContextForge bridge client. This module has no ContextForge Python dependency."""

import base64
import hashlib
import os
from types import SimpleNamespace

from fastapi import HTTPException

from aog.adapters import UncertainExecution
from aog.bridge import rpc_call, verify_message
from aog.models import Principal


def _json(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _resource(value):
    return SimpleNamespace(**value) if value is not None else None


def _principal(value):
    if "team_scope_restricted" not in value:
        raise HTTPException(502, "Gateway connector omitted the verified team scope")
    return Principal.model_validate(value)


class ContextForgeBackend:
    def __init__(self):
        self.url = os.environ.get("AOG_GATEWAY_URL", "http://127.0.0.1:4444")

    async def call(self, operation, arguments=None, timeout=120):
        return await rpc_call(self.url, operation, arguments or {}, "core", timeout=timeout)

    async def authenticate(self, request):
        context = request.headers.get("x-agentport-proxy-context")
        signature = request.headers.get("x-agentport-proxy-signature")
        if context is not None or signature is not None:
            try:
                envelope = base64.b64decode(context or "", validate=True)
                operation, args = verify_message(envelope, {"X-AgentPort-Bridge": signature or ""}, "core")
                if (operation != "proxy_auth" or args.get("method") != request.method
                    or args.get("path") != request.url.path
                    or args.get("query", []) != [list(pair) for pair in request.query_params.multi_items()]
                    or args.get("body_sha256") != hashlib.sha256(await request.body()).hexdigest()):
                    raise ValueError("Gateway proxy context does not match this request")
                return _principal(args["actor"])
            except (ValueError, KeyError, TypeError):
                raise HTTPException(401, "Invalid gateway proxy context") from None
        value = await self.call("authenticate", {"authorization": request.headers.get("authorization", ""),
            "method": request.method, "path": request.url.path,
            "client_ip": request.client.host if request.client else None})
        return _principal(value)

    async def authorize(self, actor, permission):
        return _principal(await self.call("authorize", {"actor": _json(actor), "permission": permission}))

    async def encrypt_secret(self, value):
        return await self.call("encrypt", {"value": value})

    async def decrypt_secret(self, value):
        return await self.call("decrypt", {"value": value})

    async def delegate(self, **arguments):
        return await self.call("delegate", {key: _json(value) for key, value in arguments.items()})

    async def controls(self, actor, store=None):
        return await self.call("controls", {"actor": _json(actor)})

    async def worker_public_key(self):
        return await self.call("worker_public_key")

    async def capabilities(self):
        value = await self.call("capabilities")
        if value.get("contract_version") != 1:
            raise RuntimeError("Gateway connector contract version is incompatible")
        return value

    async def prepare(self):
        await self.capabilities()
        return await self.call("prepare")

    async def migrate(self):
        return await self.call("migrate", timeout=600)

    def catalog(self, store):
        return RemoteCatalog(self, store)


class RemoteCatalog:
    def __init__(self, backend, store):
        self.backend, self.store = backend, store

    async def resource(self, rid, actor, write=False):
        return _resource(await self.backend.call("catalog_resource", {"rid": rid, "actor": _json(actor), "write": write}))

    async def list_resources(self, actor):
        return [_resource(value) for value in await self.backend.call("catalog_list", {"actor": _json(actor)})]

    async def save(self, document, actor, rid=None, migration_source_id=None):
        return _resource(await self.backend.call("catalog_save", {"document": document, "actor": _json(actor),
            "rid": rid, "migration_source_id": migration_source_id}))

    async def publish_resource(self, row, actor):
        value = {field: getattr(row, field) for field in ("id", "kind", "owner", "project_id", "visibility", "revision", "document")}
        return await self.backend.call("catalog_publish", {"row": value, "actor": _json(actor)})

    async def submit(self, kind, rid, request, actor):
        value = await self.backend.call("catalog_submit", {"kind": kind, "rid": rid,
            "request": _json(request), "actor": _json(actor)})
        return self.store.run(value["id"], actor)

    async def control(self, operation, arguments, actor):
        return await self.backend.call("catalog_control", {"operation": operation,
            "arguments": arguments, "actor": _json(actor)})

    async def invoke(self, service_id, service, inputs, key, actor, credential=None):
        try:
            return await self.backend.call("catalog_invoke", {"service_id": service_id, "service": _json(service),
                "inputs": inputs, "key": key, "actor": actor, "credential": credential},
                timeout=service.timeout_seconds + 30)
        except Exception as exc:
            raise UncertainExecution("Gateway execution requires reconciliation; it was not retried") from exc

    async def worker_control(self, service_id, service, key, actor, payload, credential=None, operator=False):
        return await self.backend.call("catalog_worker_control", {"service_id": service_id, "service": _json(service),
            "key": key, "actor": actor, "payload": payload, "credential": credential, "operator": operator})

    async def migrated_tool(self, source_id, owner):
        return _resource(await self.backend.call("catalog_migrated_tool", {"source_id": source_id, "owner": owner}))
