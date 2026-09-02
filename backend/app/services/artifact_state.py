"""What can be known about an artifact without any data.

There used to be one "validation" step that answered two unrelated questions at
once: can a runtime load this, and does it predict sensibly on a reference image.
The second needs a dataset. With a synthetic stand-in it reports on the
placeholder - "nothing was detected above score_threshold" tells you the grey
rectangle has no cars in it, not that the weights are wrong - so it is gone.

What remains needs no data at all:

  the header  a file with no graph in it (a bare state_dict, safetensors) can
              never execute, and reading a few hundred bytes says so
  the load    everything else is settled by a runtime building the graph, which
              it has to do to serve anyway - so serving *is* the check

This module holds the first half. The second lives in the deployer, which folds
each replica's load report back onto the version.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.errors import ApiError
from app.models import ModelVersion
from app.settings import settings

log = logging.getLogger(__name__)

UNSERVABLE = ("weights_only", "unknown")


async def refresh_artifact(session: AsyncSession, version: ModelVersion) -> dict:
    """Re-read the verdict when the stored one is not 'servable'.

    A verdict can be stale in two ways: the version predates inspection, or it was
    inspected by an older build that had not learned its format yet. Re-reading
    costs a range request, and it is the difference between a permanent block and
    a version that works.
    """
    artifact = version.artifact_info or {}
    if artifact.get("usability") == "servable" or not version.weights_key:
        return artifact
    try:
        _, _, fresh = await storage.verify_and_inspect(settings.s3_bucket_weights,
                                                      version.weights_key)
    except Exception as exc:
        log.warning("could not re-inspect the artifact",
                    extra={"version_id": version.id, "error": str(exc)[:200]})
        return artifact
    version.artifact_info = fresh
    await session.commit()
    return fresh


def blocks_serving(artifact: dict) -> str | None:
    """The reason this file can never be served, or None if it might be."""
    if artifact.get("usability") not in UNSERVABLE:
        return None
    detail = artifact.get("detail") or "this artifact carries no model graph"
    step = artifact.get("next_step") or ""
    return f"{detail} {step}".strip()


async def guard_servable(session: AsyncSession, version: ModelVersion,
                         code: str = "conflict") -> None:
    """Raise if the file provably cannot run; stay quiet otherwise."""
    artifact = await refresh_artifact(session, version)
    reason = blocks_serving(artifact)
    if reason:
        raise ApiError(code, reason, details={"artifact": artifact})
