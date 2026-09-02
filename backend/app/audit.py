"""R7.4 - append-only audit trail.

Every mutating route funnels through record(); the log carries who, what, which
target and the before/after payload that made the change reproducible.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

log = logging.getLogger("aperture.audit")

# Actions are a closed vocabulary so the UI can filter on them reliably.
ACTIONS = (
    "project.create", "project.update", "project.archive",
    "model.create", "model.update", "model.archive", "model.delete",
    "version.upload_start", "version.upload_complete", "version.publish",
    "version.validate", "version.deprecate", "version.archive", "version.delete",
    "version.config_update", "version.convert",
    "tag.create", "tag.move", "tag.delete", "tag.lock",
    "deployment.create", "deployment.update", "deployment.rollback", "deployment.stop",
    "deployment.scale",
    "apikey.create", "apikey.revoke", "apikey.update",
    "user.login", "user.login_failed", "user.invite", "user.role_change", "user.deactivate",
    "alert.create", "alert.update", "alert.delete",
    "capture.purge", "capture.export", "prediction.flag",
    "report.create", "report.run", "report.delete",
    "workflow.create", "workflow.update", "workflow.publish", "workflow.delete",
    "job.create", "job.cancel",
)


async def record(
    session: AsyncSession,
    *,
    action: str,
    project_id: str | None,
    actor_type: str = "user",
    actor_id: str | None = None,
    actor_label: str = "",
    target_type: str = "",
    target_id: str | None = None,
    target_label: str = "",
    payload: dict | None = None,
    client_ip: str | None = None,
    commit: bool = False,
) -> None:
    entry = AuditLog(
        project_id=project_id,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        payload=payload or {},
        client_ip=client_ip,
    )
    session.add(entry)
    if commit:
        await session.commit()
    log.info("audit", extra={"action": action, "actor": actor_label, "target": target_label,
                             "project_id": project_id})
