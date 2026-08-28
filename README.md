# ERGOLIFE 商品全生命周期协同 MVP

第一阶段是脱离飞书的流程核心，用模拟数据验证 P01～P22 串行生命周期。

## 当前范围

- P01～P22 全部节点配置化
- V1 默认串行，保留 `execution_mode`、`depends_on`、`next_nodes` 以支持后续并行
- `config/actions_v1.yaml` 已导入 Base 中的 33 个动作明细
- 节点已记录事件/结果/阈值三类触发条件；事件/阈值节点在触发前保持“未开始”
- P22 补货预警完成后回到 P12，形成补货周期闭环
- 根据业务角色分配模拟负责人
- 支持领取、提交、验收、退回、重新提交
- 支持项目完成和审计事件
- 预留 MemoryRepository → Feishu Bitable → 正式数据库的替换边界

## PostgreSQL 演示环境

设置 `DATABASE_URL` 后，运行时会自动使用 PostgreSQL，并在启动时创建
`workflow_projects`、`workflow_nodes`、`workflow_events` 三张表；首次启动还会写入三条演示商品。
未设置该变量时继续使用内存仓储，便于本地单元测试。

Railway 中建议在应用服务的 Variables 里添加 PostgreSQL 服务变量引用：

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
WORKFLOW_REPOSITORY=auto
```

其中 `Postgres` 替换为你在 Railway 中实际显示的数据库服务名。不要把连接串提交到 Git。

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

## 商品协同工作台（当前 MVP）

工作台单独读取商品主数据数据库的 `product_market_parameters` 表，使用其中的
`lifecycle_node_code` 显示当前节点。它不会依赖工作流数据库中的演示项目。

```text
PRODUCT_DATABASE_URL=${{ecommerce-postgres.DATABASE_URL}}
DEMO_MODE=true
```

网页应用打开 `/dashboard` 后可以切换“我的商品 / 我参与的商品 / 全部商品”。
在 `DEMO_MODE=true` 时页面还会显示部门角色切换器，方便用一个飞书账号演示
产品、采购、品质、物流、仓储、运营等角色；正式上线前应关闭该开关并使用飞书
真实身份登录。登录入口为 `/auth/feishu/login`，回调地址为
`/auth/feishu/callback`，两者都要加入飞书应用的安全设置。

### 公司内部员工与角色

员工从飞书网页应用打开工作台后，服务端会用 `tt.requestAuthCode` 获取当前员工的
`open_id`，再通过飞书通讯录接口同步姓名、部门和岗位。服务端会在工作流数据库中
维护三张目录表：

- `directory_users`：飞书员工资料；
- `lifecycle_role_rules`：部门名称/部门 ID 到生命周期角色的规则；
- `directory_role_members`：员工与角色的多对多关系。

应用启动时会从 `config/role_mapping.mock.yaml` 的部门字段写入默认角色规则；后续
可增加管理员页面维护规则，不需要为每位员工增加 Railway 环境变量。交接时会把卡片
发送给下一个角色的全部有效成员。生产环境建议设置 `DEMO_MODE=false` 和
`ALLOW_QUERY_ACTOR=false`，这样权限只由真实飞书身份决定。

机器人卡片中的工作台按钮使用 `https://applink.feishu.cn/client/web_app/open`，
会唤起当前飞书应用的网页主页，而不是直接把 Railway 地址交给系统浏览器。
网页主页在飞书客户端内会加载 H5 JS SDK，通过 `tt.requestAuthCode` 调用
`/api/auth/feishu/h5` 建立当前用户会话；在普通浏览器中则保留 OAuth 登录入口。

当前节点允许操作时，点击“完成并交接”会以乐观并发方式把商品的
`lifecycle_node_code` 更新为节点定义中的 `next_nodes[0]`，因此下一部门马上能在
“我的商品”看到它。后续接入完整交付物、附件和节点历史时，可以在此适配器上继续
扩展，而不必改页面和权限模型。
