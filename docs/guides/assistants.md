# 助手与运行

## 标准助手与 Smart 协作

助手的产品模式 `mode` 与执行能力 `capability` 独立。`direct` 表示标准助手；
`smart` 表示管理员配置的协作助手。API 保留 `routing_mode` 兼容字段。
标准能力包括 `general_chat`、`knowledge_qa` 和 `code_analysis`。

管理员在助手向导中选择协作成员。成员必须是启用、非系统的标准助手；不能选择自己、
已归档助手或其他协作助手，不能嵌套。成员配置使用 UUID，不能依赖可变名称。

创建会话时检查协调助手及每个成员的访问权限，并将成员 UUID 固化到
`Session.allowed_assistant_uuids`。启动 Run 时再次检查权限和成员状态。
后续成员编辑只影响新会话，不改写已有会话和运行快照。

固定协作会话的成员范围只读；每条消息仍可在该范围内使用 `@Assistant` 选择参与者。
现有 `/lens/chat` 临时协作入口保留，用户在该入口选择会话参与范围。
绑定协作成员不会授予用户原本没有的成员访问权。

## API 字段

创建或修改助手使用 `mode` 和只写的 `collaboration_member_uuids`；响应通过
只读的 `collaboration_members` 返回成员信息。创建固定协作会话仍使用普通
`POST /api/lens/sessions/` 和 `assistant_uuid`，无需另建协作 API。

## Run 时限

当前控制面为所有分析档位使用 **3600 秒**的墙钟安全上限，将其写入
`RunExecution.run_timeout_s` 并随 `run_start` 下发。分析档位仍影响轮次与 Token
预算。

LensNode 优先使用有效的 `run_timeout_s`，缺失或无效时回退 3600 秒。
恢复执行使用 `remaining_run_timeout_s`，或根据原始 `run_started_at` 扣除已消耗时间，
不会因恢复重新获得完整预算。助手后续配置修改不改变已有执行快照。

`LENSNODE_REQUEST_TIMEOUT_S` 控制 HTTP 传输请求，与整轮 Run 的墙钟上限分开。

## 相关参考

- [Decision Plugin](../reference/decision-plugins.md)：检索、证据校验和排序。
- [提示词与证据边界](../reference/prompt-boundaries.md)：上传材料、授权和引用。
- [交付物发布](../decisions/003-reviewed-deliverable-publication.md)：审核后的文件可见性。
- [Run 轨迹](../reference/run-trajectory.md)：生命周期、结果和诊断。
