"""R7.3 - workspaces: contexts that own models and workflows, and the members of each."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.db import get_session
from app.deps import Principal, current_user, readable_workspaces, workspace_role
from app.errors import ApiError
from app.models import Model, User, Workspace, WorkspaceMember
from app.schemas import MemberIn, MemberOut, WorkspaceIn, WorkspaceOut, WorkspacePatch
from app.security import require_role
from app.serialize import dump

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _counts(session: AsyncSession, ids: list[str]) -> tuple[dict, dict]:
    models = dict(
        (
            await session.execute(
                sa.select(Model.workspace_id, sa.func.count())
                .where(Model.workspace_id.in_(ids), Model.archived_at.is_(None))
                .group_by(Model.workspace_id)
            )
        ).all()
    )
    members = dict(
        (
            await session.execute(
                sa.select(WorkspaceMember.workspace_id, sa.func.count())
                .where(WorkspaceMember.workspace_id.in_(ids))
                .group_by(WorkspaceMember.workspace_id)
            )
        ).all()
    )
    return models, members


async def _out(session: AsyncSession, workspace: Workspace, role: str) -> WorkspaceOut:
    models, members = await _counts(session, [workspace.id])
    return WorkspaceOut.model_validate(
        await dump(session, workspace, role=role, model_count=int(models.get(workspace.id, 0)),
                   member_count=int(members.get(workspace.id, 0)))
    )


async def _workspace(session: AsyncSession, slug: str, principal: Principal,
                     minimum: str) -> tuple[Workspace, str]:
    found = (
        await session.execute(sa.select(Workspace).where(Workspace.slug == slug))
    ).scalar_one_or_none()
    role = None
    if found is not None:
        role = await workspace_role(session, principal.id, principal.role, found.id)
    if found is None or role is None:
        # not a member reads the same as missing: the slug itself is not public
        raise ApiError("not_found", f"no workspace '{slug}'")
    require_role(role, minimum)
    return found, role


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(principal: Principal = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> list[WorkspaceOut]:
    """The workspaces the caller can open, by name; every one for an instance admin."""
    principal.require_instance("viewer")
    workspaces = list(
        (
            await session.execute(readable_workspaces(principal).order_by(Workspace.name, Workspace.slug))
        ).scalars().all()
    )
    ids = [w.id for w in workspaces]
    roles = dict(
        (
            await session.execute(
                sa.select(WorkspaceMember.workspace_id, WorkspaceMember.role)
                .where(WorkspaceMember.user_id == principal.id)
            )
        ).all()
    )
    models, members = await _counts(session, ids)
    return [
        WorkspaceOut(
            id=w.id, slug=w.slug, name=w.name, description=w.description or "",
            created_at=w.created_at,
            role="admin" if principal.is_admin else roles[w.id],
            model_count=int(models.get(w.id, 0)), member_count=int(members.get(w.id, 0)),
        )
        for w in workspaces
    ]


@router.post("", response_model=WorkspaceOut, status_code=201)
async def create_workspace(body: WorkspaceIn, request: Request,
                           principal: Principal = Depends(current_user),
                           session: AsyncSession = Depends(get_session)) -> WorkspaceOut:
    """A developer or admin creates a workspace, becomes its admin and switches to it."""
    principal.require_instance("developer")
    existing = (
        await session.execute(sa.select(Workspace.id).where(Workspace.slug == body.slug))
    ).scalar_one_or_none()
    if existing is not None:
        raise ApiError("slug_taken", f"a workspace '{body.slug}' already exists")

    workspace = Workspace(**body.model_dump())
    session.add(workspace)
    await session.flush()
    session.add(WorkspaceMember(user_id=principal.id, workspace_id=workspace.id, role="admin"))
    await session.execute(
        sa.update(User).where(User.id == principal.id).values(last_workspace_id=workspace.id)
    )
    await audit.record(session, action="workspace.create", actor_id=principal.id,
                       actor_label=principal.label, target_type="workspace",
                       target_id=workspace.id, target_label=workspace.slug,
                       payload=body.model_dump(), client_ip=_ip(request))
    await session.commit()
    return await _out(session, workspace, "admin")


@router.patch("/{slug}", response_model=WorkspaceOut)
async def update_workspace(slug: str, body: WorkspacePatch, request: Request,
                           principal: Principal = Depends(current_user),
                           session: AsyncSession = Depends(get_session)) -> WorkspaceOut:
    workspace, role = await _workspace(session, slug, principal, "admin")
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    for key, value in changes.items():
        setattr(workspace, key, value)
    await audit.record(session, action="workspace.update", actor_id=principal.id,
                       actor_label=principal.label, target_type="workspace",
                       target_id=workspace.id, target_label=workspace.slug, payload=changes,
                       client_ip=_ip(request))
    await session.commit()
    return await _out(session, workspace, role)


@router.post("/{slug}/activate", response_model=WorkspaceOut)
async def activate_workspace(slug: str, principal: Principal = Depends(current_user),
                             session: AsyncSession = Depends(get_session)) -> WorkspaceOut:
    """Remember this as the caller's workspace: the UI opens on it at the next sign-in."""
    workspace, role = await _workspace(session, slug, principal, "viewer")
    await session.execute(
        sa.update(User).where(User.id == principal.id).values(last_workspace_id=workspace.id)
    )
    await session.commit()
    return await _out(session, workspace, role)


@router.get("/{slug}/members", response_model=list[MemberOut])
async def list_members(slug: str, principal: Principal = Depends(current_user),
                       session: AsyncSession = Depends(get_session)) -> list[MemberOut]:
    workspace, _ = await _workspace(session, slug, principal, "viewer")
    rows = (
        await session.execute(
            sa.select(User, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
            .where(WorkspaceMember.workspace_id == workspace.id).order_by(User.email)
        )
    ).all()
    return [
        MemberOut(user_id=u.id, email=u.email, name=u.name, role=role, provider=u.provider)
        for u, role in rows
    ]


@router.put("/{slug}/members", response_model=MemberOut)
async def upsert_member(slug: str, body: MemberIn, request: Request,
                        principal: Principal = Depends(current_user),
                        session: AsyncSession = Depends(get_session)) -> MemberOut:
    """Add someone with an account on this instance, or change their role here."""
    workspace, _ = await _workspace(session, slug, principal, "admin")
    email = body.email.strip().lower()
    user = (
        await session.execute(sa.select(User).where(sa.func.lower(User.email) == email))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise ApiError("not_found", f"no active account for '{email}' - an instance admin "
                                    "invites people to the instance first, under Users")

    member = (
        await session.execute(
            sa.select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id,
                                             WorkspaceMember.user_id == user.id)
        )
    ).scalar_one_or_none()
    if member is not None and member.role == "admin" and body.role != "admin":
        await _keep_an_admin(session, workspace.id, user.id)
    action = "workspace.member_role" if member else "workspace.member_add"
    if member is None:
        session.add(WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role=body.role))
    else:
        member.role = body.role
    await audit.record(session, action=action, actor_id=principal.id,
                       actor_label=principal.label, target_type="user", target_id=user.id,
                       target_label=user.email,
                       payload={"workspace": workspace.slug, "role": body.role},
                       client_ip=_ip(request))
    await session.commit()
    return MemberOut(user_id=user.id, email=user.email, name=user.name, role=body.role,
                     provider=user.provider)


@router.delete("/{slug}/members/{user_id}", status_code=204)
async def remove_member(slug: str, user_id: str, request: Request,
                        principal: Principal = Depends(current_user),
                        session: AsyncSession = Depends(get_session)) -> None:
    workspace, _ = await _workspace(session, slug, principal, "admin")
    member = (
        await session.execute(
            sa.select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id,
                                             WorkspaceMember.user_id == user_id)
        )
    ).scalar_one_or_none()
    if member is None:
        raise ApiError("not_found", "that user is not a member of this workspace")
    if member.role == "admin":
        await _keep_an_admin(session, workspace.id, user_id)
    await session.delete(member)
    await audit.record(session, action="workspace.member_remove", actor_id=principal.id,
                       actor_label=principal.label, target_type="user", target_id=user_id,
                       payload={"workspace": workspace.slug}, client_ip=_ip(request))
    await session.commit()


async def _keep_an_admin(session: AsyncSession, workspace_id: str, leaving: str) -> None:
    """A workspace never loses its last admin member; instance admins do not count."""
    others = (
        await session.execute(
            sa.select(sa.func.count()).select_from(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == "admin",
                WorkspaceMember.user_id != leaving,
            )
        )
    ).scalar_one()
    if not others:
        raise ApiError("conflict", "this is the workspace's last admin - make someone else "
                                   "admin first")
