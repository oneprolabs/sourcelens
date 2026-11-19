# SourceLens 多租户改造：设计评审与实施风险

- 日期：2026-11-11
- 分支：`fengren/多租户重构`
- 代码基线：`5a146721`（已合并 `origin/main`，含 #803 Evaluation 改造）
- 评审对象：ChatGPT 共享方案《SourceLens 权限数据库设计》（约 20 张表、四层权限模型）
- 评审方式：只读代码 + 三轮外部评审追问（ChatGPT），未修改任何产品代码
- 结论：**不建议直接按原方案建 20 张表。** 应先关闭已确认的凭据越权缺口，并就“私有助手直链预览”做出产品决策；然后做"租户内核 + 三条业务链"的最小切片；执行面隔离与数据库迁移必须拆成独立的安全边界切换。

> 本文是评审文档，不是实施计划承诺。所有 DDL 均为草案，落地前需按真实表名、主键类型和 Django 迁移状态调整。

---

## 0. 文档信息与来源

### 0.1 外部评审来源

1. 原始共享方案：`https://chatgpt.com/s/t_6af3d01dae088191ae6d6071abed7bf0`（只读副本）
2. 第一轮追问（10 个风险点 + A/B 两个高风险点；A 已确认，B 经代码复核为现有产品行为）：
   `https://chatgpt.com/c/6af3d3b5-f860-83ec-86fc-ca5a7a156902`
3. 第二轮追问（对象存储、缓存/WebSocket、备份/下线、身份、匿名面、MCP、评估/告警、搜索、MVP、威胁模型、方案复盘）：
   同一会话
4. 第三轮追问（执行拓扑、本地 FS、RLS 与蓝绿、47 个模型分类、最小改动清单）：
   同一会话
5. 新全站原型对照（租户/平台双控制台、代管、助手可见性、API Key、节点、审计）：
   `docs/prototype-multitenancy-gap.md`

原文摘录保存在本机临时目录（会话结束可能清理）：
`/private/var/folders/q_/4sch03w96l7fk3wd1p5c6fpr0000gn/T/opencode/chatgpt-answer-clean.md`
`.../chatgpt-answer2-clean.md`
`.../chatgpt-answer3-clean.md`

### 0.2 代码事实来源

本文所有"现状"结论均来自本仓库只读检查，关键位置：

| 主题 | 文件 |
|---|---|
| 身份与角色 | `backend/accounts/models.py`、`backend/accounts/access.py` |
| 权限类 | `backend/accounts/permissions.py`、`backend/lens/views/base.py` |
| 助手与访问授权 | `backend/lens/models.py`、`backend/lens/views/assistants.py` |
| 凭据接口 | `backend/lens/views/credentials.py` |
| 会话 / Run 作用域 | `backend/lens/views/sessions.py` |
| 管理端 Run 列表 | `backend/lens/views/admin_runs.py` |
| 全局设置 / 定时任务 | `backend/lens/models.py`、`backend/lens/views/global_settings.py` |
| 计量 | `backend/lens/alerts.py`、`backend/lens/run_diagnostics.py` |
| 执行面 | `lensnode/lensnode/agent_runtime/runtime.py`、`lensnode/lensnode/session_workspace.py` |
| 数据源挂载 | `lensnode/lensnode/session_datasources.py` |
| 数据库与版本 | `pyproject.toml`、`backend/core/settings/database.py` |
| 路由与 admin | `backend/core/urls.py`、`backend/lens/urls.py` |

### 0.3 术语

- **Tenant**：安全与计费边界，不是"用户组"。
- **Platform Catalog**：平台维护的模板/工具定义（Skill、MCP Server 定义、标准指标），可被多个租户引用，不含租户密钥与运行数据。
- **Tenant 级**：业务数据归某个租户所有，必须有可信 `tenant_id`。
- **全局共享**：真正跨租户的身份/公共基础信息（User、Profile）。
- **无租户**：平台基础设施或全局配置（LensNode、GlobalSetting）。
- **P0/P1/P2**：P0 = 多租户功能上线前必须解决；P1 = 可在 MVP 后按明确风险边界补齐；P2 = 可延后。

### 0.4 已确认决策（2026-11-11）

1. **一个用户只能属于一个租户。** 不实现应用内租户切换；租户从 `TenantMembership` 唯一关系推导。
2. **角色权限内置，只保留三个角色**：普通用户（member）、租户管理员（tenant_admin）、超级管理员（super_admin）。
3. 不建 `roles / permissions / role_permissions / membership_roles` 等可配置 RBAC 表；权限矩阵写在代码里，随发布变更。

这三条会显著缩小 S1 的范围；后续 ADR 与阶段计划按此修订。

---

## 1. 执行摘要

### 1.1 五个结论

1. **原设计方向正确，但不是本仓库的迁移方案。** 它假设已有 RBAC / Permission / 审计体系；本仓库只有 3 个 feature key、一个全局 `Role`、`is_staff` 和 Django model permission。
2. **当前代码是彻底的单租户。** 全仓没有 `tenant` 概念（只有飞书 `tenant_access_token` 这类第三方术语）；`lens` 47 个模型类、`accounts` 3 个、`evaluation` 5 个，全部是全局表。
3. **最大的风险不在数据库，而在执行面。** 不同租户的 Run 共享同一个 LensNode 容器和 `/workspace`，`trusted_container` 直接把 shell 委托给节点进程，官方注释明确"不提供额外沙箱"。数据库隔离再正确，也挡不住 shell 读别的租户文件。
4. **存量资源没有 owner，租户归属不可推导。** `Assistant / DataSource / Skill / MCPServer / Connection` 都没有 `created_by`；只能整库归入 bootstrap tenant，不能声称"历史数据已完成租户隔离"。
5. **已经存在一个可立即验证的越权缺口（A：凭据接口）；另一个高风险点（B：私有助手直链预览）是有测试保护的现有产品行为，需先做产品决策**（见 §2.3）。

### 1.2 推荐路线（一句话）

> **S0 止血 → S1 租户内核 + Assistant/DataSource/Run 三条链 → S2 存储/通信/DB 防线 → S3 切换与收敛 → S4 执行面与外部集成隔离。**

不要一次改完 47 个模型；不要先建 20 张表；不要把 RLS 上线当作多租户完成的标志。

---

## 2. 当前实现事实

### 2.1 身份与权限

| 维度 | 现状 | 证据 |
|---|---|---|
| 用户身份 | Django `auth.User` + `accounts.Profile`；邮箱全局唯一；allauth `EmailAddress` | `backend/accounts/models.py` |
| 租户 | **不存在**。无 `Tenant` / `TenantMembership` | 全仓 `grep tenant` 无业务命中 |
| 角色 | 全局 `Role`：`name` 全局唯一、`visible_features` JSON、`users` / `groups` M2M | `backend/accounts/models.py` |
| 权限粒度 | 只有 3 个 feature key：`workspace` / `admin_console` / `run_evaluations` | `backend/accounts/access.py` |
| 超级用户 | `is_staff` 直接获得全部 feature；`is_superuser` 可用于 Django admin | `backend/accounts/access.py` |
| 资源权限 | 仅助手有 user/group grant（`AssistantAccess`，level 固定 `view`） | `backend/lens/models.py:403` |
| 其他管理资源 | `DataSource / Skill / MCP / Connection / GlobalSetting` 等用 `BaseAdminViewSet`（staff only） | `backend/lens/views/*.py` |
| 权限判定入口 | 分散在 ViewSet / Serializer / Model / 模板；没有统一 `authorize()` | 全仓 |
| 审计 | 无通用操作审计；只有 Plugin 调用审计和诊断证据 | `backend/lens/plugins/audit.py` |

### 2.2 资源归属与作用域

| 对象 | 现状 | 风险 |
|---|---|---|
| `Assistant` | `slug` 全局唯一；`visibility` 只有 `public/private`；无 `created_by` | 第二个租户无法使用同名 slug；无法推导归属 |
| `DataSource` | 无 owner；`lensnode` 全局；`target_path` 全局 | 数据源可被任意助手绑定 |
| `Skill` / `MCPServer` / `Connection` | 全局共享；无 owner | 无法区分平台目录与租户实例 |
| `Session` | `user` FK；无 tenant | 多租户用户的历史会话归属歧义 |
| `Run` | 通过 `session__user` 过滤；无 tenant | 管理员列表跨用户；评估/告警也按全局扫描 |
| `AssistantDataSourceBinding` | 接受任意 `datasource_uuid` | 未来是跨租户 IDOR 面 |
| `Assistant.collaboration_members` | 可指向任意 Assistant | 同上 |
| `Session.allowed_assistant_uuids` | JSON 存任意助手 UUID | 同上 |
| `SharedQA` | 公开 token 分享；无 tenant | 分享链接跨租户可读，需重新定义边界 |
| `EvaluationDataset` | 有 `created_by`；无 tenant | 快照可含租户数据，Judge 会外发 |

### 2.3 两个高风险点（A 已确认，B 待产品确认）

#### A. 任意登录用户可读取/改写数据源凭据（已确认缺口）

`backend/lens/views/credentials.py:17`

```python
class DataSourceCredentialViewSet(BaseAuthenticatedViewSet):
```

`BaseAuthenticatedViewSet` 只有 `permissions.IsAuthenticated`（`backend/lens/views/base.py:15`），而路由是 `/api/lens/admin/credentials/`。因此任意已登录用户都可以：

- `GET /api/lens/admin/credentials/` 列出全部凭据元数据；
- `POST /api/lens/admin/credentials/{uuid}/reveal/` 拿到**解密后的明文 secret**；
- 创建、修改、删除凭据。

同目录的 `admin/datasources` 使用的是 `BaseAdminViewSet`（staff only），说明这是权限不一致，而不是有意设计。

#### B. 匿名用户可读取私有助手元数据（现有产品行为，需重新决策）

`backend/lens/views/assistants.py:193-206`

```python
assistant = Assistant.objects.filter(
    slug=slug,
    status=Assistant.Status.ACTIVE,
    is_system=False,
).first()
```

**没有过滤 `visibility`。** 由于 `PublicAssistantView` 设置了 `authentication_classes = []`、`permission_classes = []`，任何未登录者只要知道/猜到 slug，就能读取私有助手的 `name`、`description`、`slug`。

但需要修正第一轮评审的判断：**这不是一个未被发现的漏洞，而是有测试保护的产品行为。**

`backend/lens/tests/test_api.py:7990`
`test_public_view_previews_private_assistant_without_private_config`
明确断言：直接链接可以预览私有助手的展示元数据，但只返回 `{name, description, slug}`，且匿名不能创建会话。

因此：
- 单租户下这是“分享链接登录前预览”的体验设计；
- 多租户下它变成**跨租户元数据枚举面**：任何知道 slug 的人都能看到其他租户助手的名称/描述；
- 是否收紧（只允许 `public` 预览，或保留但加租户/登录限制）是**产品决策**，不能当作纯安全 bug 直接改掉。

> A 必须在 S0 修复并进入 CI；B 需要先确认产品是否保留“私有助手直链预览”，再决定是否加 `visibility` 过滤。两者都不能等 `tenant_id` 改造完成。

### 2.4 执行面

| 事实 | 证据 |
|---|---|
| 所有租户的 Run 通过同一个 LensNode WebSocket 下发 | `backend/lens/consumers.py`、`backend/lens/services.py` |
| LensNode 全局注册；一个节点一个 bearer token | `LensNode.auth_token_hash`、`lensnode_auth.py` |
| 工作区按 `sessions/<session_uuid>/runs/<run_uuid>` 组织 | `lensnode/lensnode/session_workspace.py` |
| datasource 以 symlink 挂入 session 的 `sources/` | `lensnode/lensnode/session_datasources.py` |
| `trusted_container` 直接委托节点进程执行 shell，无额外沙箱 | `lensnode/lensnode/agent_runtime/runtime.py:326-352` |
| 命令继承节点环境变量 | 同上（`inherit_env=True`） |

**结论：这是当前多租户最大的安全边界缺口。** 数据库 RLS、RBAC、API Key 都绕不过这一层。

### 2.5 文件、缓存、实时通信

| 维度 | 现状 | 证据 |
|---|---|---|
| 媒体根 | `MEDIA_ROOT=/opt/storage/media`；文档附件 `DOCUMENT_ATTACHMENT_STORAGE_ROOT=/opt/document-attachments` | `backend/core/settings/base.py` |
| 文件路径 | 按 UUID/业务 ID 组织；无 tenant 前缀 | `attachments.py`、`document_attachments.py`、`RunOutputFile` |
| 下载鉴权 | `RunOutputFileDownloadView` 校验 owner 或 staff | `backend/lens/views/sessions.py:566` |
| 缓存 key | `lens:run-live:{run_uuid}` | `backend/lens/services.py` |
| Channels group | `lens.lensnode.{lensnode_uuid}` | `backend/lens/services.py:210` |
| 预签名 URL | 不存在（本地 FS） | — |

UUID 只能降低枚举概率，不能代替授权；`MEDIA_ROOT` 若被静态目录或执行容器直接挂载，会形成旁路。

### 2.6 后台任务、设置与计量

| 维度 | 现状 | 风险 |
|---|---|---|
| 后台任务 | `run_retention`、`dispatch_alerts`、`datasource sync` 均全局扫描 | 无租户上下文、无 per-tenant 配额 |
| 定时任务镜像 | `ScheduledTask` 混合全局任务（retention/cleanup/health）与数据源同步（应按租户） | 不能一刀切加 `tenant_id` |
| 全局设置 | `GlobalSetting` 20+ 个单例 key（history budget、retention、并发、judge 模型等） | 无法按租户覆盖，租户不能关闭平台安全限制 |
| 计量 | `LLMUsage` 在外部包 `agentcore_metering`，只能通过 `metadata.run_uuid` 反查 | 无 tenant 维度，成本无法按租户分摊 |
| 通知 | `agentcore_notifier` 外部表；Alert 渠道/收件人全局 | 跨租户通知面 |

### 2.7 迁移与测试基线

| 项 | 数值 |
|---|---|
| 模型类 | `lens/models.py` 47 个（含抽象基类 `TimestampedUUIDModel`）、`accounts` 3 个、`evaluation` 5 个 |
| 迁移 | `accounts` 7 个 + `lens` 70 个 = 77 个 |
| 后端测试 | 1539 个 `def test_` |
| Django / DRF | Django 5.1.4 / DRF 3.15.2 |
| 数据库连接 | `CONN_MAX_AGE=0`（每请求新连接） |
| 部署 | 单生产主机、蓝绿、PostgreSQL、共享 Redis 与本地存储 |
| 规模（既往运维记录，非本次实测） | 2–3 个用户、10–32 Run/天；`RunTraceEvent` 曾达 GB 级 |

> Django 5.1 不支持 `CompositePrimaryKey`（5.2 才引入），也不支持 ORM 复合外键；复合约束只能用 `RunSQL` + `SeparateDatabaseAndState`。

---

## 3. 原设计对照

| 原设计要求 | 现状 | 差距 / 结论 |
|---|---|---|
| `users` 全平台唯一，不挂 `tenant_id`/`role` | 已有 `auth.User` + Profile | 不新建 users 表；按现有身份模型映射 |
| `tenants` + `tenant_memberships` | 无 | 必须新建；已确认一用户一租户（`UNIQUE(user_id)`） |
| `roles` + `permissions` + `role_permissions` + `membership_roles` | 只有全局 Role + 3 个 feature | **不做**：已确认三角色内置，权限矩阵写在代码里 |
| `groups` + `group_members` | Django `auth.Group` 全局，且同时用于角色继承、助手授权、告警订阅 | 不能直接给 `auth.Group` 加 tenant；需新模型或 profile 表 |
| `resource_grants` 统一资源授权 | 只有 `AssistantAccess`（view） | 扩展现有表；不要另起第三套 |
| `assistant_access_policies` + subjects | `Assistant.visibility` + `AssistantAccess` | 语义接近；`public/tenant/restricted` 需重新定义 |
| `platform_user_roles` | `is_staff` / `is_superuser` 旁路 | 必须收敛，禁止 `is_staff` 作为业务授权 |
| `tenant_impersonation_sessions` + permissions | 无 | MVP 建议直接禁用代管 |
| `api_keys` + scopes | `AgentToken`（用户级、只读 QA 路由白名单） | 两者分离；TenantApiKey 延后 |
| `audit_logs` | 无通用审计 | 需要字段级脱敏 + outbox + 分区 |
| 复合 FK `(tenant_id, id)` + RLS | 无 tenant；Django 5.1 限制 | 只对关键关联建复合 FK；RLS 作为第二道防线 |
| 资源归属 `owner_type/owner_id/tenant_id` | 大部分资源无 owner | 改为 `tenant_id + owner_user_id + visibility`，避免多态 owner |
| `resource_shares` | 无 | MVP 只支持同租户共享；跨租户分享走 public 能力 |

---

## 4. 风险登记表

> 严重度按"数据泄露可能性 × 攻击面 × 改造依赖"排序，不按原方案编号。

### 4.1 P0

| ID | 风险 | 类型 | 证据 | 影响 | 缓解 / 决策 |
|---|---|---|---|---|---|
| SEC-01 | 数据源凭据接口仅要求登录 | 授权 | `views/credentials.py:17` | 任意登录用户可 reveal 明文 secret | S0：至少改为 `admin_console`（与 `admin/datasources` 对齐）；`reveal` 独立权限 + 审计；list/retrieve 不返回明文 |
| SEC-02 | 私有助手直链预览未过滤 visibility | 授权/产品 | `views/assistants.py:193`；`tests/test_api.py:7990` 明确断言此行为 | 多租户下成为跨租户元数据枚举面 | 先确认产品是否保留直链预览；若保留，至少在 S2 加租户/登录限制；若不保留，S0 加 `visibility=public` + 统一 404 |
| TEN-01 | 全仓无 tenant 概念 | 数据 | `grep tenant` | 无法做租户隔离 | S1：Tenant + Membership + tenant-scoped ORM |
| TEN-02 | 资源无 owner，存量归属不可推导 | 数据 | Assistant/DataSource/Skill/MCP/Connection 无 `created_by` | 无法回填，只能 bootstrap | S1：加 `tenant_id + owner_user_id`；存量全部归 bootstrap；不可确认的进 quarantine |
| TEN-03 | 三套授权来源无优先级 | 授权 | RBAC 概念 + `resource_grants` + `visibility` | 同一请求不同 API 结论不同 | S1：单一 `authorize()`；统一 permission code；显式 DENY 优先 |
| TEN-04 | 平台身份双源 | 授权 | `is_staff` 全 feature；`user_sees_all_assistants`；`/admin` | staff 绕过租户隔离 | S1：`platform_user_roles`；`is_staff` 只管 Django admin 入口；`is_superuser` 不参与业务授权 |
| TEN-05 | 查询作用域全靠手写 | 数据 | `sessions.py:183/648`、各 viewset `.all()` | 漏一处 = 跨租户越权 | S1：`TenantScopedManager` + 中间件/threadlocal；白名单才允许 `.all()` |
| TEN-06 | 唯一约束是全局的 | 数据 | `Assistant.slug`、`Role.name`、`auth.Group.name`、`EvaluationDataset` | 第二个租户无法复用命名 | S1/S3：改 `(tenant_id, slug)`；公开 URL 用 `public_id` |
| RUN-01 | 不同租户共享执行容器与 `/workspace` | 执行 | `runtime.py:326-352` | shell 可直接读其他租户文件/环境变量 | S4：ExecutionBroker + 每 Run gVisor；MVP 前禁止跨租户共享可写 workspace |
| RUN-02 | LensNode 单 token 全局 | 执行 | `lensnode_auth.py` | 节点被攻陷 = 所有租户暴露 | S4：节点身份 token + 短期 Run Capability；节点不能自报 tenant |
| RUN-03 | 凭据 symlink 进共享 session | 执行 | `session_datasources.py` | 凭据边界由 OS 权限决定 | S4：CredentialLease；禁止长期凭据挂载；任务后撤销 |
| STO-01 | 文件无 tenant 前缀/归属 | 数据 | `MEDIA_ROOT`、`RunOutputFile` | 跨租户下载/枚举/备份泄露 | S2：`StorageService` + `tenants/<id>/...` + 鉴权下载代理 |
| COM-01 | 缓存/WS 无租户命名空间 | 通信 | `lens:run-live:{uuid}`、`lens.lensnode.{uuid}` | 订阅/缓存串租 | S2：`lens:t:{tenant}:...`；group 加 tenant；敏感消息重新鉴权 |
| IDN-01 | 无租户切换、成员撤销不生效 | 身份 | 全局 JWT，无 membership | 被移除成员仍可访问 | S1：请求级 tenant + 在线 Membership 检查 + `auth_version` |
| PUB-01 | 匿名面租户上下文与滥用 | 公开 | `PublicAssistantView`、`SharedQA` | 匿名跨租户/被爬/刷成本 | S2：从资源反查 tenant；匿名 principal；四层限流；public 字段白名单 |
| EXT-01 | MCP/Plugin 可跨租户用凭据/访问内网 | 外部 | `Connection`、`CredentialLease`、`PluginInvocation` | SSRF、凭据混用、外部副作用 | S4：Egress Gateway + 每调用租户绑定 + DNS/内网校验 |
| OPS-01 | 后台任务无 tenant context | 运维 | `run_retention_task`、`dispatch_alerts_task` | 任务越权/串租 | S2：TenantJob，消息只传 `job_id`，worker 从 DB 读 tenant |
| MIG-01 | 蓝绿期间旧色无 tenant 上下文 | 迁移 | 单实例蓝绿 | 开 RLS 即全拒或留宽松后门 | S3：先双版本兼容，再排空旧色，最后单独启 RLS；不建 `USING (true)` |
| TST-01 | 没有跨租户测试/门禁 | 测试 | 现有 1539 测试无租户矩阵 | 回归无法发现越权 | S0 起：双租户 fixture + 12 项攻击矩阵，PR 必跑 |

### 4.2 P1

| ID | 风险 | 类型 | 证据 | 影响 | 缓解 |
|---|---|---|---|---|---|
| STO-02 | 符号链接/路径穿越/TOCTOU | 存储 | 本地 FS + symlink | 读写越界 | `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS)`；只接受 file_id；存储根不挂进执行容器 |
| COM-02 | 长连接撤销 | 通信 | WS 握手一次鉴权 | 成员移除后仍可操作 | 敏感消息逐条重鉴权 + `auth_version` |
| IDN-02 | AgentToken 无 tenant scope | 身份 | 用户级 token | 跨租户误用 | S1/S2：加 tenant scope + Membership 校验 + 撤销 |
| EXT-02 | 外部副作用不可逆 | 外部 | `delete_ticket` / `send_email` / CRM 更新 | 误操作、跨租户写入 | 细分 `mcp.execute/write_external`；审批 + 幂等键 + 审计 |
| EVL-01 | 评估快照与 LLM Judge 外发 | 数据 | `EvaluationDataset/Run`、judge | 租户数据进入全局模型/跨租户复用 | S3：全链路 tenant；`DataExportPolicy`；默认限制外发 |
| ALT-01 | 告警渠道/收件人跨租户 | 数据 | `AlertRule/Subscription/Delivery` | 通知发错租户 | 全链路 tenant 一致校验；worker 反查租户 |
| OPS-02 | GlobalSetting 无租户覆盖 | 运维 | 20+ 单例 key | 租户无法配置，或误关安全限制 | S3：PlatformSetting + TenantSetting；平台安全策略优先 |
| OPS-03 | 无 per-tenant 配额/公平调度 | 运维 | 全局并发/队列 | noisy neighbor | S3：`tenant_quotas` + `usage_events` + 加权公平队列 |
| OPS-04 | 备份/恢复/删除/legal hold 缺失 | 运维 | 共享 PG + 本地 FS | 单租户恢复不可行；删除不干净 | S3：隔离恢复库 + 导出任务 + 删除状态机 + legal hold |
| OPS-05 | 计量无 tenant | 运维 | `metadata.run_uuid` | 成本无法分摊 | S3：`run_usage_bindings`；不 fork 外部包 |
| MIG-02 | 大表回填/索引 | 迁移 | `RunTraceEvent` GB 级 | 锁表/长事务/复制延迟 | 分批 + `NOT VALID` + `VALIDATE` + `CONCURRENTLY` + 非原子迁移 |
| DSC-01 | 搜索/索引无租户 | 数据 | ripgrep 按 workspace | 搜索父目录 = 跨租户 | 搜索 root 由 RunSandbox 解析；三层过滤；索引文档带 tenant |
| DSC-02 | 跨租户共享/公开语义未定 | 产品 | `public` 助手/分享 | 公开面成为数据入口 | 明确 `private/tenant/public`；public 白名单；MVP 只支持同租户共享 |

### 4.3 P2

| ID | 风险 | 缓解 |
|---|---|---|
| RUN-04 | 运行产物/临时文件清理不彻底 | 状态机 + 幂等清理任务；备份保留期 |
| OPS-06 | 审计数据量/脱敏/保留期 | 字段级策略 + outbox + 月分区 + 归档 |
| OPS-07 | 平台 break-glass 无治理 | 理由 + 期限 + 目标租户 + 范围 + 审计；默认只读 |
| DSC-03 | 未来向量索引旁路 | 现在只定义 tenant-aware `search()` 接口；召回前过滤 |

---

## 5. 关键设计决策（ADR 草案）

> 建议逐条评审后落到 `docs/adr/`。以下为推荐默认值。

### ADR-001 一个用户只属于一个租户

- 决策：User 全局唯一；`TenantMembership(tenant_id, user_id, status, role, auth_version, revoked_at)`，**`UNIQUE(user_id)`** 强制一用户一租户。
- 理由：产品已确认不做跨租户成员；这样租户可以从 membership 直接推导，不需要请求头/租户切换，显著减少串租风险。
- 后果：
  - 所有 API 用 `request.user.membership.tenant` 解析租户；不存在"当前租户"二义性。
  - 不实现租户切换器；登录后直接进入所属租户。
  - 平台超级管理员通过 `platform_role` 区分，不属于普通租户 membership；需要管理租户内容时走代管会话。
  - 邀请已属于其他租户的邮箱时必须拒绝或走"迁移租户"人工流程（产品需明确）。

### ADR-002 单一授权入口 `authorize()`

- 决策：REST / Admin / Celery / WebSocket / MCP / Run 执行统一调用 `authorize(principal, tenant, action, resource, context)`，返回 `AuthorizationDecision`。
- 理由：避免 Authorization Drift（同一主体不同入口结论不同）。
- 后果：ViewSet、Serializer、ModelAdmin 中不允许再各写一套 `is_staff` / role 判断。

### ADR-003 默认拒绝 + 显式 DENY 优先

- 决策：`resource_grants` 增加 `effect=allow|deny`；同一主体/资源/权限，DENY 优先；用户直授与团队授权冲突时 DENY 优先；角色与资源授权冲突时 DENY 优先。
- 判定顺序：
  1. action 在权限目录中；
  2. 资源存在且未删除；
  3. tenant 可信且 active；
  4. principal 已认证（public read 例外）；
  5. Membership 有效（用户主体）；
  6. 代管 / API Key 状态有效；
  7. 命中任何适用的 DENY → 拒绝；
  8. 跨租户只允许 `public + 明确只读 action`；
  9. 角色权限或资源 GRANT → 允许；
  10. visibility 满足 → 允许；
  11. 否则拒绝。
- 后果：DENY 必须按主体 + 作用域匹配，不能把一个人对某个资源的拒绝扩展成全租户拒绝。

### ADR-004 三角色内置，权限矩阵写在代码里

- 决策：只保留三个角色：
  1. `member`（普通用户）
  2. `tenant_admin`（租户管理员）
  3. `super_admin`（平台超级管理员）
- 租户 membership 只存 `member | tenant_admin`；`super_admin` 是平台级 `platform_role`，不是租户角色。
- 不建 `roles / permissions / role_permissions / membership_roles` 表；权限矩阵用代码常量维护：
  - `member`：使用可见助手、管理自己的会话/分享；
  - `tenant_admin`：管理本租户助手、知识库、连接、环境变量、成员、组、API Key、通知、评估；
  - `super_admin`：平台控制台、租户生命周期、共享节点、官方目录、全局配置、平台审计；进入租户内容必须代管。
- 保留一套内部 permission code 作为代码常量（便于 `authorize()` 与未来扩展），但不落库、不做管理 UI。
- 后果：`visible_features` 只能作为 UI 投影，且由角色推导；不再作为授权来源。

### ADR-005 资源归属三元组

- 决策：`tenant_id`（安全边界）+ `owner_user_id`（业务所有者）+ `visibility`（访问可见性）分离。
- 不使用 `owner_type/owner_id` 作为所有资源的唯一归属模型（多态外键无引用完整性）。
- 后果：Assistant/DataSource/Session/Run 等必须显式写 tenant；`owner_user_id` 不能用来推导 tenant。

### ADR-006 可见性语义

| visibility | 匿名 | 本租户成员 | 其他租户成员 |
|---|---|---|---|
| private | 拒绝 | owner / grant 才可 | 拒绝 |
| tenant | 拒绝 | 允许 read | 拒绝 |
| public | 允许 read | 允许 read | 允许 read |

- public 只授予明确列出的读取字段；凭据、环境变量、MCP secrets、运行记录、数据源配置永不随 public 暴露。
- `is_system` 不等于 `public`。
- 公开 URL 用 `public_id`（不可预测）+ slug；slug 改为租户内唯一。

### ADR-007 不做全量 Resource Registry（MVP）

- 决策：保留单列主键；只对关键关联建 `UNIQUE(tenant_id, id)` + 复合 FK：
  `Run→Assistant`、`Run→DataSource`、`RunOutputFile→Run`、`RunTraceEvent→Run`、`MessageAttachment→Message`、`CredentialLease→Credential/Run`、`AssistantDataSourceBinding→Assistant/DataSource`。
- 其余多态引用用 `ResourceResolver`（服务层）+ 定期完整性检查。
- 理由：47 个模型全部改复合键成本高；Registry 增加事务协调与 ORM 适配。
- 后果：Django 5.1 无法用 ORM 表达复合 FK，需 `RunSQL` + `SeparateDatabaseAndState`；约束在 ORM 层不可见，需测试兜底。

### ADR-008 QuerySet + RLS 两层

- 决策：第一道是 `TenantScopedManager` / 显式 queryset 过滤；第二道是 RLS。
- RLS 前置条件：应用账号非表 owner、无 `BYPASSRLS`、`FORCE ROW LEVEL SECURITY`、每事务 `set_config('app.tenant_id', %s, true)`、migration 用独立 owner、Celery/admin/WS 全部有 context。
- 后果：RLS 不能替代 `authorize()`；`permissive` policy 默认 OR 组合，需 restrictive policy 兜底。

### ADR-009 执行隔离：ExecutionBroker + 每 Run gVisor

- 决策：Docker API 从 LensNode 移走，新增宿主机级、窄接口 `ExecutionBroker`（只接受 `run_id/resource_limits/workspace_ref`，不接受任意 Docker 参数）；每 Run 一个 gVisor（`runsc`）容器，独立 workspace、非 root、最小 capabilities、独立网络策略、短期 CredentialLease。
- 过渡：`独立 UID + workspace` 只能减险，不能宣称 Run 级隔离。
- 后果：Runner 容器不是长期服务；蓝绿切换要保证旧色不再处理不安全任务。

### ADR-010 平台身份

- 决策：`platform_roles` + `platform_user_roles`；`is_staff` 只控制 Django admin 入口；`is_superuser` 不参与业务 API 授权。
- 平台角色：`platform.security_admin / support_operator / billing_admin / audit_reader / super_admin / break_glass_operator`。
- 后果：不能把所有 `is_staff=True` 直接映射为 `platform.super_admin`；需逐个核实职责。

### ADR-011 API Key

- 决策：保留现有 `AgentToken`（用户级只读 QA，收敛并绑定 Membership）；新增 `TenantApiKey` 延后到 S2/S3。
- TenantApiKey 必须含：`tenant_id`、`key_prefix`、`key_hash`、`status`、`expires_at`、`revoked_at`、配额、IP allowlist、`key_version`。
- 未指定资源范围 ≠ 拥有租户全部资源；`credential.reveal` 不是默认权限。

### ADR-012 代管

- 决策：MVP 直接禁用代管入口；后续以 `tenant_delegations` 实现，默认只读，审批 + 范围 + 期限 + 通知 + 审计 + 即时撤销。
- JWT 绑定 `delegation_id + delegation_version + mode + scope`；每次请求校验当前状态与版本。

### ADR-013 审计

- 决策：字段级策略；凭据只记"是否变化"，环境变量只记变量名，密文不记原文/密文；
- `credential.reveal` 只记 actor/tenant/resource/action/reason/IP/request_id/结果；
- 高风险事件入 `security_audit_events`，普通拒绝聚合；outbox + 月分区 + 异步归档 + append-only。

### ADR-014 后台任务

- 决策：`TenantJob`；消息只传 `job_id`；worker 从 DB 读 tenant；无 tenant context 拒绝执行；按租户独立 Job，不把所有租户放一个长事务。
- `ScheduledTask` 混合全局任务与数据源同步：按 `task_type` 拆，`source_sync` 归租户，其余保持平台级。

### ADR-015 设置分层

- 决策：`PlatformSetting` + `TenantSetting`；优先级 `Platform Security Policy > Tenant Policy > User Preference > Default`；租户不能关闭隔离/审计/凭据保护。

### ADR-016 计量

- 决策：不 fork `agentcore_metering`；建 `run_usage_bindings(run_uuid, tenant_id, run_id)`，通过 `metadata.run_uuid` 关联；无法关联的进 quarantine；禁止用客户端提交的 `metadata.tenant_id` 定归属。

### ADR-017 迁移节奏

- 决策：4+1 发布（S0–S4），不是 8 个独立发布；`RunTraceEvent` 用分批 + `NOT VALID`；普通表小批量回填后直接 `NOT NULL`。
- RLS 是独立安全边界切换点，不混在普通 Django migration 中。

### ADR-018 本地存储

- 决策：MVP 保留本地 FS，统一 `StorageService`；布局 `tenants/<tenant>/runs/<run>/outputs/<file>.blob`；数据库存 opaque `storage_key`；Django 鉴权 + Nginx `X-Accel-Redirect`/`internal` 下载；`Cache-Control: private, no-store`。
- 预签名 URL 只适用于未来对象存储；本地 FS 用鉴权代理替代，不能声称"60 秒 URL 即时撤销"。
- 后果：需要 `checksum_sha256`、`storage_status`、删除状态机、DB+FS 一致性备份。

### ADR-019 缓存与 WebSocket

- 决策：缓存 key `lens:t:{tenant_id}:...`；Channels group `lens.t.{tenant_id}.node/run/user`；连接握手绑定 tenant，敏感消息逐条重鉴权；客户端不能提交 group name 或切换 tenant。

### ADR-020 公开面

- 决策：匿名请求从公开资源反查 tenant；`AnonymousPrincipal` 只有 `public_read`；若允许匿名 Run，使用独立 `public_run.execute` 能力并绑定资源/时长/次数/数据源/工具；四层限流（IP/资源/租户/分享 token）。

### ADR-021 搜索

- 决策：当前 ripgrep 搜索 root 必须由 RunSandbox 解析，禁止父目录；LLM rerank 输入必须是已授权候选集；未来索引文档强制带 `tenant_id/resource_type/resource_id/visibility/classification`；召回前过滤，返回前复核。

---

## 6. 统一授权模型

### 6.1 Principal 类型

```
UserPrincipal
ApiKeyPrincipal
PlatformPrincipal
DelegatedPrincipal
SystemJobPrincipal
AnonymousPrincipal
```

不要把 API Key、代管用户、平台用户混成普通 `User`。

### 6.2 权限判定伪代码

```python
def authorize(principal, tenant, action, resource, context):
    if not permission_registry.contains(action):
        return deny("unknown_action")
    if resource is None or resource.is_deleted:
        return deny("resource_unavailable")
    if tenant is None:
        return deny("tenant_required")

    if resource.tenant_id != tenant.id:
        if not (resource.visibility == "public" and action in PUBLIC_READ_ACTIONS):
            return deny("cross_tenant")

    if not principal.is_authenticated:
        if not (resource.visibility == "public" and action in PUBLIC_READ_ACTIONS):
            return deny("authentication_required")

    if tenant.status != "active":
        return deny("tenant_inactive")

    if principal.is_user and not principal.has_active_membership(tenant.id):
        return deny("membership_required")

    if principal.is_delegated and not principal.delegation_is_valid():
        return deny("delegation_invalid")

    if principal.is_api_key and not principal.key_is_valid():
        return deny("api_key_invalid")

    if any_matching_deny(principal, tenant, action, resource):
        return deny("explicit_deny")

    if has_role_permission(principal, tenant, action) or has_resource_grant(principal, tenant, action, resource):
        return allow("rbac_or_grant")

    if visibility_allows(resource, principal, action):
        return allow("visibility")

    return deny("no_matching_allow")
```

### 6.3 查询层约束

- `TenantScopedManager` 默认 `filter(tenant_id=current_tenant)`；跨租户查询必须显式 `unscoped()` 并只允许平台服务使用。
- 所有嵌套引用（datasource_uuid、assistant_uuid、file_uuid、model_ref）必须 `get_object_or_404(Model, tenant_id=tenant, uuid=uuid)`。
- `get_object_or_404` 与 `authorize` 都要做；不能只做其中一个。

---

## 7. 执行面隔离方案

### 7.1 拓扑比较

| 方案 | 租户间 | Run 间 | 复杂度 | 判断 |
|---|---|---|---|---|
| 每租户一套 LensNode 容器栈 | 较好 | 同租户 Run 仍共享 | 高 | 不作为主方案 |
| 每 Run 一个普通 Docker 容器 | 较好 | 较好（共享内核） | 中 | 可作为 MVP 基础 |
| 每 Run 一个 gVisor 容器 | 较好 | 强于普通容器 | 中高 | **优先推荐** |
| 每 Run 一个 Firecracker microVM | 很强 | 很强 | 高 | 后续增强 |
| 独立 VM 节点池 | 很强 | 取决于内部隔离 | 很高 | 暂不推荐 |
| 仅独立 UID + workspace | 有限 | 只能隔离部分文件/进程 | 低 | 仅过渡 |

### 7.2 推荐 MVP 拓扑

```
Django API / Celery
  └── 创建 Run、鉴权、签发短期 CredentialLease
        │
        ▼
ExecutionBroker（宿主机服务，唯一持有 Docker API）
  └── create_run(run_id, image_profile, resource_limits, workspace_ref)
  └── start_run / stop_run / destroy_run / get_run_status
        │
        ▼
每 Run 一个 gVisor 容器
  └── 非 root、最小 capabilities、独立 PID/mount/net namespace
  └── 只挂载本 Run workspace，禁止 host root / Docker socket / 其他 Run
  └── 仅短期 CredentialLease；任务结束撤销
  └── 默认限制 egress；外部访问走受控代理
```

长期 LensNode 只接收状态和结果，不持有 Docker socket，不承载租户 shell。

### 7.3 这个方案不防什么

- 宿主机 root / Docker daemon / Broker 被攻陷；
- gVisor、内核、runtime 的未修复漏洞；
- Broker 授权逻辑错误（把 B 的 workspace/credential 给 A）；
- 应用层把 A 的结果写入 B 可访问的 DB/缓存/对象存储；
- 平台管理员或备份系统的全局读取权限。

### 7.4 两周最小改造（若只能做一件事）

1. 第 1–2 天：盘点 workspace 创建、datasource symlink、节点 token、共享 volume、跨 Run 复用容器。
2. 第 3–5 天：每 Run 独立 workspace、独立文件系统挂载、独立 UID、禁止共享可写临时目录。
3. 第 6–8 天：凭据隔离，短期 CredentialLease，任务结束撤销。
4. 第 9–10 天：容器/网络隔离，gVisor、capabilities、禁 Docker socket、禁云 metadata、限制 egress。
5. 第 11–12 天：攻击测试（A 读 B workspace/datasource/env/产物/凭据；被攻陷节点横向）。
6. 第 13–14 天：蓝绿发布与回滚验证。

**硬性验收（六项全过）：**
- 任意两个租户的 Run 没有共享可写 workspace；
- 一个 Run 无法通过路径遍历/symlink/shell 读取其他 Run 文件；
- datasource 凭据只对授权 Run 暴露；
- Run 结束后凭据挂载不能被其他任务使用；
- 节点被攻陷时不能读取其他租户文件；
- 节点 token 不能单独获得任意租户的 Run 执行权限。

---

## 8. 存储、缓存与实时通信

### 8.1 本地存储布局

```
PRIVATE_STORAGE_ROOT/
  tenants/<tenant_uuid>/
    runs/<run_uuid>/outputs/<file_uuid>.blob
    messages/<attachment_uuid>.blob
    documents/<document_uuid>.blob
  staging/<upload_uuid>.part
  quarantine/
```

- 数据库保存 `tenant_id`、`storage_key`、`checksum_sha256`、`storage_status`。
- 客户端只传 `file_id`；不接受路径、bucket、storage_key。
- `MEDIA_ROOT` 不作为 Web 静态目录；存储根不挂进执行容器。
- 上传先写 staging，校验后原子 rename 到正式路径。

### 8.2 下载

```
客户端 → /api/files/{file_id}/download
  → Django 查文件记录（tenant/资源归属/status）
  → authorize(file.read)
  → X-Accel-Redirect: /_private_files/tenants/.../outputs/....blob
  → Nginx internal + sendfile 处理 Range/206
```

- `Cache-Control: private, no-store`；只有明确公开、不可变文件才允许长缓存。
- 预签名 URL 一旦签发是 bearer capability，不能声称即时撤销；本地 FS MVP 用鉴权代理。
- 防 symlink：`openat2(RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS)`；传统 `openat` 逐级 `O_NOFOLLOW`，不要 `realpath()` 后再打开（TOCTOU）。

### 8.3 删除与备份

- 状态机 `ACTIVE → DELETING → FILE_UNLINKED → DELETED`，Celery 幂等删除。
- DB 备份 + 文件系统快照必须有一致性清单（恢复点、快照 ID、文件数、校验和）。
- 删除后备份保留期要明确，避免"生产删了，备份永远保留"。

### 8.4 缓存与 WebSocket

- 缓存 key：`lens:t:{tenant_id}:run-live:{run_uuid}` 等。
- Group：`lens.t.{tenant_id}.node.{node_uuid}`、`lens.t.{tenant_id}.run.{run_uuid}`、`lens.t.{tenant_id}.user.{user_uuid}`。
- 连接握手绑定 tenant；`subscribe/unsubscribe/run.execute/run.cancel/run.read_output/credential.reveal/file.download` 逐条重鉴权。
- 客户端不能提交 group name 或切换 tenant。
- LensNode 消息中的 tenant 不可信，从 Run 记录反查。

---

## 9. 身份、公开面、外部集成、评估与搜索

### 9.1 身份与邀请

- User 全局唯一；`tenant_invitations` 邀请，`token_hash` 只存 hash，24–72 小时有效；同一邮箱已有 User 时绑定，不复制身份。
- 登录 JWT 只表示用户身份，不携带长期 tenant 授权；tenant 由请求头/路由选择，服务端每次校验 Membership。
- 多标签页各自保存 tenant，服务端不存唯一 `current_tenant_id`。
- 成员撤销：`status=revoked` + `auth_version += 1`；请求时比对 token 中的 `auth_version`。
- AgentToken 每次调用：验证 token → 未撤销 → User 有效 → 目标 tenant 的 Membership → 原路由白名单 → `authorize()`。

### 9.2 匿名公开面

- 匿名请求从资源反查 tenant：`assistant.public_id` + `visibility=public` + active → tenant。
- `AnonymousPrincipal` 只有 `public_read`；若开放匿名 Run，用独立 `public_run.execute` 能力，绑定助手/租户/配置/时长/次数/数据源/工具。
- 四层限流：IP、Public Assistant、Tenant、Share Token。
- `public_access_events` 记录审计，但不要长期存完整 IP/UA。
- `public assistant != public datasource != public credentials != public connections != public run history`。

### 9.3 MCP / Plugin / Connection

- 每次调用必须从服务端可信记录取得 `tenant_id / connection_id / credential_id / run_id / invocation_id`；Run 不能自报。
- `CredentialLease` 绑定 tenant、Run、invocation、credential、allowed_actions、expires_at；不能被其他 Run 重用。
- 关联链：Tenant → Run → PluginInvocation → Connection → Credential → CredentialLease。
- 统一 Egress Gateway；防 `127.0.0.1`、RFC1918、link-local、云 metadata、IPv6 本地、DNS rebinding、重定向到内网、解析/连接 IP 不一致。
- 外部写操作细分 `mcp.execute/write_external`；审批 + 幂等键 + 审计。

### 9.4 评估与告警

- `EvaluationDataset/Case/Run/Result` 全链路 tenant 一致；不允许跨租户 Dataset。
- LLM Judge 外发需 `DataExportPolicy`；`confidential` 需授权和脱敏，`restricted` 默认禁止。
- 平台可做聚合统计（Run 数、Token、延迟），默认不返回原始 Prompt/Response/Dataset 内容。
- AlertRule/Channel/Recipient/Delivery 全链路 tenant 一致；worker 从 DB 反查 tenant。

### 9.5 搜索

- 当前搜索 root 必须由 RunSandbox 解析，禁止父目录；结果再次校验归属。
- LLM rerank 输入必须是已授权候选集，不能"先全局召回、再过滤结果"。
- 未来索引文档强制带 `tenant_id/resource_type/resource_id/visibility/classification`；查询前、索引层、返回前三层过滤。

---

## 10. 模型归属分类与最小切片

### 10.1 分类口径

- **Tenant 级**：必须加 `tenant_id`（关键表再加复合 FK）。
- **Platform Catalog**：平台模板/定义，默认只读；租户实例另建模型或 overlay。
- **全局共享**：User/Profile 等身份信息。
- **无租户**：平台基础设施/全局配置。
- **按类型区分**：同一张表混合平台与租户语义时，先拆或按 `task_type` 区分。

### 10.2 分类表

| 模型 | 分类 | 处理要点 |
|---|---|---|
| LensNode | 无租户 | 平台执行基础设施；Run→节点分配记录保存 tenant/run |
| Assistant | Tenant | `tenant_id + owner_user_id + visibility`；`UNIQUE(tenant_id, id)` |
| AssistantAccess | Tenant | 加 tenant + principal 类型；复合约束到 Assistant |
| Skill | Platform Catalog | 平台定义只读；租户自定义另建 `TenantSkill` / overlay |
| EnvironmentVariableSet | Tenant | 含敏感值；租户归属 + 加密 |
| MCPServer | Platform Catalog | 定义留平台；租户 Connection 存 endpoint/auth/credential |
| DataSource | Tenant | `tenant_id + owner_user_id + visibility` |
| DataSourceDeployment | Tenant | 加 tenant；复合 FK 到 DataSource |
| DataSourceItem | Tenant | 加 tenant；复合 FK 到 DataSource |
| DataSourceVersion | Tenant | 不可变版本；加 tenant；复合 FK |
| DataSourceCredential | Tenant | 凭据归属租户；读取/使用分权；`credential.reveal` 独立权限 |
| SecretMaterial | Tenant | 绑定租户 + credential/connection；版本化 |
| SecretVersion | Tenant | 绑定租户；历史版本可解密；禁止跨租户引用 |
| Connection | Tenant | 租户连接实例；与平台 MCPServer 定义分离 |
| LegacyIntegrationMigration | Tenant | 引用 Connection/DataSource；加 tenant + 复合 FK；全局迁移定义另表 |
| ExecutionSnapshot | Tenant | 绑定 tenant + run；不可变；不复制明文 secret |
| CredentialLease | Tenant | 绑定 tenant/run/credential/invocation/expiry/revoke |
| PluginInvocation | Tenant | 加 tenant/run/connection/credential version/lease/tool/action/destination/幂等键 |
| AssistantSkill | Tenant | 加 tenant；复合 FK 到 Assistant；Skill 若平台目录则只引用 |
| AssistantMCP | Tenant | 加 tenant；不能直接授予平台 Server 的全局凭据 |
| AssistantDataSourceBinding | Tenant | 加 tenant；复合 FK 到 Assistant 与 DataSource（重点 IDOR 面） |
| AssistantPluginBinding | Tenant | 加 tenant；复合 FK 到 Assistant 与 Connection |
| Session | Tenant | 加 tenant；Message/Attachment/SessionDataSource 沿 Session 校验 |
| SessionCleanupOperation | Tenant | 创建时固化 tenant + session_id；执行时校验；幂等 |
| SessionDataSource | Tenant | 加 tenant；与 Session 同租户 |
| Message | Tenant | 沿 Session；查询必须带 tenant |
| MessageAttachment | Tenant | 沿 Message/Session；存储路径按 tenant |
| Run | Tenant | 核心执行资源；`tenant_id + created_by + assistant_id` |
| RunStep | Tenant | 沿 Run |
| RunTraceEvent | Tenant | 加 tenant；从 Run 反查；大表分批/分区 |
| RunExecution | Tenant | 加 tenant；绑定 Run/Node/workspace；记录生命周期 |
| RunDiagnosticEvidence | Tenant | 沿 Diagnostic；引用类型化/Resolver 校验同租户 |
| RunDiagnostic | Tenant | 沿 Run |
| RunDiagnosticTurn | Tenant | 沿 Diagnostic |
| RunTraceExport | Tenant | 导出任务/文件/下载凭证三生命周期；过期失效 |
| ScheduledTask | 混合 | `source_sync` 租户级；`lensnode_cleanup/health/run_retention` 平台级；按类型拆 |
| GlobalSetting | 无租户 | 平台全局设置；租户覆盖另建 `TenantSetting` |
| SharedQA | Tenant（默认） | 同租户共享；平台精选另建全局模型 |
| RunOutputFile | Tenant | `tenant_id + storage_key + checksum + storage_status`；绑定 Run |
| SharedQAFile | Tenant（默认） | 跟随 SharedQA；不能因 "Shared" 默认全局 |
| AlertRule | Tenant | 加 tenant；M2M assistants/datasources 必须同租户 |
| AlertEvent | Tenant | 沿 Rule |
| AlertSubscription | Tenant | 订阅人必须是该租户有效成员/明确主体 |
| AlertOutbox | Tenant | 写入固化 tenant/event/recipient；发送前校验 |
| AlertDiagnosis | Tenant | 输入/输出可能含敏感数据 |
| AlertDelivery | Tenant | 渠道/收件人/租户一致 |
| EvaluationDataset | Tenant | 快照含租户数据；不可跨租户复用 |
| EvaluationCase | Tenant | 沿 Dataset |
| EvaluationMetric | 平台目录 + 可选 Tenant | 标准指标平台；自定义指标租户 |
| EvaluationRun | Tenant | 需 `data_export_policy` |
| EvaluationResult | Tenant | 沿 EvaluationRun |
| AgentToken | Tenant | 加 tenant scope/subject/scope/expiry/auth_version/revoked_at |
| Role | 内置枚举（无租户） | 不建角色表；membership 存 `member/tenant_admin`，user 存 `platform_role=super_admin` |
| Profile | 全局共享 | 不复制；用 Membership 关联多租户 |
| 外部包 `LLMConfig` | 平台目录 | 模型/provider 配置；租户不可见 provider key |
| 外部包 `LLMUsage` | Tenant 映射 | 通过 `run_usage_bindings` 关联；不直接加列 |
| 外部包 task / notifier | 映射 | 本地映射表 + 受控删除接口 |

### 10.3 不能只加 `tenant_id` 的模型

| 模型/类型 | 为什么不够 | 处理 |
|---|---|---|
| LensNode | 平台节点可承载多个租户 | 不加租户；RunExecution 记录 tenant/run |
| Skill / MCPServer / Role | 模板与实例生命周期不同 | 平台定义只读 + 租户实例/overlay |
| Profile | 全局身份 | 不加 tenant；Membership 关联 |
| AssistantAccess | 授权主体多态、依赖 Assistant | 明确 principal 类型 + 复合约束 |
| 四类 Assistant 绑定表 | 容易把 A 的 Assistant 绑到 B 的资源 | 加 tenant + 双方复合 FK |
| DataSource 子表 | 仅有 tenant 不能阻止错绑 | 复合 FK 到 DataSource |
| SecretMaterial / SecretVersion | 引用范围与密钥版本 | 以 Credential/Connection 为入口；版本化；禁止跨租户 |
| ExecutionSnapshot | 快照不可漂移 | 绑 tenant+run；记录版本/哈希；不复制明文 secret |
| CredentialLease | 有期限/范围的凭据 | 绑 tenant/run/credential/invocation；使用前复核 |
| PluginInvocation | 外部副作用 | 完整关联链 + 幂等键 + 目标地址 |
| LegacyIntegrationMigration | 全局定义 + 每租户映射 | 拆全局版本与 TenantMigrationRecord |
| SessionCleanupOperation | 异步任务可能在父对象删除后执行 | 创建时固化 tenant + session_id |
| RunTraceEvent | 大表/异步写入/分区 | 每行 tenant + run；分区键不破坏约束 |
| RunDiagnosticEvidence | 多态引用 | 类型化 FK 或 Resolver + 同租户校验 |
| RunTraceExport | 任务/文件/下载凭证 | 三个生命周期分别建模 |
| AlertOutbox | 异步发送 | 固化 tenant/event/recipient；消费前复核 |
| Evaluation* | 快照与外发 | 全链路 tenant + 数据外发策略 |
| AgentToken | 能力凭据 | tenant/scope/expiry/revoke/auth_version |
| 外部包表 | Django 不管外部表 | 本地映射 + 受控读写入口 |

### 10.4 首批最小改动（10 张表）

| 顺序 | 模型 | 最小改动 | 验收 |
|---|---|---|---|
| 1 | Assistant | `tenant_id/owner_user_id/visibility`；`UNIQUE(tenant_id,id)` | A 不能读写 B 的 Assistant |
| 2 | DataSource | 同上 | CRUD 与引用按租户隔离 |
| 3 | DataSourceCredential | `tenant_id`；与 DataSource 同租户；明文只在授权 reveal/use 解密 | A 不能列/读/用 B 凭据 |
| 4 | Run | `tenant_id`；Assistant/DataSource 绑定；复合 FK | A 不能执行 B 的助手/数据源 |
| 5 | RunExecution | `tenant_id`；绑定 Run/Node/workspace | 节点不能伪造 Run ID 取得执行权 |
| 6 | RunOutputFile | `tenant_id/storage_key/checksum/storage_status`；绑 Run | A 不能下载 B 文件；不能路径越权 |
| 7 | CredentialLease | 补齐 tenant/run/credential/invocation/expiry/revoke | Run 只能取自己的短期凭据 |
| 8 | RunTraceEvent | `tenant_id`；写入从 Run 反查 | trace/导出/实时推送不跨租户 |
| 9 | AssistantAccess | `tenant` + principal；复合约束 | 用户/组/共享不能绕过租户 |
| 10 | Session | `tenant_id`；Message/Attachment 沿 Session 校验 | 会话/消息/附件不跨租户 |

> 若必须压缩到 8 张，先保留 1–8；第 9、10 在对应功能开放前必须完成，但不能让现有 API 继续走未授权入口。

### 10.5 预留作用域缝隙（MVP 不做，但字段/接口要留）

| 未来能力 | 现在必须预留 |
|---|---|
| BYOK | `encryption_key_id` / `tenant_encryption_profiles` |
| 代管 | `delegation_id` 可空；入口默认关闭 |
| API Key 轮换 | `key_version` |
| 审计 | `request_id/tenant_id/actor_id` |
| 搜索索引 | `resource_type/resource_id/tenant_id` |
| 对象存储 | tenant 前缀 + `StorageService` |
| LLM 计量 | `run_uuid → tenant` 映射 |
| 评估 | `evaluation_run.tenant_id` |
| 通知 | `notification.tenant_id` |
| 执行节点 | `pool_id/tenant_id` 或明确 platform scope |

> 预留字段不等于允许长期为空；未启用的能力必须有默认拒绝规则。

### 10.6 可以明确延后

- 完整 BYOK / 每租户 KMS；
- 自动单租户 PITR 恢复（先做隔离恢复库 + 人工导出导入）；
- 多区域部署；
- 每租户物理执行节点；
- 完整代管审批工作流（MVP 直接禁用代管）；
- 向量搜索；
- 跨租户评估分析；
- 高级加权公平调度；
- 自动化租户导出门户；
- 完整审计数据湖。

### 10.7 绝不能延后

- 跨租户 DB 查询；
- 凭据越权读取；
- 公共接口暴露私有资源；
- 共享 workspace；
- 对象文件跨租户下载；
- WebSocket 跨租户订阅；
- Membership 撤销后的访问；
- MCP 跨租户凭据使用；
- 无 tenant context 的后台任务；
- 无租户策略的数据外发。

> MVP 可以不支持某项能力，但不能"先支持、再靠开发人员记住不要越权"。不支持代管就关闭入口；不支持跨租户共享就只做同租户共享；不支持匿名 Run 就只做匿名只读。

---

## 11. 威胁模型与 CI 门禁

### 11.1 攻击者矩阵

| 攻击者 | 入口 | 潜在影响 | 核心控制 |
|---|---|---|---|
| 跨租户普通成员 | REST、文件、搜索、WS | 读写其他租户资源 | Membership + authorize + RLS |
| 租户管理员 | Admin API、邀请、共享、配置 | 扩大授权/访问其他租户 | tenant-scoped 管理权限；不能自授平台角色 |
| 被攻陷 LensNode | Shell、workspace、节点凭据、网络 | 读取其他 Run 文件/凭据 | Sandbox、Run Capability、Egress |
| 被盗 API Key | API、MCP、QA 路由 | 使用 Key 权限或产生外部副作用 | Scope、撤销、配额、IP |
| 恶意公开用户 | Public API、分享链接、匿名 Run | 爬取、滥用配额、探测资源 | Public Scope、Rate Limit、匿名配额 |
| 平台运维 | Django admin、DB、队列、备份 | 跨租户读取敏感数据 | 最小权限、审批、审计、break-glass |

### 11.2 租户管理员边界

可做：邀请、改本租户角色、共享本租户资源、配置本租户通知/连接。
不可做：自授平台角色、改其他租户 Membership、访问其他租户资源、关闭全局安全限制、修改平台执行节点、使用平台级 DB 权限。

### 11.3 CI 必跑矩阵（12 项）

1. A 使用 B 的 Assistant/DataSource/Run ID；
2. A 调用 B 的 credential reveal；
3. 匿名访问 B 的 private/tenant Assistant；
4. A 下载 B 的附件/RunOutputFile；
5. A 命中 B 的实时状态缓存；
6. A 订阅 B 的 Run group；
7. A 的 Celery Job 执行 B 的任务；
8. 非 owner DB 账号跨租户 SELECT/UPDATE/DELETE；
9. A 的 Run 使用 B 的 CredentialLease；
10. MCP endpoint 访问内网/云 metadata（SSRF）；
11. Membership/API Key/Delegation 撤销立即生效；
12. Django admin 不能跨租户读取/批量修改。

### 11.4 门禁设计

| 门禁 | 触发 | 失败后果 |
|---|---|---|
| Security Unit Tests | 每次 PR | 禁止合并 |
| PostgreSQL Isolation Tests | 每次 PR | 禁止合并 |
| Full Tenant Attack Suite | 发布前 | 禁止上线 |

RLS 测试必须使用非 owner、无 `BYPASSRLS` 的 `sourcelens_app` 账号，并断言 `relrowsecurity=true`、`relforcerowsecurity=true`；SQLite 测试不能替代。

Celery 测试用真实 worker + PostgreSQL；WebSocket 测试用真实 Channels communicator；对象存储测试用本地 S3 兼容服务或独立测试存储。

---

## 12. 分阶段实施计划与验收清单

### S0 紧急止血（1–2 周）

**工作**
- 修 `DataSourceCredentialViewSet`：至少要求 `admin_console`（与 `admin/datasources` 对齐）；`reveal` 独立权限 + 审计；list/retrieve 不返回明文。
- 就 `PublicAssistantView` 的直链预览做产品决策：
  - 若保留：记录为“有意跨租户元数据面”，在 S2 加租户/登录限制；
  - 若收紧：S0 加 `visibility=public` + 字段白名单 + 统一 404，并更新现有测试的语义。
- 禁止 `is_staff` 作为业务数据访问授权条件（至少覆盖凭据、管理列表）。
- 建立双租户 fixture 与 12 项攻击矩阵，进 PR 必跑。

**验收**
- 用户 A 列出/ reveal 用户 B 的凭据 → 403；
- 非 `admin_console` 用户访问凭据接口 → 403；
- （若收紧预览）匿名访问 private/tenant 助手 → 404；
- 匿名访问 public 助手 → 只读白名单；
- public 助手修改仍需 manage；
- 无 Membership 的 staff 访问租户私有资源 → 拒绝。

### S1 租户内核 + 三条链

**表**
`tenants`、`tenant_memberships`、`tenant_invitations`、`tenant_api_keys`（可后半段启用）、`credential_leases`、`tenant_jobs`。

> 迁移编号：`0069_evaluation_revisions_and_reliability` 已随 #803 合入；S1 新 migration 从 `0070` 开始，不要复用 0069。

> 已确认三角色内置、一用户一租户：不建 `tenant_roles / permissions / role_permissions / resource_grants`。租户角色用 `membership.role` 枚举；平台超级管理员用 `Profile.platform_role`（或独立字段）标识；`AssistantAccess` 继续承担用户/组对助手的可见性授权。

**字段**
Assistant / DataSource / DataSourceCredential / Run / RunExecution / RunOutputFile / CredentialLease / RunTraceEvent / AssistantAccess / Session。

**代码**
- 统一 `authorize()`；`TenantScopedManager`；请求中间件从 `request.user.membership` 解析 tenant。
- JWT 不需要租户 claim；每次请求校验 membership 状态；`auth_version` 用于撤销。
- 角色 → 权限矩阵用代码常量；`visible_features` 由角色推导，仅作 UI 投影。
- 删除 `user_sees_all_assistants()` 的 `is_staff` / `admin_console` 全局旁路，改为租户内角色判断。

**验收**
- 一个用户有且只有一个 active membership；
- 登录后自动进入所属租户；没有租户切换入口；
- 撤销 Membership 后旧 JWT 立即失效（后续请求 403/401）；
- 平台 `super_admin` 不进入普通租户 membership；管理租户内容必须代管；
- A 不能读写 B 的 Assistant/DataSource/Run。

### S2 存储、通信、DB 防线

**工作**
- `StorageService` + tenant 路径 + Nginx internal 下载；删除状态机；DB+FS 一致备份。
- 缓存/WS 租户命名空间；敏感消息重鉴权。
- Celery tenant context + TenantJob。
- 关键复合 FK（Run/Assistant/DataSource/File/Lease/Binding）。
- RLS 先覆盖核心租户表；准备三个 DB 角色（legacy/app/migrator）。

**验收**
- A 不能下载 B 文件（含猜 storage_key、改 URL、过期 URL）；
- A 不能订阅 B group；撤销后旧连接敏感操作失败；
- Celery A/B/A 连续任务不串租；无 tenant context 拒绝执行；
- 非 owner 账号跨租户 SQL 返回 0 行或拒绝。

### S3 切换与收敛

**工作**
- 完成回填；`tenant_id NOT NULL`；`UNIQUE(tenant_id, id)`；删除旧 feature 授权旁路。
- `PlatformSetting` + `TenantSetting`；`tenant_quotas` + `usage_events`；`run_usage_bindings`。
- 评估/告警全链路 tenant；租户导出/删除/legal hold。
- 蓝绿：旧色排空 → 禁用 legacy DB 角色 → 单独启用 FORCE RLS。

**验收**
- `tenant_id IS NULL` 为 0（租户级表）；
- 每资源可追溯归属证据；无法确认的进 quarantine；无默认租户兜底；
- retention/sync/alert 不跨租户；
- RLS 后不能回滚到非 RLS-aware 旧二进制。

### S4 执行与外部集成隔离

**工作**
- ExecutionBroker（唯一 Docker API 持有者）；每 Run gVisor 容器；独立 workspace/UID/网络/资源限制。
- CredentialLease 强制；禁止长期凭据挂载。
- MCP Egress Gateway；DNS/内网/metadata 校验；外部副作用审批 + 幂等 + 审计。

**验收**
- §7.4 六项硬性验收全过；
- A 的 Run 不能使用 B 的 CredentialLease；
- 被撤销凭据不能发起新 Invocation；
- 每条 Invocation 可追溯到 tenant/run/connection/credential version/tool。

---

## 13. 待决策问题

1. **产品目标**：对外 SaaS 多租户，还是每客户一套私有化部署？
   - 若是后者，部署级隔离（每客户一套 compose/DB/存储）成本远低于本文 schema 改造。
   - 注：2026-11-06 对自优化中心的范围决策是"保留多租户可能但当前不实现，只留作用域缝隙"；本次是否正式推翻该决策？
2. **执行拓扑**：接受 `ExecutionBroker + 每 Run gVisor` 吗？宿主机是否允许安装 `runsc`？还是要先做"独立 UID + workspace"过渡？
3. **存储**：MVP 继续本地 FS + 鉴权代理，还是引入 MinIO/S3？若继续本地 FS，谁负责存储根权限与备份一致性？
4. **直链预览**：是否保留"私有助手直链登录前预览"？保留则接受跨租户元数据面并加限制；不保留则 S0 收紧为 `visibility=public`。
5. **功能取舍**：原型把“代管”做成核心动作。MVP 是否保留代管？若保留，至少补“原因 + 期限 + 范围 + 审计会话 ID”；跨租户共享、匿名 Run、完整 TenantApiKey 是否先禁用？
6. **平台角色**：现有 `is_staff` 用户分别对应哪个 platform role？谁承担 `break_glass_operator`？
7. **数据合规**：LLM Judge / 外部模型外发策略、审计保留期、legal hold 责任人是谁？
8. **计量**：是否推动 `agentcore_metering` 增加 tenant 维度，还是先用 `run_usage_bindings` 映射？
9. **时间与人力**：S0–S4 哪一段进入本季度？2–3 人团队是否需要外部安全评审？
10. ~~**多租户成员**~~：**已确认一个用户只能属于一个租户**，不实现租户切换；租户从 membership 推导。
11. **节点私有性**：`LensNode` 是否允许租户私有？原型内部矛盾（节点页说“没有租户私有节点”，创建租户向导说“可让租户自己注册私有节点”）。这决定 `LensNode.tenant_id` 与执行隔离方案。
12. **匿名公开**：public 助手是否允许匿名发起 Run？原型文案说“任何人免登录就能直接使用”，当前代码只支持元数据预览。
13. ~~**角色模型**~~：**已确认三角色内置**（普通用户 / 租户管理员 / 超级管理员），不建可配置 RBAC 表。

---

## 14. 附录

### 14.1 现有代码关键路径（便于实施定位）

- 身份：`backend/accounts/models.py`、`backend/accounts/access.py`、`backend/accounts/authentication.py`
- 权限类：`backend/accounts/permissions.py`、`backend/lens/views/base.py`
- 助手：`backend/lens/models.py`（`Assistant`/`AssistantAccess`）、`backend/lens/views/assistants.py`
- 会话/Run：`backend/lens/views/sessions.py`、`backend/lens/models.py`
- 凭据：`backend/lens/views/credentials.py`
- 管理端 Run：`backend/lens/views/admin_runs.py`
- 设置：`backend/lens/views/global_settings.py`、`backend/lens/models.py`（`GlobalSetting`）
- 计量：`backend/lens/alerts.py`、`backend/lens/run_diagnostics.py`
- 执行：`lensnode/lensnode/agent_runtime/runtime.py`、`lensnode/lensnode/session_workspace.py`、`lensnode/lensnode/session_datasources.py`
- 路由：`backend/lens/urls.py`、`backend/core/urls.py`
- 部署：`docker-compose*.yml`、`scripts/install.sh`、`AGENTS.md`

### 14.2 术语表

- **PDP**：Policy Decision Point，统一授权判定点。
- **BOLA/IDOR**：Broken Object Level Authorization / Insecure Direct Object Reference，越权访问对象。
- **RLS**：PostgreSQL Row Level Security。
- **DEK/KEK**：Data Encryption Key / Key Encryption Key。
- **Break-glass**：紧急访问流程，必须有理由、期限、范围和审计。
- **Legal hold**：法务保留，优先于普通 retention。

### 14.3 本文未覆盖

- 具体 Django migration 文件拆分与表名映射；
- 前端租户切换 UI 与路由守卫改造；
- nginx/ingress 级限流与 WAF 规则；
- 生产数据回填脚本与校验 SQL；
- 安全事件响应流程（IR）；
- 组织与权限治理流程（谁审批代管、谁审批导出）。

> 这些应在 S0/S1 立项时补齐，不应阻塞本文的架构决策。
