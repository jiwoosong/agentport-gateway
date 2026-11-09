import os

import jwt
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select

from aog.models import Principal


async def authenticate(request: Request) -> Principal:
    from mcpgateway.middleware.rbac import get_current_user_with_permissions

    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    # A separate signing key and audience distinguish short-lived, narrowed agent grants.
    try:
        grant = jwt.decode(
            header[7:],
            os.environ["AOG_DELEGATION_SECRET"],
            algorithms=["HS256"],
            audience="agentport-mcp",
            issuer="agentport",
        )
    except (jwt.PyJWTError, KeyError):
        grant = None
    if grant:
        actor = Principal.model_validate(grant["principal"])
        if actor.is_admin or not actor.agent_id or not actor.parent_run_id:
            raise HTTPException(401, "Invalid agent grant")
        return actor
    user = await get_current_user_with_permissions(
        request, HTTPAuthorizationCredentials(scheme="Bearer", credentials=header[7:]), None
    )
    scopes = user.get("token_scopes") or []
    if isinstance(scopes, dict):
        scopes = scopes.get("permissions", [])
    principal = Principal(
        subject=user["email"],
        teams=user.get("token_teams") or [],
        is_admin=bool(user.get("is_admin")),
        scopes=scopes,
    )
    # A restricted token owned by an administrator must not become a global admin.
    if user.get("token_teams") is not None:
        principal.is_admin = False
    return principal


async def authorize(actor: Principal, permission="tools.execute") -> Principal:
    """Refresh memberships and action permissions on every step, including after recovery."""
    from mcpgateway.db import EmailTeamMember, EmailUser, SessionLocal
    from mcpgateway.services.permission_service import PermissionService

    if not actor.scope_allows(permission):
        raise HTTPException(403, "Token scope does not permit this action")
    with SessionLocal() as db:
        user = db.scalar(select(EmailUser).where(EmailUser.email == actor.subject))
        if not user or not user.is_active:
            raise HTTPException(403, "Account unavailable")
        teams = set(
            db.scalars(
                select(EmailTeamMember.team_id).where(
                    EmailTeamMember.user_email == actor.subject, EmailTeamMember.is_active.is_(True)
                )
            )
        )
        fresh = actor.model_copy(
            update={
                "teams": sorted(teams if actor.is_admin else teams.intersection(actor.teams)),
                "is_admin": actor.is_admin and user.is_admin,
            }
        )
        if not await PermissionService(db).check_permission(
            user_email=actor.subject,
            permission=permission,
            token_teams=None if fresh.is_admin else fresh.teams,
            check_any_team=True,
            allow_admin_bypass=fresh.is_admin,
        ):
            raise HTTPException(403, "Action not permitted")
        if fresh.agent_id:
            from aog.engine import current

            parent = current().store.run(fresh.parent_run_id, fresh)
            if parent.status not in {"running", "waiting_approval"}:
                raise HTTPException(403, "Parent agent run is no longer active")
            agent = current().store.resource(fresh.agent_id, fresh, "agent")
            fresh.allowed_service_ids = sorted(
                set(fresh.allowed_service_ids or []).intersection(agent.document["allowed_service_ids"])
            )
        return fresh


def require_admin(actor: Principal):
    if not actor.is_admin:
        raise HTTPException(403, "Platform administrator required for deployment changes")
