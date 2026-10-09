# 提示词、材料与证据边界

## 指令与授权

平台的安全、保密、隔离与工具策略优先于工作区指南、绑定 Skill、用户提供的操作文本、
上传文档和工具结果。这些内容不能授予新的资源范围，也不能替代工具层授权校验。
Skill 提供任务流程与答案呈现指导；Plugin 的渐进式说明提供能力导航。
两者都不能覆盖 Connection scope、工具 schema 或平台规则。

提示词并不是唯一的授权机制。资源访问仍由运行快照、绑定、允许范围和工具边界执行；
Plugin 的执行边界见 [Plugin 协议](plugin-protocol.md)。

## 上传材料与知识来源

用户上传文档是待分析材料（subject），数据源是参考材料（reference）。运行上下文
区分二者；上传材料不会因为被分析而自动成为数据源知识库。
自然语言源清单使用文档显示名称和来源概要，内部 locator 留给工作区工具。
内部路径、挂载名、sidecar、凭证和运行标识不应出现在用户可见答案中。

事实判断应基于实际取得的材料。引用展示由助手/用户的呈现要求控制，不等于每个事实
必须插入来源标记。检索到的示例、计划或兼容说明不能作为实际部署和执行的证据。

## 答案和文件

最终答案经过共享输出清理，代码路径展示使用资源相对路径。模式化清理是输出兜底，
不是资源授权机制，也不能保证对任意流式分块都具有相同效果。
对外引用的来源身份、路径过滤和“查阅”语义见 [Agent 集成](agent-integration.md)。
证据校验和不完整覆盖的处理见 [Decision Plugin](decision-plugins.md)。
交付文件的暂存、审核后发布及可见性见
[交付物发布决策](../decisions/003-reviewed-deliverable-publication.md)。

提示词装配位于 `lensnode/lensnode/agent_runtime/system_prompts.py`；最终答案清理
位于 `lensnode/lensnode/agent_runtime/runtime.py`。运行时中间件与工具的显式装配
契约见 [LensNode 运行时决策](../decisions/002-lensnode-runtime-contract.md)。
