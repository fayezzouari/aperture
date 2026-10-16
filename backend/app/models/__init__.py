"""The metadata plane. Weights live in object storage; everything else lives here.

One module per table; importing the package registers every table on `Base.metadata`.
"""

from app.models.alert_event import AlertEvent
from app.models.alert_rule import AlertRule
from app.models.api_key import ApiKey
from app.models.assignment import Assignment
from app.models.audit_log import AuditLog
from app.models.base import JSON_T, Base, TimestampMixin, new_id
from app.models.deployment import Deployment
from app.models.drift_window import DriftWindow
from app.models.inference_log import InferenceLog
from app.models.job import Job
from app.models.membership import Membership
from app.models.model import Model
from app.models.model_version import ModelVersion
from app.models.personal_access_token import PersonalAccessToken
from app.models.project import Project
from app.models.report_schedule import ReportSchedule
from app.models.runtime import Runtime
from app.models.tag import Tag
from app.models.upload_session import UploadSession
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_run import WorkflowRun

__all__ = [
    "JSON_T",
    "AlertEvent",
    "AlertRule",
    "ApiKey",
    "Assignment",
    "AuditLog",
    "Base",
    "Deployment",
    "DriftWindow",
    "InferenceLog",
    "Job",
    "Membership",
    "Model",
    "ModelVersion",
    "PersonalAccessToken",
    "Project",
    "ReportSchedule",
    "Runtime",
    "Tag",
    "TimestampMixin",
    "UploadSession",
    "User",
    "Workflow",
    "WorkflowRun",
    "new_id",
]
