import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from aog.gateway import Catalog
from aog.models import Principal, RuntimeDefinition, Service
from aog.store import Store


@pytest.fixture
def configuration(tmp_path):
    store = Store(f"sqlite:///{tmp_path}/state.db")
    store.init()
    actor = Principal(subject="operator", is_admin=True)
    runtime = store.register_runtime(RuntimeDefinition(name="Existing runtime"), actor)
    store.bind_runtime("native-tool", runtime.id, actor)
    definition = Service(name="Tool", endpoint="http://worker/convert").model_dump()
    row = SimpleNamespace(id="native-tool", project_id=None, document=definition)
    catalog = object.__new__(Catalog)
    catalog.store = store
    catalog.resource = AsyncMock(return_value=row)
    catalog._save = AsyncMock(return_value=row)
    return catalog, store, actor, runtime, definition


async def test_existing_runtime_configuration_blocks_active_jobs_without_mutation(configuration):
    catalog, store, actor, runtime, definition = configuration
    store.phase(runtime.id, "running")
    assert store.acquire("native-tool", "job", "run", 1)
    with pytest.raises(HTTPException) as error:
        await catalog.save(definition, actor, "native-tool")
    assert error.value.status_code == 409
    assert store.phase(runtime.id) == "running"
    catalog._save.assert_not_awaited()


async def test_configure_claim_excludes_new_jobs_and_restores_previous_phase(configuration):
    catalog, store, actor, runtime, definition = configuration
    store.phase(runtime.id, "running")
    entered, finish = asyncio.Event(), asyncio.Event()

    async def update(document, caller, rid, **kwargs):
        assert document["runtime_id"] == runtime.id  # Missing references preserve the existing runtime.
        assert kwargs["claimed_runtime_id"] == runtime.id
        entered.set()
        await finish.wait()
        store.bind_runtime(rid, runtime.id, caller, claimed_runtime_id=kwargs["claimed_runtime_id"])
        return SimpleNamespace(id=rid)

    catalog._save.side_effect = update
    task = asyncio.create_task(catalog.save(definition, actor, "legacy-alias"))
    await entered.wait()
    assert store.phase(runtime.id) == "updating"
    assert not store.acquire("native-tool", "new-job", "new-run", 1)
    finish.set()
    assert (await task).id == "native-tool"
    assert store.phase(runtime.id) == "running"
    assert store.acquire("native-tool", "new-job", "new-run", 1)


async def test_rebind_holds_old_runtime_and_preserves_both_deployment_states(configuration):
    catalog, store, actor, runtime, definition = configuration
    replacement = store.register_runtime(RuntimeDefinition(name="Replacement"), actor)
    store.phase(runtime.id, "running")

    async def update(document, caller, rid, **kwargs):
        store.bind_runtime(rid, document["runtime_id"], caller,
                           claimed_runtime_id=kwargs["claimed_runtime_id"])
        return SimpleNamespace(id=rid)

    catalog._save.side_effect = update
    await catalog.save({**definition, "runtime_id": replacement.id}, actor, "native-tool")
    assert store.runtime_id("native-tool") == replacement.id
    assert store.phase(runtime.id) == "running"
    assert store.phase(replacement.id) == "stopped"


async def test_prevalidation_rejection_preserves_runtime_but_uncertain_write_fails_closed(configuration):
    catalog, store, actor, runtime, definition = configuration
    with pytest.raises(HTTPException) as error:
        await catalog.save({**definition, "project_id": "another", "visibility": "project"}, actor, "native-tool")
    assert error.value.status_code == 409
    assert store.phase(runtime.id) == "stopped"
    catalog._save.assert_not_awaited()
    catalog._save.side_effect = RuntimeError("Native write may have committed")
    with pytest.raises(RuntimeError):
        await catalog.save(definition, actor, "native-tool")
    assert store.phase(runtime.id) == "error"
    assert not store.acquire("native-tool", "new-job", "run", 1)


def test_binding_claim_is_not_a_general_rebind_bypass(configuration):
    _, store, actor, runtime, _ = configuration
    replacement = store.register_runtime(RuntimeDefinition(name="Replacement"), actor)
    with pytest.raises(HTTPException):
        store.bind_runtime("native-tool", replacement.id, actor, claimed_runtime_id=runtime.id)
    store.phase(runtime.id, "updating")
    with pytest.raises(HTTPException):
        store.bind_runtime("native-tool", replacement.id, actor, claimed_runtime_id="unrelated")
    store.phase(runtime.id, "running")
    assert store.acquire("native-tool", "active", "run", 1)
    store.phase(runtime.id, "updating")
    with pytest.raises(HTTPException):
        store.bind_runtime("native-tool", replacement.id, actor, claimed_runtime_id=runtime.id)
    assert store.runtime_id("native-tool") == runtime.id
