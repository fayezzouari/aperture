"""R7.3 - projects are the isolation boundary; membership decides what you can do."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.db import get_session
from app.deps import Principal, current_user
from app.errors import ApiError
from app.models import Membership, Model, Project, User
from app.schemas import MemberIn, MemberOut, ProjectIn, ProjectOut, ProjectPatch
from app.serialize import dump

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
async def list_projects(principal: Principal = Depends(current_user),
                        session: AsyncSession = Depends(get_session)) -> list[ProjectOut]:
    stmt = sa.select(Project).where(Project.archived_at.is_(None)).order_by(Project.name)
    readable = principal.readable_projects()
    if readable is not None:
        stmt = stmt.where(Project.id.in_(readable))
    projects = list((await session.execute(stmt)).scalars().all())

    counts = dict(
        (
            await session.execute(
                sa.select(Model.project_id, sa.func.count())
                .where(Model.archived_at.is_(None)).group_by(Model.project_id)
            )
        ).all()
    )
    return [
        ProjectOut(
            **{c.name: getattr(p, c.name) for c in Project.__table__.columns
               if c.name in ProjectOut.model_fields},
            model_count=int(counts.get(p.id, 0)),
            role="admin" if principal.is_superadmin else principal.roles.get(p.id),
        )
        for p in projects
    ]


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectIn, request: Request,
                         principal: Principal = Depends(current_user),
                         session: AsyncSession = Depends(get_session)) -> ProjectOut:
    if not principal.is_superadmin:
        raise ApiError("forbidden", "only an instance admin can create a project")
    existing = (
        await session.execute(sa.select(Project).where(Project.slug == body.slug))
    ).scalar_one_or_none()
    if existing is not None:
        raise ApiError("slug_taken", f"a project with slug '{body.slug}' already exists")

    project = Project(**body.model_dump())
    session.add(project)
    await session.flush()
    session.add(Membership(user_id=principal.id, project_id=project.id, role="admin"))
    await audit.record(session, action="project.create", project_id=project.id,
                       actor_id=principal.id, actor_label=principal.label,
                       target_type="project", target_id=project.id, target_label=project.slug,
                       payload=body.model_dump(),
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return ProjectOut.model_validate(await dump(session, project, model_count=0, role="admin"))


@router.patch("/{slug}", response_model=ProjectOut)
async def update_project(slug: str, body: ProjectPatch, request: Request,
                         principal: Principal = Depends(current_user),
                         session: AsyncSession = Depends(get_session)) -> ProjectOut:
    project = await _project(session, slug)
    principal.require(project.id, "admin")
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(project, key, value)
    await audit.record(session, action="project.update", project_id=project.id,
                       actor_id=principal.id, actor_label=principal.label,
                       target_type="project", target_id=project.id, target_label=project.slug,
                       payload=changes, client_ip=request.client.host if request.client else None)
    await session.commit()
    return ProjectOut.model_validate(
        await dump(session, project, role=principal.roles.get(project.id))
    )


@router.get("/{slug}/members", response_model=list[MemberOut])
async def members(slug: str, principal: Principal = Depends(current_user),
                  session: AsyncSession = Depends(get_session)) -> list[MemberOut]:
    project = await _project(session, slug)
    principal.require(project.id, "viewer")
    rows = (
        await session.execute(
            sa.select(User, Membership.role).join(Membership, Membership.user_id == User.id)
            .where(Membership.project_id == project.id).order_by(User.email)
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
    project = await _project(session, slug)
    principal.require(project.id, "admin")
    email = body.email.strip().lower()
    user = (
        await session.execute(sa.select(User).where(sa.func.lower(User.email) == email))
    ).scalar_one_or_none()
    if user is None:
        user = User(email=email, name=email, provider="local", password_hash=None)
        session.add(user)
        await session.flush()

    membership = (
        await session.execute(
            sa.select(Membership).where(Membership.project_id == project.id,
                                        Membership.user_id == user.id)
        )
    ).scalar_one_or_none()
    action = "user.role_change" if membership else "user.invite"
    if membership is None:
        membership = Membership(user_id=user.id, project_id=project.id, role=body.role)
        session.add(membership)
    else:
        membership.role = body.role

    await audit.record(session, action=action, project_id=project.id, actor_id=principal.id,
                       actor_label=principal.label, target_type="user", target_id=user.id,
                       target_label=user.email, payload={"role": body.role},
                       client_ip=request.client.host if request.client else None)
    await session.commit()
    return MemberOut(user_id=user.id, email=user.email, name=user.name, role=body.role,
                     provider=user.provider)


@router.delete("/{slug}/members/{user_id}", status_code=204)
async def remove_member(slug: str, user_id: str, request: Request,
                        principal: Principal = Depends(current_user),
                        session: AsyncSession = Depends(get_session)) -> None:
    project = await _project(session, slug)
    principal.require(project.id, "admin")
    await session.execute(
        sa.delete(Membership).where(Membership.project_id == project.id,
                                    Membership.user_id == user_id)
    )
    await audit.record(session, action="user.deactivate", project_id=project.id,
                       actor_id=principal.id, actor_label=principal.label, target_type="user",
                       target_id=user_id, payload={"removed_from": project.slug},
                       client_ip=request.client.host if request.client else None)
    await session.commit()


async def _project(session: AsyncSession, slug: str) -> Project:
    project = (
        await session.execute(sa.select(Project).where(Project.slug == slug))
    ).scalar_one_or_none()
    if project is None:
        raise ApiError("not_found", f"no project with slug '{slug}'")
    return project
