"""Everything under /v1, assembled in one place."""

from fastapi import APIRouter

from app.api.v1 import (
    alerts,
    audit_api,
    auth,
    deployments,
    jobs,
    keys,
    models_api,
    observability,
    predict,
    projects,
    reports,
    tags,
    versions,
    workflows,
)

v1 = APIRouter(prefix="/v1")
v1.include_router(auth.router)
v1.include_router(projects.router)
v1.include_router(models_api.router)
v1.include_router(versions.router)
v1.include_router(tags.router)
v1.include_router(deployments.router)
v1.include_router(keys.router)
v1.include_router(predict.router)
v1.include_router(jobs.router)
v1.include_router(observability.router)
v1.include_router(reports.router)
v1.include_router(alerts.router)
v1.include_router(workflows.router)
v1.include_router(audit_api.router)
