---
name: engineering-report
description: Produces a per-person engineering work report — for each person: what they completed, what's in progress, risks, and an overall comment — for a given period (daily standup, weekly, monthly, quarterly, yearly), by reading commits, PR/MR descriptions, comments, and Jira updates across GitHub, GitLab, and Jira. Observation scope (GitHub org, Jira project, GitLab range, name mapping) comes from the bound workspace guide, not hardcoded here — works for any organization without modification. Person-level only, not a team/project rollup (that's a different skill). Use this proactively whenever the user asks for a "daily report", "日报", "周报", "月报", "季报", "年报", "研发报告", "晨会汇总", "团队每个人的工作情况", "谁昨天做了什么", "上周大家的进展", or any request to see what specific people on an engineering team did/are doing/are stuck on — even casual phrasing like "帮我看看这周XX干了啥" or "整理一下团队最近的进度" without the word "report" in it.
---

# Engineering Activity Report（人物维度）

**定位**：这个技能只做一件事——给定一个统计周期，逐人列出"完成的 / 正在做的 / 风险 / 评语"。
只按人物维度输出，不做团队汇总、不做项目整体进展分析（那是另一个技能的事）。GitHub/GitLab/Jira
只是取材料的地方，每条事项要标注来源，但不要按来源分表/分列，主体永远是"人"。

## 数据获取原则

使用本次已加载 Plugin 提供的工具。Plugin 负责声明可查询的能力、参数、返回结果和授权范围；本技能
只决定怎样组合这些能力来完成按人汇总，不假设任何 CLI、组织、项目或凭证存在。

## 第一步：统计周期与时间戳

先取当前时间，换算成 Asia/Shanghai（UTC+8），按用户措辞判断周期类型：

| 用户说 | 周期类型 | 默认取值 |
|---|---|---|
| "日报"/"晨会"/未提周期 | day | 上一个工作日（周一→上周五，周六/周日→上周五） |
| "周报"/"上周" | week | 上一个完整 ISO 周（周一～周日） |
| "月报"/"上个月" | month | 上一个完整自然月 |
| "季报"/"上季度" | quarter | 上一个完整自然季度 |
| "年报"/"去年" | year | 上一个完整自然年 |

说"这周"/"这个月"等当前进行中周期时，统计从周期开始到现在；用户给了具体日期/区间时，直接按
用户说的来，不套用上表。

区间必须是完整周期，从当地时间 **00:00:00 到 23:59:59（UTC+8）**，转成 UTC 传给各工具时用
带完整时间的 ISO 8601 时间戳，不要只传日期（裸日期在多数工具里是整天含边界，会多算一天）。
回答开头用一句话写清楚周期类型和具体起止时间。

## 第二步：先读工作区指引，拿到本次的观测范围

**本技能不内置任何具体公司的组织名、project key、人名映射**——同一套技能要能在不同公司的
SourceLens 部署上直接用，公司专属的东西必须来自外部配置，不能写死在这里。每次运行开始前，先
读取当前绑定的工作区指引（workspace guide），从里面获取本次观测范围参数：
- GitHub 观测范围（组织 login，或"不限组织"）
- Jira 观测范围（project key，或"不限 project"）
- GitLab 观测范围（是否限定 group/project；实例地址是 `http://` 还是 `https://`）
- 账号到真实姓名的映射表（如果有）、产品/项目归属规则（如果有）

工作区指引没有覆盖到的细节，按"不限制、token 能看到什么就用什么"处理，不要自己假设一个范围。
下面各小节里的 `<org>`、`<PROJECT_KEY>`、`<GITLAB_HOST>` 都是占位符，实际值从工作区指引里取，
**不要把任何一次运行里读到的具体组织名/project key 当成以后也适用的固定值写死在推理过程里**——
下次换一家公司用这个技能，这些值会完全不同。

**"读取"必须是真的打开文件，不是列目录。** 用文件读取工具打开工作区指引技能目录下的
`SKILL.md`（例如 `skills/<workspace-guide-slug>/SKILL.md`）并读出正文——只做一次
`ls`/目录清单、看到文件存在就当作"已读取"是不够的，那样账号映射表等内容根本没进入上下文。
这必须是本技能执行的第一个动作，先于核实组织/project、抓取任何数据源之前完成。长对话中途如果
因为处理 GitHub/GitLab/Jira 的分页、报错重试等分心，进入第四步整理前要自问一遍"工作区指引的
正文我是不是真的读过"——没有把握就重新读一次，不要凭印象或猜测继续，尤其是账号映射表，宁可
重读一次，也不要在没读到的情况下写"当前无账号映射表"。

## 第三步：抓材料之前，先读两个文件

**这一步必须排在抓材料之前，不要等写最终答案时才想起来。** 用 `read_file` 依次打开本技能目录
下的这两个文件，读出完整正文：

1. `OUTPUT_FORMAT.md`——按人整理材料时的证据判断和已确认身份合并规则，以及最终交付物的格式要求
   （默认产出 HTML 看板文件，由现有运行时自动交付；用户明确要求仅文字时遵从用户）。
2. `FETCH_STRATEGY.md`——第四步的证据获取顺序、补查条件和停止规则。

提前读是因为：如果等抓完材料再读，很容易凭旧印象直接动笔或顺序抓取；提前知道最终要产出什么、
怎么并行抓，后面的步骤才能对着目标来，不用返工。这一步跳过、只做过 `ls`/目录清单、或者凭"技能
应该是这样"的印象往下走，都不算完成。

## 第四步：抓材料——按 FETCH_STRATEGY.md 渐进取证

先执行每个可用数据源的活动汇总工具；如果工具描述表明它覆盖本次授权资源和时间窗，一次调用就是
该源的默认取证边界。不要为了“更完整”而自动按仓库、项目、人员、页码或条目拆分成列表查询，也不
要为了拆分数据源而调用 `task` 子代理。

仅在汇总结果明确标为覆盖不全、返回错误、或确实缺少完成按人归类所必需的证据时，才按
`FETCH_STRATEGY.md` 进行有限补查。补查必须针对已经发现的具体资源或编号，不能把“补查”扩展为
新的全范围扫描。标题、编号、状态只是索引；有必要补查时才读取描述或评论来说明实际工作内容。

按 `FETCH_STRATEGY.md` 顺序读完已保存的规范化证据，不猜分页游标；完成标志出现后停止。
模型结合证据和来源限制决定报告结论，不机械套用固定评价或因修订措辞重新遍历证据。

## 第五步：按人整理——区分角色，标注来源，分清完成没完成

（账号与映射表的合并/标注规则见第三步读过的 `OUTPUT_FORMAT.md`，这里不重复。）

- 以人为主键合并三个来源的材料。平台 API 自带真实姓名字段的（如 GitLab 作者 display name）
  直接用；工作区指引里的映射表能精确匹配到的账号，翻译成真实姓名展示。
- **每一条事项标注来源**：`(GitHub)` / `(GitLab)` / `(Jira)`，事项句尾可带编号作为附带引用
  （如 "（PROJ-6790）"、"（PR #354）"）。
- **区分角色，不要把"谁关闭/标记状态"当成"谁做的事"**：发现/报告人（issue reporter）、
  实现人（PR/MR/commit 作者）、验证/收尾人（Jira 里做验收、标完成但代码不是他写的）是三种
  不同角色，同一件事里可能是不同的人。典型场景：Jira 任务指派给 A、A 标记完成，但对应的
  MR 是 B 提交合并的——记为"A：验证/收尾"，"B：实现"，不要合并说成"A 完成了这个任务"。
- **区分"完成"和"进行中"**：统计期内有可核实合并、关闭或解决事件的事项算观察到完成；当前状态为 Done 不代表本期完成，缺少事件时间应说明不确定性；仍在 review、进行中、或只有讨论没
  落地结果的算进行中，两者不能混在一起。

## 第六步：整理内容与产出

写入前重新读取 `OUTPUT_FORMAT.md`，尤其是在上下文被摘要后。模型先整理每个人的真实内容，再通过
本技能的 `scripts/render_report.py` 复用其中的 HTML/CSS 骨架生成看板，写入 `/deliverables/` 由现有
运行时自动交付。用户明确要求仅文字时按用户要求输出，不生成 HTML。不要凭印象重新设计页面。

通过现有 `run_skill_script` 调用 `engineering-report` 的 `scripts/render_report.py`，参数为
`["--output", "{{SOURCELENS_DELIVERABLE_DIR}}/engineering-report.html"]`，stdin 为模型组织的 JSON：

```json
{
  "title": "统计区间与报告标题",
  "coverage": "实际时间窗、各来源覆盖情况与缺口",
  "people": [
    {
      "name": "证据或工作区映射确认的姓名/账号",
      "completed": ["模型根据证据整理的完成事项及来源/编号"],
      "in_progress": ["模型根据证据整理的进行中事项及来源/编号"],
      "risks": "模型根据证据分析的风险与不确定性",
      "comment": "模型根据证据作出的评价",
      "observed_completion": true
    }
  ]
}
```

每个人必须独立提供，不能为了缩短合并多人。事项可用 `{"text":"事项与来源编号","url":"https://..."}`
保留可点击的原始来源链接。脚本只负责转义和套用本技能的页面骨架，不代替模型判断业务结论。
条目作者和当前负责人不是关闭、合并或更新操作者；只有 `event_actor.verified=true` 才能归因具体动作。
缺少操作者时只描述事项发生了什么，不编造谁执行了动作。

## 注意事项

- 严格按工作区指引配置的范围来（GitHub 组织、Jira project 等），不要扩大范围，也不要把某次
  运行读到的具体值当成固定值硬编码进后续推理。
- 只保留支持结论的精简证据，不要把大段原始 API 输出或评论全文堆进最终回答。
- 只做人物维度报告，不要额外输出团队汇总/项目整体进展这类内容。
- 第三步的 `OUTPUT_FORMAT.md` 和 `FETCH_STRATEGY.md` 必须在抓材料前实际读取，不能跳过——
  一个决定合并规则和最终交付物的格式，一个决定怎么并行抓材料。
