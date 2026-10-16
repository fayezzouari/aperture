"""R7.4 - append-only audit trail.

Every mutating route funnels through record(); the log carries who, what, which
target and the before/after payload that made the change reproducible.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

log = logging.getLogger("aperture.audit")

# Set by the auth dependency when the caller is a personal access token, so every
# row written during that request says "user:token" without each route having to
# know how the caller authenticated. A request task owns its own context copy.
actor_type_var: ContextVar[str | None] = ContextVar("audit_actor_type", default=None)

# Actions are a closed vocabulary so the UI can filter on them reliably.
ACTIONS = (
    "settings.update",
    "model.create", "model.update", "model.archive", "model.delete",
    "version.upload_start", "version.upload_complete", "version.publish",
    "version.validate", "version.deprecate", "version.archive", "version.delete",
    "version.config_update", "version.convert",
    "tag.create", "tag.move", "tag.delete", "tag.lock",
    "deployment.create", "deployment.update", "deployment.rollback", "deployment.stop",
    "deployment.scale",
    "apikey.create", "apikey.revoke", "apikey.update",
    "token.create", "token.revoke",
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
    actor_type: str | None = None,
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
        actor_type=actor_type or actor_type_var.get() or "user",
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
    log.info("audit", extra={"action": action, "actor": actor_label, "target": target_label})
