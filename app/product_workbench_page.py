"""Server-rendered product workbench page."""

from __future__ import annotations

import html
import json
from typing import Any
from urllib.parse import quote


XMSHOUXI_URL = "https://xmshouxi-production.up.railway.app/"


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def render_product_workbench(data: dict[str, Any], view: str, feishu_app_id: str = "") -> str:
    actor = data["actor"]
    products = data.get("products", [])
    summary = data.get("summary", {})
    view_counts = data.get("view_counts", {})
    view_labels = {"mine": "我的待办", "participating": "我参与的", "all": "全部商品"}
    source_labels = {
        "postgres": "商品数据：PostgreSQL",
        "mock": "商品数据：本地测试数据",
        "mock-fallback": "商品数据库暂不可用",
    }

    nav_items = [
        ("/dashboard", "商品工作台", True),
        ("/lifecycle", "全链路", False),
        ("/data-admin", "数据管理", False),
    ]
    primary_nav = "".join(
        f'<a class="el-nav-item{" is-active" if active else ""}" href="{href}">{label}</a>'
        for href, label, active in nav_items
    )

    if actor.get("is_admin"):
        admin_menu = """
        <details class="el-admin-menu">
          <summary>管理 <span aria-hidden="true">⌄</span></summary>
          <div class="el-admin-popover">
            <a href="/admin/directory"><b>员工与角色</b><small>配置部门与生命周期角色</small></a>
            <a href="/admin/directory/sync"><b>同步通讯录</b><small>更新飞书部门与员工</small></a>
          </div>
        </details>"""
    else:
        admin_menu = ""

    if actor.get("authenticated"):
        actor_role = actor.get("department") or (actor.get("roles") or ["未分配角色"])[0]
        identity = (
            '<div class="el-profile">'
            f'<span class="el-avatar">{_escape((actor.get("display_name") or "员")[:1])}</span>'
            f'<span><b>{_escape(actor.get("display_name") or "当前员工")}</b><small>{_escape(actor_role)}</small></span>'
            "</div>"
        )
    else:
        identity = '<a class="el-login" href="/auth/feishu/login">使用飞书身份登录</a>'

    overview = "".join(
        f'<a class="el-overview-item{" is-selected" if key == view else ""}" href="/dashboard?view={key}">'
        f'<span>{label}</span><strong>{int(view_counts.get(key, 0))}</strong>'
        f'<small>{"当前可处理 " + str(summary.get("actionable", 0)) + " 个" if key == view else "点击切换视图"}</small></a>'
        for key, label in view_labels.items()
    )

    market_options = ['<option value="all">全部市场</option>']
    for market in sorted({str(item.get("country_code") or "") for item in products if item.get("country_code")}):
        market_options.append(f'<option value="{_escape(market)}">{_escape(market)}</option>')
    stage_options = ['<option value="all">全部阶段</option>']
    for stage in dict.fromkeys(item.get("lifecycle", {}).get("stage") for item in products):
        if stage:
            stage_options.append(f'<option value="{_escape(stage)}">{_escape(stage)}</option>')

    cards = "".join(_render_product_card(item) for item in products)
    empty = (
        '<div class="el-empty" id="el-server-empty"><strong>当前视图没有商品</strong>'
        '<span>商品会根据你的飞书身份、部门和生命周期角色自动出现。</span></div>'
        if not products
        else ""
    )

    h5_auth_script = ""
    if feishu_app_id:
        h5_auth_script = (
            '<script src="https://lf-scm-cn.feishucdn.com/lark/op/h5-js-sdk-1.5.44.js"></script>'
            '<script>(function(){if(!window.tt||!window.tt.requestAuthCode)return;function request(){window.tt.requestAuthCode({appId:'
            + json.dumps(feishu_app_id)
            + ',success:function(info){if(!info||!info.code)return;fetch("/api/auth/feishu/h5",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code:info.code})}).then(function(response){if(response.ok)location.reload()})}})}if(window.h5sdk&&window.h5sdk.ready)window.h5sdk.ready(request);else request()})();</script>'
        )

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ERGOLIFE 商品工作台</title>
<style>
:root{{--el-bg:#f4f7fb;--el-surface:#fff;--el-soft:#f8faff;--el-text:#172033;--el-muted:#667085;--el-line:#e3e9f2;--el-blue:#245eea;--el-blue-soft:#edf3ff;--el-green:#087443;--el-green-soft:#eaf8f1;--el-orange:#b54708;--el-orange-soft:#fff3e8;--el-red:#b42318;--el-red-soft:#fff0ef;--el-shadow:0 12px 32px rgba(32,55,92,.08)}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--el-bg);color:var(--el-text);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}a{{color:inherit}}button,input,select{{font:inherit}}
.el-topbar{{display:flex;align-items:center;gap:24px;padding:14px 28px;background:var(--el-surface);border-bottom:1px solid var(--el-line);position:relative;z-index:5}}
.el-brand{{display:flex;align-items:center;gap:10px;text-decoration:none;white-space:nowrap}}.el-brand-mark{{display:grid;place-items:center;width:34px;height:34px;border-radius:10px;background:var(--el-blue);color:#fff;font-weight:700}}.el-brand strong,.el-brand small{{display:block}}.el-brand small{{color:var(--el-muted);font-size:11px;margin-top:-2px}}
.el-primary-nav{{display:flex;align-items:center;gap:4px;margin-right:auto}}.el-nav-item{{padding:9px 12px;border-radius:9px;text-decoration:none;color:var(--el-muted)}}.el-nav-item:hover{{background:var(--el-soft);color:var(--el-text)}}.el-nav-item.is-active{{background:var(--el-blue-soft);color:var(--el-blue)}}
.el-agent-link{{display:flex;align-items:center;gap:6px;padding:9px 12px;border:1px solid #bfd0ff;border-radius:9px;text-decoration:none;color:var(--el-blue);background:var(--el-blue-soft);white-space:nowrap}}.el-account{{display:flex;align-items:center;gap:8px}}.el-admin-menu{{position:relative}}.el-admin-menu summary{{list-style:none;cursor:pointer;padding:9px 11px;border:1px solid var(--el-line);border-radius:9px;background:var(--el-surface)}}.el-admin-menu summary::-webkit-details-marker{{display:none}}.el-admin-popover{{position:absolute;right:0;top:44px;width:250px;padding:7px;background:var(--el-surface);border:1px solid var(--el-line);border-radius:12px;box-shadow:var(--el-shadow)}}.el-admin-popover a{{display:block;padding:10px;border-radius:8px;text-decoration:none}}.el-admin-popover a:hover{{background:var(--el-soft)}}.el-admin-popover b,.el-admin-popover small{{display:block}}.el-admin-popover small{{font-size:11px;color:var(--el-muted);margin-top:2px}}
.el-profile{{display:flex;align-items:center;gap:8px;padding:4px 6px}}.el-profile b,.el-profile small{{display:block}}.el-profile small{{font-size:11px;color:var(--el-muted)}}.el-avatar{{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:var(--el-blue-soft);color:var(--el-blue)}}.el-login{{padding:9px 12px;border-radius:9px;background:var(--el-blue);color:#fff;text-decoration:none;white-space:nowrap}}
.el-main{{max-width:1240px;margin:0 auto;padding:30px 24px 54px}}.el-heading{{margin-bottom:22px}}.el-eyebrow{{font-size:11px;letter-spacing:.15em;color:var(--el-blue);margin-bottom:5px}}h1{{margin:0;font-size:28px;letter-spacing:-.03em}}.el-heading p{{margin:5px 0 0;color:var(--el-muted)}}
.el-overview{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:18px}}.el-overview-item{{display:grid;grid-template-columns:1fr auto;gap:2px 12px;padding:14px 16px;border:1px solid var(--el-line);border-radius:13px;background:var(--el-surface);text-decoration:none}}.el-overview-item strong{{grid-row:1/3;grid-column:2;font-size:26px;color:var(--el-blue)}}.el-overview-item small{{color:var(--el-muted)}}.el-overview-item.is-selected{{border-color:#9bb7ff;background:var(--el-blue-soft)}}
.el-toolbar{{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px;border:1px solid var(--el-line);border-radius:13px;background:var(--el-surface)}}.el-search{{display:flex;align-items:center;gap:8px;flex:1;min-width:230px;padding:0 10px;border:1px solid var(--el-line);border-radius:9px;background:var(--el-soft)}}.el-search input{{width:100%;padding:9px 0;border:0;outline:0;background:transparent;color:var(--el-text)}}.el-filter-group{{display:flex;align-items:center;gap:8px}}.el-filter-group label{{display:flex;align-items:center;gap:6px;color:var(--el-muted);white-space:nowrap}}select{{padding:8px 9px;border:1px solid var(--el-line);border-radius:9px;background:var(--el-surface);color:var(--el-text)}}.el-layout-switch{{display:flex;padding:3px;border:1px solid var(--el-line);border-radius:9px}}.el-layout-switch button{{border:0;background:transparent;border-radius:6px;padding:6px 9px;color:var(--el-muted);cursor:pointer}}.el-layout-switch button.is-selected{{background:var(--el-blue-soft);color:var(--el-blue)}}
.el-result-line{{display:flex;justify-content:space-between;padding:13px 2px 8px;color:var(--el-muted);font-size:12px}}.el-product-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}.el-product-grid.is-rows{{grid-template-columns:1fr}}.el-product{{padding:17px;border:1px solid var(--el-line);border-radius:15px;background:var(--el-surface);box-shadow:var(--el-shadow)}}.el-product[hidden]{{display:none}}.el-product-top{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}}.el-product-title{{display:flex;gap:10px;min-width:0}}.el-product-title h2{{margin:0 0 3px;font-size:16px}}.el-product-title p{{margin:0;color:var(--el-muted);font-size:11px}}.el-market{{display:grid;place-items:center;width:36px;height:36px;border-radius:9px;background:var(--el-blue-soft);color:var(--el-blue);font-size:11px;flex:none}}
.el-deadline{{padding:5px 8px;border-radius:999px;background:var(--el-green-soft);color:var(--el-green);font-size:11px;white-space:nowrap}}.el-deadline.due_soon{{background:var(--el-orange-soft);color:var(--el-orange)}}.el-deadline.overdue{{background:var(--el-red-soft);color:var(--el-red)}}.el-stage-line{{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;padding:18px 0 10px}}.el-stage-line span,.el-handoff span{{display:block;margin-bottom:3px;color:var(--el-muted);font-size:11px}}.el-node{{display:flex;align-items:center;gap:7px}}.el-node span{{margin:0;padding:3px 6px;border-radius:6px;background:var(--el-blue-soft);color:var(--el-blue)}}.el-progress{{height:5px;border-radius:999px;background:var(--el-line);overflow:hidden}}.el-progress span{{display:block;height:100%;border-radius:inherit;background:var(--el-blue)}}
.el-handoff{{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:12px;margin:15px 0;padding:12px;border-radius:10px;background:var(--el-soft)}}.el-handoff strong{{font-size:12px}}.el-handoff-arrow{{color:var(--el-muted)}}.el-card-actions{{display:flex;align-items:center;justify-content:space-between;gap:10px}}.el-detail-link{{padding:8px 0;color:var(--el-blue);text-decoration:none}}.el-advance{{padding:9px 12px;border:0;border-radius:9px;background:var(--el-blue);color:#fff;cursor:pointer}}.el-advance:disabled{{opacity:.6;cursor:wait}}.el-readonly{{color:var(--el-muted);font-size:12px}}.el-empty{{grid-column:1/-1;padding:50px;text-align:center;color:var(--el-muted);border:1px dashed #cbd5e1;border-radius:14px;background:var(--el-surface)}}.el-empty strong,.el-empty span{{display:block}}.el-filter-empty{{display:none}}.el-toast{{position:fixed;right:22px;bottom:22px;max-width:360px;padding:11px 14px;border-radius:10px;background:var(--el-text);color:#fff;box-shadow:var(--el-shadow);z-index:20}}
.el-source{{margin-top:18px;color:var(--el-muted);font-size:11px;text-align:right}}
@media(max-width:920px){{.el-topbar{{flex-wrap:wrap;gap:10px;padding:12px 18px}}.el-primary-nav{{order:3;width:100%}}.el-agent-link{{margin-left:auto}}.el-main{{padding:25px 18px}}.el-toolbar{{align-items:stretch;flex-direction:column}}.el-filter-group{{justify-content:space-between}}.el-product-grid{{grid-template-columns:1fr}}}}
@media(max-width:620px){{.el-main{{padding:22px 12px 40px}}.el-brand small,.el-profile>span:last-child{{display:none}}.el-primary-nav{{overflow:auto}}.el-nav-item{{padding:8px;font-size:12px}}.el-agent-link{{font-size:12px}}.el-overview{{grid-template-columns:1fr}}.el-filter-group{{align-items:stretch;flex-wrap:wrap}}.el-filter-group label{{display:block;flex:1 1 145px}}.el-filter-group label span{{display:block;margin-bottom:4px}}select{{width:100%}}.el-product-top,.el-stage-line{{align-items:flex-start;flex-direction:column}}.el-handoff{{grid-template-columns:1fr}}.el-handoff-arrow{{transform:rotate(90deg)}}.el-card-actions{{align-items:stretch;flex-direction:column}}.el-advance{{width:100%}}}}
</style></head>
<body>
<header class="el-topbar">
  <a class="el-brand" href="/dashboard"><span class="el-brand-mark">E</span><span><strong>ERGOLIFE</strong><small>商品协同中心</small></span></a>
  <nav class="el-primary-nav" aria-label="主要导航">{primary_nav}</nav>
  <a class="el-agent-link" href="{XMSHOUXI_URL}" target="_blank" rel="noopener noreferrer">部门 Agent <span aria-hidden="true">↗</span></a>
  <div class="el-account">{admin_menu}{identity}</div>
</header>
<main class="el-main">
  <section class="el-heading"><div class="el-eyebrow">PRODUCT WORKSPACE</div><h1>商品工作台</h1><p>集中处理我负责、我参与以及需要跨部门跟进的商品。</p></section>
  <section class="el-overview" aria-label="商品视图">{overview}</section>
  <section class="el-toolbar" aria-label="商品筛选">
    <label class="el-search"><span aria-hidden="true">⌕</span><input id="el-search" type="search" placeholder="搜索商品名称、SKU 或 MSKU" autocomplete="off" aria-label="搜索商品"></label>
    <div class="el-filter-group">
      <label><span>市场</span><select id="el-market">{''.join(market_options)}</select></label>
      <label><span>阶段</span><select id="el-stage">{''.join(stage_options)}</select></label>
      <div class="el-layout-switch" aria-label="视图切换"><button type="button" class="is-selected" data-layout="cards" aria-pressed="true">卡片</button><button type="button" data-layout="rows" aria-pressed="false">列表</button></div>
    </div>
  </section>
  <div class="el-result-line"><span id="el-result-count">{len(products)} 个商品</span><span>按紧急程度与节点顺序展示</span></div>
  <section class="el-product-grid" id="el-product-grid" aria-live="polite">{cards}{empty}<div class="el-empty el-filter-empty" id="el-filter-empty"><strong>没有匹配的商品</strong><span>调整搜索词或筛选条件后再试。</span></div></section>
  <div class="el-source">{_escape(source_labels.get(data.get("source"), data.get("source")))} · 权限来自飞书员工目录</div>
</main>
<div class="el-toast" id="el-toast" role="status" aria-live="polite" hidden></div>
<script>
(function(){{
  const grid=document.getElementById('el-product-grid');
  const cards=Array.from(document.querySelectorAll('.el-product'));
  const search=document.getElementById('el-search');
  const market=document.getElementById('el-market');
  const stage=document.getElementById('el-stage');
  const count=document.getElementById('el-result-count');
  const filterEmpty=document.getElementById('el-filter-empty');
  const toast=document.getElementById('el-toast');
  function showToast(message){{toast.textContent=message;toast.hidden=false;window.setTimeout(function(){{toast.hidden=true}},3000)}}
  function filterCards(){{const query=search.value.trim().toLowerCase();let visible=0;cards.forEach(function(card){{const match=(!query||card.dataset.search.includes(query))&&(market.value==='all'||card.dataset.market===market.value)&&(stage.value==='all'||card.dataset.stage===stage.value);card.hidden=!match;if(match)visible+=1}});count.textContent=visible+' 个商品';filterEmpty.style.display=visible===0&&cards.length?'block':'none'}}
  [market,stage].forEach(function(control){{control.addEventListener('change',filterCards)}});search.addEventListener('input',filterCards);
  document.querySelectorAll('[data-layout]').forEach(function(button){{button.addEventListener('click',function(){{grid.classList.toggle('is-rows',button.dataset.layout==='rows');document.querySelectorAll('[data-layout]').forEach(function(item){{const selected=item===button;item.classList.toggle('is-selected',selected);item.setAttribute('aria-pressed',String(selected))}})}})}});
  document.querySelectorAll('.el-advance').forEach(function(button){{button.addEventListener('click',async function(){{const original=button.textContent;button.disabled=true;button.textContent='正在交接…';try{{const response=await fetch('/api/products/'+encodeURIComponent(button.dataset.id)+'/advance',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:'{{}}'}});const body=await response.json();if(!response.ok)throw new Error(body.detail||'操作失败');showToast('交接成功，正在刷新商品状态');window.setTimeout(function(){{location.reload()}},500)}}catch(error){{showToast(error.message||'操作失败');button.disabled=false;button.textContent=original}}}})}});
}})();
</script>{h5_auth_script}
</body></html>"""


def _render_product_card(item: dict[str, Any]) -> str:
    lifecycle = item["lifecycle"]
    access = item["access"]
    node_code = str(lifecycle.get("node_code") or "P01")
    try:
        node_number = max(1, min(22, int(node_code.removeprefix("P"))))
    except ValueError:
        node_number = 1
    progress = round(node_number / 22 * 100, 1)
    deadline_status = str(lifecycle.get("deadline_status") or "normal")
    product_id = str(item.get("id") or "")
    search_text = " ".join(
        str(value or "") for value in (item.get("product_name"), item.get("sku"), item.get("amazon_sku"))
    ).lower()
    if access.get("can_advance") and lifecycle.get("next_code"):
        action = f'<button type="button" class="el-advance" data-id="{_escape(product_id)}">完成并交接</button>'
    elif lifecycle.get("next_code"):
        action = '<span class="el-readonly">当前身份只读</span>'
    else:
        action = '<span class="el-readonly">生命周期已完成</span>'
    next_text = " · ".join(
        part for part in (lifecycle.get("next_owner_name"), lifecycle.get("next_owner_role")) if part
    ) or "生命周期终点"
    owner_text = " · ".join(
        part for part in (lifecycle.get("owner_name"), lifecycle.get("owner_department")) if part
    ) or str(lifecycle.get("owner_role") or "未配置")
    return f"""
    <article class="el-product" data-market="{_escape(item.get('country_code'))}" data-stage="{_escape(lifecycle.get('stage'))}" data-search="{_escape(search_text)}">
      <div class="el-product-top">
        <div class="el-product-title"><span class="el-market">{_escape(item.get('country_code') or '—')}</span><div><h2>{_escape(item.get('product_name') or '未命名商品')}</h2><p>{_escape(item.get('sku'))} · {_escape(item.get('amazon_sku') or '无 MSKU')}</p></div></div>
        <span class="el-deadline {_escape(deadline_status)}">{_escape(lifecycle.get('deadline_label') or '未设置截止时间')}</span>
      </div>
      <div class="el-stage-line"><div><span>当前阶段</span><strong>{_escape(lifecycle.get('stage') or '未配置阶段')}</strong></div><div class="el-node"><span>{_escape(node_code)}</span><b>{_escape(lifecycle.get('node_name') or '未配置节点')}</b></div></div>
      <div class="el-progress" role="progressbar" aria-label="生命周期进度 {node_number}/22" aria-valuenow="{node_number}" aria-valuemin="1" aria-valuemax="22"><span style="width:{progress}%"></span></div>
      <div class="el-handoff"><div><span>当前负责人</span><strong>{_escape(owner_text)}</strong></div><span class="el-handoff-arrow" aria-hidden="true">→</span><div><span>下一交接</span><strong>{_escape(next_text)}</strong></div></div>
      <footer class="el-card-actions"><a class="el-detail-link" href="/lifecycle?project_id={quote(product_id, safe='')}">查看全链路</a>{action}</footer>
    </article>"""
