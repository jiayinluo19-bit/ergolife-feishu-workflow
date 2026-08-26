from typing import Any


def is_url_verification(payload: dict[str, Any]) -> bool:
    return payload.get("type") == "url_verification" and "challenge" in payload


def url_verification_response(payload: dict[str, Any]) -> dict[str, str]:
    return {"challenge": str(payload["challenge"])}


def extract_card_action(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event", payload)
    action = event.get("action", {})
    value = action.get("value", {})
    return {
        "event_id": payload.get("header", {}).get("event_id") or payload.get("event_id"),
        "operator_open_id": event.get("operator", {}).get("open_id"),
        "action": value.get("action"),
        "project_id": value.get("project_id"),
        "node_instance_id": value.get("node_instance_id"),
    }

