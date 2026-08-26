import pytest

from app.domain.models import ProductProject
from app.repositories.memory_repository import MemoryRepository
from app.services.workflow_service import WorkflowError
from tests.test_full_lifecycle import make_service


def test_missing_required_output_is_rejected():
    service, repo = make_service()
    project = ProductProject(product_code="X", product_name="测试", target_market="US", sales_channel="Amazon", owner_user_id="mock_product_manager")
    service.create_project(project, "mock_product_manager")
    node = repo.get_node(project.current_node_id)
    service.claim(node.id, node.owner_user_id)
    with pytest.raises(WorkflowError, match="缺少必交付物"):
        service.submit(node.id, node.owner_user_id, [])


def test_wrong_user_cannot_accept():
    service, repo = make_service()
    project = ProductProject(product_code="X", product_name="测试", target_market="US", sales_channel="Amazon", owner_user_id="mock_product_manager")
    service.create_project(project, "mock_product_manager")
    node = repo.get_node(project.current_node_id)
    service.claim(node.id, node.owner_user_id)
    service.submit(node.id, node.owner_user_id, service.definitions["P01"].required_outputs)
    with pytest.raises(WorkflowError, match="不是节点验收人"):
        service.accept(node.id, "wrong_user")

