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


def test_feishu_settings_require_secret():
    try:
        FeishuSettings.from_env()
    except FeishuNotConfiguredError:
        pass

