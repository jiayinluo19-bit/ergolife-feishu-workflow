from enum import StrEnum


class TriggerType(StrEnum):
    """Trigger categories defined by the ERGOLIFE SOP/Base."""

    EVENT = "event"
    RESULT = "result"
    THRESHOLD = "threshold"


class NodeStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    REVIEWING = "reviewing"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# Keep internal state names stable for APIs while exposing the Base labels in UI.
SOURCE_STATUS_LABELS = {
    NodeStatus.PENDING: "未开始",
    NodeStatus.READY: "未开始",
    NodeStatus.IN_PROGRESS: "进行中",
    NodeStatus.REVIEWING: "待评审",
    NodeStatus.REJECTED: "异常",
    NodeStatus.BLOCKED: "异常",
    NodeStatus.COMPLETED: "已完成",
    NodeStatus.CANCELLED: "异常",
}


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
