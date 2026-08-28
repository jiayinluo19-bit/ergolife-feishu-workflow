from pathlib import Path

from fastapi.testclient import TestClient

from app.config_loader import load_definitions, load_role_assignments
from app.main import app
from app.repositories.product_repository import ProductRepository
from app.runtime import runtime
from app.services.product_access_service import ProductAccessService


def _service() -> ProductAccessService:
    root = Path(__file__).resolve().parents[1]
    return ProductAccessService(
        ProductRepository(),
        load_definitions(root / "config" / "workflow_v1.yaml"),
        load_role_assignments(root / "config" / "role_mapping.mock.yaml"),
        demo_mode=True,
    )


def test_demo_role_filters_products_and_exposes_handoff():
    data = _service().list_products(view="mine", demo_role="product_manager")
    assert data["actor"]["role"] == "product_manager"
    assert data["products"]
    current = data["products"][0]["lifecycle"]
    assert current["node_code"].startswith("P")
    assert current["next_code"]
    assert current["next_owner_role"]


def test_mock_product_can_be_advanced_and_permission_is_enforced():
    service = _service()
    item = service.list_products(view="all", demo_role="product_manager")["products"][0]
    updated = service.advance_product(item["id"], demo_role="product_manager")
    assert updated["lifecycle"]["node_code"] == item["lifecycle"]["next_code"]

    try:
        service.advance_product(updated["id"], demo_role="warehouse_owner")
    except PermissionError as exc:
        assert "不能操作" in str(exc)
    else:
        raise AssertionError("unrelated role should not be able to advance the product")


def test_product_dashboard_api_supports_all_roles_for_demo():
    client = TestClient(app)
    response = client.get("/api/dashboard/products", params={"view": "all", "demo_role": "quality_reviewer"})
    assert response.status_code == 200
    assert response.json()["actor"]["role"] == "quality_reviewer"
    assert response.json()["products"]


def test_lifecycle_detail_uses_real_product_selector():
    client = TestClient(app)
    response = client.get("/lifecycle")
    assert response.status_code == 200
    assert "ERGOLIFE 商品全生命周期看板" in response.text
    assert "PRJ-MOCK" not in response.text
    assert response.text.count('class="product ') >= 2
