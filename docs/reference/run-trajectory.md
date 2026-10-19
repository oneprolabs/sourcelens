# Run 完整追踪实时协议

## 目标

Run 完整追踪以数据库中的 `RunTraceEvent` 为事实源，通过 SSE 向管理端推送增量。
SSE 只负责降低更新延迟；历史查询、导出、筛选和断线恢复仍依赖现有的轨迹查询接口。

该协议借鉴连续事件窗口的设计，但不引入独立的 Session Event Store：SourceLens
继续使用 Run、子 Run 和不可变 Trace Event 作为唯一持久化模型。

## 质量诊断与发布边界

执行完成与答案验证是不同信号。管理端 Run 详情的 `failure_summary` 除运行时
失败范围外，还读取不可变轨迹中的工具失败与最后一次证据审核；已有运行时失败
按工具来源去重，不重复计数，不返回参数、工具输出或异常原文。

- `accept_best_effort` 表示证据未通过，计入未解决的 verification 失败。
- `inconclusive` 或最终审核 gate 的低置信回退计入 warning；材料覆盖不足不等于答案被证明错误。
- 未被运行时摘要记录的 `tool.failed` 计入工具失败，同名工具以相同参数重试成功时
  标记 recovered；参数缺失或无关操作成功不能证明原失败已恢复。
- JSON 工具契约中的 `ok=false`、执行工具 artifact 中非零 `exit_code` 发出 `tool.failed`。
  文件内容和 shell 标准输出中的字面 JSON 不作为工具成功状态解析。

模型将 DSML 工具请求放入正文且没有有效结构化工具调用时，产生
`MODEL_TOOL_PROTOCOL_ERROR`；这些协议文本不得发布为最终答案。
纠正重试和摘要降级边界见 [Agent 运行约定](agent-runtime.md)。
摘要协议失败保留原始 `model.failed` 生命周期；宿主保留历史后，为带有 call id 的该次调用
发出 `model.recovered` 注解。轨迹显示“摘要失败，已保留历史”的 degraded 状态，并保留原始失败。
只有相同 call id 的显式恢复可改变显示；Run 完成、其他调用成功或历史失败都不能替代恢复信号。
模型分类筛选仍保留恢复注解。`deepagents.summarization.failed` 不计为成功压缩。
空摘要或占位摘要同样保留历史并发出失败诊断，但不因内容无效而改写已完成的模型调用生命周期。
证据复核纠正会重新启用收敛限制，并保留一个提示间隔内的有界纠正调用额度，
避免收敛提示耗尽后立即终止已要求的重写。累计轮数、提醒次数及剩余纠正额度随 checkpoint 恢复。

活动汇总工具的 `possibly_truncated`、`projects_truncated` 和分源 `errors` 表示来源缺口，
与审核材料不足分别记录；后续页失败保留已取得的记录。模型从私有证据账本读取记录并
生成业务报告，宿主检查阅读进度与完成条件，不生成固定模板报告。
范围、身份和交付规则见 [活动汇总契约](activity-report-contract.md)。

产出物仍遵循[最终候选批次发布规则](../decisions/003-reviewed-deliverable-publication.md)：
复核纠正丢弃旧候选清单，修订文件必须重新生成；终态仅发布最终候选批次。
完整材料审核未通过、或缺少必需的最终文件，业务 outcome 为 partial。

预算收尾的完整答案判定、证据保留与最终 outcome 规则见 [Agent 运行约定](agent-runtime.md)。

## 查询检查点

管理端先请求：

```http
GET /api/lens/admin/runs/{run_uuid}/trajectory/
```

除原有分页结果外，响应增加：

```json
{
  "stream_cursor": "opaque-cursor",
  "stream_sequence": 42,
  "revision": "opaque-revision"
}
```

- `stream_cursor` 是持久化顺序 `(created_at, uuid)` 的不透明编码。
- `stream_sequence` 是检查点之前已持久化的全局事件数，仅用于接收检查点与分页。
- `revision` 是当前事件水位、Run/子 Run 状态及执行状态的摘要。
- 事件 `sequence` / `event_sequence` 保留运行时产生的 Run 内序号，步骤编号使用它；不同 Run 可以重复。
- `ingest_sequence` 是接收顺序，`after_sequence` 分页与 SSE 顶层 `sequence` 继续使用接收顺序。
- `timestamp` / `event_timestamp` 是发生时间；`created_at` 是落库时间。展示序号不是续传游标。

## SSE 连接

```http
GET /api/lens/admin/runs/{run_uuid}/trajectory/stream/
  ?cursor={stream_cursor}
  &sequence={stream_sequence}
  &revision={revision}
  &q={keyword}
  &category={category}
Accept: text/event-stream
```

接口只允许拥有 `admin_console` 能力的已认证用户访问。游标必须属于当前 Run
可见的父/子追踪范围，非法或跨 Run 游标返回 `400`。

## 消息类型

建立连接后首先发送 `sync`。它包含查询检查点之后、连接建立之前产生的事件，消除
“先查询、后订阅”之间的竞态：

```json
{
  "type": "sync",
  "previous_revision": "revision-from-query",
  "revision": "current-revision",
  "cursor": "current-cursor",
  "sequence": 44,
  "events": [],
  "summary": {},
  "run": {
    "uuid": "...",
    "status": "running",
    "executor_status": "running",
    "outcome": ""
  }
}
```

后续变化使用 `append`，字段与 `sync` 相同。即使当前筛选条件没有命中新事件，
服务端仍可发送空 `events` 的 `append` 来推进 cursor、summary 或 Run 状态。

无变化时每 15 秒发送一次心跳：

```json
{"type":"ping","ts":"..."}
```

Run 进入终态并经过最终静默确认后发送 `done`。Run 状态先变为终态时，客户端不能
提前中断仍在工作的 SSE；收到 `done` 后必须再执行一次轨迹查询作为 final sync。
final sync 失败时应继续退避重试，直到成功或用户离开当前追踪页面。

## 客户端一致性规则

1. 事件 identity 固定为 `trace_run_uuid + event_id`，增量使用 upsert，不直接拼接。
2. `append.previous_revision` 必须等于客户端当前 revision；不相等时停止应用增量，
   重新调用轨迹查询接口建立完整检查点。
3. SSE 断开时使用指数退避，并在重连前先执行完整查询；不能永久停止刷新。
4. 切换 Run、关闭详情或离开 Trace 视图时立即取消连接。
5. 新事件不清空搜索、筛选、折叠、时间范围或当前选中事件。
6. 用户位于记录末尾时自动跟随；用户查看历史时保持滚动位置，并显示新事件提示。

## 服务端一致性与性能

- SSE 每 300ms 检查一次数据库水位，批量最多读取 500 条增量。
- 无状态或事件变化时不重新计算完整 summary。
- 新建的 delegated Run 会在下一次检查中加入同一根 Run 的事件窗口。
- 消息丢失不依赖内存广播修复，cursor 始终从数据库补洞。
- 未来可用 Channels/Redis 通知唤醒数据库检查，但通知只能作为优化，不能替代
  cursor 查询与重连重同步。

## 运行结果、调用闭合与诊断

- Run 结束事件独立携带 `lifecycle`（completed / failed / cancelled）、`outcome`
  （completed / partial / blocked / awaiting_user_input）、`health`（healthy / degraded / failed）。
  执行结束不等于业务成功；降级和已恢复的错误仍保留在调用树内。
- 插件调用拥有独立的 `call_id`，并显式关联所属工具或 Gate；并发上下文通过 ContextVar
  和线程提交时的 copy_context 传递。内部进度不能关闭外层调用。
- Gate 超时立即产生插件 interrupted 事件；Run 结束、超时、取消均闭合已注册的子调用。
  这代表观察范围结束，并不表示 Python 工作线程已经被强制停止。
- 调用关闭后的返回标记 `late_event`，Run 结束后的记录标记 `after_run_finished`；保留
  实际内容，但不能覆盖原来的结束状态或延长 Run 耗时，也不能重新推送 running 业务状态。
- 诊断包含 missing_start、missing_end、missing_parent、parent_cycle、orphan_event、
  unbound_lifecycle、parent_context_mismatch。分页/筛选期间不推断缺失记录，避免误报。
- 折叠时 Token 按独立调用累计，耗时按子树时间窗口计算（并发不相加）；Run 根节点使用
  自身执行边界。展开时隐藏层级累计值，显示具体步骤值。
- `observed_model_tokens` 仅表示轨迹中已记录的模型用量；`metered_tokens` 表示已有
  LLMUsage 计量记录。两者不相加，也不宣称覆盖插件内部未提供用量的请求。
  调用可携带 `usage_source`（agent_model / gate_model / plugin_model）。
