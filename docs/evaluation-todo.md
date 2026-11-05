# SourceLens · Agent Evaluation 集成 TODO

- 依据：外部设计（ChatGPT 共享链接 `t_6ae98bb52c30819182467cae4870b679` 最新一条回复）+ 本仓 `main` 工作区代码核查（2026-11-03）
- 看板：`docs/evaluation-kanban.html`
- 结论：**可集成，按模块化子包落地；但不能照抄原文的「只关联 Run / 新建独立 Judge / 批量排队」三处设计。**

## 0. 与设计原文的 4 处必须修正

| # | 原文建议 | 代码事实 | 修正方向 |
|---|---|---|---|
| M1 | `EvaluationResult` 只关联 Run，不复制执行数据 | `tasks.py:4221 run_retention_task` 按 `retention.run_days`（默认 30）删除终态 Run、诊断与快照 | 关联 + 冻结并存：`source_run` 用 `SET_NULL`，同时冻结 output / citations / trace 摘要 / metrics |
| M2 | 新建独立 LLM Judge 栈（`judges/`） | 已有 decision 层（`decisions.py`：score / choice / noul rubric）、`DecisionTracker` 计量、`answer_supported`/`evidence_strength` 复核，trace 中已有 `deepagents.evidence.verified` | 复用 decision rubric 做 judge；只补 correctness / relevance |
| M3 | Celery 异步批量执行 | `LENSNODE_MAX_CONCURRENT_RUNS` 默认 8；`select_execution_lensnode` 无排队与优先级，无节点直接抛 `LENSNODE_UNAVAILABLE`（`services.py:1380`） | 评估助手硬绑专用 LensNode（`assistant.lensnode`）+ 评估侧并发上限与退避 |
| M4 | 用执行结果判断任务是否完成 | `routing.py:266` 仍把 `plan_execute + action` 的 `evidence_requirement=none` 改写为 `tool_result`，产生 `evidence_unavailable` 的 partial（近 14 天 7 次） | `Run.outcome` 只作参考维度，通过率由 trace + 自有 rubric 派生 |

## 1. 待决策（阻塞实现，不能按假设写）

| ID | 决策 | 为什么现在定 | 建议 |
|---|---|---|---|
| D1 | 产品定位 A / B / C（Agent 质量 / RAG 质量 / 完整评估） | 决定指标体系、页面数量与第一阶段范围 | **B 为核心指标 + A 为交付形态**，C 的轨迹与成本只做确定性采集 |
| D2 | 评估数据源隔离：独立只读副本 vs 复用生产数据源 | 决定用例可复现性、权限边界与同步成本 | 独立只读数据源副本，评估工作区禁止写入 |
| D3 | 评估执行身份：专用系统用户 vs 数据集创建者 | 决定 Session 归属、权限审计与会话列表噪声 | 专用系统用户，Session 打 `evaluation` 来源标记 |
| D4 | Judge 口径：复用 decision 模型 vs 独立 LLM 配置 | 决定成本、一致性与是否需要人工校准 | 复用 decision，独立 model config，首版必须人工校准 |
| D5 | 评估记录保留：跟随 30 天 vs 长期保留冻结快照 | 决定 M1 的冻结字段范围与表增长 | 冻结快照长期保留，Run 侧跟随 retention |

## 2. P0 · 冲突修正与前置（先做，否则后面全部返工）

| ID | 任务 | 依赖 | 完成条件 | 主要落点 | 估 |
|---|---|---|---|---|---|
| EVAL-001 | 冻结快照模型：`source_run` 用 `SET_NULL`，output / citations / trace 摘要 / metrics 内联冻结 | D5 | 删除 Run 后评估报告仍可完整渲染；retention 任务无 `ProtectedError` | `backend/lens/evaluation/models.py`、`tasks.py` | 2d |
| EVAL-002 | 评估 Run 标记与清理隔离：`Run` 增加来源字段（expand 阶段只加不删） | D3 | 评估 Run 不进用户会话列表；retention 与 `session_cleanup` 可识别并单独处理 | `models.py`、`session_lifecycle.py`、`tasks.py` | 1.5d |
| EVAL-003 | 专用 LensNode 绑定 + 评估并发配额与退避 | M3 | 评估批次不占用生产节点；节点不可用时任务进排队态而非 503 | `services.py`、`evaluation/engine/runner.py` | 2d |
| EVAL-004 | 通过率真值派生：从 trace + rubric 判定，不直接读 `Run.outcome` | M4 | 已知 `evidence_unavailable` 样本不被判失败；判定逻辑有单测覆盖 | `evaluation/evaluators/` | 1.5d |
| EVAL-005 | 权限与开关：`admin_console` 下新增 `run_evaluations` permission + feature key | — | 无权限账号 403；关开关后入口不可见 | `models.py` Meta、`accounts/access.py`、路由 meta | 0.5d |

## 3. P1 · 数据与执行（能跑通一次 Benchmark）

| ID | 任务 | 依赖 | 完成条件 | 主要落点 | 估 |
|---|---|---|---|---|---|
| EVAL-101 | `EvaluationDataset`（name / assistant / version / status） | — | CRUD + 版本冻结，改助手配置不影响历史数据集 | `evaluation/models.py` | 0.5d |
| EVAL-102 | `EvaluationCase`（input / expected_answer / expected_facts / expected_sources / tags / config） | 101 | 必填校验；同一数据集内 input 去重 | 同上 | 0.5d |
| EVAL-103 | `EvaluationRun`（config_snapshot / 状态机 / 计数 / 聚合分） | 101 | 快照含模型、Prompt 版本、Skills、MCP、数据源绑定版本 | 同上 | 1d |
| EVAL-104 | `EvaluationResult`（scores / passed / issues / judge_evidence / execution_metrics / 冻结快照） | 001,103 | 单用例可下钻到引用与 trace 摘要 | 同上 | 1d |
| EVAL-105 | `EvaluationMetric`（category / evaluator_type / weight / threshold / config） | — | 权重与阈值可改；禁用指标不参与聚合 | 同上 | 0.5d |
| EVAL-106 | 数据集导入导出（JSONL / CSV）+ 结构校验 | 102 | 非法行整批失败并给行号；导出可复用为回归基线 | `evaluation/datasets/` | 1d |
| EVAL-107 | 迁移与索引（expand/contract 合规） | 101-105 | 空库与存量库各跑一次迁移；蓝绿窗口内旧色不受影响 | `migrations/` | 0.5d |
| EVAL-201 | 复用 `create_execution_run` 的执行封装 | 103 | 不新增 Agent Runtime；参数与线上完全一致 | `evaluation/engine/runner.py` | 1d |
| EVAL-202 | 每用例独立 Session / Run + 幂等键 | 201 | 串行结果可复现；重跑同一用例幂等不重复建 Session | 同上 | 0.5d |
| EVAL-203 | 批次状态机（queued / running / paused / cancelled / failed / completed） | 103 | 暂停与取消真实生效；失败可单用例重试 | 同上 | 1d |
| EVAL-204 | 并发控制、限流与退避 | 003,201 | 并发不超配额；节点繁忙时排队并记录等待时长 | `evaluation/engine/runner.py` | 1d |
| EVAL-205 | 超时与卡死回收（对齐 `run_diagnostics` 的 progress 语义） | 203 | 卡死批次自动落 failed 并保留已完成结果 | `evaluation/tasks.py` | 1d |
| EVAL-206 | Celery 任务入口：`lens.evaluation_run_case` / `lens.evaluation_finalize` | 205 | 任务可重入；worker 重启后可恢复 | `tasks.py` | 0.5d |
| EVAL-207 | 取消后的资源清理（Session 归档、workspace、附件） | 205 | 取消后不留孤儿 Session 与工作区 | `session_lifecycle.py` | 0.5d |

## 4. P2 · 指标与判定（能自动打分且可信）

| ID | 任务 | 依赖 | 完成条件 | 主要落点 | 估 |
|---|---|---|---|---|---|
| EVAL-301 | 确定性指标：工具成功率、轮次、重试、耗时、token | 104 | 全部可从 trace / 计量还原，无需 LLM；缺数据标 `null` 而非 0 | `evaluators/rule_based.py` | 1d |
| EVAL-302 | 引用校验：path / symbol / 起止行对比仓库快照 | 104 | 不存在的路径或错行判失败；大小写与相对路径口径统一 | `evaluators/citation.py` | 1.5d |
| EVAL-303 | 复用 `deepagents.evidence.verified` 得 faithfulness 初判 | 104 | 无复核事件时明确 `inconclusive`，不当作通过 | `evaluators/groundedness.py` | 1d |
| EVAL-304 | 新增 decision rubric：correctness / relevance | D4 | rubric 定义进 `decisions.py` catalog，有 schema 校验 | `decisions.py`、`evaluation/judges/` | 1.5d |
| EVAL-305 | Judge 调用与计量（独立 model config） | 304 | Judge 使用与 Agent 解耦的模型；usage 落库可算成本 | `runtime_decisions.py`、`evaluation/judges/` | 1d |
| EVAL-306 | Judge 输出 schema、校验与失败降级 | 305 | 非法输出不污染分数，标 `judge_failed` 并保留原始响应 | `evaluation/judges/schemas.py` | 1d |
| EVAL-307 | 聚合与阈值（加权、缺失维度不虚构） | 105,301-306 | 缺维度时报告显式标注覆盖度 | `evaluation/services/scoring_service.py` | 1d |
| EVAL-308 | 人工标注与一致性校准 | D4,305 | 20-30 条样本与 Judge 一致率达标后才允许作门禁 | `evaluation/services/` | 2d |

## 5. P3 · 前端与报告（能看、能比）

| ID | 任务 | 依赖 | 完成条件 | 主要落点 | 估 |
|---|---|---|---|---|---|
| EVAL-401 | API：datasets / cases / runs / results / report / compare | 104,307 | 分页、权限、错误码与现有 `lens` 接口口径一致 | `evaluation/views.py`、`urls.py` | 1.5d |
| EVAL-402 | 路由与侧边栏入口 | 005,401 | `/management/lens/evaluation/*` 挂进 lens 子菜单 | `admin/routes.js`、`AdminSidebar.vue` | 0.5d |
| EVAL-403 | Datasets 页（列表 / 详情 / 用例编辑 / 导入） | 401 | 空态、导入失败给行号 | `admin/pages/lens/EvaluationDataset.vue` | 2d |
| EVAL-404 | Experiments 页（发起 / 进度 / 暂停 / 取消） | 401,203 | 进度按用例实时更新，不出现永久「运行中」 | `EvaluationExperiments.vue` | 2d |
| EVAL-405 | Report 页（分布 / 失败聚类 / 证据下钻） | 401 | 结论置顶；失败可跳到 Run 轨迹页 | `EvaluationReport.vue` | 2.5d |
| EVAL-406 | 助手版本对比（A/B，基于 `config_snapshot`） | 405 | 同数据集跨版本对比，配置差异可见 | `EvaluationCompare.vue` | 2d |
| EVAL-407 | 三语文案 en / zh-CN / es | 403-406 | 三语无缺 key；后台命名一致 | `admin/locales/*.json` | 1d |
| EVAL-408 | 空态 / 失败态 / 无权限态验收 | 407 | 三种状态均有截图留痕 | 同上 | 0.5d |

## 6. P4 · 质量与回归（能用、不会退化）

| ID | 任务 | 依赖 | 完成条件 | 主要落点 | 估 |
|---|---|---|---|---|---|
| EVAL-501 | 后端单测：模型 / 状态机 / 权限 / retention 兼容 | 107,203 | 覆盖删除 Run 后读报告、越权访问、取消竞态 | `lens/tests/` | 2d |
| EVAL-502 | dev 栈真实跑通 3 条 smoke 用例 | 206,307 | 产物、分数、引用、耗时全部落库可查 | dev 栈 | 1d |
| EVAL-503 | 前端单测 + ego-browser 验收 | 408 | 关键流程浏览器走查留痕 | `frontend/tests/` | 1d |
| EVAL-504 | 定时回归任务注册 | 502 | `periodic_tasks` 注册；暂停/启停可控 | `periodic_tasks.py` | 0.5d |
| EVAL-505 | 合并前 smoke 门禁 | 504 | 指定数据集 N 条，阈值不达标则阻断合并 | CI / 脚本 | 1d |
| EVAL-506 | 告警接入（批次失败、通过率跌破阈值） | 504 | 复用 `alerts.py` 与飞书通知，不新建通道 | `alerts.py` | 0.5d |
| EVAL-507 | 文档与 `release-notes/<issue>.yaml` | 全部 | 每个 PR 自带片段；设计文档与实现一致 | `release-notes/`、`docs/` | 0.5d |

## 7. 明确不做（本轮边界）

- 不做在线实时评估（每次用户提问都打分）。
- 不做新的 Agent Runtime、Trace Collector 或外部评估服务。
- 不做安全性 LLM 评分门禁；越权与数据隔离只用确定性测试。
- 不做完整的人工标注平台（仅最小标注与导入）。
- 不引入新基础设施：Django + PostgreSQL + Celery + Vue 3 + 现有模型配置。

## 8. 成本与风险提示

- 单 Run 实测 0.3–1.6M tokens、TTFT 15–45s；50 用例批次约 17M+ tokens、数小时。
- 每用例独立 Session 会打断前缀缓存，实际成本高于日常对话。
- Judge 调用是额外成本，且需要人工校准才能作为门禁。
- 评估容器的数据源必须是只读副本，否则评估会写入生产工作区。
