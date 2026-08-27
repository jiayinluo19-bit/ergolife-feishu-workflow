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
    if not selected:
        return "<h1>ERGOLIFE 生命周期看板</h1><p>暂无商品项目</p>"
    status_labels = {"completed": "已完成", "current": "当前阶段", "upcoming": "未开始", "ready": "未开始", "in_progress": "进行中", "reviewing": "待评审", "pending": "未开始", "rejected": "异常", "blocked": "异常", "cancelled": "异常"}
    trigger_labels = {"event": "事件触发", "result": "结果触发", "threshold": "阈值触发"}
    selected_id = selected["id"]
    product_cards = "".join(
        f'<a class="product {"selected" if p["id"] == selected_id else ""}" href="/dashboard?project_id={html.escape(p["id"])}">'
        f'<strong>{html.escape(p["product_name"])}</strong><span>{html.escape(p["id"])} · {p["completed"]}/{p["total"]}</span></a>'
        for p in projects
    )
    stage_rail = ""
    for index, stage in enumerate(selected["stages"]):
        stage_rail += f'<a class="stage-node {stage["status"]}" href="#stage-{index}"><span class="stage-dot">{index + 1}</span><span><b>{html.escape(stage["name"])}</b><small>{stage["completed"]}/{stage["total"]} 节点</small></span></a>'
        if index < len(selected["stages"]) - 1:
            connector = "done" if stage["status"] == "completed" else "active" if stage["status"] == "current" else ""
            stage_rail += f'<span class="stage-connector {connector}"></span>'

    def context_card(label: str, node: dict | None, tone: str) -> str:
        if not node:
            return f'<div class="context-card empty"><span>{label}</span><strong>暂无</strong><small>生命周期起点或终点</small></div>'
        return f'<div class="context-card {tone}"><span>{label}</span><strong>{html.escape(node["id"])} {html.escape(node["name"])}</strong><small>{html.escape(node.get("source_status", status_labels.get(node["status"], node["status"])))} · {html.escape(node["owner_role"])}</small></div>'

    event_labels = {"project_created": "项目创建", "node_created": "节点生成", "node_triggered": "触发条件成立", "node_claimed": "领取任务", "node_submitted": "提交交付物", "node_accepted": "验收通过", "node_rejected": "退回修改", "node_reopened": "重新提交", "project_completed": "项目完成"}

    def node_events(node: dict) -> str:
        if not node["events"]:
            return '<div class="event muted">尚未开始，等待前置节点完成</div>'
        return "".join(f'<div class="event"><span class="event-dot"></span><div><b>{html.escape(event_labels.get(event["type"], event["type"]))}</b><small>{html.escape(event["created_at"].replace("T", " ")[:19])} · {html.escape(event["actor"])}</small></div></div>' for event in node["events"])

    stage_sections = ""
    for index, stage in enumerate(selected["stages"]):
        def render_node(node: dict) -> str:
            action_names = "、".join(action["name"] for action in node.get("actions", [])) or "暂无动作明细"
            return f'<div class="node-row {"current" if node["id"] == selected["current_node_id"] else ""}"><div class="node-main"><span class="node-status {html.escape(node["status"])}"></span><div><strong>{html.escape(node["id"])} {html.escape(node["name"])}</strong><small>{html.escape(node["owner_role"])} · {html.escape(node.get("source_status", status_labels.get(node["status"], node["status"])))}</small></div></div><details><summary>查看详情</summary><div class="node-detail"><div class="timeline">{node_events(node)}</div><div class="detail-meta">负责人：{html.escape(node["owner_user_id"])}<br>验收人：{html.escape(node["reviewer_user_id"])}<br>触发：{html.escape(trigger_labels.get(node.get("trigger_type", ""), node.get("trigger_type", "—")))}<br>触发条件：{html.escape(node.get("trigger_condition") or "—")}<br>交棒给：{html.escape(node.get("handoff") or "—")}<br>动作：{html.escape(action_names)}<br>开始：{html.escape((node["started_at"] or "—").replace("T", " ")[:19])}<br>提交：{html.escape((node["submitted_at"] or "—").replace("T", " ")[:19])}<br>完成：{html.escape((node["completed_at"] or "—").replace("T", " ")[:19])}</div></div></details></div>'

        nodes_html = "".join(render_node(node) for node in stage["nodes"])
        stage_sections += f'<details class="stage-panel" id="stage-{index}" {"open" if stage["status"] == "current" else ""}><summary><span><b>{html.escape(stage["name"])}</b><small>{stage["completed"]}/{stage["total"]} · {status_labels[stage["status"]]}</small></span><em>{"当前阶段" if stage["status"] == "current" else ""}</em></summary><div class="stage-nodes">{nodes_html}</div></details>'
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ERGOLIFE 生命周期看板</title><style>
:root{{--blue:#3370ff;--ink:#182230;--muted:#667085;--line:#e8edf5;--green:#16a36a;--amber:#e8a600}}*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fb;color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}.wrap{{max-width:1240px;margin:0 auto;padding:28px 18px 56px}}h1{{margin:0;font-size:28px;letter-spacing:-.5px}}.sub{{color:var(--muted);margin:5px 0 24px}}.products{{display:flex;gap:12px;overflow:auto;padding:2px 2px 12px;margin-bottom:10px}}.product{{display:flex;flex-direction:column;gap:5px;min-width:230px;padding:14px 16px;background:#fff;border:1px solid var(--line);border-radius:12px;color:inherit;text-decoration:none;transition:.2s ease;box-shadow:0 2px 8px #1b3a5d08}}.product:hover,.product.selected{{border-color:#99b6ff;box-shadow:0 5px 18px #3370ff20;transform:translateY(-1px)}}.product span,.product small,.stage-node small,.stage-panel summary small,.node-main small,.context-card small{{color:var(--muted);font-size:12px;display:block}}.section-label{{font-size:12px;color:var(--muted);font-weight:700;letter-spacing:.08em;text-transform:uppercase;margin:18px 0 10px}}.stage-rail{{display:flex;align-items:center;min-width:900px;padding:16px 18px 20px;background:#fff;border:1px solid var(--line);border-radius:16px;overflow:auto;box-shadow:0 5px 20px #1b3a5d08}}.stage-node{{display:flex;align-items:center;gap:9px;min-width:145px;color:var(--muted);text-decoration:none;transition:.2s ease}}.stage-node:hover{{color:var(--blue)}}.stage-node b{{display:block;font-size:13px;white-space:nowrap}}.stage-dot{{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:#eef1f6;color:#8994a7;font-weight:700;flex:none;transition:.25s ease}}.stage-node.completed{{color:#147b50}}.stage-node.completed .stage-dot{{background:#d9f5e5;color:#147b50}}.stage-node.current{{color:var(--blue)}}.stage-node.current .stage-dot{{background:var(--blue);color:#fff;box-shadow:0 0 0 6px #dce7ff;animation:pulse 2s ease-in-out infinite}}.stage-connector{{height:2px;min-width:34px;flex:1;background:#e5eaf2;margin:0 8px;position:relative}}.stage-connector.done{{background:var(--green)}}.stage-connector.active{{background:linear-gradient(90deg,var(--green),#aec7ff)}}.hero{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;margin:18px 0 12px;padding:21px 24px;background:linear-gradient(120deg,#fff 0%,#f7faff 100%);border:1px solid var(--line);border-radius:16px;box-shadow:0 5px 20px #1b3a5d08}}.hero h2{{margin:0 0 5px;font-size:22px}}.meta{{color:var(--muted)}}.progress{{font-size:28px;font-weight:800;color:var(--blue);white-space:nowrap}}.context-grid{{display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;margin-bottom:22px}}.context-card{{padding:16px;border-radius:13px;border:1px solid var(--line);background:#fff;min-height:88px;transition:.2s ease}}.context-card:hover{{transform:translateY(-2px);box-shadow:0 6px 18px #1b3a5d10}}.context-card span{{display:block;color:var(--muted);font-size:12px;margin-bottom:7px}}.context-card strong{{display:block;font-size:14px}}.context-card.current{{border-color:#9dbaff;background:#f5f8ff;box-shadow:0 0 0 2px #e3ebff}}.context-card.current strong{{color:var(--blue)}}.context-card.previous{{border-left:4px solid var(--green)}}.context-card.next{{border-left:4px solid #c5cfdf}}.context-card.empty{{opacity:.7}}.stage-panel{{background:#fff;border:1px solid var(--line);border-radius:14px;margin:10px 0;overflow:hidden;box-shadow:0 3px 12px #1b3a5d06}}.stage-panel[open]{{animation:reveal .25s ease}}.stage-panel summary{{cursor:pointer;list-style:none;padding:16px 18px;display:flex;justify-content:space-between;align-items:center}}.stage-panel summary::-webkit-details-marker{{display:none}}.stage-panel summary b{{font-size:16px}}.stage-panel summary em{{font-style:normal;color:var(--blue);font-size:12px;font-weight:700}}.stage-panel[open] summary{{border-bottom:1px solid var(--line);background:#fbfcff}}.stage-nodes{{padding:4px 14px 14px}}.node-row{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;padding:13px 5px;border-bottom:1px solid #f0f2f6;transition:.2s ease}}.node-row:last-child{{border-bottom:0}}.node-row.current{{margin:0 -5px;padding-left:10px;padding-right:10px;background:#f2f6ff;border-radius:9px}}.node-main{{display:flex;gap:10px;align-items:flex-start;min-width:250px}}.node-main strong{{display:block}}.node-status{{width:10px;height:10px;border-radius:50%;background:#d7dde7;margin-top:5px;flex:none}}.node-status.completed{{background:var(--green)}}.node-status.ready{{background:var(--amber)}}.node-status.in_progress,.node-status.reviewing{{background:var(--blue);box-shadow:0 0 0 4px #dce7ff}}.node-status.rejected{{background:#e5484d}}.node-row details{{width:min(650px,60%)}}.node-row summary{{cursor:pointer;color:var(--blue);font-size:12px;padding:2px 0;list-style:none}}.node-row summary::-webkit-details-marker{{display:none}}.node-detail{{display:grid;grid-template-columns:1.2fr 1fr;gap:18px;margin-top:9px;padding:12px;border-radius:9px;background:#f8fafc}}.event{{display:flex;gap:9px;margin:6px 0}}.event-dot{{width:7px;height:7px;background:var(--blue);border-radius:50%;margin-top:6px;flex:none}}.event b,.event small{{display:block;font-size:12px}}.event small{{color:var(--muted)}}.event.muted{{color:var(--muted);font-size:12px}}.detail-meta{{color:var(--muted);font-size:12px;line-height:1.9}}@keyframes pulse{{0%,100%{{box-shadow:0 0 0 5px #dce7ff}}50%{{box-shadow:0 0 0 9px #dce7ff80}}}}@keyframes reveal{{from{{opacity:.4;transform:translateY(-3px)}}to{{opacity:1;transform:none}}}}@media(max-width:760px){{.wrap{{padding:20px 12px 40px}}h1{{font-size:23px}}.stage-rail{{min-width:760px}}.hero{{padding:17px;flex-direction:column}}.context-grid{{grid-template-columns:1fr}}.node-row{{display:block}}.node-row details{{width:100%;margin-top:8px}}.node-detail{{grid-template-columns:1fr}}}}
</style></head><body><main class="wrap"><h1>ERGOLIFE 商品全生命周期看板</h1><div class="sub">串行 MVP · 选择商品，查看阶段进度、当前节点与完整时间线</div><div class="products">{product_cards}</div>
<div class="section-label">大阶段总览</div><nav class="stage-rail">{stage_rail}</nav>
<section class="hero"><div><h2>{html.escape(selected["product_name"])}</h2><div class="meta">{html.escape(selected["product_code"])} · {html.escape(selected["target_market"])} · {html.escape(selected["sales_channel"])}<br>当前阶段：{html.escape(selected["current_stage"] or "已完成")} · 当前节点：{html.escape(selected["current_node_id"] or "已完成")} {html.escape(selected["current_node_name"])}</div></div><div class="progress">{selected["completed"]}/{selected["total"]}<small style="display:block;color:#667085;font-size:12px;font-weight:500;text-align:right">已完成节点</small></div></section>
<div class="section-label">节点上下文</div><section class="context-grid">{context_card("上一个节点", selected["previous_node"], "previous")}{context_card("当前节点", selected["current_node"], "current")}{context_card("下一个节点", selected["next_node"], "next")}</section>
<div class="section-label">按阶段展开流程</div>{stage_sections}</main></body></html>"""


def _send_next_card(project_id: str, receive_id: str) -> None:
    if not receive_id:
        return
    data = runtime.current_card_data(project_id)
    if not data:
        return
    try:
        FeishuOpenAPI().send_interactive_card(receive_id, task_assignment_card(**data))
    except (FeishuNotConfiguredError, RuntimeError):
        # The workflow state is already advanced; card delivery is best effort.
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


@app.get("/health")
def health() -> dict[str, str]:
    storage = "postgres" if runtime.repository.__class__.__name__ == "PostgresRepository" else "memory"
    return {"status": "ok", "service": "ergolife-feishu-workflow", "storage": storage}


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
        elif action.get("action") == "trigger_node":
            node = runtime.trigger_node(action["node_instance_id"], action["operator_open_id"])
            content = f"触发条件已登记：{node.definition_id}，现在可以接受任务"
            background_tasks.add_task(_send_next_card, action["project_id"], action["operator_open_id"])
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
