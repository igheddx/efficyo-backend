from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.account_tag_key import AccountTagKey

_ALLOWED_SPECIALS = set(" +-=_:/.@")


def _is_allowed_text_char(ch: str) -> bool:
    return ch.isalnum() or ch in _ALLOWED_SPECIALS


def _validate_text(value: str, *, max_len: int, field_name: str) -> str:
    text = str(value or "")
    if len(text) == 0:
        raise ValueError(f"{field_name}_required")
    if len(text) > max_len:
        raise ValueError(f"{field_name}_too_long")
    if any(not _is_allowed_text_char(ch) for ch in text):
        raise ValueError(f"{field_name}_invalid_chars")
    return text


def validate_tag_values(tag_values: dict[str, str] | None) -> dict[str, str]:
    if not tag_values:
        return {}
    out: dict[str, str] = {}
    for raw_k, raw_v in tag_values.items():
        key = _validate_text(str(raw_k), max_len=128, field_name="tag_key")
        if key in out:
            raise ValueError("duplicate_tag_key")
        val = _validate_text(str(raw_v), max_len=256, field_name="tag_value")
        out[key] = val
    return out


def validate_tag_entries(entries: list[dict[str, str]] | None) -> dict[str, str]:
    if not entries:
        return {}
    out: dict[str, str] = {}
    for row in entries:
        key = _validate_text(str((row or {}).get("key", "")), max_len=128, field_name="tag_key")
        if key in out:
            raise ValueError("duplicate_tag_key")
        val = _validate_text(str((row or {}).get("value", "")), max_len=256, field_name="tag_value")
        out[key] = val
    return out


def list_account_tag_keys(db: Session, *, cloud_account_id: UUID) -> list[str]:
    rows = (
        db.query(AccountTagKey.key_name)
        .filter(AccountTagKey.cloud_account_id == cloud_account_id)
        .order_by(AccountTagKey.key_name.asc())
        .all()
    )
    return [str(r[0]) for r in rows]


def upsert_account_tag_keys(db: Session, *, cloud_account_id: UUID, keys: list[str]) -> None:
    if not keys:
        return
    existing = set(list_account_tag_keys(db, cloud_account_id=cloud_account_id))
    to_add = [k for k in keys if k not in existing]
    for key in to_add:
        db.add(AccountTagKey(cloud_account_id=cloud_account_id, key_name=key))
