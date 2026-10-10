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

## 批量替换模型

点击管理台助手列表顶部「新建 Assistant」旁的「批量替换模型」，在弹窗中
通过下拉式多选框选择助手。下拉菜单支持名称或标识搜索、固定高度滚动和全选，
浮在弹窗之上并根据屏幕空间调整展开方向。点击「全选」选择全部助手；搜索后全选只作用于
搜索结果，已有的其他选择保留，也可以清空已选。从列表勾选后打开弹窗，会默认
选中这些助手；未勾选时默认不选。弹窗可选择全部活动助手，不受列表分页或筛选限制。
弹窗将受影响助手集中在左侧，模型设置单独放在右侧；窄屏下纵向排列。
模型设置可指定替换主模型或多模态模型。助手范围仅由多选框决定，选择目标模型不会改变助手清单。点击
「替换模型」直接执行，成功后提示替换完成。所有选中助手均提交替换，不判断目标模型是否与原模型一致。

目标必须是启用的全局 LLM 配置；多模态目标必须支持视觉输入，Smart 协作助手
不纳入多模态替换。已归档助手不纳入更新，每批最多
1000 个助手。若助手模型在列表加载后发生变化，整批更新会拒绝，需要检查刷新后的清单后重试。

API：`POST /api/lens/assistants/replace-model/`，需要 `admin_console` 权限。
请求体包含 `model_field`（`agent_model_ref` 或 `multimodal_model_ref`）、
`target_model_ref`、`assistant_models`（`{uuid, model_ref}` 数组，原模型可为 `null`）
和 `preview`（默认 `true`，正式提交用 `false`）。响应返回 `count` 和受影响的
`assistants`；原模型不一致时返回 `409`，其他校验失败返回 `400`。更新在同一个
事务内完成，不改写已有 Run 的执行快照。

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
