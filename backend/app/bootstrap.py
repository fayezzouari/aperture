"""First-boot seeding: an admin, and a reference sample image."""

from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ModelVersion, User
from app.security import hash_password
from app.settings import settings

log = logging.getLogger("aperture.bootstrap")

async def seed(session: AsyncSession) -> None:
    email = settings.bootstrap_admin_email.strip().lower()
    admin = (
        await session.execute(sa.select(User).where(sa.func.lower(User.email) == email))
    ).scalar_one_or_none()
    if admin is None:
        admin = User(
            email=email, name="Administrator", provider="local",
            password_hash=hash_password(settings.bootstrap_admin_password),
            role="admin",
        )
        session.add(admin)
        await session.flush()
        log.info("created bootstrap admin", extra={"email": email})
        if settings.bootstrap_admin_password == "aperture":
            log.warning(
                "the bootstrap admin still uses the default password - change it before "
                "anyone else can reach this instance"
            )

    await session.commit()


async def retire_sample_validations(session: AsyncSession) -> int:
    """Strip what the old image-based validation left on existing versions.

    Those records hold a prediction count, an overlay and warnings derived from a
    reference image - a synthetic one, unless someone uploaded their own. Keeping
    them would keep showing conclusions drawn from a stand-in ("nothing was
    detected above score_threshold" against a grey rectangle with two shapes on
    it). A real load failure is worth keeping, so the errors survive; the
    dataset-derived half does not.
    """
    versions = list(
        (
            await session.execute(
                sa.select(ModelVersion).where(ModelVersion.validation != sa.text("'{}'::jsonb"))
            )
        ).scalars().all()
    )
    retired = 0
    for version in versions:
        record = version.validation or {}
        if not record or record.get("source") in ("runtime-load", "artifact-inspection"):
            continue
        if not any(key in record for key in ("summary", "overlay_key", "timings", "io")):
            continue
        version.validation = {
            "status": record.get("status", "passed"),
            "source": "legacy",
            "checked_at": record.get("checked_at"),
            "errors": record.get("errors") or [],
            "warnings": [],
        }
        retired += 1
    if retired:
        await session.commit()
        log.info("retired image-based validation records", extra={"count": retired})
    return retired
