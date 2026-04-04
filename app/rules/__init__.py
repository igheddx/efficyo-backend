"""Config-driven rule framework for MEEZI.

This package provides a declarative rule-definition layer that sits alongside
the existing hardcoded detection pipeline.  Migrated rules are expressed as YAML
config files under app/rules/config/, evaluated by the rule engine, and fed back
into the same finding → recommendation → scoring → approval lifecycle as legacy
rules.

Public surface:
    get_migrated_finding_types()  →  frozenset[str]
    run_rule_engine(db, tenant_id, cloud_account_id, sync_run_id)
"""

from app.rules.registry import get_migrated_finding_types
from app.rules.engine import run_rule_engine

__all__ = ["get_migrated_finding_types", "run_rule_engine"]
