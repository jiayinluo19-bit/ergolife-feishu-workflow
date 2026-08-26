from pathlib import Path

from app.config_loader import load_assignments, load_definitions
from app.domain.models import ProductProject
from app.repositories.memory_repository import MemoryRepository
from app.services.workflow_service import WorkflowService


ROOT = Path(__file__).parents[1]


def make_service() -> tuple[WorkflowService, MemoryRepository]:
    repo = MemoryRepository()
    service = WorkflowService(
        repo,
        load_definitions(ROOT / "config" / "workflow_v1.yaml"),
        load_assignments(ROOT / "config" / "role_mapping.mock.yaml"),
    )
    return service, repo


def test_full_serial_lifecycle_reaches_p22():
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
        service.claim(node.id, node.owner_user_id)
        outputs = service.definitions[node_id].required_outputs
        service.submit(node.id, node.owner_user_id, outputs)
        service.accept(node.id, node.reviewer_user_id)

    saved = repo.get_project(project.id)
    assert saved.status.value == "completed"
    assert saved.current_node_id is None
    assert len(repo.list_project_nodes(project.id)) == 22
    assert len(repo.events) >= 22 * 4


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

