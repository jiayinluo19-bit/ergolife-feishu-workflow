"""In-memory relational data-management demo for the Feishu workbench.

This module deliberately does not connect to PostgreSQL. It demonstrates the
interaction contract we want before wiring the same preview/confirm flow to
real ecommerce tables and a database transaction.
"""

from __future__ import annotations

import math
import json
from copy import deepcopy
from threading import RLock
from typing import Any
from uuid import uuid4


class DataAdminError(ValueError):
    """A user-correctable data-management request error."""


class DataAdminConflict(DataAdminError):
    """The state changed after a preview was generated."""


TABLE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "products": {
        "label": "商品主数据",
        "primary_key": "id",
        "columns": ["id", "sku", "product_name", "status"],
        "column_labels": {"id": "商品 ID", "sku": "SKU", "product_name": "商品名称", "status": "状态"},
        "editable_fields": {"sku", "product_name", "status"},
    },
    "sales_daily": {
        "label": "日销量",
        "primary_key": "id",
        "columns": ["id", "product_id", "sale_date", "units"],
        "column_labels": {"id": "销量记录 ID", "product_id": "商品", "sale_date": "日期", "units": "销量"},
        "editable_fields": {"product_id", "sale_date", "units"},
    },
    "inventory_positions": {
        "label": "库存位置",
        "primary_key": "id",
        "columns": ["id", "product_id", "warehouse", "on_hand", "reserved", "damaged", "available_qty"],
        "column_labels": {
            "id": "库存记录 ID",
            "product_id": "商品",
            "warehouse": "仓库",
            "on_hand": "在库",
            "reserved": "预留",
            "damaged": "损坏",
            "available_qty": "可用库存（计算）",
        },
        "editable_fields": {"product_id", "warehouse", "on_hand", "reserved", "damaged"},
    },
    "replenishment_plans": {
        "label": "补货计划",
        "primary_key": "id",
        "columns": [
            "id",
            "product_id",
            "avg_daily_sales",
            "available_qty",
            "safety_stock",
            "target_stock",
            "suggested_replenishment",
            "status",
        ],
        "column_labels": {
            "id": "计划 ID",
            "product_id": "商品",
            "avg_daily_sales": "日均销量（计算）",
            "available_qty": "可用库存（计算）",
            "safety_stock": "安全库存",
            "target_stock": "目标库存（计算）",
            "suggested_replenishment": "建议补货量（计算）",
            "status": "状态（计算）",
        },
        "editable_fields": {"product_id", "safety_stock"},
    },
}


def _initial_state() -> dict[str, list[dict[str, Any]]]:
    return {
        "products": [
            {"id": "prod-70030", "sku": "70030-2TK", "product_name": "可调节阻力登山机", "status": "active"},
            {"id": "prod-70012", "sku": "70012-2", "product_name": "握力器（白色）", "status": "active"},
        ],
        "sales_daily": [
            {"id": "sale-1001", "product_id": "prod-70030", "sale_date": "2026-08-29", "units": 18},
            {"id": "sale-1002", "product_id": "prod-70030", "sale_date": "2026-08-30", "units": 22},
            {"id": "sale-1003", "product_id": "prod-70012", "sale_date": "2026-08-30", "units": 9},
        ],
        "inventory_positions": [
            {"id": "inv-1001", "product_id": "prod-70030", "warehouse": "US-01", "on_hand": 120, "reserved": 15, "damaged": 2, "available_qty": 103},
            {"id": "inv-1002", "product_id": "prod-70012", "warehouse": "US-01", "on_hand": 60, "reserved": 10, "damaged": 1, "available_qty": 49},
        ],
        "replenishment_plans": [
            {"id": "plan-1001", "product_id": "prod-70030", "avg_daily_sales": 0, "available_qty": 0, "safety_stock": 100, "target_stock": 0, "suggested_replenishment": 0, "status": "待计算"},
            {"id": "plan-1002", "product_id": "prod-70012", "avg_daily_sales": 0, "available_qty": 0, "safety_stock": 80, "target_stock": 0, "suggested_replenishment": 0, "status": "待计算"},
        ],
    }


class DemoDataAdmin:
    """Small relational state machine used by the interactive demo."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._state = _initial_state()
        self._revision = 1
        self._previews: dict[str, dict[str, Any]] = {}
        self._recompute(self._state)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            metadata = deepcopy(TABLE_DEFINITIONS)
            for definition in metadata.values():
                definition["editable_fields"] = sorted(definition["editable_fields"])
            return {"revision": self._revision, "tables": deepcopy(self._state), "metadata": metadata}

    def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.preview_batch({"operations": [payload]})

    def preview_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            operations = payload.get("operations") or []
            if not isinstance(operations, list) or not operations:
                raise DataAdminError("至少需要一条待提交变更")

            working = deepcopy(self._state)
            requested_keys: set[tuple[str, str]] = set()
            for operation_payload in operations:
                if not isinstance(operation_payload, dict):
                    raise DataAdminError("每条变更必须是 JSON 对象")
                table = str(operation_payload.get("table") or "").strip()
                operation = str(operation_payload.get("operation") or "").strip().lower()
                record_id = str(operation_payload.get("record_id") or "").strip()
                values = operation_payload.get("values") or {}
                if table not in TABLE_DEFINITIONS:
                    raise DataAdminError(f"不支持的数据表：{table or '未选择'}")
                if operation not in {"insert", "update", "delete"}:
                    raise DataAdminError("操作必须是 insert、update 或 delete")
                if not isinstance(values, dict):
                    raise DataAdminError("values 必须是 JSON 对象")
                requested_keys.update(self._apply_operation(working, table, operation, record_id, values))
            self._recompute(working)
            changes = self._diff(self._state, working, requested_keys)
            if not changes:
                raise DataAdminError("这次操作没有产生数据变化")
            preview_id = f"preview-{uuid4().hex[:12]}"
            self._previews[preview_id] = {"revision": self._revision, "state": working, "changes": changes}
            return {"preview_id": preview_id, "revision": self._revision, "summary": self._summary(changes), "changes": changes}

    def commit(self, preview_id: str) -> dict[str, Any]:
        with self._lock:
            key = str(preview_id or "").strip()
            preview = self._previews.pop(key, None)
            if not preview:
                raise DataAdminError("预览不存在或已经确认过")
            if preview["revision"] != self._revision:
                raise DataAdminConflict("数据已经被其他操作更新，请重新生成预览")
            self._state = preview["state"]
            self._revision += 1
            return {"status": "committed", "revision": self._revision, "summary": self._summary(preview["changes"]), "changes": preview["changes"], "state": self.snapshot()}

    def reset(self) -> dict[str, Any]:
        with self._lock:
            self._state = _initial_state()
            self._recompute(self._state)
            self._revision += 1
            self._previews.clear()
            return self.snapshot()

    @staticmethod
    def _apply_operation(state: dict[str, list[dict[str, Any]]], table: str, operation: str, record_id: str, values: dict[str, Any]) -> set[tuple[str, str]]:
        definition = TABLE_DEFINITIONS[table]
        primary_key = definition["primary_key"]
        rows = state[table]
        if operation == "insert":
            normalized = DemoDataAdmin._normalize_values(table, values, inserting=True)
            new_id = record_id or str(normalized.pop(primary_key, "")) or f"{table}-demo-{uuid4().hex[:6]}"
            if any(str(row.get(primary_key)) == new_id for row in rows):
                raise DataAdminError(f"记录 ID 已存在：{new_id}")
            normalized[primary_key] = new_id
            DemoDataAdmin._validate_reference(state, table, normalized)
            rows.append(normalized)
            return {(table, new_id)}

        if not record_id:
            raise DataAdminError("update/delete 操作必须填写记录 ID")
        index = next((i for i, row in enumerate(rows) if str(row.get(primary_key)) == record_id), None)
        if index is None:
            raise DataAdminError(f"找不到记录：{table}.{record_id}")
        if operation == "update":
            normalized = DemoDataAdmin._normalize_values(table, values, inserting=False)
            candidate = {**rows[index], **normalized}
            DemoDataAdmin._validate_reference(state, table, candidate)
            rows[index] = candidate
            return {(table, record_id)}
        if operation == "delete":
            del rows[index]
            requested = {(table, record_id)}
            if table == "products":
                for related_table in ("sales_daily", "inventory_positions", "replenishment_plans"):
                    related_rows = state[related_table]
                    removed = [row for row in related_rows if row.get("product_id") == record_id]
                    state[related_table] = [row for row in related_rows if row.get("product_id") != record_id]
            return requested
        raise DataAdminError(f"不支持的操作：{operation}")

    @staticmethod
    def _normalize_values(table: str, values: dict[str, Any], *, inserting: bool) -> dict[str, Any]:
        definition = TABLE_DEFINITIONS[table]
        allowed = set(definition["editable_fields"]) | ({definition["primary_key"]} if inserting else set())
        unknown = set(values) - allowed
        if unknown:
            raise DataAdminError(f"{definition['label']} 不允许修改字段：{', '.join(sorted(unknown))}")
        normalized = dict(values)
        for field in {"units", "on_hand", "reserved", "damaged", "safety_stock"} & set(normalized):
            try:
                normalized[field] = int(normalized[field])
            except (TypeError, ValueError) as exc:
                raise DataAdminError(f"字段 {field} 必须是整数") from exc
            if normalized[field] < 0:
                raise DataAdminError(f"字段 {field} 不能小于 0")
        for field in ("product_id", "sku", "product_name", "status", "warehouse", "sale_date"):
            if field in normalized:
                normalized[field] = str(normalized[field]).strip()
                if not normalized[field]:
                    raise DataAdminError(f"字段 {field} 不能为空")
        if "status" in normalized and normalized["status"] not in {"active", "inactive"}:
            raise DataAdminError("商品状态只能是 active 或 inactive")
        return normalized

    @staticmethod
    def _validate_reference(state: dict[str, list[dict[str, Any]]], table: str, row: dict[str, Any]) -> None:
        if table in {"sales_daily", "inventory_positions", "replenishment_plans"}:
            product_id = row.get("product_id")
            if not any(product["id"] == product_id for product in state["products"]):
                raise DataAdminError(f"商品不存在：{product_id}")

    @staticmethod
    def _recompute(state: dict[str, list[dict[str, Any]]]) -> None:
        for row in state["inventory_positions"]:
            row["available_qty"] = max(0, int(row.get("on_hand", 0)) - int(row.get("reserved", 0)) - int(row.get("damaged", 0)))
        product_ids = {product["id"] for product in state["products"]}
        for plan in state["replenishment_plans"]:
            product_id = plan["product_id"]
            if product_id not in product_ids:
                continue
            sales = [row for row in state["sales_daily"] if row["product_id"] == product_id]
            inventory = [row for row in state["inventory_positions"] if row["product_id"] == product_id]
            avg_daily_sales = round(sum(int(row["units"]) for row in sales) / len(sales), 1) if sales else 0
            available_qty = sum(int(row["available_qty"]) for row in inventory)
            safety_stock = int(plan.get("safety_stock", 0))
            target_stock = math.ceil(avg_daily_sales * 14 + safety_stock)
            suggested = max(0, target_stock - available_qty)
            plan.update({"avg_daily_sales": avg_daily_sales, "available_qty": available_qty, "target_stock": target_stock, "suggested_replenishment": suggested, "status": "需补货" if suggested else "库存充足"})

    @staticmethod
    def _diff(before: dict[str, list[dict[str, Any]]], after: dict[str, list[dict[str, Any]]], requested_keys: set[tuple[str, str]]) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for table in TABLE_DEFINITIONS:
            primary_key = TABLE_DEFINITIONS[table]["primary_key"]
            old_rows = {str(row[primary_key]): row for row in before[table]}
            new_rows = {str(row[primary_key]): row for row in after[table]}
            for record_id in sorted(set(old_rows) | set(new_rows)):
                old_row, new_row = old_rows.get(record_id), new_rows.get(record_id)
                if old_row == new_row:
                    continue
                key = (table, record_id)
                change_type = "insert" if old_row is None else "delete" if new_row is None else "update"
                if key in requested_keys:
                    source = "用户操作"
                elif new_row is None:
                    source = "级联删除"
                else:
                    source = "连锁更新"
                changes.append({"table": table, "table_label": TABLE_DEFINITIONS[table]["label"], "record_id": record_id, "change_type": change_type, "source": source, "before": old_row, "after": new_row})
        return changes

    @staticmethod
    def _summary(changes: list[dict[str, Any]]) -> dict[str, int]:
        return {"total": len(changes), "user_changes": sum(change["source"] == "用户操作" for change in changes), "cascade_changes": sum(change["source"] != "用户操作" for change in changes), "deletes": sum(change["change_type"] == "delete" for change in changes)}


def render_data_admin(state: dict[str, Any]) -> str:
    """Render the standalone demo page with its current in-memory snapshot."""

    initial_state = json.dumps(state, ensure_ascii=False).replace("<", "\\u003c")
    page = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ERGOLIFE 关联数据管理 Demo</title>
<style>
:root{--blue:#3370ff;--ink:#182230;--muted:#667085;--line:#e5eaf2;--bg:#f4f7fb;--green:#087443;--orange:#b54708;--red:#b42318}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:1400px;margin:auto;padding:26px 18px 60px}h1{margin:0;font-size:27px}h2{margin:0 0 12px;font-size:18px}.sub{color:var(--muted);margin:5px 0 16px}.top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;flex-wrap:wrap}.back{color:var(--blue);text-decoration:none;background:#fff;border:1px solid var(--line);border-radius:9px;padding:8px 12px}.notice{background:#fff9ed;border:1px solid #f5d08a;border-radius:12px;padding:12px 14px;color:#7a4b00;margin:16px 0}.panel{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px;margin:14px 0;box-shadow:0 3px 14px #1b3a5d08;overflow:auto}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}.tab{border:1px solid var(--line);background:#fff;border-radius:9px;padding:9px 13px;cursor:pointer;color:var(--ink)}.tab.active{background:#edf3ff;border-color:#9dbaff;color:var(--blue);font-weight:700}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:10px}table{width:100%;border-collapse:collapse;min-width:760px}th,td{padding:10px 11px;border-bottom:1px solid #f0f2f6;text-align:left;white-space:nowrap;vertical-align:top}th{background:#f8fafc;color:var(--muted);font-weight:600}tr:last-child td{border-bottom:0}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}.empty{color:var(--muted);padding:28px;text-align:center}.form-grid{display:grid;grid-template-columns:180px 180px 1fr;gap:12px;align-items:start}label{display:block;color:var(--muted);font-size:12px;margin-bottom:5px}select,input,textarea,button{font:inherit;border:1px solid #cfd7e5;border-radius:8px;padding:8px;background:#fff;color:var(--ink)}input,select,textarea{width:100%}textarea{min-height:100px;resize:vertical;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}.field-wide{grid-column:1/-1}.actions{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-top:12px}button{cursor:pointer}button.primary{background:var(--blue);border-color:var(--blue);color:#fff;font-weight:700}button.secondary{color:var(--blue)}.hint{font-size:12px;color:var(--muted);margin-top:7px}.preview{display:none}.preview.show{display:block}.summary{display:flex;gap:22px;flex-wrap:wrap;background:#f8fafc;border-radius:10px;padding:12px 14px;margin-bottom:12px}.summary strong{font-size:20px;color:var(--blue);display:block}.summary small{color:var(--muted)}.change-row.user{background:#f4f8ff}.change-row.cascade{background:#fffaf0}.badge{display:inline-block;border-radius:99px;padding:3px 8px;font-size:11px;font-weight:700}.badge.user{background:#e0eaff;color:var(--blue)}.badge.cascade{background:#fff0c2;color:var(--orange)}.badge.delete{background:#fee4e2;color:var(--red)}.json{white-space:pre-wrap;max-width:390px;word-break:break-word;color:#475467;font-size:11px}.success{color:var(--green);font-weight:700}.error{color:var(--red);font-weight:700}.legend{font-size:12px;color:var(--muted);margin-top:10px}.legend span{margin-right:15px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}.dot.user{background:var(--blue)}.dot.cascade{background:#f79009}@media(max-width:780px){.wrap{padding:20px 12px 45px}h1{font-size:23px}.form-grid{grid-template-columns:1fr}.field-wide{grid-column:auto}.panel{padding:14px}}
</style></head><body><main class="wrap">
<div class="top"><div><h1>ERGOLIFE 关联数据管理 Demo</h1><div class="sub">先预览影响范围，再二次确认提交；当前数据仅保存在本次进程内存中</div></div><a class="back" href="/dashboard">返回商品工作台</a></div>
<div class="notice">这是第一版联动更新体验原型，暂不接真实 PostgreSQL，也暂不做权限。示例关系：修改销量会重算日均销量和建议补货量；修改库存会重算可用库存、目标库存和补货状态；删除商品会预览关联记录的级联删除。</div>
<section class="panel"><h2>当前数据</h2><div id="tabs" class="tabs"></div><div id="table-view"></div><div class="legend"><span><i class="dot user"></i>用户直接操作</span><span><i class="dot cascade"></i>系统连锁更新</span></div></section>
<section class="panel"><h2>发起数据操作</h2><div class="form-grid"><div><label for="table">数据表</label><select id="table"></select></div><div><label for="operation">操作</label><select id="operation"><option value="update">修改 UPDATE</option><option value="insert">新增 INSERT</option><option value="delete">删除 DELETE</option></select></div><div><label for="record-id">记录 ID（新增可留空）</label><input id="record-id" placeholder="例如 sale-1001"></div><div class="field-wide"><label for="values">字段值 JSON（删除时留空）</label><textarea id="values" spellcheck="false"></textarea><div id="hint" class="hint"></div></div></div><div class="actions"><button id="preview-btn" class="primary">生成影响预览</button><button id="reset-btn" class="secondary">刷新演示数据</button><span id="message"></span></div></section>
<section id="preview-panel" class="panel preview"><h2>影响预览</h2><div id="preview-summary" class="summary"></div><div class="hint">确认后才会写入本次 Demo 状态。真实版本这里应对应一个数据库事务。</div><div class="table-wrap"><table><thead><tr><th>来源</th><th>数据表</th><th>记录</th><th>变化类型</th><th>变更前</th><th>变更后</th></tr></thead><tbody id="changes"></tbody></table></div><div class="actions"><button id="commit-btn" class="primary">二次确认并提交</button><button id="cancel-btn" class="secondary">取消预览</button></div></section>
</main><script>
const initialState=__INITIAL_STATE__;let state=initialState;let currentTable='sales_daily';let pendingPreview=null;const tableMeta=state.metadata;const tableOrder=Object.keys(tableMeta);
const esc=(value)=>String(value??'').replace(/[&<>"']/g,(char)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));const pretty=(value)=>value==null?'—':esc(JSON.stringify(value,null,2));
function renderTable(){const meta=tableMeta[currentTable];document.querySelectorAll('.tab').forEach((button)=>button.classList.toggle('active',button.dataset.table===currentTable));const rows=state.tables[currentTable]||[];if(!rows.length){document.getElementById('table-view').innerHTML='<div class="empty">暂无记录</div>';return}const head=meta.columns.map((field)=>'<th>'+esc(meta.column_labels[field]||field)+'</th>').join('');const body=rows.map((row)=>'<tr>'+meta.columns.map((field)=>'<td class="'+(field==='id'||field.endsWith('_id')?'mono':'')+'">'+esc(row[field])+'</td>').join('')+'</tr>').join('');document.getElementById('table-view').innerHTML='<div class="table-wrap"><table><thead><tr>'+head+'</tr></thead><tbody>'+body+'</tbody></table></div>'}
function renderTabs(){document.getElementById('tabs').innerHTML=tableOrder.map((table)=>'<button class="tab" data-table="'+table+'">'+esc(tableMeta[table].label)+'<small> · '+(state.tables[table]||[]).length+' 条</small></button>').join('');document.querySelectorAll('.tab').forEach((button)=>button.addEventListener('click',()=>{currentTable=button.dataset.table;document.getElementById('table').value=currentTable;renderTable();fillExample()}));renderTable()}
function renderTableOptions(){document.getElementById('table').innerHTML=tableOrder.map((table)=>'<option value="'+table+'">'+esc(tableMeta[table].label)+'（'+table+'）</option>').join('');document.getElementById('table').value=currentTable}
function fillExample(){const operation=document.getElementById('operation').value;const table=document.getElementById('table').value;const id=document.getElementById('record-id');const values=document.getElementById('values');const hint=document.getElementById('hint');if(operation==='delete'){id.value=table==='products'?'prod-70030':table==='sales_daily'?'sale-1001':table==='inventory_positions'?'inv-1001':'plan-1001';values.value='';hint.textContent='删除商品会同时预览销量、库存和补货计划的级联删除。'}else if(operation==='insert'){id.value='';if(table==='sales_daily'){values.value='{"product_id":"prod-70030","sale_date":"2026-08-31","units":25}';hint.textContent='新增一条销量后，会重算对应商品的日均销量和补货建议。'}else if(table==='inventory_positions'){values.value='{"product_id":"prod-70030","warehouse":"US-02","on_hand":80,"reserved":5,"damaged":0}';hint.textContent='新增库存位置后，会重算对应商品的总可用库存。'}else if(table==='products'){values.value='{"sku":"70099-1","product_name":"演示新增商品","status":"active"}';hint.textContent='商品主数据新增本身不会自动生成补货计划。'}else{values.value='{"product_id":"prod-70030","safety_stock":120}';hint.textContent='补货计划只允许手工修改安全库存，其他指标由系统计算。'}}else{if(table==='sales_daily'){id.value='sale-1001';values.value='{"units":30}';hint.textContent='把销量 18 改为 30，观察补货计划的连锁变化。'}else if(table==='inventory_positions'){id.value='inv-1001';values.value='{"on_hand":80}';hint.textContent='把在库 120 改为 80，观察可用库存和补货建议的连锁变化。'}else if(table==='products'){id.value='prod-70030';values.value='{"product_name":"可调节阻力登山机 Pro"}';hint.textContent='商品名称修改只影响商品主数据，关联表通过 product_id 关联。'}else{id.value='plan-1001';values.value='{"safety_stock":140}';hint.textContent='安全库存是人工配置字段，其他补货指标会重算。'}}}
function setState(next){state=next;renderTabs();renderTableOptions()}
async function send(url,options){const response=await fetch(url,options);const body=await response.json().catch(()=>({}));if(!response.ok)throw new Error(body.detail||'请求失败');return body}
function renderPreview(preview){pendingPreview=preview;document.getElementById('preview-panel').classList.add('show');const summary=preview.summary;document.getElementById('preview-summary').innerHTML='<div><strong>'+summary.total+'</strong><small>总变更记录</small></div><div><strong>'+summary.user_changes+'</strong><small>用户直接操作</small></div><div><strong>'+summary.cascade_changes+'</strong><small>连锁更新</small></div><div><strong>'+summary.deletes+'</strong><small>删除记录</small></div>';document.getElementById('changes').innerHTML=preview.changes.map((change)=>'<tr class="change-row '+(change.source==='用户操作'?'user':'cascade')+'"><td><span class="badge '+(change.source==='用户操作'?'user':'cascade')+'">'+esc(change.source)+'</span></td><td>'+esc(change.table_label)+'<br><span class="mono">'+esc(change.table)+'</span></td><td class="mono">'+esc(change.record_id)+'</td><td><span class="badge '+(change.change_type==='delete'?'delete':'')+'">'+esc(change.change_type)+'</span></td><td><div class="json">'+pretty(change.before)+'</div></td><td><div class="json">'+pretty(change.after)+'</div></td></tr>').join('')}
document.getElementById('table').addEventListener('change',()=>{currentTable=document.getElementById('table').value;renderTable();fillExample()});document.getElementById('operation').addEventListener('change',fillExample);document.getElementById('preview-btn').addEventListener('click',async()=>{const message=document.getElementById('message');message.className='';try{let values={};if(document.getElementById('operation').value!=='delete'){values=JSON.parse(document.getElementById('values').value||'{}')}const preview=await send('/api/data-admin/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({table:document.getElementById('table').value,operation:document.getElementById('operation').value,record_id:document.getElementById('record-id').value,values})});renderPreview(preview);message.textContent='预览已生成';message.className='success'}catch(error){message.textContent=error.message;message.className='error'}});document.getElementById('commit-btn').addEventListener('click',async()=>{if(!pendingPreview)return;const message=document.getElementById('message');try{const result=await send('/api/data-admin/commit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({preview_id:pendingPreview.preview_id})});setState(result.state);pendingPreview=null;document.getElementById('preview-panel').classList.remove('show');message.textContent='已确认提交，演示数据已更新';message.className='success'}catch(error){message.textContent=error.message;message.className='error'}});document.getElementById('cancel-btn').addEventListener('click',()=>{pendingPreview=null;document.getElementById('preview-panel').classList.remove('show')});document.getElementById('reset-btn').addEventListener('click',async()=>{try{const result=await send('/api/data-admin/reset',{method:'POST'});setState(result);pendingPreview=null;document.getElementById('preview-panel').classList.remove('show');const message=document.getElementById('message');message.textContent='演示数据已恢复';message.className='success'}catch(error){document.getElementById('message').textContent=error.message;document.getElementById('message').className='error'}});renderTableOptions();renderTabs();fillExample();
</script></body></html>"""
    return page.replace("__INITIAL_STATE__", initial_state)


demo_data_admin = DemoDataAdmin()
