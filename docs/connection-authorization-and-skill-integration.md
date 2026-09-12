# SourceLens 连接、授权与 Skill 集成设计讨论

> 状态：设计讨论，非最终实施规范
> 日期：2026-07-20
> 范围：DataSource、Skill、MCP 使用的外部连接与凭据

## 1. 文档目的

本文档是对本次需求讨论的结构化整理，不是逐句聊天记录。
它记录：

- 当前 Skill 上传与运行机制。
- 当前授权管理与 DataSource 的耦合。
- Connection、Credential、Provider、Connector 和 Agent Tool 的边界。
- DataSource、Skill 和 MCP 的拟议接入方式。
- Skill 自描述协议的草案。
- 对讨论方案的反方审查与逻辑漏洞。
- 经过收缩后的 MVP 建议与待决策项。

## 2. 执行摘要

已确认的问题是：现有 `DataSourceCredential` 虽然是独立表，
但创建入口、字段语义、验证流程和使用场景仍属于 DataSource
领域。它还不是 Skill、MCP 和多个 DataSource 可共用的平台级
授权资产。

本次讨论的稳定方向是：

1. DataSource 只保存资源路径、目标路径和同步策略等业务配置。
2. 外部授权从 DataSource 中解耦，作为独立保存的连接资产。
3. Skill 通过机器可读 Manifest 声明所需参数、Provider、Capability
   和可接受的认证交付方式。
4. DataSource、Skill 和 MCP 可复用同一套连接管理、权限、审计和
   运行时解析能力，但不假设它们具有完全对称的执行模型。
5. 原始密码、PAT 和 App Secret 不应进入 Skill、MCP、DataSource
   配置或持久化的 Run 快照。

反方审查后的重要修正是：第一版不应先建立可任意跨连接重组的
`CredentialSet` 市场。更安全的默认复用单元应是绑定了
Provider、Endpoint/Audience 和 Credential 的 `SavedConnection`。

## 3. 当前实现

### 3.1 Skill 上传

Skill ZIP 通过 `SkillViewSet.upload` 进入 `import_skill_zip()`。当前会：

- 校验 ZIP 格式、路径、文件数量和大小。
- 查找包含 `SKILL.md` 的唯一根目录。
- 从简单 YAML frontmatter 中读取 `name` 和 `description`。
- 计算 ZIP SHA-256，生成版本和文件清单。
- 保存到 `STORAGE_ROOT/lens/skills/<slug>/<hash>/`。
- 创建或更新全局 `Skill` 记录，并设为启用。

当前的 `package_manifest` 仅是包内文件清单，不是参数、连接或
Capability 声明。

### 3.2 Assistant 绑定与 Run 快照

上传不会自动执行 Skill，也不会自动绑定 Assistant。管理员需通过
`AssistantSkill` 建立绑定。

用户创建 Run 时，后端将启用的 Skill 快照写入
`RunExecution.loaded_skills`，并通过 `run_start` 命令下发 LensNode。

因此 `loaded_skills` 和 `loaded_mcps` 只能保存非敏感的版本、Manifest
和 Connection 引用，不能保存解密后的 Header、Environment 或
SDK 参数。

### 3.3 LensNode 运行

LensNode 按 Skill UUID 和内容 Hash 缓存包，然后复制到本次 Run 目录。
`general_chat` 任务会把 Skill 内容放入 Agent 上下文，并提供通用
`run_skill_script` Tool。

当前子进程没有显式传入隔离后的 `env`，会继承 LensNode 进程
环境。这是增加 Skill Credential 注入前必须先解决的安全问题。

## 4. 需求演进

讨论从“Skill 需要环境变量”逐步扩展为以下需求：

1. 上传 Skill 后能识别它需要的普通参数和外部授权。
2. 管理员配置 Assistant/Skill 时能看到自动生成的表单。
3. 用户运行 Skill 时能选择自己的已保存连接。
4. GitHub、飞书和数据库授权不再属于某一个 DataSource。
5. DataSource 配置时只填资源路径和同步规则，并选择已保存连接。
6. MCP 后续可复用同样的连接管理，不再把 Token 直接写入
   `MCPServer.config`。

## 5. 术语与责任边界

### 5.1 ConnectionProvider

描述和实现“如何建立连接”：

- Endpoint 和非敏感连接配置 Schema。
- 接受的 Credential 形态。
- 连通性和认证验证。
- 可用 Capability 和资源发现能力。
- 运行时认证上下文生成。

示例：`github`、`feishu`、`postgres`、`mysql`。

### 5.2 SavedConnection

管理员或用户创建的已配置连接：

```text
Provider + Endpoint/Audience + Non-secret Config
+ Encrypted Credential Version + Owner/Grant
```

第一版建议复用整个 `SavedConnection`，不默认允许任意拆出
Credential 并绑定到新 Endpoint。

### 5.3 DataSourceConnector

定义“连接后读取什么、怎么同步”：

- `dataSourceSchema`。
- 资源路径选择器。
- 业务配置校验。
- 全量或增量同步。

示例：`git_repository`、`feishu_documents`、`postgres_table`。

### 5.4 Agent Tool

给 LLM 暴露的最小化受控操作。它可复用 ConnectionProvider 生成的
认证上下文和底层 Client，但不等同于 DataSourceConnector。

示例：`feishu_read_document`、`github_get_file`。

### 5.5 环境变量

环境变量是运行时交付通道，不是认证类型。

```text
App ID / App Secret  -> Credential shape
Access Token         -> Runtime credential
Environment variable -> Delivery mechanism
```

## 6. 拓扑图

![SourceLens 连接、授权与扩展消费者拓扑](./connection-authorization-topology.svg)

相关文件：

- [SVG 拓扑图](./connection-authorization-topology.svg)

该图是讨论阶段的完整拓扑，包含未经收缩的通用
`CredentialSet`、Provider Registry 和多种 Materialization。经过反方审查后，
这些不应全部进入第一期实施。

## 7. DataSource 配置流程

```text
选择 DataSourceConnector
        |
        v
选择兼容的 SavedConnection
        |
        +-- 若不存在，通过 ConnectionProvider Schema 创建
        |
        v
按 DataSourceConnector.dataSourceSchema 填写资源配置
        |
        v
配置同步策略并校验目标资源访问权限
```

表单责任划分：

| 表单部分 | 决定者 | 示例 |
|---|---|---|
| 连接配置 | `ConnectionProvider.connectionSchema` | Endpoint、Region、TLS |
| 认证字段 | Provider 接受的 Credential Schema | Token、App ID/Secret |
| 数据源配置 | `DataSourceConnector.dataSourceSchema` | Repo、Folder、Table |
| 复杂选择器 | Connector UI Schema/Widget | Repo Picker、Folder Tree |

Connection 不决定 DataSource 表单结构，但它可以为字段提供动态选项：

- GitHub Connection 提供 Repository 和 Branch 列表。
- Feishu Connection 提供 Space、Folder 和 Document 树。
- PostgreSQL Connection 提供 Schema、Table 和 Column 列表。

## 8. Skill 自描述协议

建议保留 `SKILL.md` 作为人和 Agent 可读说明，新增独立的
`skill.manifest.json` 作为平台机器协议。

示例：

```json
{
  "schemaVersion": "1.0",
  "parameters": {
    "type": "object",
    "properties": {
      "folderToken": {
        "type": "string",
        "title": "Feishu Folder",
        "x-resolution": "run"
      }
    },
    "required": ["folderToken"]
  },
  "connections": [
    {
      "key": "feishu",
      "provider": "feishu",
      "capabilities": ["documents:read"],
      "required": true,
      "resolution": "binding_or_user",
      "authentication": {
        "acceptedModes": ["agent_tools", "access_token"]
      }
    }
  ]
}
```

Manifest 只描述需求，不保存任何真实秘密。

### 8.1 参数解析范围

- `binding`：管理员配置 Assistant/Skill 时固定。
- `user`：不同用户保存自己的选择。
- `run`：每次运行输入。
- `binding_or_user`：用户配置优先，否则使用共享绑定。

真实敏感值不允许作为普通 Run 参数覆盖。

### 8.2 Skill 更新

Skill 更新如新增必填参数、Connection requirement 或扩大 Capability，
现有绑定应进入 `NEEDS_RECONFIGURATION` 或重新审批，不能因为 slug
未变而自动继承旧授权。

## 9. Skill 如何使用飞书授权

Skill 不直接读取 `FeishuDocumentsConnector` 的认证数据。Skill 声明需要
一条具有 `documents:read` Capability 的 Feishu SavedConnection。

首选方式是 Connection-bound Agent Tool：

```text
Skill Instructions
        |
        v
Agent 调用 feishu_read_document
        |
        v
Tool 使用 Run 已绑定的 Feishu SavedConnection
        |
        v
Provider 使用 App ID/Secret 换取短时 Access Token
        |
        v
Tool 返回业务数据，Agent 不接触任何秘密
```

如 Skill 必须执行自己的脚本，认证模式需协商：

```text
Provider 支持的模式
INTERSECT 平台安全策略允许的模式
INTERSECT Skill 可接受的模式
= 最终认证交付方式
```

如 Skill 只能接受 App ID/App Secret，而平台只允许 Access Token，则两者
不兼容。必须改造 Skill、增加受控包装 Tool，或把 Skill 明确升级为
需额外审批的特权 Skill。

## 10. Integration 的代码组织

飞书等平台的物理代码可放在同一个集成目录，但逻辑接口分开：

```text
integrations/feishu/
|-- connection.py    # ConnectionProvider
|-- client.py        # 底层 Feishu API Client
|-- datasource.py    # DataSourceConnector
`-- tools.py         # 可选 Agent Tools
```

当前建议将 GitHub、飞书和第一个数据库 Provider 作为 SourceLens
内置能力随项目原子发布，但通过稳定接口注册。暂不将 Provider
拆成独立项目或允许 Skill 动态安装 Provider 代码。

## 11. 反方审查：逻辑漏洞与认知偏差

### 11.1 阻断级问题

#### 授权主体未定义

必须区分交互用户、共享 Assistant、DataSource 定时任务、Celery
服务身份、LensNode 和公开入口。还需定义授权在绑定、入队、派发、
续租和重试的哪些时点检查。

#### “短时 Secret Lease”容易产生虚假安全感

租约过期只能终止再次取密，无法撤回已经到达 LensNode 的 PAT、Password
或 App Secret。静态秘密如果不允许被提取，只能经受控代理 Tool 使用。

#### 持久化 Run 快照与秘密注入冲突

`RunExecution.loaded_skills` 和 `loaded_mcps` 当前是 JSON 持久化快照。
任何解密后的 Environment、Header 或 SDK 参数都不得进入这两个字段或
WebSocket command。

#### LensNode 还不具备安全脚本注入边界

当前 `run_skill_script` 继承 LensNode 环境。修改全局 `os.environ` 会导致
并发 Run 串密。子进程必须从最小白名单构造独立 `env`，且这仍
不能防止恶意 Skill 主动外传已授予它的秘密。

#### Skill 不是默认可信执行主体

上传包可执行任意 Python/Shell。获得明文 Token 后可以打印、编码、写入
deliverable 或上传。日志脱敏不是安全边界。

### 11.2 高风险问题

- 任意重组 Credential 和可变 Endpoint 可造成凭据转移攻击。
- 任意 Endpoint 可导致 SSRF、DNS Rebinding 和向攻击者转发秘密。
- Provider/credential type 匹配不代表能访问目标 Repo、Tenant 或 Schema。
- 共享 Assistant 绑定共享 Connection 可放大数据越权和 API 成本风险。
- Credential 轮换、撤权、重试、断线重连和重复派发的并发语义尚未定义。
- Provider 的执行位置不能被通用 Resolver 隐藏：某些网络仅 LensNode
  可达，但下发长期秘密又会扩大信任边界。

### 11.3 模型与迁移问题

- Skill 普通参数的 Schema、默认值、作用域和 Run 快照规则必须单独建模。
- `Skill.package_manifest` 已表示文件清单，Capability Manifest 必须使用独立名称和版本。
- Skill 更新现在保留 Assistant 绑定，扩大权限时必须使旧批准失效。
- `DataSourceCredential.scope_config` 中的资源语义需拆回 DataSource 或绑定层，
  迁移不是简单替换外键。
- 现有 Git 凭据可能随 clone URL 进入 `.git/config`，迁移时需清理并改用
  受控 credential helper/askpass。
- 数据库授权涉及 SSL 证书、SSH Tunnel、IAM Token 和连接池等，不应
  在第一期用“通用 JSON”假设已经覆盖。

### 11.4 认知偏差

- **复用偏差**：看到字段可复用，就默认应该拆成可自由重组的实体。
- **抽象优先**：在授权主体和执行边界未定义前，先设计通用 Registry。
- **对称性偏差**：假设 DataSource、Skill 和 MCP 适用同一种绑定与注入模式。
- **命名实体化**：因为称为 Connection，就倾向让它承担完整连接、验证和 Tool 能力。
- **Happy Path 偏差**：主要讨论管理员正常配置，但忽略恶意 Skill、撤权和重试。
- **未来证明偏差**：希望第一版同时解决多 Provider、MCP、数据库和外部插件。
- **描述即执行偏差**：高估 Manifest/Schema，低估运行时隔离和服务端授权。

## 12. 收缩后的 MVP 建议

### 12.1 第一期建模

```text
ConnectionProvider
|-- GitHub
`-- Feishu

SavedConnection
|-- provider
|-- endpoint/audience
|-- encrypted credential version
|-- owner
`-- grants

DataSourceConnector
|-- source-specific schema
`-- sync implementation

SkillCapabilityManifest
|-- parameters
|-- required provider/capability
`-- accepted authentication modes

ConnectionBinding
`-- consumer + requirement key + SavedConnection reference
```

### 12.2 第一期安全策略

- 默认复用 `SavedConnection`，不支持 Credential 在任意 Endpoint 之间重组。
- 普通 Skill 仅使用受控 Agent Tool。
- 可信 Skill 可获得限定 Audience 的短时 Access Token。
- 原始 App Secret/PAT 不作为默认 Materialization。
- `RunExecution` 只保存 Connection 和 Credential Version 引用。
- LensNode 子进程使用最小环境白名单，不继承全局秘密。
- 公开/共享 Assistant 默认不能使用共享 Connection，除非单独定义策略。

### 12.3 暂缓项

- 第三方 Provider 动态安装。
- 独立 Provider SDK 项目。
- 任意 CredentialSet 自由重组。
- 所有数据库和所有认证方法。
- 通用 Connection-bound Agent Tool 市场。
- 一次性支持 Environment、Header、SDK、Git Credential 全部注入模式。

## 13. 建议实施顺序

1. 先定义授权主体、共享 Assistant 和定时任务规则。
2. 修复 LensNode 脚本环境继承与 Git Credential 持久化问题。
3. 建立 `SavedConnection` 模型、加密、Grant、审计和删除影响。
4. 将 GitHub 和飞书作为内置 ConnectionProvider。
5. 迁移 DataSource 为第一个消费者，采用双读/可回滚迁移。
6. 实现独立、版本化的 Skill Capability Manifest。
7. 为 Skill 增加 Connection Binding 和第一种受控 Feishu/GitHub Tool。
8. 完成安全与运行闭环后再扩展 MCP、数据库和外部 Provider。

## 14. 已确认方向与待决策项

### 14.1 已确认方向

- 授权不再由 DataSource 领域独占。
- DataSource 只配置资源语义和选择已保存连接。
- GitHub、飞书等平台连接能力先内置在 SourceLens，但通过稳定边界实现。
- Skill 需要独立的自描述协议。
- 环境变量是交付方式，不是 Credential 类型。
- Connector、Provider 和 Agent Tool 必须分离责任。

### 14.2 待决策项

- 用户、共享 Assistant、后台定时任务和 LensNode 的授权主体模型。
- `SavedConnection` 的 owner、discover、use、bind、manage、rotate 和 share 权限。
- 第一期 Skill 仅支持 Agent Tool，还是同时支持短时 Access Token。
- Feishu Provider 的 Token Exchange 执行于 Backend、LensNode 还是代理 Tool。
- GitHub Enterprise、私有网络和数据库连接的 SSRF/网络信任策略。
- 已有 `DataSourceCredential` 数据的迁移、回滚和秘密轮换策略。
- 拓扑图中完整 Provider Registry 和 CredentialSet 是否在后续阶段需要。

## 15. 验收标准草案

连接基础能力进入 Skill 实施前，至少满足：

- 可以脱离 DataSource 创建和验证 SavedConnection。
- 一条 SavedConnection 可被多个符合权限的 DataSource 引用。
- 删除 DataSource 不会删除仍被其他消费者使用的 SavedConnection。
- API 不返回任何原始秘密。
- Run 快照和 WebSocket command 不包含解密后数据。
- 撤权、轮换、重试和重复派发具有明确且可测试的语义。
- Skill 新增或扩大 Connection requirement 会使旧绑定重新审批。
- 普通 Skill 无法读取 LensNode 全局环境或其他 Run/Skill 的秘密。
- 能记录谁在何时以哪个 Run/DataSource 使用了哪个 Connection
  和 Credential Version，但审计记录不含秘密。

## 16. 参考当前代码

- Skill 上传：`backend/lens/views/skills.py`
- Skill ZIP 校验与保存：`backend/lens/skill_packages.py`
- Skill、DataSourceCredential、AssistantSkill、RunExecution 模型：
  `backend/lens/models.py`
- Run 快照与 LensNode 派发：`backend/lens/services.py`
- LensNode Skill/MCP 物化：`lensnode/lensnode/runtime_resources.py`
- Skill 脚本 Tool：`lensnode/lensnode/agent_tools.py`
- DataSource Git 认证与同步：`lensnode/lensnode/datasource_sync.py`
