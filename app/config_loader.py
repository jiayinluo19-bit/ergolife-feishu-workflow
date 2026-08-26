from pathlib import Path

import yaml

from .domain.models import NodeDefinition, RoleAssignment


def load_definitions(path: Path) -> dict[str, NodeDefinition]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {item["id"]: NodeDefinition.model_validate(item) for item in raw["nodes"]}


def load_assignments(path: Path) -> dict[str, str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assignments = [RoleAssignment.model_validate(item) for item in raw["roles"]]
    return {item.role: item.user_id for item in assignments}

