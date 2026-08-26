import html
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse

from .integrations.feishu.events import extract_card_action, is_url_verification, url_verification_response
from .integrations.feishu.cards import project_lifecycle_card, task_assignment_card
from .integrations.feishu.client import FeishuNotConfiguredError, FeishuOpenAPI
from .runtime import runtime
from .services.workflow_service import WorkflowError

app = FastAPI(title="ERGOLIFE 商品全生命周期协同 MVP", version="0.1.0")


def _render_dashboard(projects: list[dict], selected_project_id: str | None) -> str:
    selected = next((p for p in projects if p["id"] == selected_project_id), projects[0] if projects else None)
    product_cards = "".join(
        f'<a class="product {"selected" if p["id"] == (selected["id"] if selected else None) else ""}" href="/dashboard?project_id={html.escape(p["id"])}">'
        f'<strong>{html.escape(p["product_name"])}</strong><span>{html.escape(p["id"])} · {p["completed"]}/{p["total"]}</span></a>'
        for p in projects
    )
    if not selected:
        return "<h1>ERGOLIFE 生命周期看板</h1><p>暂无商品项目</p>"
    rows = "".join(
        f'<tr class="{ "current" if n["id"] == selected["current_node_id"] else ""}"><td>{html.escape(n["id"])}</td><td>{html.escape(n["stage"])}</td><td>{html.escape(n["name"])}</td><td><span class="status {html.escape(n["status"])}">{html.escape(n["status"])}</span></td><td>{html.escape(n["owner_role"])}</td></tr>'
        for n in selected["nodes"]
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ERGOLIFE 生命周期看板</title><style>
body{{margin:0;background:#f5f7fb;color:#172033;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:28px 18px 48px}}h1{{margin:0 0 6px;font-size:28px}}.sub{{color:#667085;margin-bottom:24px}}
.products{{display:flex;gap:12px;overflow:auto;margin-bottom:20px}}.product{{display:flex;flex-direction:column;gap:5px;min-width:220px;padding:14px;background:#fff;border:1px solid #e5eaf2;border-radius:10px;color:inherit;text-decoration:none}}.product.selected{{border-color:#3370ff;box-shadow:0 0 0 2px #dbe6ff}}.product span{{color:#667085;font-size:12px}}
.hero,.panel{{background:#fff;border:1px solid #e5eaf2;border-radius:12px;padding:20px;margin-bottom:18px}}.hero{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start}}.hero h2{{margin:0 0 6px}}.meta{{color:#667085}}.progress{{font-size:26px;font-weight:700;color:#3370ff;white-space:nowrap}}table{{width:100%;border-collapse:collapse}}th,td{{padding:11px 8px;text-align:left;border-bottom:1px solid #edf0f5}}th{{color:#667085;font-size:12px;font-weight:600}}tr.current{{background:#f0f5ff}}.status{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;background:#eef1f5}}.status.ready{{background:#fff4cc;color:#8a5b00}}.status.in_progress{{background:#dbe8ff;color:#1455b8}}.status.completed{{background:#d9f5e5;color:#14733c}}.status.pending{{color:#667085}}
</style></head><body><main class="wrap"><h1>ERGOLIFE 商品全生命周期看板</h1><div class="sub">串行 MVP · 点击商品查看 P01～P22 当前状态</div><div class="products">{product_cards}</div>
<section class="hero"><div><h2>{html.escape(selected["product_name"])}</h2><div class="meta">{html.escape(selected["product_code"])} · {html.escape(selected["target_market"])} · {html.escape(selected["sales_channel"])}<br>当前节点：{html.escape(selected["current_node_id"] or "已完成")} {html.escape(selected["current_node_name"])}</div></div><div class="progress">{selected["completed"]}/{selected["total"]}</div></section>
<section class="panel"><table><thead><tr><th>节点</th><th>阶段</th><th>动作</th><th>状态</th><th>模拟角色</th></tr></thead><tbody>{rows}</tbody></table></section></main></body></html>"""


def _send_next_card(project_id: str, receive_id: str) -> None:
    if not receive_id:
        return


def _send_lifecycle_card(project_id: str, receive_id: str) -> None:
    if not receive_id:
        return
    try:
        project = runtime.repository.get_project(project_id)
        lines = runtime.lifecycle_lines(project_id)
        FeishuOpenAPI().send_interactive_card(
            receive_id,
            project_lifecycle_card(
                product_name=project.product_name,
                project_status=project.status.value,
                progress=f"{sum(line.startswith('✅') for line in lines)}/22",
                lines=lines,
            ),
        )
    except (FeishuNotConfiguredError, KeyError, RuntimeError):
        return
    data = runtime.current_card_data(project_id)
    if not data:
        return
    try:
        FeishuOpenAPI().send_interactive_card(receive_id, task_assignment_card(**data))
    except (FeishuNotConfiguredError, RuntimeError):
        # The workflow state is already advanced; card delivery is best effort.
        return


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ergolife-feishu-workflow"}


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "ergolife-feishu-workflow", "phase": "workflow-core"}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(project_id: str | None = None) -> HTMLResponse:
    return HTMLResponse(_render_dashboard(runtime.dashboard_data(), project_id))


@app.get("/api/dashboard/projects")
def dashboard_projects() -> list[dict]:
    return runtime.dashboard_data()


@app.post("/api/feishu/events")
async def feishu_events(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if is_url_verification(payload):
        return url_verification_response(payload)
    return {"status": "accepted", "event_id": payload.get("header", {}).get("event_id")}


@app.post("/api/feishu/card-actions")
async def feishu_card_actions(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload = await request.json()
    if is_url_verification(payload):
        return url_verification_response(payload)
    action = extract_card_action(payload)
    toast_type = "success"
    try:
        if action.get("action") == "claim":
            node = runtime.claim_node(action["node_instance_id"], action["operator_open_id"])
            content = f"任务已接收：{node.definition_id}，状态已变更为进行中"
        elif action.get("action") == "view_project":
            content = runtime.project_summary(action["project_id"])
            background_tasks.add_task(_send_lifecycle_card, action["project_id"], action["operator_open_id"])
        elif action.get("action") == "simulate_complete":
            node, project = runtime.simulate_complete(action["node_instance_id"], action["operator_open_id"])
            if project.current_node_id:
                background_tasks.add_task(_send_next_card, project.id, action["operator_open_id"])
                next_node = runtime.repository.get_node(project.current_node_id)
                content = f"已模拟完成 {node.definition_id}，下一节点为 {next_node.definition_id}"
            else:
                content = "已模拟完成最后节点，项目已完成"
        else:
            content = "已收到操作，但暂不支持该动作"
    except (KeyError, WorkflowError) as exc:
        toast_type = "error"
        content = f"操作未完成：{exc}"
    return {"toast": {"type": toast_type, "content": content}, "action": action}
