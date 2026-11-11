"""Native token and virtual-server records enforce bounded agent delegation."""

import json
import secrets
from dataclasses import replace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import HTTPException
from mcpgateway.agentport import AuthorizedInvocation
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from aog import delegation
from aog.models import Principal
from aog.store import Resource, Run, Store


@pytest.fixture
def native(tmp_path, monkeypatch):
    import mcpgateway.db as native_db
    from mcpgateway.config import settings
    from mcpgateway.services.permission_service import PermissionService
    from mcpgateway.services.server_service import ServerService
    from mcpgateway.services.tool_service import ToolService
    from mcpgateway.utils.jwt_config_helper import clear_jwt_caches

    engine = create_engine(f"sqlite:///{tmp_path}/native.db")
    native_db.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(native_db, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "jwt_secret_key", SecretStr(secrets.token_urlsafe(48)))
    clear_jwt_caches()
    monkeypatch.setattr(PermissionService, "get_user_permissions", AsyncMock(return_value={"tools.read", "tools.execute"}))
    monkeypatch.setattr(ToolService, "_notify_tool_added", AsyncMock())
    monkeypatch.setattr(ServerService, "_notify_server_added", AsyncMock())
    with sessions() as db:
        db.add_all([native_db.EmailUser(email="alice@example.com", is_active=True),
                    native_db.EmailUser(email="bob@example.com", is_active=True)])
        db.flush()
        db.add_all([native_db.EmailTeam(id="team-a", name="A", slug="team-a", created_by="alice@example.com", is_personal=True),
                    native_db.EmailTeam(id="team-b", name="B", slug="team-b", created_by="bob@example.com")])
        db.flush()
        db.add_all([native_db.EmailTeamMember(team_id="team-a", user_email="alice@example.com", is_active=True),
                    native_db.EmailTeamMember(team_id="team-b", user_email="alice@example.com", is_active=True)])
        for key, team in (("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "team-a"), ("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "team-b")):
            db.add(native_db.Tool(id=key, name=key, original_name=key, custom_name=key,
                custom_name_slug=key, integration_type=delegation.LOCAL, request_type="POST", url=None,
                owner_email="alice@example.com", team_id=team, visibility="team", enabled=True,
                input_schema={"type": "object"}, extension_metadata={delegation.NAMESPACE: {"operation": "invoke"}}))
        db.commit()
    store = Store(f"sqlite:///{tmp_path}/owned.db")
    store.init()
    actor = Principal(subject="alice@example.com", teams=["team-a", "team-b"], scopes=["tools.read", "tools.execute"])
    profile = store.save("agent", {"name": "agent", "project_id": "team-a", "allowed_service_ids": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]}, actor)
    with store.tx() as db:
        db.add(Run(id="run-a", owner=actor.subject, project_id="team-a", status="running",
                   spec={}, inputs={}, principal=actor.model_dump()))
    yield sessions, store, actor, profile.id
    clear_jwt_caches()
    store.engine.dispose()
    engine.dispose()


def native_caller(raw):
    from mcpgateway.config import settings
    payload = jwt.decode(raw, settings.jwt_secret_key.get_secret_value(), algorithms=[settings.jwt_algorithm], options={"verify_aud": False})
    return AuthorizedInvocation(subject="alice@example.com", teams=tuple(payload["teams"]),
        permissions=tuple(payload["scopes"]["permissions"]), server_id=payload["scopes"]["server_id"],
        token_server_id=payload["scopes"]["server_id"], token_id=payload["jti"], expires_at=payload["exp"])


@pytest.mark.asyncio
async def test_native_grant_uses_only_selected_tools_and_readonly_job_control(native):
    from mcpgateway.db import EmailApiToken, Server, Tool
    sessions, store, actor, agent_id = native
    handoff = await delegation.issue(agent_id, ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"], "run-a", actor, 120, store)
    caller = native_caller(handoff["gateway_token"])
    resolved = await delegation.resolve(caller, store)
    assert resolved.agent_id == agent_id and not resolved.is_admin
    assert resolved.allowed_service_ids == ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
    assert caller.server_id in handoff["gateway_mcp_url"]
    controls = await delegation.ensure_controls(actor, store)
    with sessions() as db:
        server = db.get(Server, caller.server_id)
        assert {t.id for t in server.tools} == {"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", controls["get_run"]}
        assert controls["cancel"] not in {t.id for t in server.tools}
        record = db.scalar(select(EmailApiToken).where(EmailApiToken.jti == caller.token_id))
        assert record.token_hash != handoff["gateway_token"] and record.server_id == server.id
        assert len(list(db.scalars(select(Tool).where(Tool.original_name.like("ap_control_%"))))) == 5
    with store.tx() as db:
        bound = db.get(Resource, caller.token_id)
        assert handoff["gateway_token"] not in json.dumps(bound.document)
    for changed in (replace(caller, subject="bob@example.com"), replace(caller, server_id="other")):
        with pytest.raises(HTTPException):
            await delegation.resolve(changed, store)


@pytest.mark.asyncio
async def test_cross_project_or_readonly_caller_cannot_issue_grant(native):
    sessions, store, actor, agent_id = native
    for allowed, owner in ((["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"], actor), (["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"], actor.model_copy(update={"scopes": ["tools.read"]}))):
        with pytest.raises(HTTPException) as error:
            await delegation.issue(agent_id, allowed, "run-a", owner, 120, store)
        assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_or_expired_binding_fails_closed(native):
    sessions, store, actor, agent_id = native
    handoff = await delegation.issue(agent_id, ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"], "run-a", actor, 120, store)
    caller = native_caller(handoff["gateway_token"])
    with store.tx() as db:
        row = db.get(Resource, caller.token_id)
        row.document = {**row.document, "token_expires_at": 1}
    with pytest.raises(HTTPException):
        await delegation.resolve(caller, store)

    with store.tx() as db:
        db.delete(db.get(Resource, caller.token_id))
    with pytest.raises(HTTPException):
        await delegation.resolve(caller, store)


@pytest.mark.asyncio
async def test_empty_native_permissions_cannot_be_inherited_as_full_access(native, monkeypatch):
    from mcpgateway.services.permission_service import PermissionService
    sessions, store, actor, agent_id = native
    monkeypatch.setattr(PermissionService, "get_user_permissions", AsyncMock(return_value=set()))
    with pytest.raises(HTTPException) as error:
        await delegation.issue(agent_id, ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"], "run-a", actor, 120, store)
    assert error.value.status_code == 403


@pytest.mark.parametrize("mutation", ["delete", "revoke"])
async def test_queued_native_api_principal_cannot_execute_after_token_record_removal(native, monkeypatch, mutation):
    from types import SimpleNamespace

    from mcpgateway.db import EmailApiToken
    from mcpgateway.services.permission_service import PermissionService

    from aog import engine
    from aog.gateway import authorize

    sessions, store, actor, agent_id = native
    monkeypatch.setattr(PermissionService, "check_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(engine, "current", lambda: SimpleNamespace(store=store))
    handoff = await delegation.issue(agent_id, ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"], "run-a", actor, 120, store)
    granted = await delegation.resolve(native_caller(handoff["gateway_token"]), store)
    queued_principal = Principal.model_validate(granted.model_dump())
    assert queued_principal.token_use == "api"
    assert (await authorize(queued_principal)).agent_id == agent_id
    with sessions() as db:
        token = db.scalar(select(EmailApiToken).where(EmailApiToken.jti == queued_principal.token_id))
        if mutation == "delete":
            db.delete(token)
        else:
            token.is_active = False
        db.commit()
    with pytest.raises(HTTPException) as error:
        await authorize(queued_principal)
    assert error.value.status_code == 403
    assert "token has been revoked" in error.value.detail
