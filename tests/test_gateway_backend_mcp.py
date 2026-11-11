"""Managed MCP backends use the native gateway and reject direct owner calls."""

import asyncio
import json
import os
import secrets
import subprocess
import sys

import httpx
import pytest
from test_http_integration import ROOT, integration_password, poll, port, wait

pytestmark = pytest.mark.skipif(os.environ.get("AOG_INTEGRATION") != "1", reason="Opt-in real HTTP integration")


def test_managed_mcp_backend_native_registration_update_and_dispatch(tmp_path):
    gateway_port, core_port, backend_port = port(), port(), port()
    password = integration_password()
    backend = tmp_path / "backend.py"
    backend.write_text('''from mcp.server.mcpserver import MCPServer
from starlette.responses import JSONResponse

server = MCPServer("managed-backend", log_level="ERROR")
calls = 0

@server.tool()
def remote_echo(text: str) -> dict:
    global calls
    calls += 1
    return {"text": text.upper()}

async def health(request):
    return JSONResponse({"ok": True, "invocations": calls})

app = server.streamable_http_app(stateless_http=True, json_response=True)
app.add_route("/health", health)
''')
    env = {**os.environ,
        "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "upstream/contextforge"), str(ROOT), str(tmp_path))),
        "JWT_SECRET_KEY": secrets.token_urlsafe(48), "AUTH_ENCRYPTION_SECRET": secrets.token_urlsafe(48),
        "PLATFORM_ADMIN_EMAIL": "admin@example.com", "PLATFORM_ADMIN_PASSWORD": password,
        "DEFAULT_USER_PASSWORD": password, "BASIC_AUTH_PASSWORD": password,
        "AUTH_REQUIRED": "true", "MCP_CLIENT_AUTH_ENABLED": "true", "EMAIL_AUTH_ENABLED": "true",
        "MCPGATEWAY_UI_ENABLED": "true", "MCPGATEWAY_ADMIN_API_ENABLED": "true",
        "DATABASE_URL": f"sqlite:///{tmp_path}/core.db", "AOG_DATABASE_URL": f"sqlite:///{tmp_path}/aog.db",
        "DBOS_SYSTEM_DATABASE_URL": f"sqlite:///{tmp_path}/dbos.db", "LOG_LEVEL": "ERROR",
        "PASSWORD_CHANGE_ENFORCEMENT_ENABLED": "false", "SSRF_ALLOW_LOCALHOST": "true",
        "SSRF_ALLOW_PRIVATE_NETWORKS": "true", "AOG_INTERNAL_GATEWAY_URL": f"http://127.0.0.1:{gateway_port}",
        "AOG_GATEWAY_URL": f"http://127.0.0.1:{gateway_port}", "AOG_CORE_URL": f"http://127.0.0.1:{core_port}",
        "AOG_GATEWAY_BRIDGE_SECRET": secrets.token_urlsafe(48),
    }
    processes = []
    with (tmp_path / "gateway.log").open("w+") as log:
        try:
            for app, number in (("aog.integrations.contextforge.app:app", gateway_port), ("backend:app", backend_port)):
                processes.append(subprocess.Popen([sys.executable, "-m", "uvicorn", app,
                    "--port", str(number), "--log-level", "warning"], cwd=tmp_path, env=env,
                    stdout=log, stderr=log))
            with httpx.Client(base_url=f"http://127.0.0.1:{gateway_port}", timeout=15, trust_env=False) as client, \
                 httpx.Client(base_url=f"http://127.0.0.1:{backend_port}", timeout=15, trust_env=False) as remote:
                wait(client)
                wait(remote)
                processes.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "aog.app:app",
                    "--port", str(core_port), "--log-level", "warning"], cwd=tmp_path, env=env,
                    stdout=log, stderr=log))
                with httpx.Client(base_url=f"http://127.0.0.1:{core_port}", trust_env=False) as core:
                    wait(core)
                login = client.post("/auth/email/login", json={"email": "admin@example.com", "password": password})
                assert login.status_code == 200, login.text
                token = login.json()["access_token"]
                client.headers["Authorization"] = "Bearer " + token
                client.cookies.clear()
                definition = {"name": "Managed echo", "transport": "mcp", "tool_name": "remote_echo",
                    "endpoint": f"http://127.0.0.1:{backend_port}/mcp", "lifecycle": "manual", "idempotent": True,
                    "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                    "output_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}
                registered = client.post("/orchestration/v1/services", json=definition)
                assert registered.status_code == 201, registered.text
                sid = registered.json()["id"]
                edited = client.put(f"/orchestration/v1/services/{sid}", json={**definition, "name": "Managed echo updated"})
                assert edited.status_code == 200, edited.text
                facade = client.get(f"/v1/tools/{sid}").json()
                backend_id = facade["extensionMetadata"]["io.agentport/service"]["backend_tool_id"]
                native_backend = client.get(f"/v1/tools/{backend_id}").json()
                assert native_backend["originalName"] == "remote_echo"
                assert native_backend["name"] != "remote-echo"
                assert native_backend["integrationType"] == "MCP"
                assert native_backend["requestType"] == "STREAMABLEHTTP"
                second_registration = client.post("/orchestration/v1/services", json={**definition, "name": "Second binding"})
                assert second_registration.status_code == 201, second_registration.text
                second_sid = second_registration.json()["id"]
                second_facade = client.get(f"/v1/tools/{second_sid}").json()
                second_backend_id = second_facade["extensionMetadata"]["io.agentport/service"]["backend_tool_id"]
                second_backend = client.get(f"/v1/tools/{second_backend_id}").json()
                assert second_backend["originalName"] == "remote_echo"
                assert second_backend["name"] != native_backend["name"]
                requested = client.post(f"/orchestration/v1/services/{sid}/invoke",
                    json={"inputs": {"text": "through native MCP"}, "idempotency_key": "remote-one", "local_only": False})
                assert requested.status_code == 202, requested.text
                completed = poll(client, requested.json()["id"])
                assert completed["result"]["invoke"]["text"] == "THROUGH NATIVE MCP"
                independently = client.post(f"/orchestration/v1/services/{second_sid}/invoke",
                    json={"inputs": {"text": "second binding"}, "idempotency_key": "remote-two", "local_only": False})
                assert independently.status_code == 202, independently.text
                assert poll(client, independently.json()["id"])["result"]["invoke"]["text"] == "SECOND BINDING"
                rpc = client.post("/rpc", json={"jsonrpc": "2.0", "id": 62, "method": "tools/call",
                    "params": {"name": facade["name"], "arguments": {"inputs": {"text": "native RPC"}, "local_only": False}}})
                assert rpc.status_code == 200 and "result" in rpc.json(), rpc.text
                rpc_result = rpc.json()["result"]
                assert not rpc_result.get("isError"), rpc.text
                rpc_run = rpc_result.get("structuredContent") or json.loads(rpc_result["content"][0]["text"])
                assert poll(client, rpc_run["id"])["result"]["invoke"]["text"] == "NATIVE RPC"

                async def native_mcp():
                    from mcp import ClientSession, MCPError
                    from mcp.client.streamable_http import streamable_http_client

                    async with httpx.AsyncClient(headers={"Authorization": "Bearer " + token}, trust_env=False) as http:
                        async with streamable_http_client(str(client.base_url) + "/mcp", http_client=http) as streams:
                            async with ClientSession(streams[0], streams[1]) as session:
                                await session.initialize()
                                submitted = await session.call_tool(facade["name"], {"inputs": {"text": "public facade"}, "local_only": False})
                                assert not submitted.is_error, submitted
                                run_id = (submitted.structured_content or json.loads(submitted.content[0].text))["id"]
                                with pytest.raises(MCPError, match="Managed backend requires"):
                                    await session.call_tool(native_backend["name"], {"text": "direct backend"})
                                return run_id

                second = asyncio.run(native_mcp())
                assert poll(client, second)["result"]["invoke"]["text"] == "PUBLIC FACADE"
                assert remote.get("/health").json()["invocations"] == 4
                created = client.post("/auth/email/admin/users", json={"email": "second-admin@example.com",
                    "password": password, "full_name": "Second operator", "is_admin": True})
                assert created.status_code in {200, 201}, created.text
                with httpx.Client(base_url=client.base_url, trust_env=False) as second_admin:
                    logged_in = second_admin.post("/auth/email/login", json={"email": "second-admin@example.com", "password": password})
                    assert logged_in.status_code == 200, logged_in.text
                    second_admin.headers["Authorization"] = "Bearer " + logged_in.json()["access_token"]
                    second_admin.cookies.clear()
                    assert second_admin.get(f"/v1/tools/{sid}").status_code in {403, 404}
                    denied = second_admin.post("/rpc", json={"jsonrpc": "2.0", "id": 63, "method": "tools/call",
                        "params": {"name": facade["name"], "arguments": {"inputs": {"text": "other operator"}, "local_only": False}}})
                    assert denied.status_code == 200 and "error" in denied.json(), denied.text
                assert remote.get("/health").json()["invocations"] == 4
                # The pinned source has storage fields but no standalone REST passthrough router.
                paths = client.get("/openapi.json").json()["paths"]
                assert not [path for path in paths if "passthrough" in path and "/config/passthrough-headers" not in path]
        except BaseException:
            log.flush()
            print("Gateway diagnostics:", (tmp_path / "gateway.log").read_text()[-9000:])
            raise
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
