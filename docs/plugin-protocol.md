# SourceLens Plugin Protocol V1

状态：当前实现协议，适用于企业部署的受信任内置 Plugin。

Plugin 是独立项目交付的集成包。它提供 Connection 管理，并可独立选择声明
Datasource 同步或面向大模型的 Tool；声明的能力都必须由宿主校验。

## 1. 包结构

宿主只从受控目录发现 Plugin，目录身份必须与 Manifest 一致：

```text
/opt/plugins/<key>/
  plugin.json
  control.py
  runtime.py
  assets/
```

`key` 使用小写字母开头的短标识，`version` 使用三段式 SemVer，且两层目录身份
必须分别与 Manifest 的 `key` 和 `version` 一致。Plugin 项目可以独立维护源码、
测试、文档和发布流程；宿主不从 Manifest 加载任意 Python 模块、Shell 命令或
远程前端代码。

目录发现只登记安装事实，不决定生产版本。每个已安装版本由控制面维护独立的发布
状态与部署角色：新发现版本默认为 `debugging` 且没有角色；管理员显式发布后才成为
`published`，并可独立设为 `candidate` 或 `active`。首次引导现有安装时，每个 Plugin
的最高已安装版本会初始化为 `published + active`，此后安装更高版本不会自动切换
生产流量。

发布会冻结整个版本目录的 SHA-256 摘要。`published` 和 `retired` 版本的内容不可
原地覆盖；摘要不一致时，Registry 必须拒绝正常加载并返回稳定冲突错误。需要修改
内容时必须使用新的 SemVer 目录。退役只停止新绑定和部署角色分配，不删除目录；
历史执行快照仍可按精确 `plugin_key + plugin_version` 解析该版本。

## 2. Manifest 身份与能力族

最小 Manifest：

```json
{
  "key": "github",
  "version": "1.0.0",
  "protocol_version": 1,
  "capability_family": "plugin",
  "handlers": {
    "control": "python_v1",
    "runtime": "python_v1",
    "datasource": "python_v1"
  }
}
```

V1 的 `capability_family` 取值为 `plugin`。它表示 Tool 由受信任的内置 Plugin
Runtime 执行，而不是 MCP Server。实际业务授权仍由 Tool 的 `capability` 字段和
Connection 的资源范围共同决定，例如 `repository.read`。

`version` 是必填的 SemVer 业务发布版本。它用于执行归因、评分与诊断聚合、候选
验证、灰度、回滚；`protocol_version` 只表示宿主接口兼容性，不能替代业务版本。

`mcp` 只表示名称为 `mcp__...` 的 MCP Server Tool；Skill、Plugin 和 MCP 是三个
不同的能力族。为兼容旧模型输出，宿主在路由修复阶段允许将旧的
`required_capabilities=["mcp"]` 映射到当前可用的 `plugin`，但新模型提示词应优先
输出 `plugin`。

为兼容历史 V1 Manifest，工具项缺少 `capability_family` 时继承 Manifest 的值；
未声明时默认为 `plugin`。新发布的 Plugin 必须显式声明该字段。

## 3. Tool 声明

```json
{
  "key": "github_commit_list",
  "description": "List commits in an approved repository.",
  "capability_family": "plugin",
  "capability": "repository.read",
  "side_effect": "none",
  "input_schema": {
    "type": "object",
    "properties": {
      "repository": {"type": "string"},
      "ref": {"type": "string"}
    },
    "required": ["repository"],
    "additionalProperties": false
  }
}
```

宿主必须校验：

- `key` 在同一 Plugin 内唯一，且只能包含小写字母、数字和下划线；
- `description` 是面向模型的完整操作说明，不能包含凭证或隐藏指令；
- `capability_family` 必须是宿主支持的能力族；V1 仅接受 `plugin`；
- `capability` 是稳定的业务权限标识；
- V1 的 `side_effect` 必须为 `none`，只读能力才允许注册；
- `input_schema` 必须是受控的 JSON Schema 子集，不能改变 Connection 资源边界。

## 4. Assistant Guidance

Plugin 可以通过可选的 `assistant_guidance` 提供类似 Skill 的渐进式使用说明：

```json
{
  "assistant_guidance": {
    "summary": "Inspect approved repositories and pull requests.",
    "when_to_use": ["Current repository questions."],
    "topics": [
      {
        "key": "repository",
        "summary": "Read repository metadata and files.",
        "details": "Use an owner/name value from the Connection scope.",
        "tool_keys": ["github_repository_get", "github_read_file"]
      }
    ]
  }
}
```

控制面会为每个已绑定的 Connection 生成一个隔离的虚拟 Plugin Skill 快照。初始
上下文只注入该 Skill 的简短描述、适用场景和 references 导航；详细主题、能力与
资源列表写入 Skill 包中的 `references/`，由统一 Skill 渐进式加载机制按需读取。
允许仓库/项目标识可以出现在 `references/repositories.md` 中，仅用于选择 Tool
参数，不代表授权；每次调用仍由控制面和 Provider Runtime 双重校验。

虚拟 Plugin Skill 是能力和资源导航，不是用户 Skill 的业务工作流指令，也不能
改变 Connection scope、capability、Tool schema 或平台安全规则。它不包含 Token、
Secret、Connection UUID、Lease、内部 endpoint 或其他运行时材料。其缓存键至少受
`plugin_key + allowed_scope` 内容哈希影响，Connection scope 变化
后必须生成新的 Skill 内容。

Registry 必须限制主题数量、文本长度和 `tool_keys`，并确认每个 Tool key 已在同一
Manifest 声明。Guidance 是面向模型的不可信说明，不能覆盖系统规则、Connection
scope、capability 或 Tool schema，也不能包含凭证、隐藏指令、任意 URL 或运行时路径。
Guidance 与 Plugin 定义一同进入执行快照，Plugin 更新后重新加载。宿主不再注册
Plugin 专属的 `plugin_help` Tool；详细说明统一通过虚拟 Skill 文件渐进加载。

## 5. Runtime 入口

`control.py` 的 Connection Provider 必须实现
`http_origins(endpoint, connection_config)`，返回控制面连接校验和资源发现可访问的
HTTPS origin。宿主据此注入受限客户端；Provider 不得自行创建或关闭客户端。

`runtime.py` 必须导出 Tool 相关固定符号；只有 Manifest 声明 DataSource 能力时，
才必须成对导出 `build_datasource_command` 和 `sync_datasource`：

```python
PLUGIN_API_VERSION = 1
PLUGIN_KEY = "github"
PLUGIN_VERSION = "1.0.0"

def http_origins(endpoint):
    """Return validated HTTPS origins used by this runtime."""

def build_tool(definition, executor):
    """Build one model-facing Tool from a validated declaration."""

def execute_tool(key, client, arguments, secret, endpoint, config):
    """Execute one bounded operation and return JSON-safe data."""

# Required only for Plugins with a declared Datasource capability.
def build_datasource_command(snapshot, material, trigger):
    """Build one controlled Datasource synchronization command."""

def sync_datasource(command, workspace_path, emit, execute):
    """Apply Plugin sync policy, then call the shared executor."""
```

LensNode 负责快照、lease、材料读取、取消、进度上报和结果生命周期；Plugin handler
负责 provider 专属的同步策略与执行编排。共享 `execute` 仅执行 Plugin 已准备好的
通用命令，不得根据 provider key 推断地址、认证或资源规则。

宿主会校验入口文件是受控目录中的普通文件，且入口身份与 Manifest 的
`key + version` 一致，并按 `plugin_key + plugin_version + 内容哈希` 缓存加载
模块。Tool 注册后必须附带运行时元数据：

```text
metadata.capability_family = "plugin"
metadata.plugin_key = <Manifest.key>
metadata.plugin_version = <Manifest.version>
metadata.capability = <Tool.capability>
```

路由、能力恢复和执行审计只能读取这些显式元数据，不能通过 `github_*`、`jira_*`
等名称前缀猜测能力类型。

## 6. 执行与凭证边界

```text
模型 Tool Call
  → Tool ExecutionSnapshot
  → snapshot-bound Lease
  → 短时 Secret Material
  → Plugin Runtime
  → 受控 Provider API/CLI
  → 清洗后的 Tool Result
```

约束：

- 模型只能看到 Tool 名称、说明和输入 schema；
- Token、Connection 配置中的敏感值和 Lease 内容不能进入模型上下文、Tool 参数、
  普通 Trace Event 或 Tool Result；
- Snapshot 固定 `plugin_key + plugin_version + protocol_version`、Manifest、
  Connection 配置、资源范围和 SecretVersion；
- Provider 必须再次校验 endpoint、资源范围和参数；
- 外部响应必须做结构校验、大小限制、正文截断和敏感字段过滤；
- Tool 失败返回稳定错误码，不能把第三方原始异常或响应原样交给模型；
- 写操作不属于 V1，未来必须使用新的 capability、幂等键和确认策略。

### 6.1 Plugin HTTP 客户端与协议降级

控制面和 LensNode 都为 Plugin Runtime 提供宿主管理的 HTTP 客户端池。Plugin
不应自行创建或关闭 `httpx.Client`，而是使用宿主注入的 Connection-scoped
客户端。客户端按 `plugin + connection + HTTPS origin` 复用连接，认证 Header
仅附加在当前请求上，不跨 Connection 共享 Cookie 或认证状态。未保存 Connection
的资源预览使用请求级临时客户端，结束后立即关闭，不进入长期连接池。

客户端允许 HTTP/2 协商；如果目标服务端、代理或 TLS 链路不支持 HTTP/2，HTTPX
会自动回退到 HTTP/1.1。回退不会导致 Plugin 失败，仍然可以复用 HTTP/1.1
keep-alive 连接，但不会有同一条 HTTP/2 连接上的并发流复用。Plugin 不需要根据
协议版本分支处理，也不应在 HTTP/2 协商失败后自行重复请求。

客户端仅开放 `GET`、`HEAD` 和 `POST`。`POST` 请求体只能通过 `json` 或
`content` 传入，编码后最大 64 KiB；宿主拒绝同时提供两类正文、覆盖 `Host`
Header 或启用重定向。该能力用于 OAuth/应用凭证换取短时 Token 及只读导出任务，
不代表模型 Tool 自动获得写权限。

宿主仍强制 HTTPS、origin allowlist、无重定向和请求/响应上限。HTTP/2 只优化传输
层，不能放宽 Connection scope、Tool capability 或 Provider 参数校验。

## 7. Assistant Binding 约定

Direct Assistant 绑定 Plugin 时只提交 Connection 身份和启用状态：

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

Connection 是授权边界，Plugin Manifest 是能力集合来源。绑定生效后，宿主将该
Manifest 声明的全部只读 Tool 注册给模型；前端不展示工具复选框，后端也不信任
客户端传入的工具子集。历史 `tools` 字段可以继续读写以兼容旧数据，但仅作为迁移
信息，不影响运行时全量加载。

同一 Assistant 不能启用多个会产生同名 Tool 的 Connection。通常一个 Plugin Key
只绑定一个 Connection；如果需要不同资源范围，应拆分为不同 Assistant，避免模型
无法区分同名 Tool 的 Connection。MCP Adapter 属于独立兼容入口，仍可显式选择
Manifest 中的 Tool，并与 Direct Plugin binding 共同执行全局 Tool 名冲突校验。

## 8. Datasource 约定

Datasource 与 Tool 共用 Connection，但执行契约分离：Datasource 保存资源选择、
同步策略和目标目录，Plugin 负责资源校验、内容读取和增量同步。资源发现遵循
Manifest 声明的资源依赖；依赖字段变化后才请求对应的 Provider 选项。

Plugin 可以只声明 Datasource 能力并令 `tools` 为空。Connection 也可以只保存
可复用认证信息、使用空 `allowed_scope` 且不枚举资源；此时具体资源由
`datasource_schema` 收集，并由 Provider 在保存时规范化。一个 DataSource
包含多个入口时，应使用同一有界并发预算发现资源，按稳定资源标识全局去重后再
进入有界下载阶段。

Provider 的并发、超时、deadline、取消、Retry-After 和部分失败处理必须通过宿主
提供的 `PluginRequestContext`，禁止自行创建不受限制的线程或请求池。

## 9. 协议兼容

- `protocol_version` 只代表宿主与 Plugin 的接口版本，不代表业务 capability 版本；
- `plugin_version` 是 SemVer 业务发布版本，当前四个内置 Plugin 均为 `1.0.0`；
- 安装布局为 `plugins/<key>/`，物理目录不体现版本；新发现
  版本保持 `debugging`，不会因为 SemVer 更高而自动用于生产；
- 正常 Connection、Datasource、Assistant 和新执行快照只解析
  `published + active` 版本；候选验证显式解析 `published + candidate` 版本；
- Manifest、Runtime、API 和执行快照必须保留 `plugin_version`；
- 发布时冻结的包摘要用于保证同一版本内容不可变，不取代 `plugin_version`；
- `retired` 版本不能再获得部署角色，但仍保留给记录了精确版本的历史快照；
- 不兼容的入口、Manifest 或安全语义必须提升 `protocol_version`；
- 新增字段优先采用可选字段和默认值，不能删除既有字段或改变其含义；
- 宿主、Control Runtime、LensNode Runtime 必须拒绝未协商的协议版本。

## 10. 发布前一致性检查

每个独立 Plugin 项目在发布前至少应验证：

1. Manifest 的身份、handler、能力族和 JSON Schema 通过宿主 Registry 校验；
2. 每个声明的 Tool 都能由 `runtime.py` 构造，名称与 schema 完全一致；
3. Tool 的 capability、资源 allowlist、endpoint 校验在 Provider Runtime 中重复执行；
4. 成功、超时、限流、取消、超限和第三方异常均返回稳定错误码；
5. 测试输出、日志、Trace 和错误中不含 Token 或其他认证材料；
6. 同一模型轮次的独立 Tool Call 可以安全并行，不共享可变凭证状态；
7. Plugin 更新后内容哈希不会继续复用旧 Runtime 模块；
8. Manifest、Control、Runtime 和执行快照中的 `plugin_version` 完全一致。
