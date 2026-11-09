import base64
import hashlib
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from aog import worker


def test_workspace_snapshot_cannot_escape_or_change_content(tmp_path):
    body = b"print('review this')\n"
    file = {
        "path": "src/main.py",
        "content_base64": base64.b64encode(body).decode(),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    worker.import_files(tmp_path, [file])
    assert worker.export_files(tmp_path, [file["path"]]) == [file]
    with pytest.raises(HTTPException):
        worker.import_files(tmp_path, [{**file, "path": "../outside"}])
    with pytest.raises(HTTPException):
        worker.import_files(tmp_path, [{**file, "sha256": "wrong"}])
    (tmp_path / "link").symlink_to(tmp_path.parent)
    with pytest.raises(HTTPException):
        worker.export_files(tmp_path, ["link/outside"])


async def test_acp_wire_resume_identity_and_idempotency(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "root", tmp_path / "worker")
    key = Ed25519PrivateKey.generate()
    for name, value in {
        "AOG_WORKER_PUBLIC_KEY": base64.b64encode(key.public_key().public_bytes_raw()).decode(),
        "AOG_WORKER_OWNER": "alice@example.com",
        "AOG_WORKER_PROJECT": "p",
        "AOG_WORKER_AUDIENCE": "http://worker/invoke",
        "AOG_WORKER_EGRESS": "isolated",
    }.items():
        monkeypatch.setenv(name, value)
    original = worker.spawn_agent_process
    calls = []

    @asynccontextmanager
    async def spawn(client, command, **kwargs):
        calls.append(command)
        assert "AOG_DELEGATION_SECRET" not in kwargs["env"]
        assert "OPENAI_API_KEY" not in kwargs["env"]
        async with original(
            client, sys.executable, str(Path(__file__).with_name("fake_acp.py")), **kwargs
        ) as pair:
            yield pair

    monkeypatch.setattr(worker, "spawn_agent_process", spawn)

    def grant(step, subject="alice@example.com"):
        return jwt.encode(
            {
                "sub": subject,
                "project_id": "p",
                "aud": "http://worker/invoke",
                "iss": "agentport-worker",
                "run_step": step,
                "exp": int(time.time()) + 60,
            },
            key,
            algorithm="EdDSA",
        )

    value = worker.Prompt(prompt={"instruction": "Review"}, session_key="a" * 32)
    output = await worker.invoke(value, grant("step-1"), "step-1", None)
    assert output["native_session_id"] == "native-fixture"
    assert output["structured"]["approved"] is True
    assert await worker.invoke(value, grant("step-1"), "step-1", None) == output
    assert len(calls) == 1
    value.native_session_id = "native-fixture"
    assert (await worker.invoke(value, grant("step-2"), "step-2", None))[
        "native_session_id"
    ] == "native-fixture"
    with pytest.raises(HTTPException) as denied:
        await worker.invoke(value, grant("step-3", "bob@example.com"), "step-3", None)
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException):
        await worker.invoke(value, grant("step-1"), "wrong-step", None)
