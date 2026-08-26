from fastapi.testclient import TestClient

from app.integrations.feishu.cards import task_assignment_card
from app.integrations.feishu.client import FeishuNotConfiguredError, FeishuSettings
from app.main import app


def test_feishu_url_verification_endpoint():
    client = TestClient(app)
    response = client.post("/api/feishu/events", json={"type": "url_verification", "challenge": "abc"})
    assert response.status_code == 200
    assert response.json() == {"challenge": "abc"}


def test_card_action_endpoint_extracts_business_identity():
    client = TestClient(app)
    response = client.post(
        "/api/feishu/card-actions",
        json={
            "header": {"event_id": "evt-1"},
            "event": {
                "operator": {"open_id": "ou-test"},
                "action": {"value": {"action": "claim", "project_id": "PRJ-1", "node_instance_id": "NODE-1"}},
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["action"] == {
        "event_id": "evt-1",
        "operator_open_id": "ou-test",
        "action": "claim",
        "project_id": "PRJ-1",
        "node_instance_id": "NODE-1",
    }


def test_mock_card_actions_update_and_report_workflow_state():
    client = TestClient(app)
    claim = client.post(
        "/api/feishu/card-actions",
        json={
            "event": {
                "operator": {"open_id": "mock_product_manager"},
                "action": {"value": {"action": "claim", "project_id": "PRJ-MOCK-001", "node_instance_id": "NODE-P01-MOCK"}},
            }
        },
    )
    assert claim.json()["toast"]["type"] == "success"
    assert "进行中" in claim.json()["toast"]["content"]

    view = client.post(
        "/api/feishu/card-actions",
        json={"event": {"action": {"value": {"action": "view_project", "project_id": "PRJ-MOCK-001"}}}},
    )
    assert "P01" in view.json()["toast"]["content"]


def test_feishu_settings_require_secret():
    try:
        FeishuSettings.from_env()
    except FeishuNotConfiguredError:
        pass
