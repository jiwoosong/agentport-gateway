"""Validate the running demo Compose stack; use only with disposable CI credentials."""

import json
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values


def wait_ready(client: httpx.Client, path: str) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            if client.get(path, timeout=5).is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError(f"Service not ready: {client.base_url}{path.lstrip('/')}")


values = dotenv_values(".env")
credentials = {"email": values["PLATFORM_ADMIN_EMAIL"], "password": values["PLATFORM_ADMIN_PASSWORD"]}
with httpx.Client(base_url="http://127.0.0.1:4444", timeout=30, trust_env=False) as client:
    wait_ready(client, "/health")
    login = client.post("/auth/email/login", json=credentials)
    login.raise_for_status()
    client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
    client.cookies.clear()
    registered = client.post(
        "/orchestration/v1/services", json=json.loads(Path("examples/converter.service.json").read_text())
    )
    registered.raise_for_status()
    job = client.post(
        f"/orchestration/v1/services/{registered.json()['id']}/invoke", json={"inputs": {"text": "compose"}}
    )
    job.raise_for_status()
    for _ in range(60):
        result = client.get(f"/orchestration/v1/runs/{job.json()['id']}").json()
        if result["status"] in {"completed", "failed", "needs_attention"}:
            break
        time.sleep(1)
    assert result["status"] == "completed", result["status"]
    assert "COMPOSE" in result["result"]["invoke"]["text"]
with httpx.Client(base_url="http://127.0.0.1:3000", timeout=30, trust_env=False) as client:
    wait_ready(client, "/healthz")
    client.get("/app/orchestration").raise_for_status()
    login = client.post("/auth/login", json=credentials, headers={"Origin": "http://localhost:3000"})
    login.raise_for_status()
    assert client.get("/auth/session").json()["authenticated"]
    response = client.get(f"/api/orchestration/v1/runs/{job.json()['id']}")
    response.raise_for_status()
    assert response.json()["result"] == result["result"]
print("PASS: Compose PostgreSQL + DBOS conversion + BFF login/session/result; no Paperclip")
