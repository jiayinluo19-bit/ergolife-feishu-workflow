from datetime import datetime

from ..domain.enums import NodeStatus, ProjectStatus, TriggerType
from ..domain.models import AuditEvent, NodeDefinition, NodeInstance, ProductProject
from ..repositories.interfaces import Repository


class WorkflowError(ValueError):
    pass


class WorkflowService:
    def __init__(
        self,
        repository: Repository,
        definitions: dict[str, NodeDefinition],
        assignments: dict[str, str],
    ) -> None:
        self.repository = repository
        self.definitions = definitions
        self.assignments = assignments

    def create_project(self, project: ProductProject, actor_user_id: str) -> ProductProject:
        if project.current_node_id is not None:
            raise WorkflowError("项目已经启动")
        self.repository.save_project(project)
        first = self._first_definition()
        self._create_node(project, first, actor_user_id)
        self._record(project.id, "project_created", actor_user_id, {"product_code": project.product_code})
        return project

    def claim(self, node_id: str, actor_user_id: str) -> NodeInstance:
        node = self.repository.get_node(node_id)
        self._ensure_status(node, NodeStatus.READY)
        self._ensure_owner(node, actor_user_id)
        node.status = NodeStatus.IN_PROGRESS
        node.started_at = datetime.now().astimezone()
        self.repository.save_node(node)
        self._record(node.project_id, "node_claimed", actor_user_id, {}, node.id)
        return node

    def activate(self, node_id: str, actor_user_id: str, context: dict | None = None) -> NodeInstance:
        """Turn a not-yet-triggered event/threshold node into a claimable task."""
        node = self.repository.get_node(node_id)
        self._ensure_status(node, NodeStatus.PENDING)
        definition = self.definitions[node.definition_id]
        if not self._trigger_matches(definition, context or {}):
            raise WorkflowError(f"触发条件未满足: {definition.trigger_condition}")
        node.status = NodeStatus.READY
        self.repository.save_node(node)
        self._record(
            node.project_id,
            "node_triggered",
            actor_user_id,
            {"trigger_type": definition.trigger_type.value, "context": context or {}},
            node.id,
        )
        return node

    def submit(self, node_id: str, actor_user_id: str, outputs: list[str], note: str = "") -> NodeInstance:
        node = self.repository.get_node(node_id)
        self._ensure_status(node, NodeStatus.IN_PROGRESS)
        self._ensure_owner(node, actor_user_id)
        definition = self.definitions[node.definition_id]
        missing = sorted(set(definition.required_outputs) - set(outputs))
        if missing:
            raise WorkflowError(f"缺少必交付物: {', '.join(missing)}")
        node.status = NodeStatus.REVIEWING
        node.submitted_outputs = outputs
        node.submission_note = note
        node.submitted_at = datetime.now().astimezone()
        self.repository.save_node(node)
        self._record(node.project_id, "node_submitted", actor_user_id, {"outputs": outputs}, node.id)
        return node

    def accept(self, node_id: str, actor_user_id: str) -> NodeInstance:
        node = self.repository.get_node(node_id)
        self._ensure_status(node, NodeStatus.REVIEWING)
        self._ensure_reviewer(node, actor_user_id)
        node.status = NodeStatus.COMPLETED
        node.completed_at = datetime.now().astimezone()
        self.repository.save_node(node)
        project = self.repository.get_project(node.project_id)
        definition = self.definitions[node.definition_id]
        self._record(project.id, "node_accepted", actor_user_id, {}, node.id)
        if definition.next_nodes:
            next_definition = self.definitions[definition.next_nodes[0]]
            next_status = NodeStatus.READY if next_definition.trigger_type == TriggerType.RESULT else NodeStatus.PENDING
            self._create_node(project, next_definition, actor_user_id, initial_status=next_status)
        else:
            project.current_node_id = None
            project.status = ProjectStatus.COMPLETED
            project.completed_at = datetime.now().astimezone()
            self.repository.save_project(project)
            self._record(project.id, "project_completed", actor_user_id, {})
        return node

    def reject(self, node_id: str, actor_user_id: str, reason: str) -> NodeInstance:
        node = self.repository.get_node(node_id)
        self._ensure_status(node, NodeStatus.REVIEWING)
        self._ensure_reviewer(node, actor_user_id)
        node.status = NodeStatus.REJECTED
        node.rejection_reason = reason
        self.repository.save_node(node)
        self._record(node.project_id, "node_rejected", actor_user_id, {"reason": reason}, node.id)
        return node

    def resume_rejected(self, node_id: str, actor_user_id: str) -> NodeInstance:
        node = self.repository.get_node(node_id)
        self._ensure_status(node, NodeStatus.REJECTED)
        self._ensure_owner(node, actor_user_id)
        node.status = NodeStatus.IN_PROGRESS
        node.rejection_reason = None
        self.repository.save_node(node)
        self._record(node.project_id, "node_reopened", actor_user_id, {}, node.id)
        return node

    def block(self, node_id: str, actor_user_id: str, reason: str) -> NodeInstance:
        node = self.repository.get_node(node_id)
        if node.status not in {NodeStatus.READY, NodeStatus.IN_PROGRESS}:
            raise WorkflowError("只有待领取或进行中的节点可以阻塞")
        self._ensure_owner(node, actor_user_id)
        node.status = NodeStatus.BLOCKED
        node.block_reason = reason
        self.repository.save_node(node)
        project = self.repository.get_project(node.project_id)
        project.status = ProjectStatus.BLOCKED
        self.repository.save_project(project)
        self._record(project.id, "node_blocked", actor_user_id, {"reason": reason}, node.id)
        return node

    def unblock(self, node_id: str, actor_user_id: str) -> NodeInstance:
        node = self.repository.get_node(node_id)
        self._ensure_status(node, NodeStatus.BLOCKED)
        self._ensure_owner(node, actor_user_id)
        node.status = NodeStatus.IN_PROGRESS
        node.block_reason = None
        self.repository.save_node(node)
        project = self.repository.get_project(node.project_id)
        project.status = ProjectStatus.ACTIVE
        self.repository.save_project(project)
        self._record(project.id, "node_unblocked", actor_user_id, {}, node.id)
        return node

    def _create_node(
        self,
        project: ProductProject,
        definition: NodeDefinition,
        actor_user_id: str,
        *,
        initial_status: NodeStatus = NodeStatus.READY,
    ) -> NodeInstance:
        if definition.depends_on:
            for dependency in definition.depends_on:
                dep_nodes = [n for n in self.repository.list_project_nodes(project.id) if n.definition_id == dependency]
                if not dep_nodes or dep_nodes[-1].status != NodeStatus.COMPLETED:
                    raise WorkflowError(f"前置节点未完成: {dependency}")
        owner = self.assignments.get(definition.owner_role)
        reviewer = self.assignments.get(definition.reviewer_role or definition.owner_role)
        if not owner or not reviewer:
            raise WorkflowError(f"角色未配置: {definition.owner_role}")
        node = NodeInstance(
            project_id=project.id,
            definition_id=definition.id,
            status=initial_status,
            owner_user_id=owner,
            reviewer_user_id=reviewer,
            collaborator_user_ids=[self.assignments[r] for r in definition.collaborator_roles if r in self.assignments],
        )
        self.repository.save_node(node)
        project.current_node_id = node.id
        project.status = ProjectStatus.ACTIVE
        self.repository.save_project(project)
        self._record(project.id, "node_created", actor_user_id, {"definition_id": definition.id}, node.id)
        return node

    def _trigger_matches(self, definition: NodeDefinition, context: dict) -> bool:
        if definition.trigger_type == TriggerType.RESULT:
            return str(context.get("result", "accepted")).lower() in {"accepted", "approved", "pass", "completed"}
        if definition.trigger_type == TriggerType.EVENT:
            return bool(definition.trigger_event) and context.get("event") == definition.trigger_event
        if definition.trigger_type == TriggerType.THRESHOLD:
            if not definition.trigger_metric or definition.trigger_metric not in context:
                return False
            try:
                actual = float(context[definition.trigger_metric])
                expected = float(definition.trigger_value) if definition.trigger_value is not None else None
            except (TypeError, ValueError):
                return False
            if expected is None:
                return False
            operators = {
                "<": lambda: actual < expected,
                "<=": lambda: actual <= expected,
                "=": lambda: actual == expected,
                ">=": lambda: actual >= expected,
                ">": lambda: actual > expected,
            }
            return operators.get(definition.trigger_operator or "<=", lambda: False)()
        return False

    def _first_definition(self) -> NodeDefinition:
        roots = [d for d in self.definitions.values() if not d.depends_on]
        if len(roots) != 1:
            raise WorkflowError("串行 MVP 必须有且只有一个首节点")
        return roots[0]

    def _ensure_status(self, node: NodeInstance, expected: NodeStatus) -> None:
        if node.status != expected:
            raise WorkflowError(f"节点状态必须为 {expected.value}，当前为 {node.status.value}")

    def _ensure_owner(self, node: NodeInstance, actor_user_id: str) -> None:
        if node.owner_user_id != actor_user_id:
            raise WorkflowError("当前用户不是节点负责人")

    def _ensure_reviewer(self, node: NodeInstance, actor_user_id: str) -> None:
        if node.reviewer_user_id != actor_user_id:
            raise WorkflowError("当前用户不是节点验收人")

    def _record(self, project_id: str, event_type: str, actor_user_id: str, detail: dict, node_id: str | None = None) -> None:
        self.repository.add_event(AuditEvent(project_id=project_id, node_instance_id=node_id, event_type=event_type, actor_user_id=actor_user_id, detail=detail))
