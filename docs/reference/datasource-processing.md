# 数据源同步与转换

配置入口见 [数据源指南](../guides/datasources.md)，执行快照与凭证见
[Plugin 协议](plugin-protocol.md)。本文维护数据源的目录与处理状态约定。

## Git 资源与目录

旧的单资源 `repository` / `project` 配置仍可读取，Runtime 统一输出 `repositories`
集合。`target_path` 表示数据源工作区根目录；新仓库存放在
`target_path/<canonical-resource-id>`，例如 `target_path/owner/repo` 或
`target_path/group/subgroup/project`。

资源 ID 必须是安全 POSIX 相对路径，禁止绝对路径、穿越、空段、反斜杠和不安全字符。
每个资源再次校验 Connection 允许范围。manifest 中的 Git 源身份包含仓库 URL、
分支和仓库相对路径；数据源路径加入资源 ID，避免不同仓库同名文件混淆。

### 旧目录兼容

部署不会自动搬迁旧仓库。LensNode 只有在目录包含 `.git`，并且 marker 属于当前
`datasource_uuid`，或未标记的不完整同步具有精确匹配的 Git remote 时，才识别旧仓库。
支持的旧布局包括根目录单仓库、`target_path/<repository-name>` 组织仓库和
`target_path/group%2Frepository` 编码目录。

已识别的自有仓库原地同步；新增仓库使用 canonical 路径。清理只删除 marker 属于
当前数据源的仓库根目录，保留其他数据源和未标记目录。

单资源数据源有根 manifest、但无法确定 canonical 或自有旧仓库时，返回
`LENS_SOURCE_GIT_LAYOUT_MIGRATION_REQUIRED`，不猜测目录，也不额外 clone 一份。
工作区搜索、Git 历史与最近变更工具递归发现仓库，并在 Git 根目录停止下探。

移动旧仓库或重建同步需要暂停相关调度、校验仓库身份和 marker，再验证同步及代码
分析后清理旧路径。目录兼容不意味着 `source/derived` 物理布局迁移已经完成。

## 增量同步与删除

飞书完整扫描在启用 `delete_missing` 时，第一次发现资源缺失标记 `missing` 并保留
本地内容；第二次连续完整扫描仍缺失时才确认删除。扫描不完整不推进缺失计数或删除。
未启用删除时保留缺失资源。

同步完成发现、差异比较、下载和 manifest 写入后，通过独立任务推进文档转换，
不让长时间转换持续占用独占同步执行范围。转换有独立的进度、取消、恢复和失败状态。
同步成功不表示文档转换完成。相同源内容和选项的失败转换最多自动尝试 3 次，之后
需要显式重试或内容/配置变化。

对外文件目录与引用使用原始资源相对路径，不暴露内部目录或绝对挂载路径。

## 转换进度

Managed Workspace 转换通过任务执行 `metadata` 和数据源 `current_sync` 暴露分阶段
进度。兼容字段 `progress_percent`、`progress_current`、`progress_total` 和
`progress_message` 继续保留。

| 字段 | 含义 |
| --- | --- |
| `phase` | 当前阶段枚举 |
| `overall_progress_percent` | 总进度，只有终态 `SUCCESS` 才报告 100 |
| `phase_progress` | 当前阶段的 `{current, total, unit}` 计数 |
| `progress_counts` | 整体转换生命周期计数 |
| `last_substantive_progress_at` | 上次实际工作或阶段变化的 UTC 时间 |

阶段为 `DISCOVERING_FILES`、`PARSING_DOCUMENTS`、`PROCESSING_EMBEDDED_IMAGES`、
`FINALIZING`、`COMPLETED`，并非每次转换都经过所有阶段。嵌入图像阶段按当前文件
计数，由 `current_file` 标识。LensNode 最后报告 `FINALIZING` 与 99%；控制面在
任务记录为 `SUCCESS` 后报告 `COMPLETED`。

发现文件尚未结束时，未知总量为 `null`，但 `phase_progress.current` 和实际进度时间
仍可前进。发现完成后，在开始文档处理前报告最终总量。

`progress_counts.total` 统计全部发现文件；`candidates` 是策略支持的转换文件。
`processed` 包含所有结果已知的文件，包括不支持的文件。`converted`、`failed` 和
`skipped` 分类转换候选的结果；`unsupported` 分类非候选文件。

`PARSING_DOCUMENTS.phase_progress` 的总量始终是可转换文件数量。
“Processed N/M convertible files”不能证明整体任务完成；终态以任务状态为准，
总进度使用 `overall_progress_percent`。
