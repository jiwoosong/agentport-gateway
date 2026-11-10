"""Contract fixtures only: no running Paperclip or provider account required."""

import json
from pathlib import Path

import httpx
import pytest

from aog import adapters
from aog.models import Service


@pytest.mark.parametrize("operation", ["create", "list"])
async def test_paperclip_templates_send_scoped_request(monkeypatch, operation):
    path = Path(__file__).parents[1] / "examples" / f"paperclip-issues-{operation}.service.json"
    service = Service.model_validate_json(path.read_text())
    service.endpoint = service.endpoint.replace("REPLACE_COMPANY_ID", "company-a")
    inputs = {"title": "Review"} if operation == "create" else {"status": "todo"}

    def upstream(request):
        assert request.url.path == "/api/companies/company-a/issues"
        assert request.headers["Authorization"] == "Bearer personal-board-key"
        assert request.headers["Idempotency-Key"] == "step-a"
        assert "X-AOG-Context" not in request.headers
        if operation == "create":
            assert request.method == "POST"
            assert json.loads(request.content) == inputs
            return httpx.Response(201, json={"id": "issue-a", "status": "backlog"})
        assert request.method == "GET"
        assert dict(request.url.params) == inputs
        return httpx.Response(200, json=[{"id": "issue-a", "status": "todo"}])

    original = httpx.AsyncClient
    monkeypatch.setattr(
        adapters.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(upstream), **kwargs),
    )
    result = await adapters.invoke(service, inputs, "step-a", {}, "personal-board-key")
    if operation == "create":
        assert result == {"id": "issue-a", "status": "backlog"}
        assert not service.idempotent  # Submission is not completion or proven deduplication.
    else:
        assert result == [{"id": "issue-a", "status": "todo"}]
