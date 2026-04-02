from uuid import uuid4

from app.schemas.access_grant import AccessGrantCreate


def test_access_grant_create_strips_blank_cloud_account_id():
    u, t = uuid4(), uuid4()
    m = AccessGrantCreate(user_id=u, tenant_id=t, access_role="viewer", cloud_account_id="  ")
    assert m.cloud_account_id is None
