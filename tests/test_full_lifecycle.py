from pathlib import Path

import pytest

from app.config_loader import load_actions, load_assignments, load_definitions, load_rules
from app.domain.models import ProductProject
from app.repositories.memory_repository import MemoryRepository
from app.services.workflow_service import WorkflowError, WorkflowService


ROOT = Path(__file__).parents[1]


def make_service() -> tuple[WorkflowService, MemoryRepository]:
    repo = MemoryRepository()
    service = WorkflowService(
        repo,
        load_definitions(ROOT / "config" / "workflow_v1.yaml"),
        load_assignments(ROOT / "config" / "role_mapping.mock.yaml"),
    )
    return service, repo


def trigger_if_needed(service, node):
    definition = service.definitions[node.definition_id]
    if node.status.value == "pending":
        if definition.trigger_type.value == "event":
            return service.activate(node.id, node.owner_user_id, {"event": definition.trigger_event})
        if definition.trigger_type.value == "threshold":
            return service.activate(node.id, node.owner_user_id, {definition.trigger_metric: definition.trigger_value})
        return service.activate(node.id, node.owner_user_id, {"result": "accepted"})
    return node


def test_full_serial_lifecycle_loops_from_p22_to_p12():
    service, repo = make_service()
    project = ProductProject(
        product_code="MOCK-2026-001",
        product_name="ERGOLIFE 人体工学办公椅 X1",
        target_market="美国",
        sales_channel="Amazon US",
        owner_user_id="mock_product_manager",
    )
    service.create_project(project, "mock_product_manager")

    for node_id in [f"P{i:02d}" for i in range(1, 23)]:
        node = next(n for n in repo.list_project_nodes(project.id) if n.definition_id == node_id)
        node = trigger_if_needed(service, node)
        service.claim(node.id, node.owner_user_id)
        outputs = service.definitions[node_id].required_outputs
        service.submit(node.id, node.owner_user_id, outputs)
        service.accept(node.id, node.reviewer_user_id)

    saved = repo.get_project(project.id)
    assert saved.status.value == "active"
    assert saved.current_node_id is not None
    assert repo.get_node(saved.current_node_id).definition_id == "P12"
    assert len(repo.list_project_nodes(project.id)) == 23
    assert sum(node.definition_id == "P12" for node in repo.list_project_nodes(project.id)) == 2
    assert len(repo.events) >= 22 * 4


def test_trigger_rules_and_action_catalog_are_loaded():
    service, _ = make_service()
    assert len(service.definitions) == 22
    assert service.definitions["P01"].trigger_type.value == "event"
    assert service.definitions["P22"].next_nodes == ["P12"]
    actions = load_actions(ROOT / "config" / "actions_v1.yaml")
    assert len(actions) == 33
    assert actions["A033"].node_id == "P22"
    rules = load_rules(ROOT / "config" / "rules_v1.yaml")
    assert len(rules) == 20
    assert rules["R009"].rule == "阈值触发"


def test_event_and_threshold_nodes_wait_for_trigger():
    service, repo = make_service()
    project = ProductProject(
        product_code="MOCK-2026-003",
        product_name="测试触发",
        target_market="美国",
        sales_channel="Amazon US",
        owner_user_id="mock_product_manager",
    )
    service.create_project(project, "mock_product_manager")
    # Complete P01-P04; P05 is an event-triggered node and must remain pending.
    for node_id in ["P01", "P02", "P03", "P04"]:
        node = next(n for n in repo.list_project_nodes(project.id) if n.definition_id == node_id)
        trigger_if_needed(service, node)
        service.claim(node.id, node.owner_user_id)
        service.submit(node.id, node.owner_user_id, service.definitions[node_id].required_outputs)
        service.accept(node.id, node.reviewer_user_id)
    node = repo.get_node(project.current_node_id)
    assert node.definition_id == "P05"
    assert node.status.value == "pending"
    with pytest.raises(WorkflowError, match="触发条件未满足"):
        service.activate(node.id, node.owner_user_id, {"event": "wrong_event"})
    service.activate(node.id, node.owner_user_id, {"event": "sample_arrived"})
    assert repo.get_node(node.id).status.value == "ready"


def test_rejection_requires_resubmission_before_next_node():
    service, repo = make_service()
    project = ProductProject(
        product_code="MOCK-2026-002",
        product_name="测试商品",
        target_market="美国",
        sales_channel="Amazon US",
        owner_user_id="mock_product_manager",
    )
    service.create_project(project, "mock_product_manager")
    node = repo.get_node(project.current_node_id)
    service.claim(node.id, node.owner_user_id)
    service.submit(node.id, node.owner_user_id, service.definitions["P01"].required_outputs)
    service.reject(node.id, node.reviewer_user_id, "机会信息需要补充")
    assert repo.get_project(project.id).current_node_id == node.id
    service.resume_rejected(node.id, node.owner_user_id)
    service.submit(node.id, node.owner_user_id, service.definitions["P01"].required_outputs)
    service.accept(node.id, node.reviewer_user_id)
    assert repo.get_node(node.id).status.value == "completed"
    assert repo.get_project(project.id).current_node_id != node.id
