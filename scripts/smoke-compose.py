"""Validate the running demo Compose stack; use only with disposable CI credentials."""

import base64
import hashlib
import json
import random
import struct
import time
import zlib
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
    image_definition = json.loads(Path("examples/image-webp.service.json").read_text())
    image_registered = client.post("/orchestration/v1/services", json=image_definition)
    image_registered.raise_for_status()
    image_id = image_registered.json()["id"]
with httpx.Client(base_url="http://127.0.0.1:3000", timeout=30, trust_env=False) as client:
    wait_ready(client, "/healthz")
    client.get("/app/orchestration").raise_for_status()
    login = client.post("/auth/login", json=credentials, headers={"Origin": "http://localhost:3000"})
    login.raise_for_status()
    assert client.get("/auth/session").json()["authenticated"]
    response = client.get(f"/api/orchestration/v1/runs/{job.json()['id']}")
    response.raise_for_status()
    assert response.json()["result"] == result["result"]
    client.headers["X-CSRF-Token"] = login.json()["csrfToken"]
    client.headers["Origin"] = "http://localhost:3000"
    # Valid RGB PNG larger than Fastify's original 1 MiB body limit. No Pillow
    # dependency is needed in the smoke runner or the gateway server image.
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
    rng = random.Random(7)
    rows = b"".join(b"\0" + rng.randbytes(768 * 3) for _ in range(768))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 768, 768, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    photo_job = client.post(f"/api/orchestration/v1/services/{image_id}/invoke", json={
        "inputs": {"image_base64": base64.b64encode(png).decode(), "quality": 80}})
    photo_job.raise_for_status()
    for _ in range(60):
        photo = client.get(f"/api/orchestration/v1/runs/{photo_job.json()['id']}").json()
        if photo["status"] in {"completed", "failed", "needs_attention"}:
            break
        time.sleep(1)
    assert photo["status"] == "completed", photo["status"]
    output = photo["result"]["invoke"]
    webp = base64.b64decode(output["content_base64"], validate=True)
    assert webp[:4] == b"RIFF" and webp[8:12] == b"WEBP"
    assert hashlib.sha256(webp).hexdigest() == output["sha256"]
    assert output["width"] == output["height"] == 768
print("PASS: Compose PostgreSQL + native gateway + DBOS + BFF photo upload/WebP result; no Paperclip")
