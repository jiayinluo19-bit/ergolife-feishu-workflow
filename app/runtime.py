import os
import json
from pathlib import Path

from .config_loader import load_actions, load_assignments, load_definitions, load_role_assignments, load_role_rules, load_rules
from .domain.models import ProductProject
from .repositories.directory_repository import DirectoryRepository, role_rules_from_assignments
from .repositories.lifecycle_repository import LifecycleRepository
from .repositories.memory_repository import MemoryRepository
from .repositories.product_repository import ProductRepository
from .services.product_access_service import ProductAccessService
from .services.workflow_service import WorkflowService


ROOT = Path(__file__).resolve().parents[1]


class WorkflowRuntime:
    """Runtime for the legacy workflow compatibility endpoints.

    The old workflow PostgreSQL persistence has been retired. Real product
    lifecycle state is served by ProductRepository and the directory tables
    remain in the application PostgreSQL database; this compatibility layer
    is intentionally process-local for legacy compatibility. Real product
    lifecycle history uses the formal lifecycle tables in the application DB.
    """

    def __init__(self) -> None:
        self.repository = MemoryRepository()
        database_url = os.getenv("DATABASE_URL", "").strip()
        repository_mode = os.getenv("WORKFLOW_REPOSITORY", "auto").strip().lower()
        # The legacy workflow_* tables are retired. Keep the old service
        # objects in memory for compatibility with tests and historical card
        # actions, but never create or read those tables in production.
        self.definitions = load_definitions(ROOT / "config" / "workflow_v1.yaml")
        self.actions = load_actions(ROOT / "config" / "actions_v1.yaml")
        self.rules = load_rules(ROOT / "config" / "rules_v1.yaml")
        self.assignments = load_assignments(ROOT / "config" / "role_mapping.mock.yaml")
        self.role_assignments = load_role_assignments(ROOT / "config" / "role_mapping.mock.yaml")
        directory_dsn = database_url if repository_mode != "memory" else ""
        demo_mode = os.getenv("DEMO_MODE", "true").lower() in {"1", "true", "yes", "on"}
        admin_open_ids = [
            item.strip().strip('"\'')
            for item in os.getenv("FEISHU_ADMIN_OPEN_IDS", "").split(",")
            if item.strip().strip('"\'')
        ]
        if demo_mode and not admin_open_ids and os.getenv("FEISHU_TEST_RECEIVE_ID", "").strip():
            # Demo-only convenience: the existing test recipient can open the
            # admin console without another Railway variable.  Production
            # requires an explicit admin list or a Feishu tenant manager.
            admin_open_ids = [os.getenv("FEISHU_TEST_RECEIVE_ID", "").strip()]
        self.directory = DirectoryRepository(
            directory_dsn,
            role_rules=(
                load_role_rules(ROOT / "config" / "role_rules.yaml")
                or role_rules_from_assignments(self.role_assignments)
            ),
            known_roles=self.role_assignments.keys(),
            admin_open_ids=admin_open_ids,
        )
        self.directory_sync_status: dict[str, object] = {"status": "idle", "fetched": 0, "synced": 0, "error": None}
        configured_user = os.getenv("FEISHU_TEST_RECEIVE_ID", "").strip()
        if os.getenv("FEISHU_RECEIVE_ID_TYPE", "open_id") == "open_id" and configured_user:
            self.assignments["product_manager"] = configured_user
            if "product_manager" in self.role_assignments:
                self.role_assignments["product_manager"] = self.role_assignments["product_manager"].model_copy(
                    update={"user_id": configured_user}
                )
        # Production can map real Feishu open_ids without editing the workflow
        # definition files.  Example: {"product_manager":"ou_xxx", "quality_reviewer":"ou_yyy"}.
        raw_role_map = os.getenv("FEISHU_ROLE_USER_MAP_JSON", "").strip()
        if raw_role_map:
            try:
                role_map = json.loads(raw_role_map)
            except json.JSONDecodeError:
                role_map = {}
            for role, user_id in role_map.items() if isinstance(role_map, dict) else []:
                if role in self.role_assignments and str(user_id).strip():
                    normalized_user_id = str(user_id).strip()
                    self.assignments[role] = normalized_user_id
                    self.role_assignments[role] = self.role_assignments[role].model_copy(
                        update={"user_id": normalized_user_id}
                    )
        self.service = WorkflowService(self.repository, self.definitions, self.assignments)
        self.simulation_mode = os.getenv("WORKFLOW_SIMULATION_MODE", "true").lower() in {"1", "true", "yes", "on"}
        self.product_repository = ProductRepository()
        self.lifecycle_repository = LifecycleRepository(directory_dsn)
        self.product_access = ProductAccessService(
            self.product_repository,
            self.definitions,
            self.role_assignments,
            demo_mode=demo_mode,
            directory=self.directory,
            lifecycle_repository=self.lifecycle_repository,
        )
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
            self._advance_demo_project_to(project_id, completed_nodes)
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
            self.repository.delete_node(node.id)
            node.id = initial_node_id
            self.repository.save_node(node)
            project.current_node_id = node.id
            self.repository.save_project(project)
        self._advance_demo_project_to(project_id, completed_nodes)

    def _advance_demo_project_to(self, project_id: str, completed_nodes: int) -> None:
        """Idempotently bring an in-memory demo project to its checkpoint."""
        completed = sum(
            node.status.value == "completed"
            for node in self.repository.list_project_nodes(project_id)
        )
        for _ in range(max(0, completed_nodes - completed)):
            project = self.repository.get_project(project_id)
            if not project.current_node_id:
                return
            node = self.repository.get_node(project.current_node_id)
            if node.status.value == "pending":
                self.service.activate(
                    node.id,
                    node.owner_user_id,
                    self._mock_trigger_context(self.definitions[node.definition_id]),
                )
            self.service.claim(node.id, node.owner_user_id)
            definition = self.definitions[node.definition_id]
            self.service.submit(node.id, node.owner_user_id, definition.required_outputs)
            self.service.accept(node.id, node.reviewer_user_id)

    @staticmethod
    def _mock_trigger_context(definition) -> dict:
        if definition.trigger_type.value == "event":
            return {"event": definition.trigger_event}
        if definition.trigger_type.value == "threshold":
            return {definition.trigger_metric: definition.trigger_value}
        return {"result": "accepted"}

    def project_summary(self, project_id: str) -> str:
        project = self.repository.get_project(project_id)
        nodes = self.repository.list_project_nodes(project_id)
        completed = sum(node.status.value == "completed" for node in nodes)
        current = self.repository.get_node(project.current_node_id) if project.current_node_id else None
        if current:
            definition = self.definitions[current.definition_id]
            current_text = f"当前节点：{current.definition_id} {definition.name}（{self._source_status(current.status.value)}）"
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
        if node.status.value == "pending":
            node = self.service.activate(node.id, operator_user_id, self._mock_trigger_context(self.definitions[node.definition_id]))
        if node.status.value == "ready":
            node = self.claim_node(node_id, operator_user_id)
        if node.status.value == "in_progress":
            definition = self.definitions[node.definition_id]
            node = self.service.submit(node.id, node.owner_user_id, definition.required_outputs)
        if node.status.value == "reviewing":
            node = self.service.accept(node.id, node.reviewer_user_id)
        project = self.repository.get_project(node.project_id)
        return node, project

    def trigger_node(self, node_id: str, operator_user_id: str):
        node = self.repository.get_node(node_id)
        definition = self.definitions[node.definition_id]
        return self.service.activate(node.id, operator_user_id, self._mock_trigger_context(definition))

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
            "node_status": node.status.value,
            "source_status": self._source_status(node.status.value),
            "trigger_type": definition.trigger_type.value,
            "trigger_condition": definition.trigger_condition,
        }

    def lifecycle_lines(self, project_id: str) -> list[str]:
        project = self.repository.get_project(project_id)
        nodes = {node.definition_id: node for node in self.repository.list_project_nodes(project_id)}
        lines = []
        for definition_id, definition in self.definitions.items():
            node = nodes.get(definition_id)
            status = node.status.value if node else "pending"
            icon = {"completed": "✅", "in_progress": "🔵", "ready": "🟡", "reviewing": "🟣", "rejected": "🔴"}.get(status, "⚪")
            lines.append(f"{icon} {definition_id} {definition.name}｜{self._source_status(status)}")
        return lines

    def real_lifecycle_dashboard_data(self, product_id: str | None = None) -> list[dict]:
        """Adapt product_market_parameters rows to the detailed lifecycle view.

        The product master currently stores the current lifecycle node, not a
        separate node-event history.  We therefore derive node states from
        that value and leave event lists empty when the formal history store
        is not configured.
        """
        if self.lifecycle_repository.dsn:
            return self._real_lifecycle_dashboard_data_with_history(product_id)
        products = self.product_repository.list_active(limit=500)
        if not products:
            return []
        product = next((item for item in products if item.id == product_id), products[0])
        ordered_codes = list(self.definitions)
        current_code = product.lifecycle_node_code
        current_index = ordered_codes.index(current_code) if current_code in ordered_codes else 0
        nodes = []
        for index, code in enumerate(ordered_codes):
            definition = self.definitions[code]
            assignment = self.role_assignments.get(definition.owner_role)
            if code == current_code:
                status = "in_progress"
                owner_user_id = assignment.user_id if assignment else ""
                started_at = product.updated_at
            elif index < current_index:
                status = "completed"
                owner_user_id = assignment.user_id if assignment else ""
                started_at = None
            else:
                status = "pending"
                owner_user_id = assignment.user_id if assignment else ""
                started_at = None
            nodes.append(
                {
                    "id": code,
                    "name": definition.name,
                    "stage": definition.stage,
                    "status": status,
                    "source_status": self._source_status(status),
                    "owner_role": definition.owner_role,
                    "owner_user_id": owner_user_id,
                    "reviewer_user_id": assignment.user_id if assignment else "",
                    "started_at": started_at,
                    "submitted_at": None,
                    "completed_at": None,
                    "events": [],
                    "trigger_type": definition.trigger_type.value,
                    "trigger_condition": definition.trigger_condition,
                    "trigger_event": definition.trigger_event,
                    "trigger_metric": definition.trigger_metric,
                    "trigger_operator": definition.trigger_operator,
                    "trigger_value": definition.trigger_value,
                    "initiator": definition.initiator,
                    "handoff": definition.handoff,
                    "action_ids": definition.action_ids,
                    "actions": [self.actions[action_id].model_dump(mode="json") for action_id in definition.action_ids if action_id in self.actions],
                    "outcome_options": definition.outcome_options,
                }
            )
        stage_names = []
        for node in nodes:
            if node["stage"] not in stage_names:
                stage_names.append(node["stage"])
        stages = []
        for stage in stage_names:
            stage_nodes = [node for node in nodes if node["stage"] == stage]
            completed = sum(node["status"] == "completed" for node in stage_nodes)
            if completed == len(stage_nodes):
                stage_status = "completed"
            elif stage == self.definitions[current_code].stage if current_code in self.definitions else False:
                stage_status = "current"
            else:
                stage_status = "upcoming"
            stages.append({"name": stage, "status": stage_status, "completed": completed, "total": len(stage_nodes), "nodes": stage_nodes})
        current_node = nodes[current_index] if nodes else None
        selected_project = {
            "id": product.id,
            "product_code": product.sku,
            "product_name": product.product_name,
            "target_market": product.country_code,
            "sales_channel": product.store or "—",
            "status": "active" if product.is_active else "inactive",
            "current_node_id": current_code,
            "current_node_name": self.definitions[current_code].name if current_code in self.definitions else "未配置节点",
            "completed": sum(node["status"] == "completed" for node in nodes),
            "total": len(nodes),
            "current_stage": self.definitions[current_code].stage if current_code in self.definitions else "未配置阶段",
            "previous_node": nodes[current_index - 1] if current_index > 0 and nodes else None,
            "current_node": current_node,
            "next_node": nodes[current_index + 1] if current_index + 1 < len(nodes) else None,
            "nodes": nodes,
            "stages": stages,
            "rules": [rule.model_dump(mode="json") for rule in self.rules.values()],
            "source": self.product_repository.last_source,
        }
        # Keep every real product in the selector at the top of the detail
        # page, while only expanding the selected product's 22-node detail.
        result = [selected_project]
        for item in products:
            if item.id == product.id:
                continue
            item_index = ordered_codes.index(item.lifecycle_node_code) if item.lifecycle_node_code in ordered_codes else 0
            result.append(
                {
                    "id": item.id,
                    "product_code": item.sku,
                    "product_name": item.product_name,
                    "target_market": item.country_code,
                    "sales_channel": item.store or "—",
                    "status": "active" if item.is_active else "inactive",
                    "current_node_id": item.lifecycle_node_code,
                    "current_node_name": self.definitions[item.lifecycle_node_code].name if item.lifecycle_node_code in self.definitions else "未配置节点",
                    "completed": item_index,
                    "total": len(ordered_codes),
                    "current_stage": self.definitions[item.lifecycle_node_code].stage if item.lifecycle_node_code in self.definitions else "未配置阶段",
                    "previous_node": None,
                    "current_node": None,
                    "next_node": None,
                    "nodes": [],
                    "stages": [],
                    "rules": [],
                    "source": self.product_repository.last_source,
                }
            )
        return result

    def _real_lifecycle_dashboard_data_with_history(self, product_id: str | None = None) -> list[dict]:
        products = self.product_repository.list_active(limit=500)
        if not products:
            return []
        product = next((item for item in products if item.id == product_id), products[0])
        assignments = {role: assignment.user_id for role, assignment in self.role_assignments.items()}
        self.lifecycle_repository.ensure_product(
            product.id, product.lifecycle_node_code, self.definitions, assignments
        )
        snapshot = self.lifecycle_repository.snapshot(product.id)
        if snapshot is None:
            return []
        nodes = []
        for record in snapshot.nodes:
            definition = self.definitions.get(record.definition_id)
            if definition is None:
                continue
            nodes.append(
                {
                    "id": record.id,
                    "definition_id": record.definition_id,
                    "occurrence": record.occurrence,
                    "name": definition.name,
                    "stage": definition.stage,
                    "status": record.status,
                    "source_status": self._source_status(record.status),
                    "owner_role": definition.owner_role,
                    "owner_user_id": record.owner_user_id,
                    "reviewer_user_id": record.reviewer_user_id,
                    "started_at": record.started_at,
                    "submitted_at": record.submitted_at,
                    "completed_at": record.completed_at,
                    "events": record.events,
                    "trigger_type": definition.trigger_type.value,
                    "trigger_condition": definition.trigger_condition,
                    "trigger_event": definition.trigger_event,
                    "trigger_metric": definition.trigger_metric,
                    "trigger_operator": definition.trigger_operator,
                    "trigger_value": definition.trigger_value,
                    "initiator": definition.initiator,
                    "handoff": definition.handoff,
                    "action_ids": definition.action_ids,
                    "actions": [
                        self.actions[action_id].model_dump(mode="json")
                        for action_id in definition.action_ids
                        if action_id in self.actions
                    ],
                    "outcome_options": definition.outcome_options,
                }
            )
        current_index = next(
            (index for index, node in enumerate(nodes) if node["id"] == snapshot.current_node_id),
            next((index for index, node in enumerate(nodes) if node["definition_id"] == snapshot.current_node_code), 0),
        )
        current_node = nodes[current_index] if nodes else None
        current_definition = self.definitions.get(snapshot.current_node_code)
        stage_names = list(dict.fromkeys(node["stage"] for node in nodes))
        stages = []
        for stage in stage_names:
            stage_nodes = [node for node in nodes if node["stage"] == stage]
            completed = sum(node["status"] == "completed" for node in stage_nodes)
            stage_status = (
                "completed"
                if completed == len(stage_nodes)
                else "current"
                if current_definition and stage == current_definition.stage
                else "upcoming"
            )
            stages.append(
                {
                    "name": stage,
                    "status": stage_status,
                    "completed": completed,
                    "total": len(stage_nodes),
                    "nodes": stage_nodes,
                }
            )
        selected_project = {
            "id": product.id,
            "product_code": product.sku,
            "product_name": product.product_name,
            "target_market": product.country_code,
            "sales_channel": product.store or "—",
            "status": "active" if product.is_active else "inactive",
            "current_node_id": snapshot.current_node_id or snapshot.current_node_code,
            "current_node_code": snapshot.current_node_code,
            "current_node_name": current_definition.name if current_definition else "未配置节点",
            "completed": sum(node["status"] == "completed" for node in nodes),
            "total": len(nodes),
            "current_stage": current_definition.stage if current_definition else "未配置阶段",
            "previous_node": nodes[current_index - 1] if current_index > 0 and nodes else None,
            "current_node": current_node,
            "next_node": nodes[current_index + 1] if current_index + 1 < len(nodes) else None,
            "nodes": nodes,
            "stages": stages,
            "rules": [rule.model_dump(mode="json") for rule in self.rules.values()],
            "source": self.product_repository.last_source,
            "history_source": self.lifecycle_repository.source,
        }
        result = [selected_project]
        ordered_codes = list(self.definitions)
        for item in products:
            if item.id == product.id:
                continue
            item_index = ordered_codes.index(item.lifecycle_node_code) if item.lifecycle_node_code in ordered_codes else 0
            item_definition = self.definitions.get(item.lifecycle_node_code)
            result.append(
                {
                    "id": item.id,
                    "product_code": item.sku,
                    "product_name": item.product_name,
                    "target_market": item.country_code,
                    "sales_channel": item.store or "—",
                    "status": "active" if item.is_active else "inactive",
                    "current_node_id": item.lifecycle_node_code,
                    "current_node_code": item.lifecycle_node_code,
                    "current_node_name": item_definition.name if item_definition else "未配置节点",
                    "completed": item_index,
                    "total": len(ordered_codes),
                    "current_stage": item_definition.stage if item_definition else "未配置阶段",
                    "previous_node": None,
                    "current_node": None,
                    "next_node": None,
                    "nodes": [],
                    "stages": [],
                    "rules": [],
                    "source": self.product_repository.last_source,
                    "history_source": self.lifecycle_repository.source,
                }
            )
        return result

    def dashboard_data(self) -> list[dict]:
        result = []
        for project in self.repository.projects.values():
            nodes = {node.definition_id: node for node in self.repository.list_project_nodes(project.id)}
            current = self.repository.get_node(project.current_node_id) if project.current_node_id else None
            definitions = list(self.definitions.values())
            node_rows = []
            for definition in definitions:
                node = nodes.get(definition.id)
                events = []
                if node:
                    events = [
                        {
                            "type": event.event_type,
                            "actor": event.actor_user_id,
                            "created_at": event.created_at.isoformat(),
                        }
                        for event in self.repository.events
                        if event.project_id == project.id and event.node_instance_id == node.id
                    ]
                node_rows.append(
                    {
                        "id": definition.id,
                        "name": definition.name,
                        "stage": definition.stage,
                        "status": node.status.value if node else "pending",
                        "source_status": self._source_status(node.status.value if node else "pending"),
                        "owner_role": definition.owner_role,
                        "owner_user_id": node.owner_user_id if node else self.assignments.get(definition.owner_role, ""),
                        "reviewer_user_id": node.reviewer_user_id if node else self.assignments.get(definition.reviewer_role or definition.owner_role, ""),
                        "started_at": node.started_at.isoformat() if node and node.started_at else None,
                        "submitted_at": node.submitted_at.isoformat() if node and node.submitted_at else None,
                        "completed_at": node.completed_at.isoformat() if node and node.completed_at else None,
                        "events": events,
                        "trigger_type": definition.trigger_type.value,
                        "trigger_condition": definition.trigger_condition,
                        "trigger_event": definition.trigger_event,
                        "trigger_metric": definition.trigger_metric,
                        "trigger_operator": definition.trigger_operator,
                        "trigger_value": definition.trigger_value,
                        "initiator": definition.initiator,
                        "handoff": definition.handoff,
                        "action_ids": definition.action_ids,
                        "actions": [self.actions[action_id].model_dump(mode="json") for action_id in definition.action_ids if action_id in self.actions],
                        "outcome_options": definition.outcome_options,
                    }
                )
            stage_names = []
            for row in node_rows:
                if row["stage"] not in stage_names:
                    stage_names.append(row["stage"])
            stages = []
            current_stage = current.definition_id if current else None
            current_stage_name = next((row["stage"] for row in node_rows if row["id"] == current_stage), None)
            for stage_name in stage_names:
                stage_nodes = [row for row in node_rows if row["stage"] == stage_name]
                completed = sum(row["status"] == "completed" for row in stage_nodes)
                if completed == len(stage_nodes):
                    stage_status = "completed"
                elif stage_name == current_stage_name:
                    stage_status = "current"
                else:
                    stage_status = "upcoming"
                stages.append(
                    {
                        "name": stage_name,
                        "status": stage_status,
                        "completed": completed,
                        "total": len(stage_nodes),
                        "nodes": stage_nodes,
                    }
                )
            current_index = next((index for index, row in enumerate(node_rows) if row["id"] == current_stage), None)
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
                    "current_stage": current_stage_name,
                    "previous_node": node_rows[current_index - 1] if current_index is not None and current_index > 0 else None,
                    "current_node": node_rows[current_index] if current_index is not None else None,
                    "next_node": node_rows[current_index + 1] if current_index is not None and current_index + 1 < len(node_rows) else None,
                    "nodes": node_rows,
                    "stages": stages,
                    "rules": [rule.model_dump(mode="json") for rule in self.rules.values()],
                }
            )
        return result

    def product_dashboard_data(
        self,
        *,
        view: str = "mine",
        open_id: str | None = None,
        demo_role: str | None = None,
    ) -> dict:
        return self.product_access.list_products(view=view, open_id=open_id, demo_role=demo_role)

    def advance_product(
        self,
        product_id: str,
        *,
        open_id: str | None = None,
        demo_role: str | None = None,
    ) -> dict:
        return self.product_access.advance_product(product_id, open_id=open_id, demo_role=demo_role)

    def sync_feishu_user(self, user: dict) -> None:
        """Project the Feishu login profile into the local employee directory."""
        open_id = str(user.get("open_id") or "").strip()
        if not open_id:
            return
        department_ids = user.get("department_ids") or []
        department_names = user.get("department_names") or []
        if isinstance(department_ids, str):
            department_ids = [department_ids]
        if isinstance(department_names, str):
            department_names = [department_names]
        self.directory.upsert_user(
            open_id=open_id,
            user_id=user.get("user_id"),
            display_name=user.get("name") or user.get("en_name") or "未命名用户",
            email=user.get("email") or user.get("enterprise_email"),
            job_title=user.get("job_title"),
            department_ids=department_ids,
            department_names=department_names,
            active=not bool(user.get("is_frozen", False)),
            is_tenant_manager=bool(user.get("is_tenant_manager", False)),
        )

    def sync_all_feishu_users(self) -> dict[str, int]:
        """One-shot tenant directory import, normally triggered by an admin."""
        from .integrations.feishu.client import FeishuOpenAPI

        self.directory_sync_status = {"status": "running", "fetched": 0, "synced": 0, "error": None}
        try:
            users = FeishuOpenAPI().list_directory_users()
            for user in users:
                self.sync_feishu_user(user)
            result = {"status": "succeeded", "fetched": len(users), "synced": len(users), "error": None}
            self.directory_sync_status = result
            return result
        except Exception as exc:
            self.directory_sync_status = {
                "status": "failed",
                "fetched": 0,
                "synced": 0,
                "error": str(exc)[:500],
            }
            raise

    def directory_admin_data(self) -> dict:
        return {
            "users": self.directory.list_users(),
            "role_rules": self.directory.list_role_rules(),
            "roles": [item["role"] for item in self.product_access.available_roles()],
            "sync_status": self.directory_sync_status,
        }

    @staticmethod
    def _source_status(status: str) -> str:
        return {
            "pending": "未开始",
            "ready": "未开始",
            "in_progress": "进行中",
            "reviewing": "待评审",
            "completed": "已完成",
            "rejected": "异常",
            "blocked": "异常",
            "cancelled": "异常",
        }.get(status, status)


runtime = WorkflowRuntime()
