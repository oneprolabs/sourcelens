# 设计稿：内置集成 Plugin（Tool + Datasource）

- 状态：GitHub、GitLab、Jira Cloud V1、通用 manifest UI、Skill capability 依赖、
  调用审计、凭证轮换/撤销、MCP Adapter 和可回滚旧凭证迁移工具已完成；现有
  GitLab/Jira Server/DC Skill 包迁移和制品签名仍属于后续发布工作。Plugin
  能力族协议已固化为 `docs/plugin-protocol.md`。
- 日期：2026-09-02
- 范围：GitHub、GitLab、Jira 等内置集成；同时服务代码数据源和助手工具。

本设计借鉴 Dify 的 Plugin Daemon、双 Provider 和 Manifest 机制，但不采用其在
调用请求中传递完整 credentials 的方式。Plugin 是商业化交付物，以独立项目构建和
发布，由企业部署安装；普通用户不能上传或安装 Plugin。

## 1. 背景与问题

当前 GitHub、GitLab、Jira 能力主要以可上传的 CLI/Skill 形式存在。凭证被建模为
DataSource 的附属对象，无法自然复用于 Skill 和 MCP；Skill 还需要自行处理 CLI
参数、第三方 API 和 secret，导致授权、审计、动态配置和错误处理分散。

Dify 的 Plugin Provider 模型验证了另一条路径：一个插件可以同时注册 Tool
Provider 和 Datasource Provider，平台根据插件声明动态展示配置并负责调用路由。
SourceLens 采用这一方向，但增加独立的 Connection、Capability 和受控运行时，避免
将原始凭证无条件传给 Skill、模型或任务消息。

## 2. 目标与非目标

### 目标

- 将现有管理 CLI 封装为 GitHub、GitLab、Jira 等内置 Plugin Runtime。
- 一个 Plugin 同时提供 DataSource 适配器和面向大模型的 Tool Provider。
- 凭证、连接目标、资源范围和消费者配置分层，支持真正复用。
- 管理页面由 Plugin manifest 驱动；前端只内置安全字段类型和通用资源依赖渲染器，
  不内置 GitHub/GitLab/Jira 的资源 key、工具列表或响应结构。
- 改造数据源管理，使其通过 Connection 与 Plugin Provider 同步，而不再向 LensNode
  下发解密后的 credential config。
- 支持 Plugin 内部并发请求第三方 API，并统一处理超时、限流、重试和取消。
- 对每次调用实施用户、Assistant、Skill、Connection、Capability 和资源范围校验。
- V1 仅支持受信任内置 Plugin 提供的、代码圈定的只读操作。
- 保留现有 CLI 作为底层执行器，逐步迁移到统一 Plugin 协议。

### 非目标

- 第一阶段不开放任意第三方 Python/原生插件执行平台权限。
- 第一阶段不允许普通上传 Skill 直接读取长期 Token。
- 不把所有 CLI 子命令原样暴露给模型。
- 第一阶段不支持写操作、第三方可执行 Plugin 或通用 OAuth 流程。
- 不在本设计中解决多租户产品模型之外的组织身份同步。

## 3. 核心概念

```text
PluginDefinition
  ├─ ToolProvider
  ├─ DatasourceProvider
  ├─ credential_schema / connection_schema
  ├─ capability_family
  ├─ capabilities
  └─ runtime_adapter

PluginRegistry
  └─ discovers trusted installed Plugin projects and negotiated versions

SecretMaterial ──< SecretVersion
Connection ──────── references one SecretVersion
  ├─ endpoint / tenant / audience
  ├─ allowed scopes
  └─ grants / status

DataSource / Skill / MCP
  └─ binds Connection and declares required capabilities

ExecutionSnapshot
  └─ immutable configuration resolved when a run starts
```

### Plugin

代表一个外部系统集成，如 `github`、`gitlab`、`jira`。每个 Plugin 作为独立项目
管理，注册协议、能力、动态配置 schema 和运行时适配器。

### Plugin Registry 与目录发现

企业部署将经过 CI 构建和校验的 Plugin 包安装到受控目录，例如：

```text
/opt/sourcelens/plugins/
  github/
  gitlab/
  jira/
```

平台启动或显式刷新时扫描目录，读取 manifest，并通过 Registry 注册
`plugin_key + plugin_version + protocol_version`。目录发现是部署扩展机制，
不是授权边界；
Registry 只加载平台允许的内置 handler，不允许 manifest 指定任意 Python import、
shell command、远程组件或前端代码。

当前安装布局按 `plugin_key` 保存发布目录，物理目录不体现版本。目录扫描只登记安装
事实：新版本默认为 `debugging`，不会因 SemVer 更高而自动切换生产流量。管理员发布后，控制面冻结目录的 SHA-256 摘要，并独立分配
`candidate` 或 `active` 部署角色；正常新执行只选择 `published + active`。控制端与
LensNode 在调度/启动时校验 Plugin key、业务发布版本、包摘要和协议兼容性；执行
快照保留精确业务版本，因此 active 切换不影响运行中或历史执行，已退役版本也会
保留用于回放和诊断。

### SecretMaterial

只保存加密的 Token、App Secret、OAuth refresh token 等秘密材料，并支持版本、轮换、
撤销和状态。它不包含仓库、项目或文件夹范围。

### Connection

代表可以被消费者使用的完整连接：Plugin、endpoint/tenant/audience、非敏感配置、
允许范围、SecretVersion 和授权关系。DataSource、Skill、MCP 绑定 Connection，而不
直接绑定 SecretMaterial。Connection 是当前可编辑配置，不作为历史执行配置的唯一
来源。

### DataSource

DataSource 是 Plugin 的内容摄取消费者，而不是 Connection 的别名。它保存具体资源选择
（如 repository、branch、directory）、索引目标、同步策略、状态和同步历史；Connection
保存可复用认证、endpoint 和平台资源策略。一个 Connection 可被多个 DataSource 复用。

外部 DataSource 的资源选择必须是 Connection 允许范围的子集。`managed_workspace`
不使用外部 Connection，保留现有本地文件、上传和转换生命周期。

### Capability

描述最小业务权限，例如：

- `repository.read`
- `pull_request.read`
- `issue.read`
- `issue.write`
- `jira.issue.search`
- `jira.issue.transition`

Capability 同时用于 UI 展示、绑定校验、运行时授权、风险分级和审计。

V1 只发布只读 capability。平台的资源范围策略和内置 Plugin 代码是实际执行边界；
Provider PAT 的最小权限属于纵深防御，不能以 Connection 的 allowed scope 替代。

### ExecutionSnapshot

Tool 调用或 DataSource 同步实际开始时，控制端将当前有效 Connection 解析成不可变
快照。快照记录 endpoint、规范化资源范围、已授予的 capability、Plugin/manifest
版本、SecretVersion 引用、actor、任务和节点标识；不记录明文 secret。

排队任务在开始时使用当前已批准配置；已经开始的任务只使用自己的快照和短期 lease。
这为实际执行提供可审计、可复现的记录，而不在 V1 引入完整的
ConnectionRevision、绑定迁移和审批图谱。

## 4. Plugin Manifest

Manifest 是平台可读的稳定契约，不是安全边界。建议结构如下：

```yaml
key: github
version: 1.0.0
protocol_version: 1
capability_family: plugin
display_name: GitHub
description: Access authorized GitHub repositories and pull requests.

connection_schema:
  fields:
    - key: endpoint
      type: url
      required: true
    - key: tenant
      type: string
      required: false
    - key: token
      type: secret
      required: true

capabilities:
  - key: repository.read
    risk: low

datasource:
  supported: true
  resources: [organization, repository, branch]
  datasource_schema:
    fields:
      - key: repository
        type: provider_resource
        required: true
      - key: branch
        type: string
        required: false
      - key: directory
        type: path
        required: false

tools:
  - key: github_read_file
    capability_family: plugin
    capability: repository.read
    side_effect: none
  - key: github_search_code
    capability: repository.read
    side_effect: none
  - key: github_issue_get
    capability: repository.read
    side_effect: none
  - key: github_pull_request_get
    capability: repository.read
    side_effect: none
```

Manifest 应包含：

- 身份、版本、展示名称、图标和说明；
- credential/connection 字段类型、默认值、校验和脱敏规则；
- capability、风险等级、读写属性和是否需要确认；V1 只允许只读工具；
- 可选的 DataSource 资源发现与同步声明；
- 声明 DataSource 时所需的资源选择、同步配置和索引输入 schema；
- Tool 名称、描述、输入/输出 JSON Schema 和所需 capability；
- 运行时版本和兼容的 CLI/API 协议版本。

同一个 Manifest 可独立声明 Tool 和 DataSource 能力。Plugin 项目可以
独立维护代码、测试、文档和发布流程，SourceLens 主仓库只维护 Registry、通用
Connection、DataSource、Task、Lease 和 schema renderer。

### Assistant Guidance：渐进式能力说明

Plugin 可以在 Manifest 中提供可选的 `assistant_guidance`，让内置 Plugin 具备类似
Skill 的渐进式说明能力，同时避免把完整文档默认塞入每次对话上下文：

```yaml
assistant_guidance:
  summary: Inspect approved repositories and pull requests.
  when_to_use:
    - Current repository questions.
  topics:
    - key: repository
      summary: Read repository metadata and files.
      details: Use an owner/name value from the Connection scope.
      tool_keys: [github_repository_get, github_read_file]
```

控制面会为每个已绑定 Connection 生成隔离的虚拟 Plugin Skill 快照，复用现有
Skill 的渐进式加载机制：

1. 初始 System Prompt 只注入虚拟 Skill 的简短描述、适用场景和 references 导航；
   详细能力、主题与允许资源列表写入 Skill 包中的 `references/`，按需读取。
2. `references/repositories.md`（或 Provider 对应的资源文件）可以列出当前
   Connection 的允许仓库/项目标识，仅用于选择 Tool 参数，不代表授权。每次调用
   仍由控制面和 Provider Runtime 双重校验。

`assistant_guidance` 是不可信的说明数据，不是授权或执行配置。它不能授予新的
Connection scope、capability 或 Tool，也不能覆盖平台安全边界；真实执行仍必须经过
ExecutionSnapshot、Lease、Secret Material 和 Plugin Runtime。Registry 需要校验
主题数量、文本长度、主题 key 唯一性，以及 `tool_keys` 必须引用同一 Manifest 中
已声明的 Tool。虚拟 Skill 不得包含 Token、Secret、Connection UUID、Lease、内部
endpoint 或其他运行时材料。其缓存内容至少受 `plugin_key +
allowed_scope` 哈希影响；更新 Plugin 或 Connection scope 后重新生成 Skill 快照。
宿主不再注册 Plugin 专属的 `plugin_help` Tool，详细说明统一通过 Skill 文件读取。

动态页面使用 Manifest schema 生成供应商字段和资源选择控件；通用 renderer 只保留
少量受控组件，后端始终再次执行完整校验。

## 5. Tool Provider 设计

Plugin Tool 是给大模型调用的原子业务工具，不是 CLI passthrough。

```json
{
  "name": "github_read_file",
  "description": "Read a text file from an authorized GitHub repository. "
                 "Do not use this tool to modify files.",
  "input_schema": {
    "type": "object",
    "properties": {
      "repository": {"type": "string"},
      "path": {"type": "string"},
      "ref": {"type": "string"}
    },
    "required": ["repository", "path"]
  }
}
```

运行路径：

```text
LLM tool call
  → Tool Gateway
  → 校验 actor / Assistant / Skill / Connection / capability / resource
  → Plugin Runtime
  → CLI 或第三方 API
  → 结果清洗、大小限制和敏感信息过滤
  → Tool result 返回模型
```

当前 GitHub V1 的 LensNode 实现固定使用以下受控链路：

```text
模型 Tool Call（ToolRuntime.tool_call_id）
  → POST /api/lens/plugin-runtime/tool-snapshots/
  → GET  /api/lens/plugin-runtime/snapshots/{uuid}/
  → POST /api/lens/plugin-runtime/leases/
  → POST /api/lens/plugin-runtime/leases/{uuid}/material/
  → https://api.github.com（禁止重定向）
```

一个模型 Tool Call 对应一个 `tool_invoke` ExecutionSnapshot，调用 ID 作为幂等键；
同一模型轮次中彼此独立的 Tool Call 由现有 ToolNode 并行执行，共享线程安全的
HTTPX Client 连接池。PAT 只在受信任的 LensNode/Provider 内存中短暂存在，绝不进入
Tool 参数、Run 快照、模型上下文、返回结果或普通事件明细。GitHub V1 当前提供以下
只读资源族：

- 仓库元数据和分支列表；
- 文件读取和仓库内代码搜索；
- Commit 列表、详情和变更文件摘要；
- Issue 列表、详情和评论；
- Pull Request 列表、详情、变更文件和 Review；
- Release 列表；
- GitHub Actions Workflow Run 列表和详情。

`github_read_file` 最多读取 200 KB，其他 JSON 响应最多读取 1 MB；代码搜索最多
返回 20 项，其他列表每页最多返回 50 项，Issue、Comment、Review 和 Release 正文
会截断到固定长度。Commit 和 Pull Request 文件结果不返回 patch，Actions 不返回
job、日志或 artifact。超限、非文本和重定向均返回稳定错误码。

V1 不允许多个 Connection 暴露同名模型 Tool，避免在不向模型泄露 Connection 身份
的前提下产生歧义。GitHub 搜索会拒绝 `repo:`、`org:`、`user:` 等模型可控范围限定
符号，仓库范围只由 Connection 允许范围和 Provider 代码决定；搜索不接受不会被
GitHub API 使用的 `ref` 参数。

禁止提供 `github_raw_exec(command)`、任意 `gh api` 或任意 URL 工具。当前 GitHub
只读工具统一使用 `repository.read`。Direct Assistant 绑定一个 Plugin Connection
后自动获得该 Manifest 声明的全部只读操作，管理员不再维护工具子集。未来写操作必须有独立 capability、幂等键和按风险配置的用户确认或审批；它
不属于 V1 范围。

Skill 可以保留流程说明，但只声明依赖：

```yaml
required_plugins:
  - plugin: github
    capabilities: [repository.read]
```

Skill 负责告诉模型如何组合工具，Plugin Tool 负责实际访问外部系统。

## 6. Datasource Provider 设计

Datasource Provider 面向资源发现、内容摄取和增量同步，不直接复用 Tool 的业务
流程。

```text
DataSource
  - plugin_key: github
  - connection: github-company-readonly
  - resource: HyperBDR/sourcelens
  - branch: main
  - sync policy

Plugin Datasource Provider
  ├─ discover organizations / repositories / branches
  ├─ validate connection and resource scope
  ├─ fetch repository content
  ├─ report progress
  └─ return files / metadata for indexing
```

同一个 GitHub Connection 可以服务多个 DataSource；每个 DataSource 独立保存仓库、
分支、目录、索引目标和同步策略，不能反向修改 Connection 的 endpoint 或资源策略。
`datasource_config` 不得存 Token、endpoint 或 Connection scope；Plugin Datasource
Provider 在创建、更新、资源发现和执行前均验证其资源选择属于 Connection scope。

数据源管理页面分为两层：先选择或创建 Connection，随后按 manifest 的
`datasource_schema` 选择资源并配置同步。API 同样分离 Connection 的 CRUD/验证与
DataSource 的资源选择/调度，避免继续向通用 `config` 字段塞入认证字段。

manifest 通过受限 `write_to` 将 Connection 字段映射到 `endpoint`、`secret_value`、
`config.<key>` 或 `allowed_scope.<key>`；资源发现统一返回 `resources.<name>.items`，
子资源通过 `depends_on` 与父资源的 `options` 关联。前端不解释供应商专用 payload。

## 7. MCP 集成

MCP 可以作为 Plugin 的另一种工具呈现方式：

- Plugin Tool 适合平台原生、稳定且需要强约束的工具；
- MCP 适合外部工具集合或已有 MCP Server；
- 两者共享 Connection、Capability、授权和审计；
- MCP Server 不能通过任意 header 直接绕过 Connection 校验。

```text
Plugin Connection
  ├─ Native Tool Provider
  └─ MCP Adapter
```

当前 MCP Adapter 是现有 `MCPServer` 的受控 `plugin` transport：管理员只能选择一个
有效 Connection 和该 Plugin manifest 已声明的只读工具。它不能配置 MCP endpoint、
header、config、environment 或额外 credential。Assistant 仍可通过 `mcp_bindings`
选择该资源，但控制端在固化 Run 时将其转换为 `loaded_plugins`；LensNode 因而继续走
原生 Tool ExecutionSnapshot、lease、scope 校验和 PluginInvocation 审计链路，而不会
启动一个可绕过策略的远程 MCP Server。原有 URL/STDIO MCP 保持独立路径。

Skill 的 `required_plugins` capability 可以由直接 Plugin binding 或 Plugin MCP
Adapter 满足；两种绑定之间仍要求模型 Tool 名全局唯一。

## 8. 凭证与运行时安全

### 8.1 连接和凭证分离

GitHub PAT 不包含仓库 URL；Jira Token 不包含项目选择。Connection 才绑定
endpoint、tenant、audience 和允许范围。allowed scope 是平台请求约束，不会把 PAT
本身降权；V1 依赖固定的只读 Plugin Tool 和服务端资源校验来限制实际请求。

### 8.2 Secret 交付

V1 的执行位置是 LensNode。默认调用路径：

```text
Control plane → task metadata + connection reference → LensNode
LensNode → node-authenticated lease request → Credential service
LensNode → node-authenticated material exchange → Plugin Runtime
Plugin Runtime → Provider API/CLI
```

任务消息不携带 credential。LensNode 不能直接读取数据库或长期 Token，而是以节点
身份为本次 Run 请求短期 lease。普通 Skill 只获得工具，不获得原始 Token；模型上下文
也不包含 credential 或授权材料。lease 至少绑定 tenant、node、actor、Run、Plugin、
Tool、Capability、资源范围、ExecutionSnapshot 和过期时间，并只在 Plugin Runtime
内存中使用。当前控制面提供三个内部接口：LensNode 先读取
`GET /api/lens/plugin-runtime/snapshots/{snapshot_uuid}/` 获取脱敏的执行配置，
再创建
`POST /api/lens/plugin-runtime/leases/` 获取 opaque lease，再调用
`POST /api/lens/plugin-runtime/leases/{lease_uuid}/material/` 领取快照绑定的短时
材料。第二个响应只对已认证且绑定的 LensNode 返回，材料不写入任务消息、快照、日志，
Provider 使用完成后应立即释放引用。后续可将该接口替换为节点本地密钥代理或
GitHub App installation token，而不改变任务消息契约。

该模型与 Dify 的主要差异是：Dify 常将 credentials 作为 API 到 Plugin Daemon 的
调用数据传递；SourceLens 不将完整 credential 放入任务消息、Tool 参数或普通调用
日志，而由受信任 LensNode 按 ExecutionSnapshot 申请 lease。该模型降低队列消息、
Run 快照和模型上下文泄露凭证的风险，但 LensNode 是受信任执行
边界：若未来支持非平台管理节点，应改为控制端代理调用或更严格的隔离模型。

DataSource 的初始、手动、定时和重试同步都走相同路径：控制端在任务实际开始时解析
DataSource 与 Connection，创建 ExecutionSnapshot；LensNode 只接收 task、snapshot、
datasource 和插件运行参数标识，并以节点身份取得短期 lease。任何 Connection 无效、
资源范围不匹配或 lease 申请失败都应使任务以明确状态失败，不能回退到旧 credential。

### 8.3 强制校验

每次调用必须校验：

1. 当前用户和有效 actor；
2. Assistant/Skill 是否允许使用该 Plugin；
3. Connection 是否属于当前租户且状态有效；
4. Capability 是否满足；
5. endpoint、仓库、项目或文件夹是否在允许范围；
6. 工具读写级别、确认和速率限制；
7. lease、SecretVersion、ExecutionSnapshot 和 Plugin manifest 是否仍有效。

## 9. Plugin 内部并发与异步

工具对模型可以同步返回，但 Plugin 内部可以并行请求：

```text
github_get_pull_request_context
  ├─ pull request metadata
  ├─ changed files
  ├─ reviews
  └─ comments
```

建议 Runtime 使用宿主提供的统一 `PluginRequestContext`/HTTP 客户端池：

- Connection-scoped HTTP client 和连接池；
- 每次调用和每个 Connection 的并发上限；
- deadline、取消传播和重试策略；
- Provider rate-limit budget；
- correlation ID 和脱敏日志。

只读、互相独立的请求可使用 `asyncio.gather`/任务组并行；写操作和有依赖的请求
必须保持顺序。部分失败应返回可用结果与结构化 warning，而不是静默丢失上下文。

长任务（大仓库同步、批量导出）使用平台任务系统并流式报告进度；短任务内部并行后
一次性返回给模型。

## 10. 现有 CLI 的复用方式

第一阶段保留既有代码同步链路，模型工具使用固定 HTTP API：

```text
GitHub Plugin Runtime
  ├─ Tool adapter → 固定 GitHub REST API 与安全字段投影
  └─ Datasource adapter → 现有代码同步 CLI
```

适配器负责：

- 将结构化工具参数转换为固定 CLI 参数；
- 禁止 shell 拼接和任意命令；
- 注入短期认证上下文；
- 限制工作目录、网络和文件输出；
- 将 stdout/stderr 转为脱敏的结构化结果和进度事件。

当前 Provider 通过共享 `PluginRequestContext` 统一约束内部并行请求：每个
Connection 有界线程池、请求超时、调用 deadline、取消事件、指数退避和受限的
`Retry-After`。资源发现会保留成功项，并以 `warnings[{resource,label,code}]` 返回
单项失败；连接验证仍在不可恢复或全部失败时返回稳定错误。当前实现保持 Tool 对
模型同步返回，独立资源请求在 Provider 内部并行；后续可将同一上下文替换为
`httpx.AsyncClient`，不改变上层 Provider 契约。

Datasource 同步的取消信号会从控制通道传播到 LensNode 的执行队列和 Git
子进程；Git 进程使用独立进程组，取消或超时会终止整个进程组，并向控制端
回传稳定的 `cancelled` 终态。同步结果还受文件数和总字节数上限约束，避免
仓库内容耗尽 LensNode 工作区。GitHub Plugin 默认不递归拉取 submodule，
因此不会跟随仓库声明的任意外部地址；若未来开放该能力，必须增加显式 host
allowlist 和资源配额。

对于 GitHub/GitLab/Jira 的标准 HTTP API，Plugin Runtime 使用宿主管理的 HTTPX
客户端池；当前同步 Tool 通过线程并发安全地复用连接，后续如切换异步实现不改变
上层 Provider 契约。客户端启用 HTTP/2 协商，服务端不支持时自动回退到 HTTP/1.1
keep-alive，不需要 Plugin 分支处理。CLI 保留给已有复杂流程或必须复用的同步实现。
GitLab 自托管 endpoint
允许企业管理员配置任意 HTTPS 根域名，生产部署应在网络层或管理员配置中增加
出站 allowlist，并防范私网解析和 DNS rebinding，不能仅依赖 URL 格式校验。

## 11. API 与数据模型草案

建议新增：

```text
PluginDefinition
PluginVersion
SecretMaterial
SecretVersion
Connection
ConnectionGrant
PluginToolBinding
PluginDatasourceBinding
PluginInvocation
ExecutionSnapshot
```

建议 API：

```text
GET  /api/lens/admin/plugins/
GET  /api/lens/admin/plugins/{key}/manifest/
GET  /api/lens/admin/plugins/{key}/tools/
GET  /api/lens/admin/plugins/{key}/datasources/

GET  /api/lens/admin/connections/
POST /api/lens/admin/connections/
PATCH /api/lens/admin/connections/{uuid}/
POST /api/lens/admin/connections/{uuid}/validate/
GET  /api/lens/admin/connections/{uuid}/resources/
POST /api/lens/admin/connections/{uuid}/revoke/
GET  /api/lens/admin/plugin-invocations/
POST /api/lens/plugin-runtime/tool-snapshots/
GET  /api/lens/plugin-runtime/snapshots/{uuid}/
POST /api/lens/plugin-runtime/leases/
POST /api/lens/plugin-runtime/leases/{uuid}/material/
```

上面的 Tool Runtime 接口仅供已认证的 LensNode 使用；模型不直接访问这些接口。
未来如需提供面向其他内部调用方的统一 invoke API，必须复用同一快照、租约和
Provider 校验链路，不能绕过 Connection scope。

当前 V1 已落地的 Connection 管理接口为：

```text
GET    /api/lens/admin/connections/
POST   /api/lens/admin/connections/
PATCH  /api/lens/admin/connections/{uuid}/
DELETE /api/lens/admin/connections/{uuid}/
POST   /api/lens/admin/connections/{uuid}/validate/
GET    /api/lens/admin/connections/{uuid}/resources/
POST   /api/lens/admin/connections/{uuid}/revoke/
```

Connection 的 `secret_value` 仅允许写入，不会出现在响应中；PATCH 更新
`secret_value` 会创建新的 SecretVersion，并复用同一个 SecretMaterial。V1 GitHub
Provider 只接受 `https://github.com` endpoint，后续企业 GitHub 域名需要单独增加受控
allowlist。普通轮换保留旧 SecretVersion，使已经固化的 ExecutionSnapshot 继续使用
原版本；紧急撤销会停用整个 SecretMaterial 血缘、所有共享该材料的 Connection，并
撤销其尚未过期的 active lease。重复撤销是幂等的。

Assistant 创建和更新接口通过可选的 `plugin_bindings` 管理模型工具授权：

```json
{
  "plugin_bindings": [
    {
      "connection_uuid": "<connection-uuid>",
      "enabled": true
    }
  ]
}
```

绑定时控制端校验已安装 Plugin、Connection 与 Secret 生命周期；Manifest 是 Direct
Plugin 工具集合的唯一来源，历史 `tools` 字段仅为兼容而接受，不再作为运行时 allowlist。
响应只返回 Connection 身份、名称、Plugin key 和兼容性的工具信息，不返回 endpoint、scope、
SecretVersion 内容或密文。Direct `general_chat` 至少需要一个启用的 Skill 或一个有效的
Plugin Connection。Run 创建时保存非敏感 `loaded_plugins`，Worker 实际开始执行时按当前有效
绑定重新固化，并将工具定义下发给 LensNode。LensNode 使用模型真实 Tool Call ID
创建 Tool ExecutionSnapshot，再通过 snapshot-bound lease 获取材料并调用固定的
GitHub API；执行失败不会回退到旧凭证路径。

持久化 Run 关联 ExecutionSnapshot；该快照记录 Plugin、Connection、
SecretVersion、Capability 和资源引用，不记录解析后的 secret。

DataSource API 保持数据源生命周期入口，但外部类型的写入契约调整为 `plugin_key`、
`connection_uuid` 和受 manifest 校验的 `datasource_config`。创建/更新后注册同步策略；
初始、手动、定时和重试任务统一通过 snapshot + lease 下发。迁移期间旧
`source_type`、`config` 和 `credential_uuid` 仅作为兼容读路径，不能成为新写入路径。

## 12. 迁移路径

### Phase 1：Plugin Registry 与 GitHub 只读垂直切片

- 定义独立 Plugin 项目布局、受控目录发现、Registry、受限 manifest、只读 capability、
  Tool/Datasource Provider 和 ExecutionSnapshot 契约；
- 定义 Connection、SecretVersion、节点认证和短期 lease；
- 将 GitHub 模型能力实现为固定 REST API Tool，不提供任意 CLI/API passthrough；
- 实现 `GitHubDatasourceProvider` 并集成 DataSource 管理：Connection 选择、manifest
  驱动的资源配置、资源范围校验和同步任务快照；
- 跑通 Connection → DataSource 初始、手动、定时和重试同步，LensNode 不接收解密
  credential config；
- 跑通仓库、代码、Commit、Issue、Pull Request、Release 和 Actions 只读工具，并验证
  同轮独立调用并行；
- 只支持一种 GitHub 认证方式，并限定允许的 endpoint；
- 完成控制端与 LensNode 的 Plugin/version/protocol 握手；
- 保留旧 DataSourceCredential 读路径，但禁止新功能继续扩展旧模型。

### Phase 2：动态管理页、Skill 迁移与更多 Provider

- 已根据 manifest 动态渲染 Connection、DataSource 和 Assistant Plugin binding；
- 已增加通用资源发现、依赖选项、能力筛选、`required_plugins` 和绑定校验；
- GitHub Skills 可声明流程依赖并使用平台 Tool；GitLab/Jira Skill 包的实际迁移
  需要在各自 Skill 项目中移除 Token/任意 CLI 读取，属于独立发布任务；
- GitLab 内置 Plugin 同时提供 DataSource 与 Tool；Jira 仅提供 Tool。Jira Cloud
  使用 API v3，自建 Jira 使用 API v2，均复用受控的 Basic account/token 认证和
  Connection 项目 allowlist。

### Phase 3：扩展集成与受控能力

- 已增加凭证轮换、紧急撤销和 Secret-free PluginInvocation 审计；
- 已增加受控 `plugin` MCP Adapter，并复用 Connection/Capability/运行时安全链路。

写操作、第三方 Plugin、OAuth callback/refresh token rotation 和完整的
ConnectionRevision 仅在明确的产品需求出现后另行设计。

### Phase 4：兼容迁移和清理

- 已提供 `migrate_legacy_plugin_integrations` 管理命令；默认 dry-run，`--apply` 才写入，
  `--rollback` 恢复旧执行路径并停用迁移生成的 Connection；
- 仅自动迁移有效 GitHub HTTPS Token 和可无歧义解析的单仓库 DataSource，同时保留旧
  credential/config 作为回滚依据；每个决策写入 `LegacyIntegrationMigration`；
- 组织批量配置、公开访问、非 GitHub Provider、异常 endpoint/scope 或无法解析的资源
  标记为 `manual_review`，不会猜测转换；
- 删除旧的 DataSourceCredential API 和明文 Reveal 能力。

## 13. 主要风险与取舍

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| LensNode 被攻破后读取运行时凭证 | 高 | 节点身份、短期 lease、内存使用、撤销和受信任节点运营 |
| 动态 schema 过于复杂 | 中 | 第一阶段限制字段类型，保留少量自定义组件 |
| CLI 参数被模型注入 | 高 | 只暴露业务工具，固定参数映射，禁止 raw exec |
| 第三方 API 限流 | 中 | Connection/provider 并发预算、Retry-After、退避 |
| 旧凭证迁移失败 | 高 | 双读、版本化、回滚和迁移前 scope 校验 |
| Tool 与 Datasource 语义混淆 | 中 | 两套 Provider 接口，共享 Connection 但分离执行契约 |
| 数据源仍经消息传递解密 Token | 高 | 同步下发统一改为 task metadata、snapshot 和 lease |
| DataSource 资源越过 Connection scope | 高 | 创建、更新、发现和执行时做子集校验 |
| endpoint 变更导致 Token 外发 | 高 | allowlist、显式验证、执行快照；PATCH 后不自动探测 |
| Plugin 目录或版本被错误加载 | 高 | 受控安装目录、Registry handler allowlist、制品校验和版本握手 |
| 单个资源发现失败 | 中 | 保留成功结果并返回结构化 warning；重试受 deadline、取消信号和并发预算约束 |
| 管理端验证/资源发现请求被滥用 | 中 | 当前依赖管理员权限和上限；后续增加按用户/Connection 的限流 |
| manifest 无法表达复杂供应商 UI | 中 | 保持受限通用协议；确有第三个稳定用例后再扩展字段类型 |

## 14. 验收标准

### GitHub V1 已满足

- GitHub Plugin 可以同时出现在 DataSource 和 Skill 工具选择流程中；
- 同一个 Connection 可绑定多个 DataSource 和多个 Direct Assistant；
- Direct Assistant 绑定 Connection 后可看到该 Manifest 的全部只读工具；
- 原始 secret 不进入 prompt、Tool 参数、Run 快照和普通日志；
- V1 只暴露代码圈定的只读 Tool，且不提供 raw exec 或任意 URL 请求；
- LensNode 仅接收任务元数据，并按执行快照取得与任务绑定的短期 lease；
- 外部 DataSource 持有 `plugin_key + connection + datasource_config`，不持有 Token 或
  endpoint；其资源选择经过 manifest 与 Connection scope 校验；
- 初始、手动、定时和重试同步均不向 LensNode 下发解密 credential config；
- 控制端和 LensNode 拒绝执行未注册、版本不兼容或协议不兼容的 Plugin。
- Connection 与 DataSource 管理不硬编码供应商资源 key、工具或响应结构；
- Skill capability 可由直接 Plugin binding 或 Plugin MCP Adapter 满足，且两者共享快照、
  lease、scope 和审计链路；
- 普通轮换不破坏旧快照，紧急撤销覆盖共享 SecretMaterial 的 Connection 和 active lease；
- 旧 GitHub 凭证迁移支持 dry-run、审计、人工复核标记和回滚。

### 项目级后续验收

- Plugin 内部并行请求已具备统一的并发上限、超时、取消、重试和部分失败结果协议；
- 现有 GitHub/GitLab/Jira Skills 完成流程化迁移，移除自行读取 Token 和执行任意
  CLI 的逻辑（待各 Skill 项目发布）；
- GitLab Provider 提供 DataSource 与 Tool，Jira Provider 仅提供 Tool；
- 现有 CLI 能在不改变业务行为的情况下作为 Plugin Runtime 后端执行器。
- Plugin 可作为独立项目构建、测试和发布，并由企业部署安装到受控目录；普通用户
  无 Plugin 上传或安装权限；

## 15. 待确认问题

1. 第一阶段 Connection 是否只允许管理员创建和授权？
2. GitHub V1 采用 fine-grained PAT 还是 GitHub App？
3. GitHub/GitLab 的资源 scope 是 Connection 级限制，还是允许 DataSource 级细化？
4. Jira 是否需要在现有 Basic account/token 之外增加 Cloud OAuth？
5. 如何定义 interactive Run、Smart 子助手、定时同步和重试的 effective actor？
6. 外部 DataSource 是否允许在 Connection scope 之下单独配置更窄的资源范围？
7. Plugin Registry 的安装目录、制品校验方式和升级保留窗口如何配置？

## 16. 独立 Plugin Runtime V1

每个受信任的企业内置 Plugin 是可单独构建、测试、安装的目录项目：

```text
plugins/<plugin-key>/
  plugin.json
  control.py
  runtime.py
```

`plugin.json` 只声明固定的 `python_v1` control/runtime handler，不能指定任意
import、命令、前端代码或远程 URL。控制面和 LensNode 都仅按冻结的
`plugin_key + plugin_version` 精确找到该目录；拒绝路径穿越、符号链接、目录逃逸、缺失
entrypoint、超限文件及导出身份不一致的包。

`control.py` 导出 Connection Provider 和 Tool Provider；Connection Provider 声明
`http_origins(endpoint, connection_config)`，让控制面为连接校验和资源发现注入
受限客户端。`runtime.py` 导出：

```python
PLUGIN_API_VERSION = 1
PLUGIN_KEY = "github"
PLUGIN_VERSION = "1.0.0"

http_origins(endpoint)
build_tool(definition, executor)
execute_tool(tool_key, client, arguments, secret, endpoint, config)
# Required as a pair when the Manifest declares Datasource support.
build_datasource_command(snapshot, material, trigger)
sync_datasource(command, workspace_path, emit, execute)
```

LensNode 保留通用的快照、lease、材料读取、审计和 finally 清理职责；Provider API
调用、endpoint 策略、工具签名、结果上限、同步命令规范化与同步编排均由 Plugin
runtime 承担。Plugin handler 可调用 LensNode 提供的受限共享执行器，但执行器不得
根据 provider key 推断供应商规则。
新 Plugin 或新版本因此不需要向 SourceLens/LensNode 增加供应商 key 分支。V1 仍是
企业管理员安装的受信任 Python 代码；进程隔离、制品签名和外部第三方 Plugin 运行权
限是后续安全发布工作。
