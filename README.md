# ERGOLIFE 商品全生命周期协同 MVP

第一阶段是脱离飞书的流程核心，用模拟数据验证 P01～P22 串行生命周期。

## 当前范围

- P01～P22 全部节点配置化
- V1 默认串行，保留 `execution_mode`、`depends_on`、`next_nodes` 以支持后续并行
- 33个动作作为节点内部交付物/检查项的来源
- 根据业务角色分配模拟负责人
- 支持领取、提交、验收、退回、重新提交
- 支持项目完成和审计事件
- 预留 MemoryRepository → Feishu Bitable → 正式数据库的替换边界

## 本地运行

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
uvicorn app.main:app --reload
```

健康检查：`http://127.0.0.1:8000/health`

## 发送第一条真实飞书卡片

将 `.env.example` 复制为 `.env`，填写 App Secret 和你自己的测试接收人。接收人可以使用 `open_id`，也可以将 `FEISHU_RECEIVE_ID_TYPE` 改为飞书 API 支持的接收人类型。

```powershell
python scripts/send_test_card.py
```

不要把 `.env` 提交到 Git 或发送到聊天中。

## 后续飞书对接

飞书 SDK、通讯录、多维表格、机器人消息和事件回调放在 `app/integrations/feishu/`，不进入当前流程核心。

本地接入需要在运行环境设置 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`。不要把 App Secret 提交到 Git。回调地址为 `/api/feishu/events` 和 `/api/feishu/card-actions`；正式接入前还需要补齐事件验签/加解密、幂等处理和 Feishu Repository。
