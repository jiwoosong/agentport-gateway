"""Product templates dispatch through native gateway tools, with scoped credentials."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcpgateway.agentport import require_backend_invocation

from aog.gateway import SERVICE_METADATA, Catalog
from aog.models import Service


@pytest.mark.parametrize("operation", ["create", "list"])
async def test_paperclip_templates_use_native_backend(monkeypatch, operation):
    import mcpgateway.db as native_db

    path = Path(__file__).parents[1] / "examples" / f"paperclip-issues-{operation}.service.json"
    service = Service.model_validate_json(path.read_text())
    inputs = {"title": "Review"} if operation == "create" else {"status": "todo"}
    output = {"id": "issue-a", "status": "backlog"} if operation == "create" else [{"id": "issue-a"}]
    backend = SimpleNamespace(id="native-backend", name="native-tool", owner_email="operator@example.com")
    public = SimpleNamespace(extension_metadata={SERVICE_METADATA: {"backend_tool_id": backend.id}})
    db = MagicMock()
    db.get.side_effect = lambda _, rid: public if rid == "managed-tool" else backend
    session = MagicMock()
    session.__enter__.return_value = db
    monkeypatch.setattr(native_db, "SessionLocal", lambda: session)
    catalog = Catalog(None)
    catalog.resource = AsyncMock(return_value=SimpleNamespace(id="managed-tool"))

    async def invoke(db, name, arguments, **kwargs):
        require_backend_invocation({"id": backend.id,
            "extension_metadata": {SERVICE_METADATA: {"internal": True}}})
        assert name == backend.name and arguments == inputs
        assert kwargs["user_email"] == backend.owner_email
        assert kwargs["request_headers"] == {
            "Idempotency-Key": "step-a", "X-Upstream-Authorization": "Bearer personal-board-key"}
        return SimpleNamespace(is_error=False, structured_content=output)

    catalog.tools.invoke_tool = AsyncMock(side_effect=invoke)
    assert await catalog.invoke("managed-tool", service, inputs, "step-a",
        {"subject": "alice@example.com"}, "personal-board-key") == output
    catalog.resource.assert_awaited_once()
    assert not service.idempotent or operation == "list"
    assert service.http_method == ("POST" if operation == "create" else "GET")
