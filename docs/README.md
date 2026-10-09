# SourceLens 文档

本目录维护当前平台的使用方式、运行约定和接口边界。开发设计讨论、分阶段实施计划、
临时验收记录和已失效方案不作为当前文档保留；历史内容可通过 Git 查看。

## 使用指南

| 文档 | 内容 |
| --- | --- |
| [助手与运行](guides/assistants.md) | 标准助手、Smart 协作、成员权限、Run 时限 |
| [数据源](guides/datasources.md) | GitHub/GitLab、匿名公开访问、飞书连接和资源选择 |
| [每日研发汇总示例](guides/daily-summary.md) | 通过已有授权工具编写运营助手提示词 |

## 接口与运行参考

| 文档 | 内容 |
| --- | --- |
| [Plugin 协议](reference/plugin-protocol.md) | 包布局、Manifest、Connection、执行快照、凭证和绑定 |
| [Decision Plugin](reference/decision-plugins.md) | 门禁、证据校验、排序与回退 |
| [数据源同步与转换](reference/datasource-processing.md) | 增量同步、兼容目录、转换生命周期和进度字段 |
| [Agent 集成](reference/agent-integration.md) | 外部编码 Agent 的 REST 调用、身份、引用与授权范围 |
| [Run 实时轨迹](reference/run-trajectory.md) | 查询检查点、SSE、事件顺序和运行诊断 |
| [提示词与证据边界](reference/prompt-boundaries.md) | 指令优先级、上传材料、引用和输出清理 |

## 运维与验证

| 文档 | 内容 |
| --- | --- |
| [蓝绿部署](operations/blue-green-deployment.md) | 单主机升级、回滚、迁移兼容和 nginx 切流 |
| [前端验证](operations/frontend-verification.md) | 单元测试、构建、ego-browser 验收及已有测试资产 |

## 有效架构决策

| 文档 | 内容 |
| --- | --- |
| [LensNode 运行时契约](decisions/002-lensnode-runtime-contract.md) | 显式中间件、工具、提示词及依赖升级约束 |
| [审核后发布交付物](decisions/003-reviewed-deliverable-publication.md) | 文件暂存、最终发布、重试和旧节点兼容 |

项目启动与常用命令见 [项目说明](../README.zh-CN.md)。`images/` 保留项目说明使用的图片。

## 维护方式

- 一个主题保留一份主要说明，相关主题使用链接引用，避免复制接口和状态定义。
- 行为说明以当前代码和协议为依据；示例不代表已部署配置或实际验收结果。
- 接口或运行约定变化时同步更新对应文档；新增文档必须加入此索引。
- 移动或删除文件时修复仓库引用，并检查相对链接。
