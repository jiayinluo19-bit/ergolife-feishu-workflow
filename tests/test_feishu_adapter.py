from fastapi.testclient import TestClient

from app.integrations.feishu.cards import task_assignment_card
from app.integrations.feishu.client import FeishuNotConfiguredError, FeishuSettings
from app.main import app
from app.runtime import runtime


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


def test_dashboard_groups_stages_and_exposes_current_context():
    project = next(item for item in runtime.dashboard_data() if item["id"] == "PRJ-MOCK-003")
    assert project["current_node_id"] == "P12"
    assert project["current_stage"] == "采购量产与质量"
    assert project["previous_node"]["id"] == "P11"
    assert project["next_node"]["id"] == "P13"
    assert len(project["stages"]) == 6
    pending = next(node for node in project["nodes"] if node["id"] == "P22")
    assert pending["status"] == "pending"
    assert pending["events"] == []
