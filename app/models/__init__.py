"""Model imports for SQLAlchemy mapper registration.

Importing model modules here ensures relationship string references are resolvable
when the ORM configures mappers in worker/API processes.
"""

from app.models.access_grant import AccessGrant
from app.models.account_tag_key import AccountTagKey
from app.models.approval_request import ApprovalAssignment, ApprovalRequest
from app.models.cloud_account import CloudAccount
from app.models.cost_snapshot import CostApiUsageLog, CostFetchLock, CostSnapshot, CostSyncPolicy
from app.models.execution_audit_event import ExecutionAuditEvent
from app.models.execution_owner import ExecutionOwnerAssignment
from app.models.execution_policy import ExecutionPolicy
from app.models.finding import Finding
from app.models.ingestion_job import IngestionJob
from app.models.notification import Notification
from app.models.notification_delivery_log import NotificationDeliveryLog
from app.models.notification_policy import NotificationPolicy
from app.models.notification_schedule import NotificationSchedule
from app.models.notification_snooze import NotificationSnooze
from app.models.org_integration import OrgIntegration
from app.models.organization import OrgMembership, Organization
from app.models.platform_setting import PlatformSetting
from app.models.policy_profile import PolicyProfile
from app.models.recommendation import Recommendation
from app.models.recommendation_outcome import RecommendationOutcome
from app.models.resource_snapshot import ResourceSnapshot
from app.models.sync_pipeline import SyncJob, SyncJobEvent, SyncTask
from app.models.tagging_batch import TaggingBatch, TaggingBatchResource
from app.models.tenant import Tenant
from app.models.user_notification_destination import UserNotificationDestination
from app.models.user import AuthSession, User

__all__ = [
	"AccessGrant",
	"AccountTagKey",
	"ApprovalAssignment",
	"ApprovalRequest",
	"CloudAccount",
	"CostApiUsageLog",
	"CostFetchLock",
	"CostSnapshot",
	"CostSyncPolicy",
	"ExecutionAuditEvent",
	"ExecutionOwnerAssignment",
	"ExecutionPolicy",
	"Finding",
	"IngestionJob",
	"Notification",
	"NotificationDeliveryLog",
	"NotificationPolicy",
	"NotificationSchedule",
	"NotificationSnooze",
	"OrgIntegration",
	"OrgMembership",
	"Organization",
	"PlatformSetting",
	"PolicyProfile",
	"Recommendation",
	"RecommendationOutcome",
	"ResourceSnapshot",
	"SyncJob",
	"SyncJobEvent",
	"SyncTask",
	"TaggingBatch",
	"TaggingBatchResource",
	"Tenant",
	"UserNotificationDestination",
	"AuthSession",
	"User",
]
