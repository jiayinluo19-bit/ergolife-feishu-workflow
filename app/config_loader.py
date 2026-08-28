from pathlib import Path

import yaml

from .domain.models import ActionDefinition, NodeDefinition, RoleAssignment, RuleDefinition


def load_definitions(path: Path) -> dict[str, NodeDefinition]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {item["id"]: NodeDefinition.model_validate(item) for item in raw["nodes"]}


def load_assignments(path: Path) -> dict[str, str]:
    return {role: item.user_id for role, item in load_role_assignments(path).items()}


def load_role_assignments(path: Path) -> dict[str, RoleAssignment]:
    """Load the complete role map, including department and display name.

    The workflow engine only needs ``role -> user_id``.  The product workbench
    also needs the human-facing department metadata, so keep both views
    available without changing the existing repository contract.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assignments = [RoleAssignment.model_validate(item) for item in raw["roles"]]
    return {item.role: item for item in assignments}


def load_role_rules(path: Path) -> dict[str, str]:
    """Load department key -> lifecycle role rules."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        str(item["department_key"]): str(item["role_code"])
        for item in raw.get("rules", [])
        if item.get("department_key") and item.get("role_code")
    }


def load_actions(path: Path) -> dict[str, ActionDefinition]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {item["id"]: ActionDefinition.model_validate(item) for item in raw["actions"]}


def load_rules(path: Path) -> dict[str, RuleDefinition]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {item["id"]: RuleDefinition.model_validate(item) for item in raw["rules"]}
