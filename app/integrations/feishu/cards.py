def task_assignment_card(
    *, project_id: str, node_instance_id: str, product_name: str, node_name: str, owner_name: str
) -> dict:
    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "ERGOLIFE 新任务"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**商品：**{product_name}\n**节点：**{node_name}\n**负责人：**{owner_name}"}},
            {"tag": "hr"},
            {"tag": "action", "actions": [
                {"tag": "button", "text": {"tag": "plain_text", "content": "接受任务"}, "type": "primary", "value": {"action": "claim", "project_id": project_id, "node_instance_id": node_instance_id}},
                {"tag": "button", "text": {"tag": "plain_text", "content": "查看项目"}, "type": "default", "value": {"action": "view_project", "project_id": project_id, "node_instance_id": node_instance_id}},
                {"tag": "button", "text": {"tag": "plain_text", "content": "模拟完成当前节点"}, "type": "default", "value": {"action": "simulate_complete", "project_id": project_id, "node_instance_id": node_instance_id}},
            ]},
        ],
    }
