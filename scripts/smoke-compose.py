"""Validate the running demo Compose stack; use only with disposable CI credentials."""

import json
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

values = dotenv_values(".env")
with httpx.Client(base_url="http://127.0.0.1:4444", timeout=30, trust_env=False) as client:
    for _ in range(120):
        try:
            if client.get("/health").is_success:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    login = client.post(
        "/auth/email/login",
        json={"email": values["PLATFORM_ADMIN_EMAIL"], "password": values["PLATFORM_ADMIN_PASSWORD"]},
    )
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
with httpx.Client(trust_env=False) as client:
    assert client.get("http://127.0.0.1:3000/healthz").is_success
print("PASS: Compose PostgreSQL + gateway + DBOS conversion + BFF health; no Paperclip")
