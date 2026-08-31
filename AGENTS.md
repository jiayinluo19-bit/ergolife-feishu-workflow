# ERGOLIFE 电商智能协同系统开发上下文

本文件是 `E:\code\ecommerce-agent-v1` 及其子目录的项目级开发说明。开启新的 Codex 任务后，应先阅读本文件，再根据任务范围读取当前仓库或兄弟仓库的 `README.md`。不要仅凭仓库名推断两个系统的职责。

## 1. 项目总览

ERGOLIFE 当前由两个独立部署、相互协作的应用组成。

### 1.1 xmshouxi：多部门 Agent 系统

- 本地仓库：`E:\code\ecommerce-agent-v1`
- GitHub：`https://github.com/jiayinluo19-bit/xmshouxi.git`
- 产品定位：面向公司不同部门的 AI 工作入口。
- 技术栈：Next.js 14 前端、Python/FastAPI 后端、Agent/Skill/Tool 架构。
- 当前部门入口包括销售、库存、物流、选品、广告、运营、财务、设计和招聘。
- 核心职责：对话、分析、查询、生成内容、调用部门工具及执行受控的业务工作流。
- 它不是商品生命周期的权威状态机，不应自行维护另一套商品生命周期状态。

### 1.2 ergolife-feishu-workflow：商品全生命周期与飞书协同应用

- 本地仓库：`E:\code\ergolife-feishu-workflow`
- GitHub：`https://github.com/jiayinluo19-bit/ergolife-feishu-workflow.git`
- 产品定位：面向具体商品的全生命周期管理系统。
- 技术栈：Python/FastAPI、PostgreSQL、飞书网页应用、飞书机器人和消息卡片。
- 核心职责：管理 P01～P22 商品生命周期、当前节点、负责人、部门交接、角色权限、飞书通知和后续的交付物/附件/审计历史。
- 网页应用是主要工作台；机器人主要负责提醒、任务通知和状态变化通知。

## 2. 两个系统的关系

```text
公司员工
   │
   ├─ 飞书 ERGOLIFE 应用
   │    ├─ 商品工作台 / 全生命周期看板
   │    ├─ 节点操作 / 部门交接 / 角色管理
   │    ├─ 机器人通知
   │    └─ 跳转到 xmshouxi 部门 Agent 界面
   │
   └─ xmshouxi 多部门 Agent
        ├─ 部门 Agent
        ├─ Skills / Tools
        ├─ 数据分析与业务辅助
        └─ 通过受控 API 查询或操作商品生命周期
```

总体原则：`xmshouxi` 是“员工如何智能地完成工作”，`ergolife-feishu-workflow` 是“商品现在走到哪里、由谁负责、如何交接”。

## 3. 数据库拓扑与当前决策

当前连接方式是合理且有意保留的，不要默认把所有表合并到一个数据库。

```text
xmshouxi
   └─ DATABASE_URL
       └─ Railway: ecommerce-postgres

ergolife-feishu-workflow
   ├─ PRODUCT_DATABASE_URL
   │   └─ Railway: ecommerce-postgres
   └─ DATABASE_URL
       └─ Railway: Postgres（工作流应用数据库）
```

### 3.1 ecommerce-postgres：共享业务主数据

这是电商业务数据的主要数据库，由 `xmshouxi` 的 Alembic 迁移维护。当前重要表包括：

- `market_codes`：国家/市场代码。
- `product_market_parameters`：SKU + 国家市场 + MSKU 维度的商品主数据。
- `replenishment_policies`：补货策略。
- `replenishment_item_settings`：具体商品的补货参数。
- `inventory_position_snapshots`：库存位置快照。
- 其他由销售、物流、库存工作流产生的业务数据表。

`ergolife-feishu-workflow` 通过 `PRODUCT_DATABASE_URL` 读取 `product_market_parameters`。当前商品工作台和全链路看板使用其中的 `lifecycle_node_code` 作为商品当前生命周期节点。

### 3.2 Postgres：飞书工作流应用运行数据

这是 `ergolife-feishu-workflow` 的应用数据库。当前表包括：

- `workflow_projects`：旧版/演示工作流项目聚合。
- `workflow_nodes`：工作流节点实例。
- `workflow_events`：工作流审计事件。
- `directory_users`：飞书通讯录员工。
- `lifecycle_role_rules`：部门到生命周期角色的映射。
- `directory_role_members`：员工与生命周期角色关系。
- `directory_role_overrides`：特殊员工的角色覆盖。

这些表不在飞书中；它们位于 `ergolife-feishu-workflow` 的 `DATABASE_URL` 所指向的 PostgreSQL。

### 3.3 数据归属原则

- 商品、市场、库存、补货、销售等跨系统业务主数据归 `ecommerce-postgres`。
- 飞书身份、角色映射、通知状态、工作流执行和审计数据归工作流数据库。
- 当前 `product_market_parameters.lifecycle_node_code` 是便于列表查询的“当前状态投影”。
- 后续完整生命周期历史应由 `ergolife-feishu-workflow` 管理，建议增加正式的生命周期实例、节点事件、交付物和附件表，并以 `product_market_parameters.id` 作为商品外部关联键。
- 不允许两个应用各自维护一套可写的生命周期状态。生命周期推进的最终写入口应归 `ergolife-feishu-workflow`。
- `xmshouxi` 可以读取商品和生命周期投影；需要推进、退回、交接商品时，应调用 `ergolife-feishu-workflow` 的受控 API，而不是直接更新数据库字段。

如果未来为了降低 Railway 成本而使用同一个 PostgreSQL 实例，也应通过不同数据库或 schema 保留上述边界，不要把“同一个实例”误解为“同一个业务模型”。

## 4. 飞书内整合 xmshouxi 的目标方案

计划在 `ergolife-feishu-workflow` 的飞书网页应用中增加“部门 Agent”入口，跳转到部署后的 `xmshouxi` 界面。推荐按以下阶段实施：

1. MVP：在飞书网页应用中增加入口，使用飞书内新页面打开 `xmshouxi`。
2. 身份统一：`xmshouxi` 接入同一个飞书企业身份，使用 `open_id` 识别员工。
3. 权限统一：由服务端把飞书用户映射为部门和角色，前端参数不能作为可信权限依据。
4. 系统调用：`xmshouxi` 通过服务端 API 调用商品生命周期能力，使用短期签名令牌或服务间凭证。
5. 体验整合：在 xmshouxi 对话或工具结果中提供商品详情、生命周期节点和可执行动作的深链接。

不要在 URL 中传递 App Secret、数据库连接串或长期有效凭证。两个 Railway 域名之间不能依赖共享浏览器 Cookie；身份联通应使用飞书 OAuth/免登或后端签发的短期一次性令牌。默认优先使用飞书内页面跳转，不依赖 iframe。

## 5. 当前实现状态（2026-08-31）

### xmshouxi

- 已有 Next.js 部门入口和聊天界面。
- 已有 FastAPI AgentEngine、AgentRegistry、ToolRegistry 和统一 LLM Client。
- 已有 PostgreSQL/Alembic 商品、市场、补货和库存基础模型。
- 已有物流销量、缓存、Excel/工作簿等工具型工作流。
- 数据库连接目标为 `ecommerce-postgres`。

### ergolife-feishu-workflow

- 已部署到 Railway，并接入已发布的飞书企业自建应用。
- 已有 P01～P22 串行生命周期定义，并保留未来并行扩展字段。
- 商品工作台读取 `ecommerce-postgres.product_market_parameters`。
- 全链路详情读取真实商品和 `lifecycle_node_code`，不再使用 `PRJ-MOCK-*` 作为用户页面数据。
- 当前商品表只保存当前节点和更新时间，尚没有完整的真实节点历史；页面不得伪造历史事件。
- 已接入飞书身份、全员通讯录同步、部门/角色映射、管理员入口和节点交接通知。
- 管理员来自 `FEISHU_ADMIN_OPEN_IDS` 或飞书租户管理员身份。

## 6. 新任务的默认阅读顺序

开启新任务后：

1. 先阅读本文件。
2. 查看当前仓库 `git status --short`，保留用户已有改动。
3. 阅读 `E:\code\ecommerce-agent-v1\README.md`。
4. 如果任务涉及飞书、生命周期、角色、通知或商品交接，再阅读 `E:\code\ergolife-feishu-workflow\README.md` 及相关代码。
5. 如果任务涉及数据库结构，先确认目标连接是 `DATABASE_URL` 还是 `PRODUCT_DATABASE_URL`，禁止凭表名猜测数据库。

## 7. 开发约束

- 不提交 `.env`、App Secret、数据库密码、MCP Key 或其他凭证。
- `ecommerce-postgres` 的结构变更必须使用 `backend/alembic/versions/` 中的 Alembic 迁移。
- 跨系统字段必须定义清楚唯一标识；商品统一使用 `product_market_parameters.id`，不要仅靠 SKU 关联。
- 涉及生命周期写操作时，优先扩展 `ergolife-feishu-workflow` API，不在 xmshouxi Tool 中直接写生命周期字段。
- 飞书 `open_id` 是用户身份标识，不是商品业务主键。
- 页面展示的数据来源必须明确；真实数据不可静默回退为演示数据。
- 修改任一项目后，在对应仓库运行测试，并只提交本次任务相关文件。
- 两个项目可独立部署和回滚，禁止制造必须同时发布才能启动的强耦合。

## 8. 近期架构路线

1. 在工作流数据库建立正式商品生命周期实例和事件历史，关联 `product_market_parameters.id`。
2. 将 `lifecycle_node_code` 保留为当前状态投影，并由工作流服务在事务中同步更新。
3. 为 xmshouxi 提供只读查询和受控动作 API。
4. 让 xmshouxi 接入飞书身份与部门角色。
5. 在 ERGOLIFE 飞书工作台增加“部门 Agent”入口，完成飞书内统一入口。
6. 后续再增加交付物、附件、节点 SLA、审批、提醒、失败重试和审计能力。

