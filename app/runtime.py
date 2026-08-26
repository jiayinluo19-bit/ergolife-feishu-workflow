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
        self._seed_demo_project(
            project_id="PRJ-MOCK-001",
            product_code="MOCK-2026-001",
            product_name="ERGOLIFE 人体工学办公椅 X1",
            completed_nodes=0,
            initial_node_id="NODE-P01-MOCK",
        )
        self._seed_demo_project(
            project_id="PRJ-MOCK-002",
            product_code="MOCK-2026-002",
            product_name="ERGOLIFE 智能升降桌 E2",
            completed_nodes=4,
        )
        self._seed_demo_project(
            project_id="PRJ-MOCK-003",
            product_code="MOCK-2026-003",
            product_name="ERGOLIFE 运动护腰 Pro",
            completed_nodes=11,
        )

    def _seed_demo_project(
        self,
        *,
        project_id: str,
        product_code: str,
        product_name: str,
        completed_nodes: int,
        initial_node_id: str | None = None,
    ) -> None:
        if project_id in self.repository.projects:
            return
        owner = self.assignments["product_manager"]
        project = ProductProject(
            id=project_id,
            product_code=product_code,
            product_name=product_name,
            target_market="美国",
            sales_channel="Amazon US",
            owner_user_id=owner,
        )
        self.service.create_project(project, owner)
        if initial_node_id:
            node = self.repository.get_node(project.current_node_id)
            del self.repository.nodes[node.id]
            node.id = initial_node_id
            self.repository.save_node(node)
            project.current_node_id = node.id
            self.repository.save_project(project)
        for _ in range(completed_nodes):
            node = self.repository.get_node(project.current_node_id)
            self.service.claim(node.id, node.owner_user_id)
            definition = self.definitions[node.definition_id]
            self.service.submit(node.id, node.owner_user_id, definition.required_outputs)
            self.service.accept(node.id, node.reviewer_user_id)

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

    def lifecycle_lines(self, project_id: str) -> list[str]:
        project = self.repository.get_project(project_id)
        nodes = {node.definition_id: node for node in self.repository.list_project_nodes(project_id)}
        lines = []
        for definition_id, definition in self.definitions.items():
            node = nodes.get(definition_id)
            status = node.status.value if node else "pending"
            icon = {"completed": "✅", "in_progress": "🔵", "ready": "🟡", "reviewing": "🟣", "rejected": "🔴"}.get(status, "⚪")
            lines.append(f"{icon} {definition_id} {definition.name}｜{status}")
        return lines

    def dashboard_data(self) -> list[dict]:
        result = []
        for project in self.repository.projects.values():
            nodes = {node.definition_id: node for node in self.repository.list_project_nodes(project.id)}
            current = self.repository.get_node(project.current_node_id) if project.current_node_id else None
            result.append(
                {
                    "id": project.id,
                    "product_code": project.product_code,
                    "product_name": project.product_name,
                    "target_market": project.target_market,
                    "sales_channel": project.sales_channel,
                    "status": project.status.value,
                    "current_node_id": current.definition_id if current else None,
                    "current_node_name": self.definitions[current.definition_id].name if current else "已完成",
                    "completed": sum(node.status.value == "completed" for node in nodes.values()),
                    "total": len(self.definitions),
                    "nodes": [
                        {
                            "id": definition_id,
                            "name": definition.name,
                            "stage": definition.stage,
                            "status": nodes[definition_id].status.value if definition_id in nodes else "pending",
                            "owner_role": definition.owner_role,
                        }
                        for definition_id, definition in self.definitions.items()
                    ],
                }
            )
        return result


runtime = WorkflowRuntime()
