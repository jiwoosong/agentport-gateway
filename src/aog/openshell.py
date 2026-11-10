"""Optional OpenShell SDK backend. No CLI, agent credentials, or Docker socket."""

import asyncio
import json
import os
from pathlib import Path

from aog.adapters import UncertainExecution
from aog.models import OpenShellTarget


def connect(gateway: str):
    from openshell import SandboxClient, TlsConfig

    # This operator-mounted file is never accepted through an API or exported.
    config = json.loads(Path(os.environ["AOG_OPENSHELL_CONFIG"]).read_text())[gateway]
    tls = TlsConfig(
        ca_path=Path(config["ca_path"]),
        cert_path=Path(config["cert_path"]),
        key_path=Path(config["key_path"]),
    )
    return SandboxClient(config["endpoint"], tls=tls, timeout=30)


class OpenShell:
    def __init__(self, connector=connect):
        self.connector = connector

    async def execute(self, target: OpenShellTarget, operation: str, timeout: int):
        if operation not in {"start", "stop", "status"}:
            raise ValueError("OpenShell image updates require an operator-provisioned replacement sandbox")
        return await asyncio.to_thread(self._execute, target, operation, timeout)

    def _execute(self, target, operation, timeout):
        with self.connector(target.gateway) as client:
            before = client.get(target.sandbox, workspace=target.workspace)
            if before.id != target.sandbox_id:
                raise RuntimeError("OpenShell sandbox identity changed; rebind after operator verification")
            if operation != "status":
                # No automatic retries: a lost mutation response may already have taken effect.
                try:
                    changed = getattr(client, operation)(target.sandbox, workspace=target.workspace)
                    if changed.id != target.sandbox_id:
                        raise RuntimeError("OpenShell sandbox replaced during operation")
                    waiter = client.wait_ready if operation == "start" else client.wait_stopped
                    before = waiter(target.sandbox, workspace=target.workspace, timeout_seconds=timeout)
                    if before.id != target.sandbox_id:
                        raise RuntimeError("OpenShell sandbox replaced during wait")
                    # wait_ready can also return a completed batch workload in the SDK.
                    from openshell._proto import openshell_pb2 as pb

                    expected = pb.SANDBOX_PHASE_READY if operation == "start" else pb.SANDBOX_PHASE_STOPPED
                    if before.phase != expected:
                        raise RuntimeError("OpenShell did not reach the requested phase")
                except Exception:
                    raise UncertainExecution("OpenShell operation requires reconciliation") from None
            from openshell._proto import openshell_pb2 as pb

            return {
                "backend": "openshell",
                "sandbox_id": before.id,
                "phase": pb.SandboxPhase.Name(before.phase),
                "policy_version": before.current_policy_version,
            }
