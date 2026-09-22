# 设计稿：Decision Plugin（TypeSafe AI / Jev）——统一决策 seam

- 状态：**P1 → P3 已实施**（2026-09-22；lensnode 1040 测试全绿；backend/前端
  对应测试全绿；见文末「实施记录（P1 → P3）」）
- 日期：2026-09-22
- 评审记录（第 1 轮自查）：见 §12 风险与 §14 待确认补充项；曾误判装配时序
  需提前，经核实 `_prepare_model_and_tools` 早于 gate 调用点，结论为**无需
  提前**，已写入 §5。
- 评审记录（第 2 轮，全文重审）：修复 B1（gate 越权改 `route` 类型，改为
  只提案 `evidence_requirement` 字段 + 重归一化，§6.2.1）与 M/L 系列；补齐
  fail-safe 四 gate 表（§6.3）、resume 缺口（§6.4）、事件与可观测（§6.5）、
  输入裁剪（§6.2.3）、跨进程校验拆分（§6.2.2）、call_id 幂等约定（§6.2）。
- 评审记录（第 3 轮，外部评论评审）：采纳其 4 项建议——P1 收敛为
  `search_needed` 单 gate、`rank_candidates` 移出 `ControlDecisionPolicy`
  归 `DecisionRanker`、God Object 约束入非目标、新增中立契约
  `DecisionResult`；纠正其 1 处实现级错误（gate **不修正 route**，只修正
  evidence 字段，§6.2.1）、补明 provider 无关性现状（传输层仍硬编码，
  P1.5 解锁，§6.2）、seam 表述改为"控制流/排序两条"（§2、§3）。
- 评审记录（第 4 轮）：修 R1（**`value`/`confidence` 混淆**——阈值作用对象
  统一为 `value`，noul 无 `confidence` 字段否则 gate 静默失效，§3/§4.1）、
  R2–R5 内部矛盾（§4/§5 签名、§13 未知 gate 验收、§6.1 未激活 key 语义、
  恢复第 2 轮记录）、R6–R14（P 标注、§9 调用点行、P3 manifest 缺口等）。
- 范围：TypeSafe AI（Jev）绑定进 Assistant 后作为 **Decision Capability**，参与
  控制流决策（gate）与多方案分析（rank）。核心设计目标：**运行时调用点无条件
  调用 seam（无 if/else）**，未配置即当前行为。
- 不升 `protocol_version`，不新建编排引擎。

---

## 1. 背景与现状

### 1.1 TypeSafe 已是普通 Plugin

`plugins/typesafe/plugin.json`（`capability_family: "plugin"`，`plugin_type:
"decision"`，三个原语 `typesafe_noul/choice/score`，`capability:
decision.evaluate`，**P1.5 后均为 `exposure: "internal"`**——只经 gate 调用，
不注册给主模型）+ `plugins/typesafe/runtime.py::build_tool/execute_tool` +
`lensnode/lensnode/plugin_tools.py`/`plugin_runtime.py` 的统一执行链路。

### 1.2 缺口

1. 无「插件类型」概念：`capability_family` 被 V1 硬限定为 `{"plugin"}`
   （`backend/lens/plugins/registry.py:19`）。
2. 控制流决策硬编码在主模型：检索门 `runtime.py:462`、路由
   `runtime.py:1073`。
3. 无统一方案比较原语：现有 guidance 要求"同一 rubric 逐个打分再在代码里排序"
   （`plugins/typesafe/plugin.json:35`），却靠模型自觉、不确定。
4. 宿主 provider 硬编码：`lensnode/lensnode/plugin_http.py:116`。

---

## 2. 核心主张：两条 seam，而不是到处 if

> **两条 seam、调用点永远一行、没有 if/else：**
> **控制流决策**统一经过 `ControlDecisionPolicy`；**排序决策**统一经过
> `DecisionRanker`。未配置时各自是当前实现的直通 / Null 包装；配置后套
> Gate 装饰器 / 实体实现。

```text
              runtime 调用点（无条件）
                     │
                     ▼
        state.decision_policy.needs_retrieval(...)
        state.decision_policy.select_route(...)
                     │
        ┌────────────┴─────────────┐
        │                          │
  ModelDecisionPolicy        GateDecisionPolicy
  （默认，直通现有实现）      （装饰器：按各 gate 声明的时序与 fallback）
                                   │
                             inner = ModelDecisionPolicy
```

两种时序（**非统一**，由 gate 语义决定）：

- `needs_retrieval`：**gate 先行**，`None` 回退 inner（inner 是保底）；
- `select_route`：**inner 先算**，gate 在其结果上校正（inner 给 route 骨架，
  gate 只动 `evidence_requirement`，见 §6.2.1）。

同理，排序用 **Null Object**：`build_decision_ranker()` 未配置时返回
`NullDecisionRanker`（`rank()` 恒 `None`、`as_tools()` 恒 `[]`），
调用点永远 `state.decision_ranker.rank(...)` / `tools.extend(ranker.as_tools())`。

**唯一的分支发生在装配期**（`build_decision_policy` / `build_decision_ranker`
两个 builder 构造时各判断一次有没有绑定），之后所有决策点、所有工具装配都
无条件调用。

### 2.1 运行时决策点全景（接入范围）

接入原则：**只把「语义判断」交给 decision；规则、统计、授权、预算保持确定性
代码。** 这与 TypeSafe guidance 的原则一致（`plugins/typesafe/plugin.json:10`：
"Keep the workflow, known rules, and lookups in code; call Jev for semantic
judgment only"）。运行时真正由辅助模型做的判定只有 **2 处**
（均带 `runtime_control_call=True`），是 seam 的主要接入面。

**A. 接入 Decision Capability**

| 决策点 | 位置 | 当前实现 | 归属 |
|---|---|---|---|
| 检索门 `needs_retrieval` | `routing.py:139` | 辅助模型 | **P1 已实施** `search_needed` |
| 路由 / 证据需求分类 | `routing.py:442` | 辅助模型 | **P3 已实施** 校正 `evidence_requirement`（校正式） |
| 证据充分性 | 主循环后置（`_execute_agent` 收尾） | 主模型 ReAct 自行决定 | **P3 已实施** `evidence_sufficient`（**观测-only**） |
| 答案是否有据 | 主循环后置（同上） | 主模型 ReAct 自行决定 | **P3 已实施** `answer_supported`（**观测-only**） |
| 检索证据重排 | `workspace.py search_workspace`（`_apply_rerank`） | 确定性 `_rank_matches` | **P4 已实施** `evidence_relevance`（host 编排，order-only） |
| 方案比较 / 择优 | 新增 | 无 | **P2 已实施** `rank` / `decision_rank` |

**B. 不接入（确定性，概率化即回归）**

| 位置 | 性质 | 理由 |
|---|---|---|
| `runtime.py:143 _high_confidence_report_route` | 关键词启发式 | 确定性、零成本 |
| `routing.py:523 _fallback_route_decision` | 关键词兜底 | 兜底必须稳定 |
| `routing.py:615 _normalize_route_evidence_capabilities` | 规则修复 | 结构修复，非语义判断 |
| `outcomes.py _evidence_termination_detail` / `_unverified_execution_answer` | 统计判定 | 需可审计、可复现 |
| `planned_evidence.py:731 build_evidence_bundle` | 去重/排序/截断 | 确定性排序；P2 仅可选增强且失败回退 |
| `workspace.py:684 _rank_matches` | 检索排序 | 本身仍确定性；其上叠加 **P4** evidence rerank seam（见 §A），失败回退原序 |
| `capabilities.py` 恢复/预算 | 授权与安全 | 安全边界，不能概率化 |
| `runtime.py:157 _report_scoped_tools` | 工具裁剪 | 授权边界 |

**C. 主循环内模型行为（非宿主 gate）**

- 是否继续检索 / 证据是否足够 → 主模型 ReAct（P3 用软 gate 增强，不硬控）；
- 是否委派子 agent（deepagents task / `assembly.py _fast_subagent`）；
- 是否 `save_deliverable`、plan 内容（`write_todos`）。

**D. 编排 / 后台（生成或确定性，不接入）**

- Smart Collaboration 协调器（`runtime.py:535`）→ 汇总可用 `rank` 择优（P2 可选）；
- 子 agent 委派（`remote_subagent.py`）；
- 模型选择（`gateway_model`，由配置决定）；
- 会话标题（`backend/lens/session_titles.py`）、图片描述（`document_convert.py`）→
  是**生成**，不是决策；
- 上下文压缩（`summarization.py` / `offload.py`）→ token 阈值确定性触发。

**结论**：seam 接入面 = A 的两个 gate（`search_needed` **P1**、
`evidence_requirement` **P3**）+ 证据充分性（**P3**）+ `rank`（**P2**）；
B 明确不接；C/D 仅 P2 的 `rank` 择优可选接入。

---

## 3. 目标与非目标

### 目标

- Manifest 新增 `plugin_type`（`integration` 默认 / `decision`）与 `decisions`
  清单（`control` / `analysis`）。
- 收敛为两个 seam（两条抽象，不合并）：
  - `ControlDecisionPolicy`——**控制流**决策（检索门、路由校正；调用点永远
    一行 `policy.xxx(...)`）。**不含排序。**
  - `DecisionRanker`——**排序**决策（宿主编排 `rank()` + 模型工具
    `as_tools()` → `decision_rank`；未配置为 `NullDecisionRanker`）。
- 未配置时行为与现状逐项一致。

### 非目标

- 不建任意决策 DAG；gate/rank 集合与顺序由宿主固定。
- 不让概率直接跳过证据；低置信一律回退。
- 不新增 `capability_family`，不升 `protocol_version`。
- 不改 Assistant 工具集合与授权语义。
- **不堆 God Object**：`ControlDecisionPolicy` 一方法 = 一个调用点 + 一个
  gate key；排序不进该接口（归 `DecisionRanker`）；控制流方法 P3 后达 4 个
  即为上限，超出则拆为并列 policy，不继续加方法。
- **纯规则 / 无置信度后端不在当前契约范围**：gate 的 threshold/margin 语义
  以 `DecisionResult.value ∈ [0,1]`（noul 标量即校准概率）为前提；
  `confidence` 仅附加元数据，不参与阈值判定（§4.1）。

### 3.1 双模式兼容原则（零配置零影响）

| 维度 | 未配置 | 配置后 |
|---|---|---|
| 检索门（doc/code，gate `search_needed`） | `ModelDecisionPolicy` 直通 `_message_needs_retrieval` | `GateDecisionPolicy` 先试 gate，回退内层 |
| 路由（general_chat，gate `evidence_requirement`） | `ModelDecisionPolicy` 直通 `_select_general_chat_route` | inner 结果上做 `evidence_requirement` 校正 |
| 模型工具 | `NullDecisionRanker.as_tools()` 返回 `[]` | 追加 `decision_rank` |
| 事件/快照/延迟 | 与现状完全相同（零额外） | 新增 `deepagents.decision.*` / `source=decision_*` |

---

## 4. 接口定义

```python
class ControlDecisionPolicy:
    """Uniform control-flow decision seam for one run.

    The runtime always calls these methods; configuration only changes
    which implementation is assembled.

    One method maps to exactly one decision point and one gate key, so
    document/code and general_chat semantics never share a gate:

    - ``needs_retrieval``  -> gate ``search_needed``      (doc/code only)
    - ``select_route``     -> gate ``evidence_requirement`` (general_chat only)
    """

    def needs_retrieval(self, model, question, history) -> bool:
        """Doc/code retrieval gate (``runtime.py:462``)."""
        raise NotImplementedError

    def select_route(self, model, question, **context) -> dict:
        """Route classification, then gate correction (``runtime.py:1073``)."""
        raise NotImplementedError


class ModelDecisionPolicy(ControlDecisionPolicy):
    """Default policy: today's primary-model classification, unchanged."""

    def needs_retrieval(self, model, question, history):
        return _message_needs_retrieval(model, question, history)

    def select_route(self, model, question, **context):
        return _select_general_chat_route(model, question, **context)


class GateDecisionPolicy(ControlDecisionPolicy):
    """Decorator: try the bound gate, then apply its declared fallback.

    Each gate declares one of two fallbacks at assembly time:

    - ``fallback="model"`` — inner policy method keeps today's classifier
      as the default (``search_needed``, ``evidence_requirement``).
    - ``fallback="fixed"`` — no model equivalent exists, so a constant
      fail-safe from config applies (``evidence_sufficient`` ->
      ``True``/not blocking, ``answer_supported`` -> pass).
    """

    def __init__(self, inner, runner, gates): ...

    def needs_retrieval(self, model, question, history):
        value = self.runner.evaluate("search_needed", state=question)
        if value is None:                       # 未绑定/超时/错误/低置信
            return self.inner.needs_retrieval(model, question, history)
        return value >= self.gates["search_needed"]["threshold"]

    def select_route(self, model, question, **context):
        route = self.inner.select_route(model, question, **context)
        value = self.runner.evaluate("evidence_requirement", state=question)
        if value is None:
            return route                         # 保留 inner 结果
        proposal = self._propose_evidence(route, value)
        if proposal is None:                     # 低置信区间：不校正
            return route
        route["evidence_requirement"] = proposal
        return _normalize_route_evidence_capabilities(route)
```

要点：

- `ModelDecisionPolicy` 的方法**就是现有模块函数**，不重写逻辑 → 未配置等价。
- 分支只存在于 `GateDecisionPolicy` 内部（装饰器一处），调用点无感。
- **一个方法一个 gate**：`search_needed` 只进 `needs_retrieval`（doc/code），
  `evidence_requirement` 只进 `select_route`（general_chat），两者语义不同、
  不共用判定，也各自约束适用模式（见 §6.2）。
- `select_route` 是**校正而非替代**，且校正受三层约束（详见 §6.2.1）：
  1. gate **只提案 `evidence_requirement`**，不改 `route` 类型；
  2. 标量 → 枚举的映射由 `_propose_evidence` 定义（低置信区间提案 `None`，
     即不动）；
  3. 提案落地后**先重跑 `_normalize_route_evidence_capabilities`，再调
     `_enforce_route_evidence_invariants`**——后者是本轮从
     `_parse_route_decision` 抽出的 route↔evidence 不变式（`direct_answer` +
     工具证据 → `direct_execute`；`direct_execute` + `none` → 升回），
     由它决定最终值，gate 既写不出非法组合，也无法穿透 route 约束。
- **排序不在此接口**：`rank_candidates` 已移出（第 3 轮评审），排序统一走
  `DecisionRanker.rank(...)`——宿主编排与模型工具 `decision_rank` 共用同一
  实例两张脸，见 §7；未配置时 `rank()` 返回 `None`（保持调用方原序）。

### 4.1 中立结果契约 `DecisionResult`

host ↔ 插件之间只认中立形状；**厂商字段名的投影由插件 `runtime.py` 导出的
`project_decision(tool_key, result)` 完成**（P1.5 落地：typesafe 把自己的
`answers.decision` 投影成 `{kind, value, confidence, legend, usage}`），host
只做中立校验（`decision_contract.validate_decision_result`），
不读 `noul` / `probabilities` 等厂商词汇——换 decision 后端时 host 一行不动。

**`value` vs `confidence`（第 4 轮 R1 纠正，不可混）**：

- **`value`** 是 gate 的判定对象：noul 时即标量本身（校准概率 ∈ [0,1]），
  choice/score 时为概率映射。threshold/margin 作用于它（§4、§6.3）。
- **`confidence`** 仅附加元数据（后端自评的校准度），**noul 分支可缺省**——
  typesafe 的 `_validate_response` 只在 choice/score 投影 `confidence`，
  而两个 control gate 全是 `kind: noul`。若把「`confidence is None` →
  fail-safe」写进 Runner（第 4 轮前的 §4.1 字面语义），**P1 gate 将永远
  回退、静默失效**。故：`confidence` 缺省不构成失败，非法判定只看 `value`
  越界 / 非有限。

```python
@dataclass(frozen=True)
class DecisionResult:
    """Neutral host<->plugin decision contract."""

    kind: str                 # "noul" | "choice" | "score"
    value: float | dict       # noul: 标量；choice/score: 概率映射
    confidence: float | None  # 附加元数据；noul 可缺省；不参与阈值判定
    legend: dict | None       # rubric 文本（score）
    usage: dict               # token 统计
```

- gate（threshold/margin）只对能给出 `[0,1]` 标量的后端启用；纯规则 /
  无置信后端不在当前契约（§3 非目标）。
- `value` 缺失 / 非有限 / 越出 [0,1]（noul 标量）→ `DecisionRunner` 返回
  `None` → fail-safe（§6.3），与超时/错误同路径。

---

## 5. 装配：唯一的分支点

```python
def build_decision_policy(command, config, http_client, plugin_http_pool,
                          emit_event, run_uuid):
    """Assemble the control-decision seam for one run."""

    policy = ModelDecisionPolicy()                     # 默认 = 现状
    gates = command.get("decision_gates") or {}
    if not gates:                                      # 唯一分支
        return policy
    runner = DecisionRunner(command, config, http_client,
                            plugin_http_pool, emit_event, run_uuid)
    return GateDecisionPolicy(policy, runner, gates)


def build_decision_ranker(command, config, http_client, plugin_http_pool,
                          emit_event, run_uuid):
    """Assemble the ranking seam, or a Null ranker when unconfigured."""

    analyses = command.get("decision_analyses") or {}
    if not analyses:
        return NullDecisionRanker()                 # Null Object
    return DecisionRanker(command, config, http_client,
                          plugin_http_pool, emit_event, run_uuid)
```

`state.decision_policy = build_decision_policy(...)` 与
`state.decision_ranker = build_decision_ranker(...)` 在
`runtime.py:971 build_plugin_tools` 旁无条件执行；随后
`state.tools.extend(state.decision_ranker.as_tools())` 无条件执行
（Null 时即 `extend([])`）。

**下发通路**：绑定 → 冻结（`services.py:1887`）→ `command` **顶层**携带
`decision_gates` / `decision_analyses`，每项含 `plugin_key`、
`connection_id`、`gates`。同一 `plugin_key` 至多一条绑定携带非空
`decision_gates` **或 `decision_analyses`**（冻结时校验，多条拒绝保存）——
这同时固定了「gate / rank 由哪个 connection 提供」（§14-7 的默认答案）。

**装配时序（已核实）**：`971` 位于 `_prepare_model_and_tools`
（`runtime.py:776`），由 `_prepare_runtime` 无条件调用（`:760`），而
`_prepare_runtime` 在 `_answer_sync:382` 执行——**早于**两个 gate 调用点
（检索门 `:419`、路由 `:422`），也早于 `_build_agent`（`:425`）。因此
seam 在 `build_plugin_tools` 旁装配即满足所有调用点，无需额外提前。
`plugin_http_pool` 由 `__init__:345` 持有，同样就绪。

> 该时序是前提条件：若日后把 `_prepare_model_and_tools` 拆到 419 之后，
> gate 会因 `state.decision_policy` 未初始化而失败。§13 有回归断言锁定。

注意 `:1071` 的 `_high_confidence_report_route(...) or _select_general_chat_route(...)`
是既有短路——命中关键词启发式时 `select_route` **不会被调用**，gate 自然
不生效（启发式优先级高于 gate，属 §2.1 B 类不接入点的既定语义）。

---

## 6. Control 模式（gate）

### 6.1 决策点（调用点无条件）

| 调用点 | 现状 | 变更 |
|---|---|---|
| `runtime.py:462`（守卫 `general_chat`/`resume_state`/subject 前置条件后） | `if _message_needs_retrieval(...)` | `if state.decision_policy.needs_retrieval(...)` |
| `runtime.py:1073` | `_high_confidence_report_route(...) or _select_general_chat_route(...)` | `_high_confidence_report_route(...) or state.decision_policy.select_route(...)` |

第二行的 `or` **保持原样**：关键词启发式（§2.1 B 类）优先级高于 gate，命中
即短路，policy 不被调用——这是既定语义，不是遗漏。第一行的守卫链（`:444-461`
的模式/恢复/subject/image 检查）原样保留，只替换最后的判定调用。

不再新增 `_needs_retrieval` 之类的分支函数；决策逻辑全部在 policy 内。

**阶段说明**：两个调用点在 **P1 一并切换到 seam**（保证"调用点无分支"一次
到位）；P1 下 `select_route` 的 `evidence_requirement` gate 自 **P3** 生效。
未落地阶段的 control key 可被绑定但**不生效**——注意**不是"无调用点"**
（`:1073` 的 seam 调用 P1 就在跑）：P1 的 registry 不含该 key →
`evaluate` 返回 `None`（`fallback_reason=not_bound`）→ inner 直通、
**零外部调用**，但因 gates 非空照发 `fallback` 事件（admin 据事件可见
"为何绑了没生效"；§6.5 仅 gates 整体为空时零事件）。管理 API 与文档须提示。

### 6.2 配置与执行

`AssistantPluginBinding.decision_gates`（仅 `plugin_type == "decision"` 允许）：

```json
{"search_needed": {"threshold": 0.5, "margin": 0.1, "max_state_chars": 4000}}
```

执行复用现有 tool snapshot 链路（`tool_snapshots.py:15,66` 只要求 run 活跃 +
connection 在 `loaded_plugins` + `call_id` 合法；`decision.evaluate` 已在
`READ_ONLY_TOOL_CAPABILITIES`）。`resolved_config.source = "decision_gate"`。
**不新增 snapshot kind，不升协议。** gate 执行的 `tool_key` 取自**同一
bound connection** 的 manifest `decisions[].tool_keys`，只能是该绑定插件
自身声明的工具——gate 不可能被指向其它插件的工具。

**Provider 无关性（P1.5 已解锁）**：POST 放行不再看 `plugin_key`/路径硬编码，
改由插件 runtime 导出的 **`http_post_paths(endpoint)`** 声明可 POST 的绝对路径
（精确匹配、缺省拒绝；origin 仍由 `http_origins` 限定）。结果包络也不再由 host
解析厂商字段，而由 runtime 导出的 **`project_decision`** 投影到中立形状。
两处都缺省即等价旧行为（只读 GET/HEAD）。见 §10 P1.5、§14-13。

**call_id 唯一性**：合成 `call_id = "gate:<name>:<seq>"`，`seq` 为 Run 内
单调递增计数器（同一 gate 被多次评估时 `seq` 不同）。固定 `seq`（如恒为 1）
会在同 Run 内二次评估时因 args 不同触发 `_validate_idempotent_snapshot`
的 `TOOL_CALL_CONFLICT`（`tool_snapshots.py:214`）。同 `seq` 重试仍幂等
（返回原 snapshot），新 `seq` 才是新评估。

#### 6.2.1 `evidence_requirement` 校正语义（evidence correction policy，P3 已生效）

> **命名与边界（第 3 轮评审纠正）**：本 gate 是 **evidence correction
> policy**——它修正的是 route 决策里的 **`evidence_requirement` 字段**，
> **不是修正 `route` 类型**（routing decision 仍完全由 inner 决定，规则 4）。
> 实现者若按"修正 route"理解，会写出 `direct_execute + none` 这类系统认定
> 非法的组合（即第 2 轮评审的 B1 缺陷）。

`evidence_requirement` 是**枚举**（`none / tool_result / artifact /
user_input`，另有 `adaptive`），gate 却返回标量 `p`；且它与 `route` **双向
耦合**（`routing.py:216-235`：`direct_answer + {tool_result, artifact}` →
route 升级为 `direct_execute`；`direct_execute` / `plan_execute+action` +
`none` → evidence 强制升回）。既有归一化跑在 **inner 内部**
（`routing.py:385`，`_select_general_chat_route` 返回前），gate 校正跑在它
**之后**——若不设契约，gate 会写出系统认定非法的组合、或被不变式悄悄抵消。
因此规则固定为四条：

1. **映射**（区间判定归 `evaluate` 统一完成，§6.2.3——`|p - threshold| <
   margin` 时 policy 收到的已是 `None`，规则 1 只处理区间外取值）：
   `p ≥ threshold + margin` → 提案「需要证据」；`p ≤ threshold - margin`
   → 提案 `none`。低置信不校正，同 §6.3 低置信。
2. **「需要证据」的枚举值**复用既有确定性规则（`routing.py:218-221`）：
   inner route 的 capabilities 含 `artifact_delivery` → `artifact`，
   否则 `tool_result`。
3. **提案落地后先重跑 `_normalize_route_evidence_capabilities`，再调
   `_enforce_route_evidence_invariants`**（P1 实施时从
   `_parse_route_decision` 抽出），由后者裁决 route↔evidence 耦合：
   - 提案 `none` 而 route 不允许（如 `direct_execute`）→ 被强制升回，
     **gate 等效无操作**（预期行为，文档化而非缺陷）；
   - 提案 `tool_result/artifact` 而 route 为 `direct_answer` → route 被升级
     为 `direct_execute`（与现状 inner 行为一致）。
   > 注意：`_normalize_route_evidence_capabilities` 只修 `required_capabilities`
   > 与 `capability_unavailable`，**不含** route↔evidence 不变式；若只重跑它，
   > gate 会留下 `direct_answer + tool_result` 这类非法组合。不变式必须由
   > `_enforce_route_evidence_invariants` 应用（第 5 轮实施发现，原稿判断有误）。
4. gate **永不直接写 `route`**；route 类型始终由 inner 决定。

> 备选（§14-2）：把 gate 结论作为路由分类的**输入**（替代式），可省一次
> 模型调用，但需改 inner 函数签名；本稿先采用「校正 + 重归一化」。

#### 6.2.2 校验职责（跨进程拆分）

backend 与 lensnode 是两个进程/两个包，**无法共享 Python 模块**，故按
「结构在 backend、语义在 lensnode」拆分，语义表只存一份：

| 时机 | 位置 | 校验内容 |
|---|---|---|
| 绑定时 | backend（`serializers` / `assistant_lifecycle`） | **结构**：key 存在于该 manifest `decisions`、字段类型合法、`threshold`/`margin` 仅 `kind ∈ {noul, score}`（`choice` 须 `target_option` 不得配 `threshold`）、`applies_to` 与 Assistant 模式匹配（`search_needed` 仅 `knowledge_qa`/`code_analysis`，`evidence_requirement` 仅 `general_chat`） |
| 冻结时 | backend（`services.py`） | **唯一性**：同 `plugin_key` 至多一条非空绑定 |
| 装配时 | lensnode（`decision_gates.py` registry） | **语义 allowlist**：登记 4 个已知 key 的 `fallback`/阈值适用性与 `enabled_phase`；**配置了但不在已知表**的 key → `fallback_reason=unknown_gate`；**已知但本阶段未激活**（如 P1 绑 `evidence_requirement`）→ `fallback_reason=not_bound`；**插件升级后不再声明该 key**（绑定仍带着、宿主也认识）→ 冻结时写入 `declared: false`，装配期 `fallback_reason=not_declared`。以上均回退 inner、零外部调用，区分只在事件可见性 |

backend **不依赖** lensnode 的 registry；§8 的「落在宿主已知 gate 注册表」
即指 lensnode 装配期这一层。

**唯一的例外：gate phase 的只读镜像（P1.5）**。管理 API 需要在 UI 上标注
「该 gate 本阶段未生效」，而 phase 是宿主语义；因此 backend 保留一份**极小的
只读镜像** `lens/plugins/decisions.py::GATE_PHASES` / `GATE_ACTIVE_PHASE`，
仅用于展示，**不参与任何校验或执行**。漂移后果轻微（只影响 UI 标注；运行时仍
回退 inner）。防漂移由跨包测试
`lensnode/tests/test_decision_gate_phases.py` 守护——它直接读 backend 源码里的
字面量并与 lensnode 的 `GATE_REGISTRY`/`GATE_PHASE` 比对。

#### 6.2.3 state 裁剪（判定输入 / 数据外发面）

**不能只传 `question`**：inner 还用到 history / tools / skills
（`routing.py:1073-1084`），gate 输入过少会让判定质量**低于**被它替代的
inner（§12 风险）。各 gate 输入显式定义：

| gate | 输入字段 | 裁剪 |
|---|---|---|
| `search_needed` | `question` + `history` | history 仅最近 K 轮，总长 ≤ `max_state_chars` |
| `evidence_requirement` | `question` + `history` + `runtime_mode` 标识 + available tool **名**列表 | 同上；不传 skills/工具内容、不传文档正文 |

- 上限取 schema `maxLength`(100000) 与 `max_state_chars` 的较小值。
  **P1 出厂默认**：history `K=4` 轮、`max_state_chars=4000`（§14-10 已定）。
- **不传文档正文、工作区内容**；绑定 decision 插件前须向管理员明示
  「宿主会自动向该插件发送用户问题与对话历史」。
- K 轮数与 `max_state_chars` 默认值已定（§14-10），绑定可覆盖。

`DecisionRunner`（`decision_gates.py`）封装 snapshot→lease→material→
`execute_tool`，先校验得到 **`DecisionResult | None`**（§4.1；`None` = 未绑定 /
超时 / 错误 / 非法响应）；control gate 的 `evaluate` 再提取 **`value` 标量**、
执行阈值±margin 低置信判定，最终向 policy 返回 **`float | None`**
（`None` 一律走 §6.3 fail-safe；`confidence` 不参与本判定，§4.1）。

### 6.3 fail-safe

`DecisionRunner.evaluate` 对超时/错误/非法响应一律返回 `None` → 按 gate
在宿主 registry 中声明的 fallback 处理；低置信（`|p - threshold| < margin`）
同样返回 `None`。

| gate | kind | fallback | `None` 时行为 |
|---|---|---|---|
| `search_needed` | noul | `model` | 回退内层 `_message_needs_retrieval`（`:123-152` 保守语义） |
| `evidence_requirement` | noul | `model` | 保留 inner route 原值，不校正 |
| `evidence_sufficient` | noul | `fixed` | 取 `True`（不阻塞主循环） |
| `answer_supported` | choice | `fixed` | 取 `pass_option`（`"supported"`，不阻断） |

> `evidence_sufficient` / `answer_supported` 没有对应的现有 LLM 判定，
> 不存在可回退的 inner 方法，故只能用固定默认值——装饰器必须支持
> `fallback="model"` 与 `fallback="fixed"` 两种形态，不能一律假设 inner 存在。
> 固定默认值由宿主 registry 声明（`DecisionRunner.default_verdict`），
> 不在绑定配置里（管理员无从决定 fail-safe 方向）。

**P3 已实施：两个后置 gate 是"观测-only"（§14-1 旁的决定）**。它们在
`_execute_agent` 收尾处评估一次（`ControlDecisionPolicy.post_run_checks`），
verdict 写入 `termination_detail["decision_gates"]` 并 emit `gate.start/done`，
**不改变任何控制流**（不做额外检索轮、不阻断作答）——即"不硬控"的字面实现。
先积累"本该拦住多少次"的数据，再决定是否升级为硬控。要点：

- 只评估**该绑定已声明**的 gate；未绑定的 gate 不调用、不发事件、零流量
  （避免每个 Run 都产生 `not_bound` 噪音）。
- `evidence_sufficient`（noul）取 `value >= threshold`；`answer_supported`
  （choice）取概率 argmax 选项。低置信/超时/错误 → 上表固定默认值。
- 未配置任何 gate 时 `ModelDecisionPolicy.post_run_checks` 返回 `{}` →
  `termination_detail` 不变、零事件（零配置零影响）。

### 6.4 resume 一致性

`_route_runtime` 在 `route_was_resumed` 时直接读 `resume_state.route_decision`
（`runtime.py:1063-1064`），不重跑 `select_route` → **路由 gate 的校正结果已随
`route_decision` 随 `save_resume_metadata`（`checkpoint.py:202`）持久化**，
恢复时一致。检索门受 `resume_state` 守卫跳过（`runtime.py:449`），与现状一致。

**P3 已实施（`decision_gates` 列）**：`evidence_sufficient` /
`answer_supported` 的结论不属 `route_decision`，故 `lensnode_run_metadata`
新增 `decision_gates TEXT NOT NULL DEFAULT '{}'` 列（CREATE TABLE +
ALTER-if-missing），由独立的 `save_decision_gates(run_uuid, workspace_path,
verdicts)` 写入（单独 UPDATE 单列，**不覆盖** `route_decision`），
`load_resume_state` 读入 `ResumeState.decision_gates`。
`runtime._post_run_decision_gates`：存量 verdicts 存在则**回放**（不重跑、
不发事件），否则评估一次并持久化。

**由此，预算与 `call_id` 序号的"每作答 vs 每 Run"问题消解**：pre-loop 两个
gate 受 `resume_state` 守卫跳过，post-run 两个 gate 在恢复时回放 →
同一 Run 内 gate 评估至多一次，seq 必为 `1`。唯一残留窗口：上一轮已完成
post-run gate 调用、但在 `save_decision_gates` 之前崩溃 → 本轮以同 seq
不同 args 重试 → `TOOL_CALL_CONFLICT`（409）→ fail-safe 固定默认值
（不崩、不阻断）。已记入 §15「已知遗留」。

### 6.5 事件与可观测

每次 gate 评估发一对事件（命名沿用 `deepagents.*` 约定）：

- `deepagents.decision.gate.start` / `deepagents.decision.gate.done`
- payload：`{gate, source: "decision_gate", verdict: "accept|fallback",
  value, threshold, margin, fallback_reason, duration_ms, call_id}`
- `fallback_reason ∈ {not_bound, not_declared, unknown_gate, timeout, error,
  invalid_response, low_confidence, budget_exceeded}`
- **未配置（`decision_gates` 整体为空 → 装配 `ModelDecisionPolicy`）时不发
  任何事件**（零配置零影响，§3.1）；已配置但某 key 回退/未激活则照发
  `verdict=fallback`（含 `not_bound`/`not_declared`/`unknown_gate`，§6.1/§6.2.2）——
  两者的分界是"gates 是否非空"，不是"该 key 是否命中"。
- 聚合面：按 `fallback_reason` 分维度出 fallback 率指标（§12「静默回退」
  风险的缓解）；`PluginInvocation` 审计带 `source`，可与模型工具调用区分。
- **后置 gate（P3）**：同样发一对事件；其**消费面**是
  `termination_detail["decision_gates"]`（观测-only，不参与控制流）。
  `source` 一律 `decision_gate`；rank 侧为 `decision_rank`。

---

## 7. Analysis 模式（rank）

### 7.1 场景

多候选方案择优、候选答案评分排序、证据语义重排、Smart Collaboration 成员产出
择优。

### 7.2 Manifest 声明

```json
{"key": "plan_quality", "mode": "analysis", "kind": "score",
 "summary": "...", "rubric": ["weak", "acceptable", "strong"],
 "tool_keys": ["typesafe_score"]}
```

**`rank` 的 kind 限制**：排序要求一个可比较的标量。

- `kind: "score"`：rubric 即有序刻度，聚合为 `sum(index * probability)`。
- `kind: "choice"`：manifest 的 `rubric` 承载 choice 选项列表，`target_option`
  必须取自该列表；该选项的概率即"好的程度"，排序直接取 `target_option`
  的概率。

故 `rank_eligible` 接受 `kind == "score"`，或 `kind == "choice"` 且
`target_option ∈ rubric`；不满足者在绑定时被拒
（`backend/lens/plugins/decisions.py::rank_eligible`）。

### 7.3 模型工具 `decision_rank`

`DecisionRanker.as_tools()` 在配置了 analysis 绑定时返回模型工具
`decision_rank`（未配置时 `NullDecisionRanker.as_tools()` 返回 `[]`）：

```text
输入:  decision=<analysis key>, candidates=[{label, content}], instructions
执行:  对每个 candidate 用同一 rubric 走 tool snapshot（call_id rank:<d>:<i>）
输出:  {ok, decision, ranked:[{label, score, probability, legend}], usage}
```

- 聚合确定性：`score` 按 `sum(index * probability)` 降序；并列以**主导概率**、
  再以**输入序号**稳定打破（`decision_contract.aggregate_ranked`）。
- 有界（P2 已定，§14-9）：**单次最多 8 候选**、**单候选超时 3s**、
  **每 Run 最多 2 次 rank**、Run 内按 (plugin, tool, content, instructions) 缓存；
  部分失败返回可用结果 + `failed:[{label,reason}]`。
- 只返回类型化分数与概率，不生成自然语言理由。
- `instructions` 为空时回退到 analysis 的 `summary`，再回退到
  「Score the candidate against this ordered rubric: …」，保证可评分。

### 7.4 宿主编排（可选，P2 或其后，见 §14-3）

- Smart Collaboration（`runtime.py:1048`）：`state.decision_ranker.rank(...)`，
  返回 `None` 时保持协调器原选择（与模型工具 `decision_rank` 共用同一
  ranker 实例）。
- 证据重排（**P4 已实施**）：`search_workspace` 在 `_rank_matches` 之后、
  `matches[:max_results]` 截断之前，经 `_apply_rerank` 调用
  `state.decision_ranker.rank_evidence(...)`（host 预留 key
  `evidence_relevance`）。order-only、重排窗口 = top-8、与模型工具
  `decision_rank` 共用每 Run 2 次预算，失败/未绑定回退确定性原序。

---

## 8. Manifest 变更

```json
{
  "key": "typesafe", "version": "1.4.0", "protocol_version": 1,
  "capability_family": "plugin", "plugin_type": "decision",
  "decisions": [
    {"key": "search_needed", "mode": "control", "kind": "noul",
     "applies_to": ["knowledge_qa", "code_analysis"],
     "tool_keys": ["typesafe_noul"]},
    {"key": "evidence_requirement", "mode": "control", "kind": "noul",
     "applies_to": ["general_chat"],
     "tool_keys": ["typesafe_noul"]},
    {"key": "evidence_sufficient", "mode": "control", "kind": "noul",
     "applies_to": ["knowledge_qa", "code_analysis", "general_chat"],
     "tool_keys": ["typesafe_noul"]},
    {"key": "answer_supported", "mode": "control", "kind": "choice",
     "applies_to": ["knowledge_qa", "code_analysis", "general_chat"],
     "tool_keys": ["typesafe_choice"]},
    {"key": "plan_quality", "mode": "analysis", "kind": "score",
     "rubric": ["weak", "acceptable", "strong"],
     "tool_keys": ["typesafe_score"]},
    {"key": "evidence_relevance", "mode": "analysis", "kind": "score",
     "rubric": ["irrelevant", "related", "helpful", "direct_answer"],
     "tool_keys": ["typesafe_score"]}
  ]
}
```

> 上例即当前 `plugins/typesafe/plugin.json`（1.4.0；三个原语均为
> `exposure: "internal"`，无 `assistant_guidance`）。

- `plugin_type` 可选、缺省 `integration`；`decision` 时才允许 `decisions`。
- 校验：key 唯一、`mode ∈ {control, analysis}`、`kind` 合法、analysis 必须有
  `rubric`、`tool_keys` 引用本 Manifest 已声明工具。
- `control` 决策必须有非空 `applies_to`，且 key 必须存在于本 manifest
  `decisions`（backend **结构**校验）。宿主语义 allowlist（已知 gate key：
  `search_needed` / `evidence_requirement` / `evidence_sufficient` /
  `answer_supported`、`fallback`、阈值适用性）在 **lensnode 装配期**校验
  （§6.2.2）；未知 key 不在绑定时报错，而是装配期视为未配置、回退 inner
  并记 `fallback_reason=unknown_gate`。
- **P3 落地时的 manifest 缺口（第 4 轮 R8）**：host allowlist 认识
  `evidence_sufficient` / `answer_supported`，但上例只声明了 2 个 control
  条目——P3 上线这两个 gate 时必须**同步在 `decisions` 增加对应条目**
  （含 `tool_keys`，并定义其 `applies_to` 模式集，backend 结构校验要求
  非空），**再次 SemVer 升版**；否则绑定校验（key 须在 manifest）直接拒绝。
- **`fallback` 与固定默认值由宿主 registry 拥有**（fail-safe 方向是宿主语义，
  插件无从知晓该往哪边退）；manifest 只声明 `applies_to` 与 `kind`。
- `rank` 可用性：`kind == "score"`，或 `kind == "choice"` 且有 `target_option`
  （见 §7.2）。
- 版本冻结：改 `plugin.json` 必须升 SemVer（`1.0.0 → 1.1.0`）。
- **展示语义（P1.5 UI 修正）**：`plugin_type` 是**类别**（`integration` /
  `decision`），`capability_family` 是**执行族**，两者都**不是"消费方式"**。
  连接管理页按**类别**展示（decision → "Decision"，不再从 `tools.length`
  派生 "Tool"）；"能当工具调用 / 能当数据源 / 能提供 gate/rank" 属**消费方式**，
  留给具体绑定页。TypeSafe 与 GitHub/Jira 的天然差别正在此：它虽有可被模型
  调用的原语（`exposure: model`），但类别是 decision。
- **工具级 `exposure`（P1.5）**：`tools[]` 每项可声明
  `"exposure": "model" | "internal"`（缺省 `model`）。`internal` 工具
  **不注册给主模型**（`build_plugin_tools` 跳过），但仍留在冻结快照的
  `tools[]` 中用于授权（`_frozen_tool`），仅供 gate/rank 调用；
  `assistant_guidance.topics[].tool_keys` **不得**引用 `internal` 工具
  （backend 校验拒绝）。decision 插件因此可以是"零模型工具"的纯后端。
- **runtime 导出（P1.5，非 manifest）**：可选导出
  `http_post_paths(endpoint)`（可 POST 的绝对路径，精确匹配）与
  `project_decision(tool_key, result)`（厂商 → 中立投影）；`build_tool`
  改为按需（仅在暴露 `model` 工具时必需），`execute_tool` 仍是唯一必需 RPC。

---

## 9. 代码落点汇总

| 层 | 位置 | 变更 |
|---|---|---|
| Manifest | `plugins/typesafe/plugin.json` | `plugin_type` + `decisions` |
| Registry | `backend/lens/plugins/registry.py` | `PLUGIN_TYPES`、`plugin_type`、校验 `decisions`；工具级 `TOOL_EXPOSURES`/`exposure`、guidance 仅可引用 `model` 工具 |
| Gate registry | `lensnode/lensnode/agent_runtime/decision_gates.py` | 宿主侧 key→`fallback`/阈值适用性语义表（**仅 lensnode**，backend 不依赖，§6.2.2；单一来源，非散落 if） |
| 绑定模型 | `backend/lens/models.py:1215` | `decision_gates` / `decision_analyses` JSON（迁移 `0063` / `0064`） |
| 绑定校验 | `backend/lens/plugins/decisions.py`（新）+ `serializers.py` | gate **结构**校验（`plugin_type`、key∈manifest、threshold 适用性、`applies_to`、同 plugin 唯一）；语义 allowlist 不在此（§6.2.2） |
| 冻结 | `backend/lens/services.py` `build_loaded_plugins` + 命令下发 | 解析 `tool_key`/`kind`/`exposure` 进 loaded 条目；命令顶层 `decision_gates`；条目条件 `tools or gate_config`（P1.5：允许纯 decision 插件） |
| 快照 | `backend/lens/plugins/tool_snapshots.py` | `resolved_config.source ∈ {model_tool, decision_gate, decision_rank}`（P1：JSON 字段，**未加 `PluginInvocation.source` 列**） |
| 管理 API | `backend/lens/views/plugins.py` | 暴露 `plugin_type` / `decisions` / 工具 `exposure` |
| 契约 | `lensnode/lensnode/decision_contract.py` | 中立 `DecisionResult` + `validate_decision_result`（校验插件投影，不解析厂商字段） |
| Seam | `lensnode/lensnode/agent_runtime/decision_policy.py` | `ControlDecisionPolicy` / `ModelDecisionPolicy` / `GateDecisionPolicy` / `build_decision_policy`（控制流）+ `DecisionRanker` / `NullDecisionRanker` / `build_decision_ranker` / `decision_rank` 工具（P2） |
| Gate runner | `lensnode/lensnode/agent_runtime/decision_gates.py` | `GATE_REGISTRY`（fallback/phase/instructions）+ `DecisionRunner`（3s 超时、每 Run 8 次、事件、调 `project_decision`） |
| 不变式 | `lensnode/lensnode/agent_runtime/routing.py` | `_enforce_route_evidence_invariants`（从 `_parse_route_decision` 抽出，供 gate 校正复用） |
| 传输 | `lensnode/lensnode/plugin_http.py` | **P1.5 已解**：POST 放行改按 runtime 导出 `http_post_paths`（精确路径、缺省拒绝），删 `typesafe`/`/v1/systemone` 硬编码 |
| Runtime 契约 | `lensnode/lensnode/plugin_package_loader.py` | `execute_tool` 必需；`build_tool` / `http_origins` / `http_post_paths` / `project_decision` 可选 |
| 装配 | `runtime.py`（`_prepare_model_and_tools`，早于 `_maybe_answer_without_retrieval` / `_route_runtime`） | `build_decision_policy`（**唯一分支点**）；`:1048` 的 ranker 归 P2 |
| 调用点 | `runtime.py` `_maybe_answer_without_retrieval` / `_route_runtime`（`:1048` P2 ranker） | 无条件 `policy.xxx(...)` / `ranker.rank(...)`，无 if |
| Resume | `lensnode/lensnode/checkpoint.py` | `decision_gates` 列 + `save_decision_gates` + `ResumeState.decision_gates`（P3 已实施，见 §6.4） |
| 后置 gate | `decision_policy.post_run_checks` + `runtime._post_run_decision_gates`（`_execute_agent` 收尾） | `evidence_sufficient` / `answer_supported` 观测-only，verdict 入 `termination_detail["decision_gates"]` |
| 指标 | `backend/lens/plugins/tool_snapshots.py` `resolved_config.source` | gate/rank 快照与模型工具调用按 `source` 区分；凡按 snapshot 计数的展示/诊断不得混计（`PluginInvocation.source` 列 P2 再加） |
| 工具元数据 | `lensnode/lensnode/plugin_tools.py` | metadata 加 `plugin_type`；跳过 `exposure=internal`；`build_tool` 缺失时按需报错 |
| 前端 | Assistant 绑定页 + 连接管理 + `admin/locales/*` | P1.5：连接管理按**类别**显示 Decision（不再派生 Tool）；绑定页按 manifest `decisions` 渲染 gate 启用/阈值/容差，写入 `plugin_bindings[].decision_gates`；`internal` 工具不进任何 UI 列表。analysis(rank) 配置 UI 仍后置 |

---

## 10. 分阶段落地

1. **P1（Control 最小闭环）**：`plugin_type` + `decisions` + seam
   （policy/runner/`DecisionResult`）+ gate registry（lensnode 侧）+
   **两个调用点切到 seam** + **`search_needed` 单 gate** + fail-safe +
   事件/审计。**无前端 UI**（配置走绑定 API 裸 JSON）。最干净的
   vertical slice：fallback 是一次平铺的模型调用，不涉及不变式交互。
   **默认参数（§14-9/10 已定）**：`threshold=0.5`、`margin=0.1`、
   `K=4`、`max_state_chars=4000`、单次超时 3s、每 Run 8 次 gate 评估。
2. **P1.5（Provider 解锁）— 已实施**：POST 放行从硬编码
   （`typesafe` + `/v1/systemone`）改为插件 runtime 导出的
   **`http_post_paths(endpoint)`**（绝对路径精确匹配、缺省拒绝）；
   结果包络改为 runtime 导出的 **`project_decision`**（host 不再解析厂商字段）；
   并解掉"decision 插件必须同时是模型工具插件"的隐含要求——
   `build_tool` 改按需、manifest 工具加 `exposure: model|internal`、
   `build_loaded_plugins` 不再要求 `tools` 非空。**SemVer 升版即可，不升
   `protocol_version`**。详见 §15 P1.5 记录。
3. **P2（Analysis）— 已实施**：`DecisionRanker` + `NullDecisionRanker` +
   `decision_rank` 模型工具 + 确定性聚合（`aggregate_ranked`）+ 边界
   （8 候选 / 3s / 每 Run 2 次 / 缓存 / 部分失败）；`decision_analyses`
   绑定字段 + 迁移 `0064`；绑定 UI 同批（analysis 勾选 + rubric 只读展示）。
   Smart Collaboration 编排点**未接**（§14-3 已定：留作独立后续）。
   详见 §15 P2 记录。
4. **P3（Control 扩展）— 已实施**：`evidence_requirement` 激活（校正契约，
   §6.2.1；phase 改为**排序**判定：`PHASE_ORDER[phase] > PHASE_ORDER[active]`
   → `not_bound`，`GATE_ACTIVE_PHASE = "P3"`，四个 gate 全部生效）；
   `evidence_sufficient` / `answer_supported` 以**观测-only**落地（§6.3）；
   resume 回放（§6.4）；manifest 升 `1.3.0`（新增两条 control 决策）。
   详见 §15 P3 记录。
5. **P4（证据重排）— 已实施**：`DecisionRanker` 增并发打分（≤8 候选并行、
   保持确定性聚合）与 `rank_evidence`；`search_workspace` 增 `_apply_rerank`
   缝（`_rank_matches` 之后、截断之前）；`build_agent_tools` 透传
   `state.decision_ranker`（装配前移到工具构造之前）；manifest 升 `1.4.0`
   （新增 analysis `evidence_relevance`）。order-only、top-8、与模型工具
   `decision_rank` 共用每 Run 2 次预算、失败/未绑定回退原序。
6. **后续（需协议升级）**：decision 端点进协议、正式
   `capability_family: "decision"`。

---

## 11. 兼容性与迁移

- 未配置时 `build_decision_policy` 返回 `ModelDecisionPolicy`（直通现有函数），
  `build_decision_ranker` 返回 `NullDecisionRanker`（`rank()` 恒 `None`、
  `as_tools()` 恒 `[]`）；模型调用、工具集合、事件序列、快照、
  延迟与现状逐项一致。
- 新增**双模式回归测试**：同一请求在「无绑定」与「有绑定但 gate 全回退」下，
  断言事件序列、模型调用次数、工具集合与基线一致。
  **已落地（P1.5 review 补测）**：
  `lensnode/tests/test_decision_seam_runtime.py`（真实 `_prepare_runtime` 走一遍，
  对比两种模式的事件序列/模型调用数/工具集；另有 gate 接受时不调 inner 分类器）。
- 发布：TypeSafe 升 `1.1.0` → 发布并设 active → 回归
  `lensnode/tests/plugins/test_typesafe_tools.py`、
  `lensnode/tests/test_plugin_http.py`、
  `backend/lens/tests/test_plugin_registry.py`、
  `backend/lens/tests/test_plugin_bindings.py`，并新增 seam 专项测试。

---

## 12. 风险与取舍

| 风险 | 影响 | 缓解 |
|---|---|---|
| seam 抽象过度、增加间接层 | 中 | 接口 2 个方法（P3 后 4 个；排序独立于 `DecisionRanker`，God Object 约束见 §3）；默认实现直通现有函数，不复制逻辑 |
| gate 输入少于 inner → 判定质量**低于现状** | 中 | §6.2.3 显式输入裁剪（含 history/模式/工具名）+ fail-safe 回退 + fallback 率观测 |
| 宣称可换 provider 但**传输层硬编码** | 中 | P1.5 解锁（`plugin_http.py:113-121` 改按 `plugin_type` + manifest 可选 endpoint；不升 `protocol_version`）；解锁前文档明示执行层仅 TypeSafe（§6.2） |
| threshold/margin 与后端**校准绑定** | 中 | per-binding 可配；换 decision 后端必须重测；默认值标注适用后端（§14） |
| 概率判定抖动 | 中 | 阈值 + margin；不确定即 `None` 回退 |
| 远程决策延迟/成本 | 中 | 有界并行、超时、Run 内缓存、候选上限 |
| 误用概率绕过证据 | 高 | 固定 allowlist + fail-safe；概率非 ground truth |
| 复用 tool snapshot 被改坏 | 中 | 测试锁定 call_id 约定与 idempotency |
| 决策插件读取敏感上下文 | 高 | §6.2.3：只传 `question` + 截断 history（+模式/工具名），不传文档正文与 skills 内容；绑定时向管理员明示外发范围；沿用插件 secret 边界 |
| `plugin_type` 与 `capability_family` 混淆 | 中 | 文档固定：前者业务分类，后者执行族，互不授权 |
| gate 长期失败**静默回退**，管理员无感 | 中 | 聚合 fallback 率指标 + 告警；trace 记 `fallback_reason` |
| gate 与路由分类**并存** → +1 次外部调用/延迟 | 中 | 有界超时；§14-2 已定保持校正式 |
| P3 gate 结果未持久化 → resume 重跑/不一致 | 中 | **已修**：`decision_gates` 列 + 回放（§6.4） |
| 后置 gate（观测-only）**每个 Run 多两次外部调用** | 中 | 只在**已绑定**该 gate 时才评估（未绑定零流量）；与其它 gate 共用每 Run 预算；verdict 不改控制流，失败即固定默认值 |
| 观测-only 的 verdict **无人消费** → 纯开销 | 低 | 有意为之：先积累"本该拦住多少次"的数据再决定是否上硬控（§6.3） |
| `decision_rank` 与 provider 自带单次工具并存混淆模型 | 低 | **已消解**：provider 的 `typesafe_score` 为 `internal`，不进模型工具集 |
| 与 `_high_confidence_report_route` 短路交互（`:1071 or`） | 低 | 启发式优先、gate 不生效为既定语义；测试锁定顺序 |
| gate 绕过 `CapabilityBoundaryMiddleware` 预算 | 中 | 每 Run 独立 gate 预算，超预算固定回退不报错 |

---

## 13. 验收标准

- **零配置等价**：未绑定时模型调用、工具集合、事件序列、快照与现状逐项一致；
  配置但全回退时除 `deepagents.decision.*` 事件外结果一致。
- **无分支实现**：运行时决策点与工具装配代码中不存在
  `if decision_gates`/`if configured` 之类分支；分支仅在
  `build_decision_policy` / `build_decision_ranker` 内（policy/registry 内部
  按 gate key 分派属设计内，不算调用点分支）。
- **Control（P1）**：绑定 `search_needed` 后命中走决策、否则回退，trace 记录
  `fallback_reason`；每次评估产生 `deepagents.decision.gate.*` 事件对，
  未配置时零事件；绑了未激活 key → `not_bound` 回退（零外部调用）且事件可见。
- **后置 gate（P3，已落地）**：`evidence_sufficient` / `answer_supported`
  verdict 写入 `termination_detail["decision_gates"]`，未绑定时零事件、不改
  `termination_detail`；resume 有存量 verdicts 时回放、不重跑。
  测试：`lensnode/tests/test_decision_gates.py`（`evaluate_choice` /
  `default_verdict` / `post_run_checks` 观测-only 与固定默认值）、
  `lensnode/tests/test_decision_seam_runtime.py`（record + replay）、
  `lensnode/tests/test_checkpoint.py`（`decision_gates` 往返）。
- **校正契约（P3）**：`evidence_requirement` 提案经
  `_normalize_route_evidence_capabilities` 裁决后，不产生 route/evidence
  非法组合；提案 `none` 而 route 不允许时等效无操作（不变式测试锁定）。
- **Analysis（P2，已落地）**：`decision_rank` 对 N 个候选返回确定性排序；部分失败
  返回可用结果 + `failed`。测试：`lensnode/tests/test_decision_rank.py`（聚合/边界/
  缓存/工具/Null）、`lensnode/tests/plugins/test_typesafe_gate.py::
  test_rank_executes_through_the_real_plugin_pipeline`（真实 HTTP + `source=decision_rank`）、
  `lensnode/tests/test_decision_seam_runtime.py::
  test_bound_analysis_exposes_the_decision_rank_tool`（装配后进模型工具集）。
- **审计边界（P1）**：gate/rank 在 `PluginInvocation` 审计中可识别（`source`），
  token/材料不入快照、日志、模型上下文。
- 非法 `plugin_type`、未声明 `tool_key`、冻结时同 key 多条非空绑定、超候选
  上限返回稳定错误；**未知 gate 不报错**——回退 inner 并记
  `fallback_reason=unknown_gate` 事件（配置错误可见但不中断，与 §6.2.2/
  §8 一致）；每 Run gate/rank 受预算约束，超预算回退不报错。
- 同一 gate 在一 Run 内多次评估生成不同 `call_id`，不触发
  `TOOL_CALL_CONFLICT`；同 `seq` 重试幂等返回原 snapshot。
- **Resume（P3）**：恢复后 gate 决策与恢复前一致，不产生新的 gate 外部调用
  （`route_decision` 已随 `save_resume_metadata` 持久化；新 gate 结果显式并入）。
- 凡按 snapshot 计数的展示/诊断（`PluginInvocation`、trace、UI）不把
  gate/rank 计入模型工具调用数（按 `source` 过滤）。
- 时序回归：断言 `state.decision_policy` 在 `:419` 首次被访问前已初始化
  （锁定 `_prepare_model_and_tools` 早于调用点的顺序）。
  **已落地（P1.5 review 补测）**：
  `lensnode/tests/test_decision_seam_runtime.py::
  test_decision_policy_is_assembled_before_the_gate_call_sites`（spy 访问器，
  已用"移除装配点"变异验证会失败）。gate 的**真实 HTTP 链路**（经 pool +
  `http_post_paths`）另由 `lensnode/tests/plugins/test_typesafe_gate.py` 覆盖。
- 模式约束：`search_needed` 绑到 general_chat、`evidence_requirement` 绑到
  doc/code 时，绑定校验拒绝。
- **契约对象（P1）**：noul gate 按 `value`（非 `confidence`）判定——构造
  `DecisionResult(value=p, confidence=None)` 必须能通过阈值判定
  （锁死 §4.1 R1 语义，防回归成静默失效）。

---

## 14. 待确认

**范围与形态**

1. ~~`evidence_requirement` 按既定节奏放 **P3**，还是因 general_chat 主路径
   价值提前到 P1/P2？~~ **已实施（P3）**：按既定节奏在 P3 激活（`GATE_ACTIVE_PHASE
   = "P3"`），未提前。两个后置 gate 的动作语义另按 §6.3 定为**观测-only**。
2. ~~gate 与路由分类**并存**（+1 次外部调用/延迟）还是**替代**其中的证据判定？~~
   **已定（P3）**：保持**并存/校正式**（inner 先分类，gate 只在高/低置信区间
   改 `evidence_requirement`）；替代式会改 inner 签名、风险更高。
3. ~~`decision_rank` 是否同时作为宿主编排点（Smart Collaboration / plan 择优）？~~
   **已定（P2）**：`rank()` 宿主编排 API 已交付并测试，但**不接** Smart
   Collaboration 协调器（改既有协作行为、风险面不同，留作独立后续）。
4. 是否需要**全局 kill switch**（一键停用所有 gate/rank，不依赖逐个解绑）？
5. ~~`decision_rank` 是否计入用户可见的 step / 工具调用展示？~~
   **已定（P2）**：**计入**——它是正常的模型工具调用，照常出现在 step/工具列表；
   gate 仍按 `source` 排除（不是模型调用）。

**绑定与版本**

6. `decision_gates` 钉的是**绑定时** manifest 版本还是 active 版本？插件升级
   后旧绑定如何处置（失效 / 保留 / 需重新绑定）？
   **已定**：钉**绑定时**版本做校验；插件升级后若删/改 key，运行时由装配期
   `unknown_gate` 回退兜底（§6.2.2），不强制重新绑定。
7. MCP adapter 绑定（`mcp_bindings`）是否参与 gate 提供？普通多绑定的默认
   **已定**：同一 `plugin_key` 至多一条非空绑定，冻结时校验
   （§5），并以 `connection_id` 钉住执行通道（§6.2）。
   **已定（MCP 项）**：**P1 不参与**——gate 仅支持普通 connection，MCP 作为
   decision 后端后续按需再评估。
8. ~~`decision_rank` 与 provider 自带 `typesafe_score` 并存，会让模型混淆或
   重复计数吗？绑定 analysis 时是否隐藏单次工具？~~
   **已消解（P1.5）**：TypeSafe 转纯 decision 后 `typesafe_score` 为
   `internal`，不进模型工具集，二者不会并存，无需隐藏。

**取值与可观测**

9. ~~候选数上限、单候选超时、每 Run 的 gate/rank 预算取值。~~
   **rank 已定（P2）**：单次最多 **8 候选**、单候选超时 **3s**、每 Run 最多
   **2 次 rank**、Run 内缓存（§7.3）。
   **P1 gate 已定**：单次超时 **3s**、每 Run 最多 **8 次** gate 评估，
   超限回退不报错（§10 P1）；rank 侧（P2）另定。
10. `search_needed` / `evidence_requirement` 阈值与 margin 默认值，是否暴露
    给管理员调节；gate 输入的 history 轮数 K 与 `max_state_chars` 默认值
    （§6.2.3）；**换 decision 后端时阈值必须重测**（校准绑定，§12）。
    **已定（P1 出厂默认，绑定可覆盖）**：`threshold=0.5`、`margin=0.1`、
    `K=4` 轮、`max_state_chars=4000`；上线后按 fallback 率再调。
11. TypeSafe 对固定 model 是否**确定性可重放**？否则审计需记录实际返回值。
12. `ControlDecisionPolicy` 放在 `state` 上还是随 `runtime` 实例传递
    （时序已核实两处皆可，取舍在可测性与序列化面）。

**P1.5（Provider 解锁）**

13. ~~manifest 可选 endpoint/origin 放行字段的具体形态~~ **已定（P1.5 实施）**：
    不用 manifest 字段，改为 runtime 导出 `http_post_paths(endpoint) -> (绝对
    路径,)`；host 侧精确匹配 `urlsplit(url).path`，缺省为空即拒绝 POST；
    路径必须以 `/` 开头、不含 `?`/`#`/`\`/`..`，否则 `PLUGIN_HTTP_SCOPE_INVALID`。
14. ~~用哪个非 typesafe 的 decision 后端/插件作为解耦验收样例。~~
    **已定（P1.5 实施）**：**TypeSafe 自身转为纯 decision 后端**——三个原语
    `exposure: "internal"`、移除 `assistant_guidance`（模型侧不再可见），
    只经 gate 调用；解耦验收由"纯 decision 运行时（无 `build_tool`、全
    internal 工具）"单测 + TypeSafe 自身共同覆盖。见 §15。

---

## 15. 实施记录（P1 → P3，2026-09-22）

### 已落地

- **Manifest**：`plugins/typesafe/plugin.json` 升 `1.1.0`，加 `plugin_type:
  "decision"` + 两个 control decision（`search_needed` →
  `["knowledge_qa","code_analysis"]`、`evidence_requirement` →
  `["general_chat"]`，均 `tool_keys: ["typesafe_noul"]`）；
  `runtime.py` / `control.py` 的 `PLUGIN_VERSION` 同步升版。
- **Backend registry**：`PLUGIN_TYPES` / `DECISION_MODES` / `DECISION_KINDS`、
  `InstalledPlugin.plugin_type` / `.decisions`、`_validate_decisions`
  （key 唯一、mode/kind、`tool_keys` 必须引用本 manifest 工具、control 必须有
  `applies_to`、score 必须有 rubric）。
- **Backend 校验**：新增 `lens/plugins/decisions.py`
  （`validate_decision_gates` / `resolve_decision_gates` / `control_decision`，
  默认 `threshold=0.5`、`margin=0.1`、`max_state_chars=4000`）；`serializers.py`
  接入字段级结构校验 + `_validate_decision_bindings`（`applies_to` 匹配
  Assistant capability、同 `plugin_key` 至多一条非空 gate 绑定）。
- **Backend 冻结/下发**：`build_loaded_plugins` 解析 `tool_key`/`kind` 进 loaded
  条目；命令顶层新增 `decision_gates`；`tool_snapshots` 落
  `resolved_config.source`（allowlist `model_tool|decision_gate|decision_rank`）。
- **Backend 管理 API**：`plugins` list/manifest 暴露 `plugin_type` / `decisions`。
- **迁移**：`lens/migrations/0063_assistantpluginbinding_decision_gates.py`。
- **LensNode**：新增 `decision_contract.py`（`DecisionResult` +
  `project_decision_result`，只读 `value`）、`decision_gates.py`
  （`GATE_REGISTRY`、`DecisionRunner`：3s 超时、每 Run 8 次、`gate:<name>:<seq>`
  call_id、start/done 事件、`not_bound`/`unknown_gate`/`budget_exceeded`/
  `timeout`/`error`/`invalid_response`/`low_confidence`）、`decision_policy.py`
  （`ControlDecisionPolicy` / `ModelDecisionPolicy` / `GateDecisionPolicy` /
  `build_decision_policy`）；`runtime.py` 装配 seam + 两个调用点无条件调用。
- **不变式**：从 `_parse_route_decision` 抽出
  `_enforce_route_evidence_invariants`，gate 校正后
  `_normalize_route_evidence_capabilities` → `_enforce_route_evidence_invariants`
  两段执行（见下方偏差 1）。

### P1.5 追加：TypeSafe 转为纯 decision 后端

按"类别 ≠ 消费方式"的判断（TypeSafe 与 GitHub/Jira 天然不同），TypeSafe 不再
作为模型工具插件暴露：

- `plugin.json`：3 个原语 `exposure: "internal"`；**移除整个
  `assistant_guidance`**（topics 引用 internal 工具会被 registry 拒绝，且该文案
  本就是"教模型调用 Jev"，纯 decision 后端不应有）。
- 结果：模型侧看不到任何 TypeSafe 工具；只有 gate（P1 `search_needed`、P3
  `evidence_requirement`）+ 未来 `rank` 经 `execute_tool` 调用其原语；原语仍留
  在冻结快照 `tools[]` 中用于授权（`_frozen_tool`）。
- `services.build_loaded_plugin_skills`：**无模型可见工具的插件不再生成
  advisory 虚拟 Skill**（否则模型上下文里仍会出现"TypeSafe Plugin"及其工具）。
- 前端：连接管理按**类别**显示 "Decision"（不再派生 "Tool"）；`Mcp.vue` 与
  助手绑定的工具列表排除 `internal`；绑定页对 `plugin_type === "decision"`
  的插件始终可选（否则纯 decision 插件会从 UI 消失、配不了 gate）。
- **绑定 UI（P1.5 收尾）**：助手绑定页对 decision 连接渲染 gate 配置——
  按 manifest `decisions`（`mode: control` 且 `applies_to` 含当前 capability）
  列出每个 gate 的启用开关 + 阈值/容差输入（默认 0.5/0.1），勾选连接时按默认值
  自动启用适用 gate；capability 变更时清理不再适用的 gate。
  `Assistants.vue` 的载入/提交两处补齐 `decision_gates` 透传。
  analysis(rank) 配置 UI 仍后置。
- **gate phase 提示**：backend 镜像 `GATE_PHASES`/`GATE_ACTIVE_PHASE`（只读、
  仅展示），manifest API 返回 `gate_phases`/`gate_active_phase`；绑定页把非当前
  阶段的 gate 置灰并标注「未生效（P3）」，且**不自动启用**它们（避免"配了不生效"）。
  跨包一致性由 `lensnode/tests/test_decision_gate_phases.py` 守护。

### 与设计稿的偏差（P1 范围裁剪）

1. **不变式归属修正**：原稿认为重跑 `_normalize_route_evidence_capabilities`
   即可裁决 route↔evidence，实测该函数不含此不变式（它在
   `_parse_route_decision` 内）。故抽出 `_enforce_route_evidence_invariants`
   共享；§4/§6.2.1 已同步更正。
2. **`PluginInvocation.source` 列未加**：P1 只把 `source` 写入
   `ExecutionSnapshot.resolved_config`（JSON，无迁移）；审计列留待 P2 与 rank
   一起加（避免为单一来源再加一次迁移）。
3. **`decision_analyses` 未加**：绑定模型 P1 只加 `decision_gates`；rank 相关
   字段与代码随 P2。
4. **`applies_to` 用真实 capability 名**：设计稿早期写作 `document_qa`，
   实际 Assistant capability 为 `knowledge_qa`；manifest 与校验按实际值。
5. **命令下发拆出 `build_decision_gates`**：实施中曾漏下发
   `plugin_version`，会让 runner 恒判 `unknown_gate`（静默失效）；抽出纯函数
   `services.build_decision_gates` 并加断言锁定（无需 Redis 即可测）。

### 验证状态

- lensnode：`1040 passed` ——
  `tests/test_decision_gates.py`（seam/runner/契约；P3 增 `evaluate_choice` /
  `default_verdict` / `post_run_checks` 观测-only 与固定默认值）、
  `tests/test_decision_seam_runtime.py`（**装配时序 + 双模式等价 + gate 接受
  跳过 inner + post-run record/replay**，真实 `_prepare_runtime`）、
  `tests/test_decision_rank.py`（聚合/边界/缓存/工具/Null）、
  `tests/plugins/test_typesafe_gate.py`（**gate 与 rank 的真实 HTTP 链路**：
  snapshot→lease→material→provider POST→投影；未声明 POST path 必回退）、
  `tests/test_decision_gate_phases.py`（跨包 phase 镜像守护）、
  `tests/test_checkpoint.py`（`decision_gates` 往返）。
- backend：`test_plugin_registry.py`（decision manifest + `exposure`/guidance +
  manifest API 的 `gate_phases`）、
  `test_plugin_bindings.py::DecisionGateBindingTests`（含命令下发、纯 decision
  插件无模型工具/无虚拟 Skill、**gate key 被移除后助手仍可编辑**）、
  `test_typesafe_provider.py`（纯 decision 断言）、
  `test_plugin_tool_snapshots.py`（`source` 仅在非 model_tool 时落库 +
  未知 source 拒绝）。
- **未在本地跑完的部分**：依赖 Redis（channel layer）与 Docker 的 dispatch /
  快照端到端测试本机无法执行（本机无 docker daemon、无 redis-server）——
  含本轮新增的 3 个 snapshot `source` 测试；需在 dev 容器内跑 `pytest` 复验，
  并按 AGENTS 约定：新增 migration 后 `docker restart sourcelens-api-dev`，
  lensnode 代码改动后 `docker restart sourcelens-lensnode-dev`。

### P2 实施记录（Analysis / rank）

- **Manifest**：`plugins/typesafe/plugin.json` 升 `1.2.0`，加 analysis 决策
  `plan_quality`（`kind: "score"`、`rubric: ["weak","acceptable","strong"]`、
  `tool_keys: ["typesafe_score"]`）；`runtime.py` / `control.py` 同步升版。
- **Backend**：绑定模型加 `decision_analyses`（迁移 `0064`）；
  `decisions.py` 加 `analysis_decision` / `rank_eligible` /
  `validate_decision_analyses` / `resolve_decision_analyses`；
  serializer 字段级 + `_validate_decision_bindings`（声明性、可排序性、
  同 plugin 唯一性覆盖 gates+analyses）；`build_decision_analyses` 下发命令
  顶层 `decision_analyses`。
- **LensNode**：`decision_contract` 加 `rank_score` / `rank_tiebreak` /
  `aggregate_ranked` / `RankedCandidate`；`decision_gates.run_decision_tool`
  抽出供 gate 与 rank 共用；`decision_policy` 加 `analysis_bindings` /
  `DecisionRanker` / `NullDecisionRanker` / `build_decision_ranker` /
  `decision_rank` 工具（`as_tools()`）；runtime 装配 ranker 并把工具并入
  `state.tools`；快照 `source=decision_rank`。
- **UI**：绑定页为 decision 插件渲染 analysis 勾选（只列 `kind == "score"`，
  展示 rubric 只读）；`Assistants.vue` 载入/提交透传 `decision_analyses`。
- **设计修正**：§7.2 的 `choice` + `target_option` 路径**不可实现**（manifest
  无 choice 选项列表字段），P2 收紧为 score-only；已改稿并说明放开条件。
- **未接**：Smart Collaboration 协调器（§14-3 已定，留作独立后续）。

### P3 实施记录（Control 扩展 / resume）

- **Manifest**：`plugins/typesafe/plugin.json` 升 `1.3.0`，新增两条 control 决策
  `evidence_sufficient`（noul，`applies_to: [knowledge_qa, code_analysis,
  general_chat]`，`tool_keys: [typesafe_noul]`）与 `answer_supported`（choice，
  同 applies_to，`tool_keys: [typesafe_choice]`）；`runtime.py`/`control.py`
  同步升版。
- **激活机制**：phase 由"相等判定"改为**排序判定**
  （`PHASE_ORDER = {"P1": 1, "P3": 3}`，`GATE_PHASE = "P3"`），
  `search_needed`(P1) 与三个 P3 gate 全部生效；backend 只读镜像
  `GATE_ACTIVE_PHASE = "P3"`（跨包守护测试覆盖）。
- **`evidence_requirement` 激活**：契约已就位（§6.2.1 四条 +
  `_enforce_route_evidence_invariants`），本轮仅放开 phase；保持**校正式**。
- **后置 gate（观测-only）**：`GATE_REGISTRY` 补 instructions/criteria/
  `pass_option`；`_gate_arguments` 加 choice 分支（`criteria` JSON）；
  `DecisionRunner` 重构出 `evaluate_choice` / `default_verdict` /
  `_gate_result`（`evaluate` 行为不变）；`ControlDecisionPolicy.post_run_checks`
  默认 `{}`、`GateDecisionPolicy.post_run_checks` 只评估已绑定 gate；
  `runtime._post_run_decision_gates` 回放/评估 + 持久化，verdict 入
  `termination_detail["decision_gates"]`。**不改控制流**。
- **Resume**：`lensnode_run_metadata.decision_gates` 列（DDL + ALTER-if-missing）、
  `save_decision_gates`（单列 UPDATE，不覆盖 `route_decision`）、
  `ResumeState.decision_gates`、`load_resume_state` 读取并校验。
- **UI**：choice gate 不再带 `threshold`/`margin`（`gateDefaults(gate)` 按 kind
  分派；模板对 choice 隐藏阈值/容差输入）——否则后端
  `_normalize_gate_config` 会以 "does not accept a threshold" 拒绝绑定。
- **未接**：Smart Collaboration 协调器（§14-3 已定）。

### 已知遗留（review 记录，未修）

- ~~预算/`call_id` 序号是**每次作答**而非每 Run~~ **P3 后已等价**：pre-loop
  两个 gate 受 `resume_state` 守卫跳过、post-run 两个 gate 恢复时回放 ⇒
  每 Run 至多一次评估（seq 恒为 1）。仅剩"post-run 调用完成、但写
  `decision_gates` 前崩溃"这一窄窗口会以同 seq 不同 args 重试 → 409 →
  fail-safe 固定默认值（不崩、不阻断）。
- 插件升级移除 gate key 时按 `not_declared` 回退：绑定仍带着该 key、宿主也认识
  它，但当前插件版本已不再声明，故与 `not_bound`（阶段未激活）、`unknown_gate`
  （宿主不认识该 key）三者区分。
- `evidence_requirement` 的 state 在截断后追加工具名，可超 `max_state_chars`
  （受插件 schema 100000 兜底）；现 gate 已生效，属**低危**（仅插件侧多收几个
  字符的工具名列表），待顺手修。
