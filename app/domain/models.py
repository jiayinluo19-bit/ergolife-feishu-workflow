from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .enums import NodeStatus, ProjectStatus, TriggerType


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NodeDefinition(BaseModel):
    id: str
    name: str
    stage: str
    execution_mode: str = "serial"
    depends_on: list[str] = Field(default_factory=list)
    owner_role: str
    collaborator_roles: list[str] = Field(default_factory=list)
    reviewer_role: str | None = None
    required_outputs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    sla_hours: int = 24
    warning_before_hours: int = 8
    approval_required: bool = False
    next_nodes: list[str] = Field(default_factory=list)
    trigger_type: TriggerType = TriggerType.RESULT
    trigger_condition: str = ""
    trigger_event: str | None = None
    trigger_metric: str | None = None
    trigger_operator: str | None = None
    trigger_value: float | None = None
    initiator: str | None = None
    handoff: str | None = None
    action_ids: list[str] = Field(default_factory=list)
    deliverable_labels: dict[str, str] = Field(default_factory=dict)
    outcome_options: list[str] = Field(default_factory=list)


class ActionDefinition(BaseModel):
    """One of the 33 atomic actions from the Base action-detail table."""

    id: str
    node_id: str
    name: str
    trigger_condition: str
    owner_role: str
    owner_title: str
    collaborator_departments: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)
    acceptance_criteria: str
    next_action: str
    handoff: str
    sla: str


class RuleDefinition(BaseModel):
    id: str
    category: str
    rule: str
    definition: str
    purpose: str


class RoleAssignment(BaseModel):
    role: str
    department: str
    user_id: str
    display_name: str


class ProductProject(BaseModel):
    id: str = Field(default_factory=lambda: f"PRJ-{uuid4().hex[:8].upper()}")
    product_code: str
    product_name: str
    target_market: str
    sales_channel: str
    owner_user_id: str
    status: ProjectStatus = ProjectStatus.ACTIVE
    current_node_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None


class NodeInstance(BaseModel):
    id: str = Field(default_factory=lambda: f"NODE-{uuid4().hex[:10].upper()}")
    project_id: str
    definition_id: str
    status: NodeStatus = NodeStatus.PENDING
    owner_user_id: str | None = None
    reviewer_user_id: str | None = None
    collaborator_user_ids: list[str] = Field(default_factory=list)
    submitted_outputs: list[str] = Field(default_factory=list)
    submission_note: str | None = None
    rejection_reason: str | None = None
    block_reason: str | None = None
    started_at: datetime | None = None
    submitted_at: datetime | None = None
    completed_at: datetime | None = None


class AuditEvent(BaseModel):
    id: str = Field(default_factory=lambda: f"EVT-{uuid4().hex[:10].upper()}")
    project_id: str
    node_instance_id: str | None = None
    event_type: str
    actor_user_id: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
