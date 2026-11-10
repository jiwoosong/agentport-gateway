import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from aog.adapters import UncertainExecution
from aog.lifecycle import Lifecycle
from aog.models import OpenShellTarget, Principal, Service
from aog.openshell import OpenShell
from aog.store import Store

TARGET = dict(gateway="pc", workspace="team", sandbox="alice", sandbox_id="sandbox-1")


def definition(**changes):
    return Service(
        name="Sandbox worker",
        kind="agent",
        transport="acp",
        endpoint="http://worker/invoke",
        health_url="http://worker/health",
        openshell=TARGET,
        **changes,
    )


def test_runtime_validation_and_unique_binding(tmp_path):
    with pytest.raises(ValidationError):
        definition(stack_id="also-komodo")
    store = Store(f"sqlite:///{tmp_path}/db")
    store.init()
    actor = Principal(subject="alice", is_admin=True)
    row = store.save("service", definition().model_dump(), actor)
    with pytest.raises(HTTPException) as error:
        store.save("service", definition().model_dump(), actor)
    assert error.value.status_code == 409
    store.phase(row.id, "running")
    with pytest.raises(HTTPException):
        store.save("service", definition().model_copy(update={"openshell": None}).model_dump(), actor, row.id)
    assert store.resource(row.id, actor).document["openshell"] == TARGET


async def test_openshell_identity_and_uncertain_mutation():
    pytest.importorskip("openshell")
    from openshell._proto import openshell_pb2 as pb

    ref = SimpleNamespace(id="sandbox-1", phase=pb.SANDBOX_PHASE_READY, current_policy_version=1)
    calls = []

    class Client:
        def get(self, name, *, workspace):
            assert (name, workspace) == ("alice", "team")
            return ref

        def start(self, name, *, workspace):
            calls.append("start")
            raise TimeoutError("possibly started")

    @contextmanager
    def connect(gateway):
        assert gateway == "pc"
        yield Client()

    backend = OpenShell(connect)
    target = OpenShellTarget(**TARGET)
    assert (await backend.execute(target, "status", 1))["phase"] == "SANDBOX_PHASE_READY"
    with pytest.raises(UncertainExecution):
        await backend.execute(target, "start", 1)
    assert calls == ["start"]
    ref.id = "replacement"
    with pytest.raises(RuntimeError, match="identity"):
        await backend.execute(target, "start", 1)
    assert calls == ["start"]
    with pytest.raises(ValueError):
        await backend.execute(target, "update", 1)


async def test_openshell_lifecycle_keeps_busy_sandbox(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/db")
    store.init()
    service = definition(lifecycle="on_demand", concurrency=2, idle_seconds=10)
    row = store.save("service", service.model_dump(), Principal(subject="alice"))
    calls = []

    class Backend:
        async def execute(self, target, operation, timeout):
            calls.append(operation)
            await asyncio.sleep(0.01)

    async def health(*args):
        pass

    lifecycle = Lifecycle(store, health=health)
    lifecycle.openshell = Backend()
    await asyncio.gather(*(lifecycle.acquire(row.id, service, f"job-{n}", f"run-{n}") for n in range(2)))
    assert calls == ["start"]
    store.release("job-0")
    assert not store.claim_stop(row.id, 0)
    store.release("job-1")
    assert store.claim_stop(row.id, 0)
    await lifecycle.control(service, "stop")
    assert calls == ["start", "stop"]


@pytest.mark.skipif(__import__("os").environ.get("AOG_OPENSHELL_WIRE") != "1", reason="Opt-in loopback gRPC")
async def test_real_openshell_sdk_wire():
    """Real SDK and protobuf RPCs against a local server; no sandbox/GPU required."""
    grpc = pytest.importorskip("grpc")
    from openshell import SandboxClient
    from openshell._proto import openshell_pb2 as pb
    from openshell._proto import openshell_pb2_grpc as rpc

    calls = []

    class Server(rpc.OpenShellServicer):
        phase = pb.SANDBOX_PHASE_STOPPED

        def response(self, request):
            assert request.name == "alice"
            assert request.workspace_scope.workspace == "team"
            return pb.SandboxResponse(
                sandbox=pb.Sandbox(
                    metadata={"id": "sandbox-1", "name": "alice", "workspace": "team"},
                    status={"phase": self.phase, "current_policy_version": 7},
                )
            )

        def GetSandbox(self, request, context):
            return self.response(request)

        def StartSandbox(self, request, context):
            calls.append("start")
            self.phase = pb.SANDBOX_PHASE_READY
            return self.response(request)

        def StopSandbox(self, request, context):
            calls.append("stop")
            self.phase = pb.SANDBOX_PHASE_STOPPED
            return self.response(request)

    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    rpc.add_OpenShellServicer_to_server(Server(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        backend = OpenShell(lambda _: SandboxClient(f"127.0.0.1:{port}", timeout=2))
        target = OpenShellTarget(**TARGET)
        assert (await backend.execute(target, "start", 2))["phase"] == "SANDBOX_PHASE_READY"
        assert (await backend.execute(target, "stop", 2))["phase"] == "SANDBOX_PHASE_STOPPED"
        assert calls == ["start", "stop"]
    finally:
        server.stop(0).wait()
