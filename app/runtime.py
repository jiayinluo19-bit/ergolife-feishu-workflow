import os
from pathlib import Path

from .config_loader import load_assignments, load_definitions
from .domain.models import ProductProject
from .repositories.memory_repository import MemoryRepository
from .services.workflow_service import WorkflowService


ROOT = Path(__file__).resolve().parents[1]


class WorkflowRuntime:
    """MVP process-local runtime; replace the repository with Bitable later."""

    def __init__(self) -> None:
        self.repository = MemoryRepository()
        self.definitions = load_definitions(ROOT / "config" / "workflow_v1.yaml")
        self.assignments = load_assignments(ROOT / "config" / "role_mapping.mock.yaml")
        configured_user = os.getenv("FEISHU_TEST_RECEIVE_ID", "").strip()
        if os.getenv("FEISHU_RECEIVE_ID_TYPE", "open_id") == "open_id" and configured_user:
            self.assignments["product_manager"] = configured_user
        self.service = WorkflowService(self.repository, self.definitions, self.assignments)
        self.simulation_mode = os.getenv("WORKFLOW_SIMULATION_MODE", "true").lower() in {"1", "true", "yes", "on"}
        self._ensure_mock_project()

    def _ensure_mock_project(self) -> None:
        project_id = "PRJ-MOCK-001"
        if project_id in self.repository.projects:
            return
        owner = self.assignments["product_manager"]
        project = ProductProject(
            id=project_id,
            product_code="MOCK-2026-001",
            product_name="ERGOLIFE 人体工学办公椅 X1",
            target_market="美国",
            sales_channel="Amazon US",
            owner_user_id=owner,
        )
        self.service.create_project(project, owner)
        node = self.repository.get_node(project.current_node_id)
        del self.repository.nodes[node.id]
        node.id = "NODE-P01-MOCK"
        self.repository.save_node(node)
        project.current_node_id = node.id
        self.repository.save_project(project)

    def project_summary(self, project_id: str) -> str:
        project = self.repository.get_project(project_id)
        nodes = self.repository.list_project_nodes(project_id)
        completed = sum(node.status.value == "completed" for node in nodes)
        current = self.repository.get_node(project.current_node_id) if project.current_node_id else None
        if current:
            definition = self.definitions[current.definition_id]
            current_text = f"当前节点：{current.definition_id} {definition.name}（{current.status.value}）"
            owner_text = f"负责人：{current.owner_user_id}"
        else:
            current_text = "当前节点：已完成"
            owner_text = ""
        return (
            f"商品：{project.product_name}\n"
            f"项目状态：{project.status.value}\n"
            f"进度：{completed}/{len(self.definitions)}\n"
            f"{current_text}\n{owner_text}"
        )

    def claim_node(self, node_id: str, operator_user_id: str):
        node = self.repository.get_node(node_id)
        if self.simulation_mode and node.owner_user_id != operator_user_id:
            # In the MVP one real Feishu user can impersonate the configured
            # department role so the complete serial chain can be demonstrated.
            return self.service.claim(node_id, node.owner_user_id)
        return self.service.claim(node_id, operator_user_id)

    def simulate_complete(self, node_id: str, operator_user_id: str):
        node = self.repository.get_node(node_id)
        if node.status.value == "ready":
            node = self.claim_node(node_id, operator_user_id)
        if node.status.value == "in_progress":
            definition = self.definitions[node.definition_id]
            node = self.service.submit(node.id, node.owner_user_id, definition.required_outputs)
        if node.status.value == "reviewing":
            node = self.service.accept(node.id, node.reviewer_user_id)
        project = self.repository.get_project(node.project_id)
        return node, project

    def current_card_data(self, project_id: str) -> dict[str, str] | None:
        project = self.repository.get_project(project_id)
        if not project.current_node_id:
            return None
        node = self.repository.get_node(project.current_node_id)
        definition = self.definitions[node.definition_id]
        return {
            "project_id": project.id,
            "node_instance_id": node.id,
            "product_name": project.product_name,
            "node_name": f"{node.definition_id} {definition.name}",
            "owner_name": f"模拟角色：{definition.owner_role}",
        }


runtime = WorkflowRuntime()
