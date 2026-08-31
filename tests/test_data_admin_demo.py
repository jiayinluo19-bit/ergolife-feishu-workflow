import pytest
from fastapi.testclient import TestClient

from app.data_admin_demo import DataAdminConflict, DemoDataAdmin
from app.main import app


def _row(service: DemoDataAdmin, table: str, record_id: str) -> dict:
    return next(row for row in service.snapshot()["tables"][table] if row["id"] == record_id)


def test_preview_does_not_mutate_and_commit_applies_sales_cascade():
    service = DemoDataAdmin()
    before_sale = _row(service, "sales_daily", "sale-1001")
    before_plan = _row(service, "replenishment_plans", "plan-1001")

    preview = service.preview(
        {"table": "sales_daily", "operation": "update", "record_id": "sale-1001", "values": {"units": 30}}
    )

    assert _row(service, "sales_daily", "sale-1001") == before_sale
    assert _row(service, "replenishment_plans", "plan-1001") == before_plan
    assert preview["summary"] == {"total": 2, "user_changes": 1, "cascade_changes": 1, "deletes": 0}
    assert any(change["source"] == "连锁更新" and change["table"] == "replenishment_plans" for change in preview["changes"])

    service.commit(preview["preview_id"])
    assert _row(service, "sales_daily", "sale-1001")["units"] == 30
    assert _row(service, "replenishment_plans", "plan-1001")["avg_daily_sales"] == 26.0


def test_product_delete_preview_includes_cascade_deletes():
    service = DemoDataAdmin()
    preview = service.preview({"table": "products", "operation": "delete", "record_id": "prod-70030", "values": {}})

    assert preview["summary"]["deletes"] == 5
    assert {change["table"] for change in preview["changes"]} == {
        "products",
        "sales_daily",
        "inventory_positions",
        "replenishment_plans",
    }
    assert all(change["source"] in {"用户操作", "级联删除"} for change in preview["changes"])


def test_preview_is_optimistic_and_cannot_commit_after_state_changes():
    service = DemoDataAdmin()
    first = service.preview({"table": "inventory_positions", "operation": "update", "record_id": "inv-1001", "values": {"on_hand": 80}})
    second = service.preview({"table": "sales_daily", "operation": "update", "record_id": "sale-1001", "values": {"units": 25}})
    service.commit(first["preview_id"])

    with pytest.raises(DataAdminConflict):
        service.commit(second["preview_id"])


def test_data_admin_page_and_api_are_available_without_permissions():
    client = TestClient(app)
    page = client.get("/data-admin")
    assert page.status_code == 200
    assert "数据工作台" in page.text
    assert "确认并提交全部变更" in page.text

    preview = client.post(
        "/api/data-admin/preview",
        json={"table": "inventory_positions", "operation": "update", "record_id": "inv-1001", "values": {"on_hand": 80}},
    )
    assert preview.status_code == 200
    assert preview.json()["summary"]["cascade_changes"] == 1

    commit = client.post("/api/data-admin/commit", json={"preview_id": preview.json()["preview_id"]})
    assert commit.status_code == 200
    assert commit.json()["status"] == "committed"

    reset = client.post("/api/data-admin/reset")
    assert reset.status_code == 200
    assert reset.json()["tables"]["inventory_positions"][0]["on_hand"] == 120
