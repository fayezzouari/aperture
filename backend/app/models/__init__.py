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
from app.models.instance_settings import InstanceSettings
from app.models.job import Job
from app.models.model import Model
from app.models.model_version import ModelVersion
from app.models.personal_access_token import PersonalAccessToken
from app.models.report_schedule import ReportSchedule
from app.models.runtime import Runtime
from app.models.tag import Tag
from app.models.upload_session import UploadSession
from app.models.user import User
from app.models.workflow import Workflow
from app.models.workflow_run import WorkflowRun
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember

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
    "InstanceSettings",
    "Job",
    "Model",
    "ModelVersion",
    "PersonalAccessToken",
    "ReportSchedule",
    "Runtime",
    "Tag",
    "TimestampMixin",
    "UploadSession",
    "User",
    "Workflow",
    "WorkflowRun",
    "Workspace",
    "WorkspaceMember",
    "new_id",
]
