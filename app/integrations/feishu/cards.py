import os
from urllib.parse import quote


def task_assignment_card(
    *, project_id: str, node_instance_id: str, product_name: str, node_name: str, owner_name: str,
    node_status: str = "ready", source_status: str = "未开始", trigger_type: str = "", trigger_condition: str = ""
) -> dict:
    dashboard_url = os.getenv("PUBLIC_BASE_URL", "https://ergolife-feishu-workflow-production.up.railway.app")
    dashboard_url = f"{dashboard_url.rstrip('/')}/dashboard?project_id={quote(project_id)}"
    waiting_trigger = node_status == "pending"
    trigger_labels = {"event": "事件触发", "result": "结果触发", "threshold": "阈值触发"}
    actions = []
    if waiting_trigger:
        actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "模拟触发条件"}, "type": "primary", "value": {"action": "trigger_node", "project_id": project_id, "node_instance_id": node_instance_id}})
    else:
        actions.append({"tag": "button", "text": {"tag": "plain_text", "content": "接受任务"}, "type": "primary", "value": {"action": "claim", "project_id": project_id, "node_instance_id": node_instance_id}})
    actions.extend([
        {"tag": "button", "text": {"tag": "plain_text", "content": "打开生命周期看板"}, "type": "default", "url": dashboard_url},
        {"tag": "button", "text": {"tag": "plain_text", "content": "模拟完成当前节点"}, "type": "default", "value": {"action": "simulate_complete", "project_id": project_id, "node_instance_id": node_instance_id}},
    ])
    trigger_text = f"\n**状态：**{source_status}\n**触发：**{trigger_labels.get(trigger_type, trigger_type)} · {trigger_condition}" if trigger_condition else ""
    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "ERGOLIFE 新任务"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**商品：**{product_name}\n**节点：**{node_name}\n**负责人：**{owner_name}{trigger_text}"}},
            {"tag": "hr"},
            {"tag": "action", "actions": actions},
        ],
    }


def project_lifecycle_card(*, product_name: str, project_status: str, progress: str, lines: list[str]) -> dict:
    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {"template": "turquoise", "title": {"tag": "plain_text", "content": "ERGOLIFE 商品全链路"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**商品：**{product_name}\n**项目状态：**{project_status}\n**进度：**{progress}"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
        ],
    }
