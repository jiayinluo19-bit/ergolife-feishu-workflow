from typing import Any

from fastapi import FastAPI, Request

from .integrations.feishu.events import extract_card_action, is_url_verification, url_verification_response

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
    return {"status": "accepted", "action": extract_card_action(payload)}
