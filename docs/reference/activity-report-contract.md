# 工作活动汇总契约

此契约用于通用对话中的工作总结、活动报告及日报等请求，并要求助手已加载至少一个
批量活动工具。普通故障报告、代码总结及其他运行模式不进入确定性活动报告链路。

## 来源与覆盖

Jira 使用 Server 的 startAt 或 Cloud 的 nextPageToken 分页；查询覆盖窗口内的
创建、更新、解决日期，再用精确时区时间过滤。默认最多 500 条、允许最多 1000 条，
每页最多 100 条，每个项目最多 10 页。重复页面或游标、预算耗尽及后续页失败均标记
possibly_truncated，保留前页记录。

GitLab 的全部项目模式枚举 token 可见项目，不额外限制 membership；每页 100 个，
最多 200 个项目。每项资源每页最多 50 条、最多 10 页；提交上限 1000 条，MR/issue
上限 500 条，默认结果上限 500 条。创建、更新、合并、关闭日期按精确窗口核验。

GitHub 每项资源最多 10 页、500 条。提交采用提交者日期，PR 按更新时间排序并核验
创建、更新、合并、关闭日期；issue 分别查询 updated、created、closed 后按编号去重，
避免历史关闭记录因后续更新落在窗口外而漏掉。搜索 incomplete_results 仍是覆盖限制。

这些是有界快照查询，不保证全部历史操作覆盖。列表页预算耗尽、响应错误、单工具结果
超过 900 KB 后的尾部裁剪，以及缺少任一预期来源，都必须进入报告覆盖限制。它们不能
被解释为无工作、无错误或已获得完整证据。

## 证据与交付

运行时在工作区私有 `.sourcelens/activity-evidence/<Run hash>` 保存来源投影与查询参数。
缓存按 provider、connection、plugin version、工具名和完整参数隔离；相同查询并发串行，
恢复后可复用。账本不保存 token、lease material 等认证内容；终态或 checkpoint 过期清理。
HTML 和可下载 evidence JSON 保存最终使用的有界来源快照供核对。

全部批量来源已记录后，ActivityReportMiddleware 在下一次模型调用前生成报告并结束
agent graph；render_activity_report 工具与终态也使用同一渲染器。模型草稿被账本结果
替代，旧候选清单被最终 HTML/JSON 清单替代。内容指纹和候选 UUID 相同可避免重复上传，
证据 recheck 清空候选后重新上传。沿用既有 staged deliverable 协议，不新增数据库字段。

记录按来源账号列出，不用显示名推测跨系统身份。Jira 当前负责人、issue/MR 作者与实际
更新者明确区分。相同提交 SHA 可以合并镜像来源，不按标题合并不同 SHA 或 backport。
当前 Done/closed 状态不能推断窗口内完成；仅来源时间字段能支持当期解决/关闭/合并。

来源文字全部 HTML 转义，链接仅接受 HTTP(S)。报告包含时间窗口和覆盖限制；材料不足
时运行结果为 partial。快照未提供的历史操作者和变更记录仍不能补造。

## 验证

`test_activity_pagination.py` 使用真实 httpx MockTransport 验证多页、重复页面、短页游标
和后续页失败。`test_activity_reports.py` 验证持久复用、日期/身份语义、转义、结果预算、
最终候选替换和同步/异步真实 graph 在来源就绪后不再调用模型。

原 Run 的离线回放只使用原轨迹已保存的来源材料；未取得的历史页不能通过回放恢复。
本地测试与回放不代表代码已部署或原 Run 已在线重跑。
