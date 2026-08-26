from typing import Any

from fastapi import FastAPI, Request

from .integrations.feishu.events import extract_card_action, is_url_verification, url_verification_response
from .runtime import runtime
from .services.workflow_service import WorkflowError

app = FastAPI(title="ERGOLIFE 商品全生命周期协同 MVP", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ergolife-feishu-workflow"}


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "ergolife-feishu-workflow", "phase": "workflow-core"}


@app.post("/api/feishu/events")
async def feishu_events(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if is_url_verification(payload):
        return url_verification_response(payload)
    return {"status": "accepted", "event_id": payload.get("header", {}).get("event_id")}


@app.post("/api/feishu/card-actions")
async def feishu_card_actions(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if is_url_verification(payload):
        return url_verification_response(payload)
    action = extract_card_action(payload)
    toast_type = "success"
    try:
        if action.get("action") == "claim":
            node = runtime.service.claim(action["node_instance_id"], action["operator_open_id"])
            content = f"任务已接收：{node.definition_id}，状态已变更为进行中"
        elif action.get("action") == "view_project":
            content = runtime.project_summary(action["project_id"])
        else:
            content = "已收到操作，但暂不支持该动作"
    except (KeyError, WorkflowError) as exc:
        toast_type = "error"
        content = f"操作未完成：{exc}"
    return {"toast": {"type": toast_type, "content": content}, "action": action}
