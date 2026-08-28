import html
import json
import os
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .integrations.feishu.events import extract_card_action, is_url_verification, url_verification_response
from .integrations.feishu.cards import product_handoff_card, project_lifecycle_card, task_assignment_card
from .integrations.feishu.client import FeishuNotConfiguredError, FeishuOpenAPI
from .integrations.feishu.identity import FeishuIdentity, FeishuIdentityError
from .runtime import runtime
from .services.workflow_service import WorkflowError

app = FastAPI(title="ERGOLIFE 商品全生命周期协同 MVP", version="0.1.0")
feishu_identity = FeishuIdentity()


def _current_open_id(request: Request, explicit: str | None = None) -> str | None:
    session_open_id = feishu_identity.read_session(request.cookies.get("ergolife_session"))
    # Query/body impersonation is opt-in for local demos only.  Normal web
    # requests must use the signed Feishu session cookie.
    if explicit and runtime.product_access.demo_mode and os.getenv("ALLOW_QUERY_ACTOR", "false").lower() in {"1", "true", "yes", "on"}:
        return explicit
    return session_open_id


def _render_product_workbench(data: dict, view: str, demo_role: str | None) -> str:
    actor = data["actor"]
    view_labels = {"mine": "我的商品", "participating": "我参与的商品", "all": "全部商品"}
    source_labels = {"postgres": "已连接商品 PostgreSQL", "mock": "演示数据", "mock-fallback": "数据库暂不可用 · 演示数据"}
    demo_suffix = f"&demo_role={html.escape(demo_role)}" if demo_role else ""
    tabs = "".join(
        f'<a class="tab {"active" if key == view else ""}" href="/dashboard?view={key}{demo_suffix}">{label}</a>'
        for key, label in view_labels.items()
    )
    role_links = "".join(
        f'<a class="role-pill {"active" if item["role"] == actor.get("role") else ""}" href="/dashboard?view={html.escape(view)}&demo_role={html.escape(item["role"])}">{html.escape(item["department"])} · {html.escape(item["display_name"])}</a>'
        for item in data["roles"]
    )
    cards = []
    for item in data["products"]:
        lifecycle = item["lifecycle"]
        access = item["access"]
        if access["can_advance"] and lifecycle["next_code"]:
            action = f'<button class="advance" data-id="{html.escape(item["id"])}" data-next="{html.escape(lifecycle["next_code"])}">{html.escape(access["action_label"])}</button>'
        elif lifecycle["next_code"]:
            action = '<span class="readonly">当前角色只读</span>'
        else:
            action = '<span class="readonly">生命周期已结束</span>'
        cards.append(
            f'<article class="product-card"><div class="card-top"><div><h3>{html.escape(item["product_name"])}</h3><p>{html.escape(item["sku"])} · {html.escape(item["country_code"])} · {html.escape(item["amazon_sku"] or "无 MSKU")}</p></div><div><span class="node-badge">{html.escape(lifecycle["node_code"])}</span><span class="deadline {html.escape(lifecycle.get("deadline_status", "normal"))}">{html.escape(lifecycle.get("deadline_label", "未设置截止时间"))}</span></div></div>'
            f'<div class="stage">{html.escape(lifecycle["stage"])}<strong>{html.escape(lifecycle["node_name"])}</strong></div>'
            f'<div class="handoff"><span>负责人：{html.escape(lifecycle["owner_name"] or lifecycle["owner_role"] or "未配置")}</span><span>部门：{html.escape(lifecycle["owner_department"] or "—")}</span></div>'
            f'<div class="flow"><span class="done">{html.escape(lifecycle["previous_code"] or "起点")}</span><i>→</i><b>{html.escape(lifecycle["node_code"])}</b><i>→</i><span>{html.escape(lifecycle["next_code"] or "终点")}</span></div>{action}</article>'
        )
    empty = '<div class="empty">当前身份在此视图下没有商品。可以切换上方角色进行演示。</div>' if not cards else ""
    identity = html.escape(actor.get("display_name") or "未识别用户")
    login = '<a class="login" href="/auth/feishu/login">使用飞书身份登录</a>' if not actor.get("role") else f'<span class="login">已识别：{identity}</span>'
    login += ' <a class="login" href="/lifecycle">全链路详情</a>'
    if actor.get("is_admin"):
        login += ' <a class="login" href="/admin/directory">管理角色</a> <a class="login" href="/admin/directory/sync">同步全员</a>'
    summary = data.get("summary", {})
    h5_auth_script = ""
    if os.getenv("FEISHU_APP_ID", "").strip():
        h5_auth_script = (
            '<script src="https://lf-scm-cn.feishucdn.com/lark/op/h5-js-sdk-1.5.44.js"></script>'
            '<script>(function(){if(!window.tt||!window.tt.requestAuthCode)return;function request(){window.tt.requestAuthCode({appId:'
            + json.dumps(os.getenv("FEISHU_APP_ID", ""))
            + ',success:function(info){if(!info||!info.code)return;fetch("/api/auth/feishu/h5",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:info.code})}).then(function(){location.reload()})}})}if(window.h5sdk&&window.h5sdk.ready)window.h5sdk.ready(request);else request()})();</script>'
        )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ERGOLIFE 商品工作台</title><style>
:root{{--blue:#3370ff;--ink:#182230;--muted:#667085;--line:#e8edf5;--green:#16a36a;--bg:#f4f7fb}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}.wrap{{max-width:1240px;margin:auto;padding:28px 18px 56px}}h1{{margin:0;font-size:28px}}.sub{{color:var(--muted);margin:5px 0 18px}}.toolbar{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0 18px}}.tab,.role-pill,.login{{padding:8px 12px;border:1px solid var(--line);border-radius:9px;background:#fff;color:var(--ink);text-decoration:none}}.tab.active,.role-pill.active{{background:#edf3ff;border-color:#9dbaff;color:var(--blue);font-weight:700}}.login{{margin-left:auto;color:var(--blue)}}.demo-note{{color:var(--muted);font-size:12px;margin:8px 0}}.summary{{display:flex;gap:18px;align-items:center;flex-wrap:wrap;background:#fff;border:1px solid var(--line);border-radius:14px;padding:15px 18px;margin-bottom:15px}}.summary-item{{min-width:100px}}.summary-item strong{{font-size:21px;color:var(--blue);display:block}}.summary-item small{{color:var(--muted);display:block}}.summary-user{{margin-left:auto;color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}}.product-card{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:17px;box-shadow:0 3px 12px #1b3a5d08;transition:.2s}}.product-card:hover{{transform:translateY(-2px);box-shadow:0 8px 22px #1b3a5d12}}h3{{margin:0;font-size:16px}}p{{margin:4px 0;color:var(--muted);font-size:12px}}.card-top{{display:flex;justify-content:space-between;gap:10px}}.node-badge{{display:inline-block;background:#e6efff;color:var(--blue);border-radius:999px;padding:4px 9px;font-weight:700}}.deadline{{display:block;text-align:right;font-size:11px;margin-top:5px}}.deadline.overdue{{color:#e5484d}}.deadline.due_soon{{color:#d28b00}}.deadline.normal{{color:var(--muted)}}.stage{{margin:18px 0 10px;color:var(--muted);font-size:12px}}.stage strong{{display:block;color:var(--ink);font-size:18px;margin-top:2px}}.handoff{{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;border-top:1px solid #f0f2f6;padding-top:10px}}.flow{{display:flex;align-items:center;gap:8px;margin:15px 0;color:#98a2b3;font-size:12px}}.flow b{{color:var(--blue);font-size:14px}}.flow .done{{color:var(--green)}}.flow i{{font-style:normal;color:#c1cad8}}button.advance{{width:100%;border:0;border-radius:9px;padding:10px;background:var(--blue);color:#fff;cursor:pointer;font-weight:700}}.readonly{{display:block;padding:9px;text-align:center;background:#f7f8fa;border-radius:9px;color:var(--muted);font-size:12px}}.empty{{background:#fff;border:1px dashed #cbd5e1;border-radius:14px;padding:35px;text-align:center;color:var(--muted);grid-column:1/-1}}@media(max-width:700px){{.wrap{{padding:20px 12px}}h1{{font-size:23px}}.login{{margin-left:0}}.summary-user{{margin-left:0}}}}</style></head><body><main class="wrap"><h1>ERGOLIFE 商品协同工作台</h1><div class="sub">按你的部门角色查看负责商品、参与商品，并在当前节点完成交接</div><div class="toolbar">{tabs}{login}</div>{f'<div class="demo-note">演示角色切换（仅 DEMO_MODE 开启时显示）：</div><div class="toolbar">{role_links}</div>' if runtime.product_access.demo_mode else ''}<div class="summary"><div class="summary-item"><strong>{summary.get("total", len(data["products"]))}</strong><small>{view_labels.get(view, view)}</small></div><div class="summary-item"><strong>{summary.get("actionable", 0)}</strong><small>当前可处理</small></div><div class="summary-item"><strong>{summary.get("due_soon", 0)}</strong><small>24小时内到期</small></div><div class="summary-item"><strong>{summary.get("overdue", 0)}</strong><small>已逾期</small></div><div class="summary-user">{source_labels.get(data["source"], data["source"])} · {identity}</div></div><section class="grid">{''.join(cards)}{empty}</section></main><script>document.querySelectorAll('.advance').forEach(function(button){{button.addEventListener('click',async function(){{button.disabled=true;button.textContent='正在交接…';const response=await fetch('/api/products/'+encodeURIComponent(button.dataset.id)+'/advance',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{demo_role:{json.dumps(demo_role)},next_node:button.dataset.next}})}});const body=await response.json();if(!response.ok){{alert(body.detail||'操作失败');button.disabled=false;button.textContent='重试';return}}location.reload()}})}});</script>{h5_auth_script}</body></html>"""


def _render_directory_admin(data: dict) -> str:
    roles = data.get("roles", [])
    role_options = "".join(f'<option value="{html.escape(role)}">{html.escape(role)}</option>' for role in roles)
    sync_status = data.get("sync_status") or {}
    sync_label = {
        "idle": "尚未同步",
        "running": "正在后台同步，请稍后刷新",
        "succeeded": f"同步完成：读取 {sync_status.get('fetched', 0)} 人，写入 {sync_status.get('synced', 0)} 人",
        "failed": f"同步失败：{sync_status.get('error') or '请检查飞书通讯录权限'}",
    }.get(str(sync_status.get("status") or "idle"), "同步状态未知")
    rule_rows = "".join(
        f'<tr><td>{html.escape(department)}</td><td>{html.escape(role)}</td><td><button class="danger" data-delete-rule="{html.escape(department)}">删除</button></td></tr>'
        for department, role in sorted(data.get("role_rules", {}).items())
    ) or '<tr><td colspan="3">暂无规则</td></tr>'
    rule_rows = f'<tr><td colspan="3"><strong>通讯录同步：</strong>{html.escape(sync_label)}</td></tr>' + rule_rows
    user_rows = []
    for user in data.get("users", []):
        checks = "".join(
            f'<label><input type="checkbox" data-role="{html.escape(role)}" {"checked" if role in user.get("roles", []) else ""}>{html.escape(role)}</label>'
            for role in roles
        )
        departments = "、".join(user.get("department_names", [])) or "未返回部门"
        user_rows.append(
            f'<tr data-user="{html.escape(user["open_id"])}"><td><strong>{html.escape(user.get("display_name") or "未命名")}</strong><small>{html.escape(user["open_id"])}</small></td><td>{html.escape(departments)}<br><small>{html.escape(user.get("job_title") or "")}</small></td><td class="role-checks">{checks}</td><td><button class="save-user">保存角色</button> <button class="auto-user">恢复自动</button></td></tr>'
        )
    users_html = "".join(user_rows) or '<tr><td colspan="4">员工首次从飞书打开工作台后会出现在这里</td></tr>'
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ERGOLIFE 角色配置</title><style>:root{{--blue:#3370ff;--ink:#182230;--muted:#667085;--line:#e8edf5;--bg:#f4f7fb;--red:#d92d20}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}main{{max-width:1180px;margin:auto;padding:28px 18px 60px}}h1{{margin:0}}.sub,small{{color:var(--muted)}}.panel{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px;margin:16px 0;overflow:auto}}table{{width:100%;border-collapse:collapse}}th,td{{padding:11px 9px;border-bottom:1px solid #f0f2f6;text-align:left;vertical-align:top}}th{{color:var(--muted);font-weight:600}}input,select,button{{font:inherit;padding:7px 9px;border:1px solid #d0d5dd;border-radius:7px;background:#fff}}button{{cursor:pointer;color:var(--blue)}}button.primary{{background:var(--blue);color:#fff;border-color:var(--blue)}}button.danger{{color:var(--red)}}.role-checks{{display:flex;gap:9px;flex-wrap:wrap}}.role-checks label{{white-space:nowrap}}.top{{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap}}.hint{{font-size:12px;color:var(--muted);margin:8px 0}}.back{{color:var(--blue);text-decoration:none}}</style></head><body><main><div class="top"><div><h1>员工与角色配置</h1><div class="sub">部门规则自动生效，特殊人员可单独覆盖</div></div><a class="back" href="/dashboard">返回商品工作台</a></div><section class="panel"><h2>部门 → 生命周期角色</h2><form id="rule-form"><input id="department-key" placeholder="部门名称或 department_id" required><select id="role-code" required>{role_options}</select><button class="primary">保存规则</button></form><div class="hint">规则保存后立即影响该部门员工的“我的商品”和交接通知。</div><table><thead><tr><th>部门</th><th>角色</th><th>操作</th></tr></thead><tbody>{rule_rows}</tbody></table></section><section class="panel"><h2>员工角色覆盖</h2><div class="hint">默认按部门自动匹配；保存角色会对该员工启用手工覆盖。点击“恢复自动”可撤销覆盖。</div><table><thead><tr><th>员工</th><th>部门 / 岗位</th><th>角色</th><th>操作</th></tr></thead><tbody>{users_html}</tbody></table></section></main><script>async function send(url,options){{const response=await fetch(url,options);const body=await response.json().catch(function(){{return{{}}}});if(!response.ok)throw new Error(body.detail||'操作失败');return body}}document.getElementById('rule-form').addEventListener('submit',async function(event){{event.preventDefault();try{{await send('/api/admin/directory/rules',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{department_key:document.getElementById('department-key').value,role_code:document.getElementById('role-code').value}})}});location.reload()}}catch(error){{alert(error.message)}}}});document.querySelectorAll('[data-delete-rule]').forEach(function(button){{button.addEventListener('click',async function(){{if(!confirm('确定删除该部门规则吗？'))return;try{{await send('/api/admin/directory/rules/'+encodeURIComponent(button.dataset.deleteRule),{{method:'DELETE'}});location.reload()}}catch(error){{alert(error.message)}}}})}});document.querySelectorAll('.save-user').forEach(function(button){{button.addEventListener('click',async function(){{const row=button.closest('tr');const roles=[...row.querySelectorAll('input[data-role]:checked')].map(function(input){{return input.dataset.role}});try{{await send('/api/admin/directory/users/'+encodeURIComponent(row.dataset.user)+'/roles',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{roles:roles}})}});location.reload()}}catch(error){{alert(error.message)}}}})}});document.querySelectorAll('.auto-user').forEach(function(button){{button.addEventListener('click',async function(){{const row=button.closest('tr');try{{await send('/api/admin/directory/users/'+encodeURIComponent(row.dataset.user)+'/roles/auto',{{method:'POST'}});location.reload()}}catch(error){{alert(error.message)}}}})}});</script></body></html>"""


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
            if node.get("status") == "completed":
                return '<div class="event muted">暂无历史事件（当前商品表未保存节点事件）</div>'
            return '<div class="event muted">尚未开始，等待前置节点完成</div>'
        return "".join(f'<div class="event"><span class="event-dot"></span><div><b>{html.escape(event_labels.get(event["type"], event["type"]))}</b><small>{html.escape(event["created_at"].replace("T", " ")[:19])} · {html.escape(event["actor"])}</small></div></div>' for event in node["events"])

    stage_sections = ""
    for index, stage in enumerate(selected["stages"]):
        def render_node(node: dict) -> str:
            action_names = "、".join(action["name"] for action in node.get("actions", [])) or "暂无动作明细"
            return f'<div class="node-row {"current" if node["id"] == selected["current_node_id"] else ""}"><div class="node-main"><span class="node-status {html.escape(node["status"])}"></span><div><strong>{html.escape(node["id"])} {html.escape(node["name"])}</strong><small>{html.escape(node["owner_role"])} · {html.escape(node.get("source_status", status_labels.get(node["status"], node["status"])))}</small></div></div><details><summary>查看详情</summary><div class="node-detail"><div class="timeline">{node_events(node)}</div><div class="detail-meta">负责人：{html.escape(node["owner_user_id"])}<br>验收人：{html.escape(node["reviewer_user_id"])}<br>触发：{html.escape(trigger_labels.get(node.get("trigger_type", ""), node.get("trigger_type", "—")))}<br>触发条件：{html.escape(node.get("trigger_condition") or "—")}<br>交棒给：{html.escape(node.get("handoff") or "—")}<br>动作：{html.escape(action_names)}<br>开始：{html.escape((node["started_at"] or "—").replace("T", " ")[:19])}<br>提交：{html.escape((node["submitted_at"] or "—").replace("T", " ")[:19])}<br>完成：{html.escape((node["completed_at"] or "—").replace("T", " ")[:19])}</div></div></details></div>'

        nodes_html = "".join(render_node(node) for node in stage["nodes"])
        stage_sections += f'<details class="stage-panel" id="stage-{index}" {"open" if stage["status"] == "current" else ""}><summary><span><b>{html.escape(stage["name"])}</b><small>{stage["completed"]}/{stage["total"]} · {status_labels[stage["status"]]}</small></span><em>{"当前阶段" if stage["status"] == "current" else ""}</em></summary><div class="stage-nodes">{nodes_html}</div></details>'
    source_note = f' · 数据源：{selected["source"]}' if selected.get("source") else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ERGOLIFE 生命周期看板</title><style>
:root{{--blue:#3370ff;--ink:#182230;--muted:#667085;--line:#e8edf5;--green:#16a36a;--amber:#e8a600}}*{{box-sizing:border-box}}body{{margin:0;background:#f4f7fb;color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}.wrap{{max-width:1240px;margin:0 auto;padding:28px 18px 56px}}h1{{margin:0;font-size:28px;letter-spacing:-.5px}}.sub{{color:var(--muted);margin:5px 0 24px}}.products{{display:flex;gap:12px;overflow:auto;padding:2px 2px 12px;margin-bottom:10px}}.product{{display:flex;flex-direction:column;gap:5px;min-width:230px;padding:14px 16px;background:#fff;border:1px solid var(--line);border-radius:12px;color:inherit;text-decoration:none;transition:.2s ease;box-shadow:0 2px 8px #1b3a5d08}}.product:hover,.product.selected{{border-color:#99b6ff;box-shadow:0 5px 18px #3370ff20;transform:translateY(-1px)}}.product span,.product small,.stage-node small,.stage-panel summary small,.node-main small,.context-card small{{color:var(--muted);font-size:12px;display:block}}.section-label{{font-size:12px;color:var(--muted);font-weight:700;letter-spacing:.08em;text-transform:uppercase;margin:18px 0 10px}}.stage-rail{{display:flex;align-items:center;min-width:900px;padding:16px 18px 20px;background:#fff;border:1px solid var(--line);border-radius:16px;overflow:auto;box-shadow:0 5px 20px #1b3a5d08}}.stage-node{{display:flex;align-items:center;gap:9px;min-width:145px;color:var(--muted);text-decoration:none;transition:.2s ease}}.stage-node:hover{{color:var(--blue)}}.stage-node b{{display:block;font-size:13px;white-space:nowrap}}.stage-dot{{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:#eef1f6;color:#8994a7;font-weight:700;flex:none;transition:.25s ease}}.stage-node.completed{{color:#147b50}}.stage-node.completed .stage-dot{{background:#d9f5e5;color:#147b50}}.stage-node.current{{color:var(--blue)}}.stage-node.current .stage-dot{{background:var(--blue);color:#fff;box-shadow:0 0 0 6px #dce7ff;animation:pulse 2s ease-in-out infinite}}.stage-connector{{height:2px;min-width:34px;flex:1;background:#e5eaf2;margin:0 8px;position:relative}}.stage-connector.done{{background:var(--green)}}.stage-connector.active{{background:linear-gradient(90deg,var(--green),#aec7ff)}}.hero{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;margin:18px 0 12px;padding:21px 24px;background:linear-gradient(120deg,#fff 0%,#f7faff 100%);border:1px solid var(--line);border-radius:16px;box-shadow:0 5px 20px #1b3a5d08}}.hero h2{{margin:0 0 5px;font-size:22px}}.meta{{color:var(--muted)}}.progress{{font-size:28px;font-weight:800;color:var(--blue);white-space:nowrap}}.context-grid{{display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;margin-bottom:22px}}.context-card{{padding:16px;border-radius:13px;border:1px solid var(--line);background:#fff;min-height:88px;transition:.2s ease}}.context-card:hover{{transform:translateY(-2px);box-shadow:0 6px 18px #1b3a5d10}}.context-card span{{display:block;color:var(--muted);font-size:12px;margin-bottom:7px}}.context-card strong{{display:block;font-size:14px}}.context-card.current{{border-color:#9dbaff;background:#f5f8ff;box-shadow:0 0 0 2px #e3ebff}}.context-card.current strong{{color:var(--blue)}}.context-card.previous{{border-left:4px solid var(--green)}}.context-card.next{{border-left:4px solid #c5cfdf}}.context-card.empty{{opacity:.7}}.stage-panel{{background:#fff;border:1px solid var(--line);border-radius:14px;margin:10px 0;overflow:hidden;box-shadow:0 3px 12px #1b3a5d06}}.stage-panel[open]{{animation:reveal .25s ease}}.stage-panel summary{{cursor:pointer;list-style:none;padding:16px 18px;display:flex;justify-content:space-between;align-items:center}}.stage-panel summary::-webkit-details-marker{{display:none}}.stage-panel summary b{{font-size:16px}}.stage-panel summary em{{font-style:normal;color:var(--blue);font-size:12px;font-weight:700}}.stage-panel[open] summary{{border-bottom:1px solid var(--line);background:#fbfcff}}.stage-nodes{{padding:4px 14px 14px}}.node-row{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;padding:13px 5px;border-bottom:1px solid #f0f2f6;transition:.2s ease}}.node-row:last-child{{border-bottom:0}}.node-row.current{{margin:0 -5px;padding-left:10px;padding-right:10px;background:#f2f6ff;border-radius:9px}}.node-main{{display:flex;gap:10px;align-items:flex-start;min-width:250px}}.node-main strong{{display:block}}.node-status{{width:10px;height:10px;border-radius:50%;background:#d7dde7;margin-top:5px;flex:none}}.node-status.completed{{background:var(--green)}}.node-status.ready{{background:var(--amber)}}.node-status.in_progress,.node-status.reviewing{{background:var(--blue);box-shadow:0 0 0 4px #dce7ff}}.node-status.rejected{{background:#e5484d}}.node-row details{{width:min(650px,60%)}}.node-row summary{{cursor:pointer;color:var(--blue);font-size:12px;padding:2px 0;list-style:none}}.node-row summary::-webkit-details-marker{{display:none}}.node-detail{{display:grid;grid-template-columns:1.2fr 1fr;gap:18px;margin-top:9px;padding:12px;border-radius:9px;background:#f8fafc}}.event{{display:flex;gap:9px;margin:6px 0}}.event-dot{{width:7px;height:7px;background:var(--blue);border-radius:50%;margin-top:6px;flex:none}}.event b,.event small{{display:block;font-size:12px}}.event small{{color:var(--muted)}}.event.muted{{color:var(--muted);font-size:12px}}.detail-meta{{color:var(--muted);font-size:12px;line-height:1.9}}@keyframes pulse{{0%,100%{{box-shadow:0 0 0 5px #dce7ff}}50%{{box-shadow:0 0 0 9px #dce7ff80}}}}@keyframes reveal{{from{{opacity:.4;transform:translateY(-3px)}}to{{opacity:1;transform:none}}}}@media(max-width:760px){{.wrap{{padding:20px 12px 40px}}h1{{font-size:23px}}.stage-rail{{min-width:760px}}.hero{{padding:17px;flex-direction:column}}.context-grid{{grid-template-columns:1fr}}.node-row{{display:block}}.node-row details{{width:100%;margin-top:8px}}.node-detail{{grid-template-columns:1fr}}}}
</style></head><body><main class="wrap"><h1>ERGOLIFE 商品全生命周期看板</h1><div class="sub">串行 MVP · 选择商品，查看阶段进度、当前节点与完整时间线{html.escape(source_note)}</div><div class="products">{product_cards}</div>
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


def _send_product_handoff_card(product: dict) -> None:
    lifecycle = product.get("lifecycle", {})
    next_owners = list(lifecycle.get("next_owner_user_ids") or [])
    if not next_owners and lifecycle.get("next_owner_user_id"):
        next_owners = [lifecycle["next_owner_user_id"]]
    if not next_owners or not lifecycle.get("next_code"):
        return
    if runtime.product_access.demo_mode and all(str(item).startswith("mock_") for item in next_owners):
        test_receiver = os.getenv("FEISHU_TEST_RECEIVE_ID", "").strip()
        next_owners = [test_receiver] if test_receiver else []
    if not next_owners:
        return
    card = product_handoff_card(
        product_name=product.get("product_name", "未命名商品"),
        sku=product.get("sku", ""),
        current_node=lifecycle.get("node_code", ""),
        next_node=f"{lifecycle['next_code']} {lifecycle.get('next_name', '')}".strip(),
        next_owner=lifecycle.get("next_owner_name") or lifecycle.get("next_owner_role") or "下一节点负责人",
    )
    try:
        api = FeishuOpenAPI()
        for next_owner in dict.fromkeys(str(item) for item in next_owners if item):
            api.send_interactive_card(next_owner, card)
    except (FeishuNotConfiguredError, RuntimeError):
        return


@app.get("/health")
def health() -> dict[str, str]:
    storage = "postgres" if runtime.repository.__class__.__name__ == "PostgresRepository" else "memory"
    product_storage = "postgres" if runtime.product_repository.dsn else "mock"
    return {
        "status": "ok",
        "service": "ergolife-feishu-workflow",
        "storage": storage,
        "product_storage": product_storage,
        "directory_storage": runtime.directory.source,
        "demo_mode": str(runtime.product_access.demo_mode).lower(),
    }


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "ergolife-feishu-workflow", "phase": "workflow-core"}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    project_id: str | None = None,
    view: str = "mine",
    demo_role: str | None = None,
    actor_open_id: str | None = None,
) -> HTMLResponse:
    if project_id:
        real_projects = runtime.real_lifecycle_dashboard_data(project_id)
        if any(item.get("id") == project_id for item in real_projects):
            return HTMLResponse(_render_dashboard(real_projects, project_id))
        return HTMLResponse(_render_dashboard(runtime.dashboard_data(), project_id))
    open_id = _current_open_id(request, actor_open_id)
    try:
        data = runtime.product_dashboard_data(view=view, open_id=open_id, demo_role=demo_role)
    except ValueError as exc:
        return HTMLResponse(f"<h1>请求有误</h1><p>{html.escape(str(exc))}</p>", status_code=400)
    return HTMLResponse(_render_product_workbench(data, view, demo_role))


@app.get("/lifecycle", response_class=HTMLResponse)
def lifecycle_dashboard(project_id: str | None = None) -> HTMLResponse:
    """Open the detailed full-lifecycle view from the real product master."""
    return HTMLResponse(_render_dashboard(runtime.real_lifecycle_dashboard_data(project_id), project_id))


def _require_directory_admin(request: Request) -> str:
    open_id = _current_open_id(request)
    if not open_id:
        raise HTTPException(status_code=401, detail="请先从飞书登录")
    if not runtime.directory.is_admin(open_id):
        raise HTTPException(status_code=403, detail="当前用户没有员工目录管理权限")
    return open_id


@app.get("/admin/directory", response_class=HTMLResponse)
def directory_admin_page(request: Request) -> HTMLResponse:
    try:
        _require_directory_admin(request)
    except HTTPException as exc:
        return HTMLResponse(f"<h1>无法打开角色配置</h1><p>{html.escape(str(exc.detail))}</p>", status_code=exc.status_code)
    return HTMLResponse(_render_directory_admin(runtime.directory_admin_data()))


def _run_directory_sync() -> None:
    try:
        runtime.sync_all_feishu_users()
    except Exception:
        # The detailed error is retained in runtime.directory_sync_status and
        # can be inspected by the administrator through the status endpoint.
        return


@app.get("/admin/directory/sync", response_class=HTMLResponse)
def sync_directory_page(request: Request, background_tasks: BackgroundTasks) -> Response:
    try:
        _require_directory_admin(request)
        runtime.directory_sync_status = {"status": "running", "fetched": 0, "synced": 0, "error": None}
        background_tasks.add_task(_run_directory_sync)
        return RedirectResponse("/admin/directory?sync=started", status_code=303)
    except HTTPException as exc:
        return HTMLResponse(f"<h1>无法同步通讯录</h1><p>{html.escape(str(exc.detail))}</p>", status_code=exc.status_code)


@app.get("/api/admin/directory/sync-status")
def directory_sync_status(request: Request) -> dict:
    _require_directory_admin(request)
    return runtime.directory_sync_status


@app.get("/api/admin/directory")
def directory_admin_data(request: Request) -> dict:
    _require_directory_admin(request)
    return runtime.directory_admin_data()


@app.post("/api/admin/directory/rules")
async def save_directory_rule(request: Request) -> dict:
    _require_directory_admin(request)
    body = await request.json()
    try:
        runtime.directory.set_role_rule(body.get("department_key"), body.get("role_code"))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "role_rules": runtime.directory.list_role_rules()}


@app.delete("/api/admin/directory/rules/{department_key}")
def delete_directory_rule(department_key: str, request: Request) -> dict:
    _require_directory_admin(request)
    runtime.directory.remove_role_rule(department_key)
    return {"status": "ok", "role_rules": runtime.directory.list_role_rules()}


@app.post("/api/admin/directory/users/{open_id}/roles")
async def save_user_roles(open_id: str, request: Request) -> dict:
    _require_directory_admin(request)
    body = await request.json()
    roles = body.get("roles") or []
    if not isinstance(roles, list):
        raise HTTPException(status_code=400, detail="roles 必须是数组")
    try:
        runtime.directory.set_manual_roles(open_id, roles)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="员工尚未从飞书登录") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "open_id": open_id, "roles": runtime.directory.roles_for_user(open_id)}


@app.post("/api/admin/directory/users/{open_id}/roles/auto")
def restore_auto_roles(open_id: str, request: Request) -> dict:
    _require_directory_admin(request)
    runtime.directory.clear_manual_roles(open_id)
    return {"status": "ok", "open_id": open_id, "roles": runtime.directory.roles_for_user(open_id)}


@app.get("/api/dashboard/projects")
def dashboard_projects() -> list[dict]:
    return runtime.dashboard_data()


@app.get("/api/dashboard/products")
def dashboard_products(
    request: Request,
    view: str = "mine",
    demo_role: str | None = None,
    actor_open_id: str | None = None,
) -> dict:
    return runtime.product_dashboard_data(
        view=view,
        open_id=_current_open_id(request, actor_open_id),
        demo_role=demo_role,
    )


@app.post("/api/products/{product_id}/advance")
async def advance_product(product_id: str, request: Request, background_tasks: BackgroundTasks) -> dict:
    body = await request.json()
    try:
        result = runtime.advance_product(
            product_id,
            open_id=_current_open_id(request, body.get("actor_open_id")),
            demo_role=body.get("demo_role"),
        )
        background_tasks.add_task(_send_product_handoff_card, result)
        return result
    except (KeyError, ValueError, PermissionError, RuntimeError) as exc:
        status = 403 if isinstance(exc, PermissionError) else 409 if isinstance(exc, RuntimeError) else 400
        from fastapi import HTTPException

        raise HTTPException(status_code=status, detail=str(exc)) from exc


@app.get("/api/me")
def current_identity(request: Request) -> dict:
    open_id = _current_open_id(request)
    actor = runtime.product_access.resolve_actor(open_id)
    return {
        "open_id": actor.open_id if open_id else None,
        "role": actor.role,
        "roles": list(actor.roles),
        "department": actor.department,
        "display_name": actor.display_name,
        "is_admin": bool(runtime.directory.is_admin(open_id)) if open_id else False,
        "directory_storage": runtime.directory.source,
        "authenticated": bool(open_id),
    }


@app.post("/api/auth/feishu/h5")
async def feishu_h5_auth(request: Request) -> dict:
    body = await request.json()
    try:
        user = feishu_identity.exchange_h5_code(str(body.get("code") or ""))
    except FeishuIdentityError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime.sync_feishu_user(user)
    response = {"authenticated": True, "open_id": str(user["open_id"])}
    # Set-Cookie cannot be returned from a plain dict, so use the same response
    # shape as the browser OAuth callback below.
    from fastapi.responses import JSONResponse

    result = JSONResponse(response)
    result.set_cookie(
        "ergolife_session",
        feishu_identity.sign_session(str(user["open_id"])),
        httponly=True,
        secure=feishu_identity.public_base_url.startswith("https://"),
        samesite="lax",
        max_age=86400,
    )
    return result


@app.get("/auth/feishu/login")
def feishu_login() -> RedirectResponse:
    try:
        return RedirectResponse(feishu_identity.authorization_url(), status_code=307)
    except FeishuIdentityError as exc:
        return HTMLResponse(f"<h1>飞书登录暂不可用</h1><p>{html.escape(str(exc))}</p>", status_code=503)


@app.get("/auth/feishu/callback")
def feishu_callback(code: str, state: str) -> Response:
    try:
        user = feishu_identity.exchange_code(code, state)
    except FeishuIdentityError as exc:
        return HTMLResponse(f"<h1>飞书登录失败</h1><p>{html.escape(str(exc))}</p>", status_code=400)
    runtime.sync_feishu_user(user)
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(
        "ergolife_session",
        feishu_identity.sign_session(str(user["open_id"])),
        httponly=True,
        secure=feishu_identity.public_base_url.startswith("https://"),
        samesite="lax",
        max_age=86400,
    )
    return response


@app.get("/auth/feishu/logout")
def feishu_logout() -> RedirectResponse:
    response = RedirectResponse("/dashboard", status_code=303)
    response.delete_cookie("ergolife_session")
    return response


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
