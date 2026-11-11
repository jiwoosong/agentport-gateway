"""Real ContextForge login + DBOS + service + MCP. No cloud credentials or Paperclip.

Run with AOG_INTEGRATION=1 pytest tests/test_http_integration.py -q -s.
Each test uses temporary databases, accounts, ports and child processes.
"""

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("AOG_INTEGRATION") != "1", reason="Opt-in real HTTP integration"
)
ROOT = Path(__file__).resolve().parents[1]


def integration_password():
    return "Aa1!" + "".join(secrets.choice("eJqVxZ") + secrets.choice("279") + secrets.choice("!#") for _ in range(10))


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait(client, path="/health"):
    for _ in range(120):
        try:
            if client.get(path).is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    pytest.fail("Service did not become ready; inspect temporary server log")


def poll(client, rid, status="completed"):
    for _ in range(120):
        response = client.get(f"/orchestration/v1/runs/{rid}")
        assert response.status_code == 200, response.status_code
        run = response.json()
        if run["status"] == status:
            return run
        if run["status"] in {"failed", "needs_attention"} and status != run["status"]:
            pytest.fail(f"Run failed: {run}")
        time.sleep(0.15)
    pytest.fail(f"Run did not reach {status}: {run}")


def test_real_gateway_login_workflow_isolation_and_mcp(tmp_path):
    gateway_port, core_port, service_port = port(), port(), port()
    password = integration_password()
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "upstream/contextforge"), str(ROOT))),
        "JWT_SECRET_KEY": secrets.token_urlsafe(48),
        "AUTH_ENCRYPTION_SECRET": secrets.token_urlsafe(48),
        "PLATFORM_ADMIN_EMAIL": "admin@example.com",
        "PLATFORM_ADMIN_PASSWORD": password,
        "DEFAULT_USER_PASSWORD": password,
        "BASIC_AUTH_PASSWORD": password,
        "AUTH_REQUIRED": "true",
        "MCP_CLIENT_AUTH_ENABLED": "true",
        "EMAIL_AUTH_ENABLED": "true",
        "MCPGATEWAY_UI_ENABLED": "true",
        "MCPGATEWAY_ADMIN_API_ENABLED": "true",
        "DATABASE_URL": f"sqlite:///{tmp_path}/core.db",
        "AOG_DATABASE_URL": f"sqlite:///{tmp_path}/aog.db",
        "DBOS_SYSTEM_DATABASE_URL": f"sqlite:///{tmp_path}/dbos.db",
        "LOG_LEVEL": "ERROR",
        "PASSWORD_CHANGE_ENFORCEMENT_ENABLED": "false",
        "SSRF_ALLOW_LOCALHOST": "true",
        "SSRF_ALLOW_PRIVATE_NETWORKS": "true",
        "AOG_INTERNAL_GATEWAY_URL": f"http://127.0.0.1:{gateway_port}",
        "AOG_GATEWAY_URL": f"http://127.0.0.1:{gateway_port}",
        "AOG_CORE_URL": f"http://127.0.0.1:{core_port}",
        "AOG_GATEWAY_BRIDGE_SECRET": secrets.token_urlsafe(48),
    }  # Disposable test accounts only.
    gateway_log = (tmp_path / "gateway.log").open("w+")
    processes = []
    try:
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "aog.integrations.contextforge.app:app",
                    "--port",
                    str(gateway_port),
                    "--log-level",
                    "warning",
                ],
                cwd=tmp_path,
                env=env,
                stdout=gateway_log,
                stderr=gateway_log,
            )
        )
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "examples.converter:app",
                    "--port",
                    str(service_port),
                    "--log-level",
                    "warning",
                ],
                cwd=tmp_path,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        client = httpx.Client(base_url=f"http://127.0.0.1:{gateway_port}", timeout=15, trust_env=False)
        wait(client)
        processes.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "aog.app:app",
            "--port", str(core_port), "--log-level", "warning"], cwd=tmp_path, env=env,
            stdout=gateway_log, stderr=gateway_log))
        with httpx.Client(base_url=f"http://127.0.0.1:{core_port}", trust_env=False) as core:
            wait(core)
        with httpx.Client(base_url=f"http://127.0.0.1:{service_port}", trust_env=False) as c:
            wait(c)
        login = client.post("/auth/email/login", json={"email": "admin@example.com", "password": password})
        assert login.status_code == 200, f"Login status: {login.status_code}"
        token = login.json()["access_token"]
        client.headers["Authorization"] = "Bearer " + token
        client.cookies.clear()
        me = client.get("/orchestration/v1/me")
        assert me.status_code == 200, me.text
        assert me.json()["is_admin"]
        definition = json.loads((ROOT / "examples/converter.service.json").read_text())
        definition["endpoint"] = f"http://127.0.0.1:{service_port}/convert"
        definition["health_url"] = f"http://127.0.0.1:{service_port}/health"
        response = client.post("/orchestration/v1/services", json=definition)
        assert response.status_code == 201, response.text
        sid = response.json()["id"]
        response = client.post(
            f"/orchestration/v1/services/{sid}/invoke",
            json={"inputs": {"text": "hello\nworld"}, "idempotency_key": "file-conversion"},
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]
        run = poll(client, run_id)
        assert "HELLO" in run["result"]["invoke"]["text"]
        # Duplicate submission returns the same run, not another side effect.
        retry = client.post(
            f"/orchestration/v1/services/{sid}/invoke",
            json={"inputs": {"text": "hello\nworld"}, "idempotency_key": "file-conversion"},
        )
        assert retry.json()["id"] == run_id
        workflow = {
            "name": "Parallel conversion with approval",
            "nodes": [
                {"id": "left", "type": "tool", "target": sid, "inputs": {"text": {"$ref": "input.text"}}},
                {"id": "right", "type": "tool", "target": sid, "inputs": {"text": "second"}},
                {
                    "id": "approve",
                    "type": "approval",
                    "depends_on": ["left", "right"],
                    "inputs": {"left": {"$ref": "nodes.left.text"}, "right": {"$ref": "nodes.right.text"}},
                },
                {
                    "id": "join",
                    "type": "join",
                    "depends_on": ["approve"],
                    "inputs": {"$ref": "nodes.approve.payload"},
                },
            ],
        }
        response = client.post("/orchestration/v1/workflows", json=workflow)
        assert response.status_code == 201, response.text
        wid = response.json()["id"]
        response = client.post(
            f"/orchestration/v1/workflows/{wid}/invoke", json={"inputs": {"text": "chosen"}}
        )
        assert response.status_code == 202, response.text
        rid = response.json()["id"]
        poll(client, rid, "waiting_approval")
        with httpx.Client(trust_env=False) as probe:
            before_restart = probe.get(f"http://127.0.0.1:{service_port}/health").json()["invocations"]
        # Restart Core while ContextForge remains available; completed work must not replay.
        processes[2].kill()
        processes[2].wait()
        processes[2] = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "aog.app:app",
                "--port",
                str(core_port),
                "--log-level",
                "warning",
            ],
            cwd=tmp_path,
            env=env,
            stdout=gateway_log,
            stderr=gateway_log,
        )
        with httpx.Client(base_url=f"http://127.0.0.1:{core_port}", trust_env=False) as core:
            wait(core)
        poll(client, rid, "waiting_approval")
        assert (
            client.post(
                f"/orchestration/v1/runs/{rid}/steps/approve/approve", json={"approved": True}
            ).status_code
            == 200
        )
        assert "CHOSEN" in poll(client, rid)["result"]["join"]["left"]
        with httpx.Client(trust_env=False) as probe:
            assert (
                probe.get(f"http://127.0.0.1:{service_port}/health").json()["invocations"] == before_restart
            )
        # Local selection -> exact-payload approval -> external call. No implicit context forwarding.
        cloud = client.post(
            "/orchestration/v1/services",
            json={
                **definition,
                "name": "Cloud stand-in",
                "location": "cloud",
                "egress_policy": "unrestricted",
            },
        ).json()["id"]
        privacy = {
            "name": "Explicit release",
            "local_only": False,
            "nodes": [
                {"id": "select", "type": "join", "inputs": {"text": {"$ref": "input.public"}}},
                {
                    "id": "release",
                    "type": "approval",
                    "release": True,
                    "depends_on": ["select"],
                    "inputs": {"$ref": "nodes.select"},
                },
                {
                    "id": "external",
                    "type": "tool",
                    "target": cloud,
                    "depends_on": ["release"],
                    "inputs": {"$ref": "nodes.release.payload"},
                },
            ],
        }
        private_workflow = client.post("/orchestration/v1/workflows", json=privacy).json()["id"]
        private_run = client.post(
            f"/orchestration/v1/workflows/{private_workflow}/invoke",
            json={"inputs": {"public": "selected", "private": "SENSITIVE_FIXTURE_987"}, "local_only": False},
        ).json()["id"]
        waiting = poll(client, private_run, "waiting_approval")
        release = next(step for step in waiting["steps"] if step["id"] == "release")
        assert release["output"]["payload"] == {"text": "selected"}
        # Editing the graph while it waits affects only the next run.
        edited = {**privacy, "name": "Changed after submission"}
        assert client.put(f"/orchestration/v1/workflows/{private_workflow}", json=edited).status_code == 200
        assert (
            client.post(
                f"/orchestration/v1/runs/{private_run}/steps/release/approve", json={"approved": True}
            ).status_code
            == 200
        )
        done = poll(client, private_run)
        assert done["definition_revision"] == 1 and done["name"] == "Explicit release"
        assert "SELECTED" in done["result"]["external"]["text"]
        assert "SENSITIVE_FIXTURE_987" not in json.dumps(done)
        gateway_log.flush()
        assert "SENSITIVE_FIXTURE_987" not in (tmp_path / "gateway.log").read_text()
        cancelled_run = client.post(
            f"/orchestration/v1/workflows/{private_workflow}/invoke",
            json={"inputs": {"public": "cancel"}, "local_only": False},
        ).json()["id"]
        poll(client, cancelled_run, "waiting_approval")
        runtime_response = client.post(
            "/orchestration/v1/services",
            json={**definition, "name": "ACP contract only", "kind": "agent", "transport": "acp",
                  "endpoint": f"http://127.0.0.1:{service_port}/invoke"},
        )
        assert runtime_response.status_code == 201, runtime_response.text
        runtime = runtime_response.json()["id"]
        delegated_agent = client.post(
            "/orchestration/v1/agents",
            json={
                "name": "Narrow grant",
                "service_id": runtime,
                "allowed_service_ids": [sid],
                "max_tool_calls": 1,
            },
        ).json()["id"]
        # Issue a real native token from the trusted job controller, not a handcrafted MCP token.
        native_issue = r"""
import asyncio, json, sys
from aog.integrations.contextforge.delegation import issue
from aog.models import Principal
from aog.store import Store
from mcpgateway.db import SessionLocal
from mcpgateway.schemas import ServerCreate
from mcpgateway.services.server_service import ServerService
payload=json.load(sys.stdin)
async def main():
    handoff=await issue(payload['agent_id'],[payload['service_id']],payload['run_id'],
        Principal.model_validate(payload['actor']),120,Store(payload['store_url']))
    with SessionLocal() as db:
        other=await ServerService().register_server(db,
            ServerCreate(name='other_scope', associated_tools=[payload['cloud_id']]),
            owner_email=payload['actor']['subject'], created_by=payload['actor']['subject'], visibility='private')
    handoff['other_server_id']=other.id
    print(json.dumps(handoff))
asyncio.run(main())
"""
        issued = subprocess.run(
            [sys.executable, "-c", native_issue], cwd=tmp_path, env=env, capture_output=True,
            input=json.dumps({"agent_id": delegated_agent, "service_id": sid, "run_id": cancelled_run,
                             "cloud_id": cloud, "actor": me.json(), "store_url": env["AOG_DATABASE_URL"]}),
            text=True, timeout=30,
        )
        assert issued.returncode == 0, issued.stderr[-5000:]
        handoff = json.loads(issued.stdout.splitlines()[-1])
        grant = handoff["gateway_token"]
        native_service = client.get(f"/v1/tools/{sid}")
        assert native_service.status_code == 200, native_service.text
        service_name = native_service.json()["name"]
        backend_id = native_service.json()["extensionMetadata"]["io.agentport/service"]["backend_tool_id"]
        backend_response = client.get(f"/v1/tools/{backend_id}")
        assert backend_response.status_code == 200, backend_response.text
        with httpx.Client(base_url=f"http://127.0.0.1:{service_port}", trust_env=False) as converter:
            before_direct = converter.get("/health").json()["invocations"]
            direct = client.post("/rpc", json={"jsonrpc": "2.0", "id": 51, "method": "tools/call",
                "params": {"name": backend_response.json()["name"], "arguments": {"text": "must not execute"}}})
            assert direct.status_code in {200, 403}, direct.text
            if direct.status_code == 200:
                assert "error" in direct.json() or direct.json().get("result", {}).get("isError"), direct.text
            assert converter.get("/health").json()["invocations"] == before_direct
        cloud_name = client.get(f"/v1/tools/{cloud}").json()["name"]
        get_run_name = client.get(f"/v1/tools/{me.json()['mcp_tools']['get_run']}").json()["name"]
        with httpx.Client(
            base_url=client.base_url, headers={"Authorization": "Bearer " + grant}, trust_env=False
        ) as narrowed:
            assert narrowed.get("/orchestration/v1/services").status_code == 403

        async def delegated_mcp_check():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with httpx.AsyncClient(
                headers={"Authorization": "Bearer " + grant}, trust_env=False
            ) as http:
                async with streamable_http_client(
                    handoff["gateway_mcp_url"], http_client=http
                ) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        catalog = await session.list_tools()
                        assert {tool.name for tool in catalog.tools} == {service_name, get_run_name}
                        assert (await session.call_tool(get_run_name, {"run_id": cancelled_run})).is_error
                        assert (await session.call_tool(cloud_name, {"inputs": {"text": "outside grant"}})).is_error
                        args = {"inputs": {"text": "delegated"}, "idempotency_key": "scoped-tool"}
                        accepted = await session.call_tool(service_name, args)
                        assert not accepted.is_error, accepted
                        assert not (await session.call_tool(service_name, args)).is_error
                        assert (await session.call_tool(service_name,
                            {**args, "idempotency_key": "over-budget"})).is_error
                        return (accepted.structured_content or json.loads(accepted.content[0].text))["id"]

        delegated_run = asyncio.run(delegated_mcp_check())
        poll(client, delegated_run)
        assert client.post(f"/orchestration/v1/runs/{cancelled_run}/cancel").status_code == 200
        poll(client, cancelled_run, "cancelled")
        # A real second ContextForge account must not see or invoke the private service or run.
        created = client.post(
            "/auth/email/admin/users",
            json={
                "email": "other@example.com",
                "password": password,
                "full_name": "Other test user",
                "is_admin": False,
            },
        )
        assert created.status_code in {200, 201}, created.text
        login2 = client.post("/auth/email/login", json={"email": "other@example.com", "password": password})
        assert login2.status_code == 200, login2.status_code
        other = httpx.Client(
            base_url=client.base_url,
            headers={"Authorization": "Bearer " + login2.json()["access_token"]},
            trust_env=False,
        )
        assert other.get("/orchestration/v1/services").json() == []
        assert other.get("/v1/tools").status_code == 200
        assert other.get(f"/v1/tools/{sid}").status_code in {403, 404}
        assert other.get(f"/v1/tools/{backend_id}").status_code in {403, 404}
        handshake = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "scope-regression", "version": "1"}}}
        protocol_headers = {"Accept": "application/json, text/event-stream"}
        assert other.post(handoff["gateway_mcp_url"], json=handshake,
                          headers=protocol_headers).status_code in {403, 404}
        with httpx.Client(base_url=client.base_url, headers={"Authorization": "Bearer " + grant}, trust_env=False) as scoped:
            assert scoped.post(f"/servers/{handoff['other_server_id']}/mcp", json=handshake,
                               headers=protocol_headers).status_code in {403, 404}
            assert scoped.get(f"/v1/tools/{backend_id}").status_code == 403
        local_registration = {"tool": {"name": "unauthorized_local", "integration_type": "AGENTPORT_LOCAL",
            "request_type": "POST", "extensionMetadata": {"io.agentport/local": {"operation": "invoke"}}}}
        assert other.post("/v1/tools", json=local_registration).status_code == 403
        with httpx.Client(base_url=client.base_url, trust_env=False) as anonymous:
            assert anonymous.post("/rpc", json={"jsonrpc": "2.0", "id": 1,
                "method": "tools/call", "params": {"name": service_name, "arguments": {"inputs": {"text": "denied"}}}}).status_code == 401
        assert other.get(f"/orchestration/v1/runs/{run_id}").status_code == 404
        assert other.post(f"/orchestration/v1/services/{sid}/invoke", json={}).status_code in {403, 404}
        artifact = client.post(
            "/orchestration/v1/artifacts", json={"name": "private.txt", "content_base64": "cHJpdmF0ZQ=="}
        ).json()
        assert other.get(f"/orchestration/v1/artifacts/{artifact['id']}").status_code == 404
        assert other.post("/orchestration/v1/services", json=definition).status_code == 403
        # New runtime APIs must retain the existing admin boundary.
        sandbox_definition = json.loads((ROOT / "examples/openshell-agent.service.json").read_text())
        sandbox_definition.update(endpoint=f"http://127.0.0.1:{service_port}/invoke",
                                  health_url=f"http://127.0.0.1:{service_port}/health")
        sandbox_response = client.post("/orchestration/v1/services", json=sandbox_definition)
        assert sandbox_response.status_code == 201, sandbox_response.text
        sandbox_id = sandbox_response.json()["id"]
        assert client.post("/orchestration/v1/services", json=sandbox_definition).status_code == 409
        assert client.post(f"/orchestration/v1/services/{sandbox_id}/operations/update").status_code == 409
        assert other.get(f"/orchestration/v1/services/{sandbox_id}/runtime").status_code == 403
        assert other.post(f"/orchestration/v1/services/{sandbox_id}/operations/start").status_code == 403
        assert (
            other.post(
                f"/orchestration/v1/services/{sandbox_id}/reconcile",
                json={
                    "phase": "running",
                    "deployment_verified": True,
                },
            ).status_code
            == 403
        )

        # Exercise the actual SDK MCP handshake and tool call, not a JSON-RPC mock.
        async def mcp_check():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with httpx.AsyncClient(
                headers={"Authorization": "Bearer " + token}, trust_env=False
            ) as http:
                async with streamable_http_client(
                    f"http://127.0.0.1:{gateway_port}/mcp", http_client=http
                ) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        await session.initialize()
                        result = await session.list_tools()
                        assert service_name in {tool.name for tool in result.tools}

        asyncio.run(mcp_check())
        print(
            "PASS: actual login, conversion, idempotency, parallel graph, approval, crash recovery without replay, two-user isolation, MCP; Paperclip absent"
        )
    except BaseException:
        gateway_log.flush()
        print("Gateway diagnostics:", (tmp_path / "gateway.log").read_text()[-12000:])
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
        gateway_log.close()
