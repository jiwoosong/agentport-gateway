"""The Core app imports and installs without the optional gateway framework."""

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from aog import backends, security
from aog.models import Principal


def test_core_import_does_not_import_contextforge():
    result = subprocess.run([sys.executable, "-c", '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "mcpgateway" or fullname.startswith("mcpgateway."):
            raise AssertionError("Core attempted a gateway framework import: " + fullname)
sys.meta_path.insert(0, Block())
from aog.app import app
assert app.title == "AgentPort Core"
from aog.backends import get_backend
assert type(get_backend()).__name__ == "ContextForgeBackend"
assert not any(name == "mcpgateway" or name.startswith("mcpgateway.") for name in sys.modules)
'''], capture_output=True, text=True, env={**os.environ, "AOG_GATEWAY_BACKEND": "contextforge"}, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_installed_gateway_entrypoint_controls_identity_without_core_changes(monkeypatch):
    calls = []

    class InstalledBackend:
        async def authenticate(self, request):
            calls.append(request)
            return Principal(subject="provider-user", teams=["portable-project"])

        async def authorize(self, actor, permission):
            calls.append(permission)
            return actor

    monkeypatch.setenv("AOG_GATEWAY_BACKEND", "installed-example")
    monkeypatch.setattr(backends, "entry_points", lambda **query: (
        [SimpleNamespace(load=lambda: InstalledBackend)]
        if query == {"group": "agentport.gateway_backends", "name": "installed-example"} else []))
    backends.get_backend.cache_clear()
    try:
        actor = await security.authenticate("request")
        assert (await security.authorize(actor, "tools.execute")).subject == "provider-user"
        assert calls == ["request", "tools.execute"]
    finally:
        backends.get_backend.cache_clear()
