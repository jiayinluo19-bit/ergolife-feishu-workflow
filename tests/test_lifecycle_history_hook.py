from pathlib import Path

from app.config_loader import load_definitions, load_role_assignments
from app.repositories.product_repository import ProductRepository
from app.services.product_access_service import ProductAccessService


class SpyLifecycleRepository:
    def __init__(self):
        self.calls = []

    def ensure_product(self, product_id, current_node_code, definitions, assignments):
        self.calls.append(("ensure", product_id, current_node_code, assignments))

    def record_advance(self, product_id, expected_node_code, next_node_code, actor_user_id, definitions, assignments):
        self.calls.append(("advance", product_id, expected_node_code, next_node_code, actor_user_id))


def test_product_advance_records_formal_lifecycle_transition():
    root = Path(__file__).resolve().parents[1]
    definitions = load_definitions(root / "config" / "workflow_v1.yaml")
    roles = load_role_assignments(root / "config" / "role_mapping.mock.yaml")
    history = SpyLifecycleRepository()
    service = ProductAccessService(
        ProductRepository(),
        definitions,
        roles,
        demo_mode=True,
        lifecycle_repository=history,
    )

    product = service.list_products(view="all", demo_role="product_manager")["products"][0]
    service.advance_product(product["id"], demo_role="product_manager")

    assert history.calls[0][0] == "ensure"
    assert history.calls[0][2] == "P01"
    assert history.calls[1] == ("advance", product["id"], "P01", "P02", "mock_product_manager")
