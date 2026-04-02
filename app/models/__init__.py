"""Model imports for SQLAlchemy mapper registration.

Importing model modules here ensures relationship string references are resolvable
when the ORM configures mappers in worker/API processes.
"""

from app.models.access_grant import AccessGrant
from app.models.account_tag_key import AccountTagKey
from app.models.approval_request import ApprovalRequest
from app.models.cloud_account import CloudAccount
from app.models.cost_snapshot import CostSnapshot
from app.models.execution_audit_event import ExecutionAuditEvent
from app.models.execution_owner import ExecutionOwner
from app.models.execution_policy import ExecutionPolicy
from app.models.finding import Finding
from app.models.ingestion_job import IngestionJob
from app.models.notification import Notification
from app.models.organization import Organization
from app.models.policy_profile import PolicyProfile
from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.resource_snapshot import ResourceSnapshot
from app.models.sync_pipeline import SyncTask, SyncTaskDependency
from app.models.tenant import Tenant
from app.models.user import User

__all__ = [
	"AccessGrant",
	"AccountTagKey",
	"ApprovalRequest",
	"CloudAccount",
	"CostSnapshot",
	"ExecutionAuditEvent",
	"ExecutionOwner",
	"ExecutionPolicy",
	"Finding",
	"IngestionJob",
	"Notification",
	"Organization",
	"PolicyProfile",
	"Recommendation",
	"RecommendationOutcome",
	"ResourceSnapshot",
	"SyncTask",
	"SyncTaskDependency",
	"Tenant",
	"User",
]
