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


runtime = WorkflowRuntime()
