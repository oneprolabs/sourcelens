# Decision Plugin 运行约定

Decision Plugin 提供有界的结构化判断；主 Agent 模型继续负责推理、文本和工作流。
包、Connection、快照与授权共用 [Plugin 协议](plugin-protocol.md)。

## 声明与绑定

Manifest 使用 `plugin_type: "decision"`，`decisions` 声明 `key`、`mode`、`kind`
和 `tool_keys`。`mode` 分为 `control` 与 `analysis`，`kind` 为 `noul`、`choice`
或 `score`。Control 声明适用的 Assistant capability；分析排序使用共享 rubric，
Choice 排序的 `target_option` 必须来自声明选项。

助手绑定使用 `decision_gates` 和 `decision_analyses`，控制面验证声明、适用能力与
配置后，将工具身份及参数写入运行快照。自动绑定采用 Manifest 默认值；手工绑定
采用保存配置。单次运行不能从可变插件名称推断决策能力。

TypeSafe 的原语为 `exposure: internal`，模型不能直接调用；纯内部工具插件不生成
面向模型的能力导航 Skill。排序通过宿主 `decision_rank` 工具或证据重排入口调用。

## 控制门禁

| 门禁 | 用途 | 无有效判断时 |
| --- | --- | --- |
| `search_needed` | 文档/代码问答是否需要检索 | 原模型策略 |
| `evidence_requirement` | General Chat 的证据要求校正 | 保留原路由 |
| `evidence_sufficient` | 回答是否限于已取得证据 | 固定默认判断 |
| `answer_supported` | 回答是否有证据支持 | 固定默认判断 |
| `evidence_strength` | 主要事实中最弱的证据等级 | 固定默认判断 |

`evidence_requirement` 先取得原路由，只提议证据要求，再由宿主重新归一化能力和
校验路由不变式；它不能直接改写路由类型或授予工具访问权。

标量判断使用 `value`，不是可选的 `confidence`。阈值默认 0.5、容差默认 0.1，
一般输入上限默认 4000 字符；具体绑定/Manifest 可以覆盖。Choice 不接受标量阈值。
缺失、非有限、越界、声明不匹配、超时、执行错误和预算耗尽都有稳定回退。

当前宿主调用超时为 3 秒，门禁预算为每 Run 16 次。超时关闭观察范围，不能保证已在
后台执行的线程或远端请求立即停止。判断结果和回退原因进入运行轨迹。

证据门禁本身返回判断；证据中间件依据完整性决定是否进行一次补充取证/重答。
材料被截断、压缩或只有预览时标记检查不完整，不能宣称验证通过，也不能仅凭不完整
检查触发重新生成。完整材料下仍未通过的回答保留 partial outcome 与验证诊断。
最终文件发布规则见 [交付物发布决策](../decisions/003-reviewed-deliverable-publication.md)。

## 分析与排序

宿主对候选项使用相同 rubric，校验结构化结果后确定性聚合排序。当前单批最多 8 个
候选、调用超时 3 秒、每 Run 最多 2 批；模型排序与 `evidence_relevance` 证据重排
共享预算。未绑定分析时不会增加该类外部请求。

Score 的等级位置按零起始索引解释，概率分布表示等级权重；Choice 的概率是相互
竞争的选项。概率集中程度不代表判断正确性。排序结果不能替代证据检索或授权检查。

## 恢复与参考代码

运行快照与 checkpoint 保存决策配置和已产生的 verdict，恢复时复用已保存状态。
不把当前绑定配置追溯应用到历史执行。外部调用完成与 checkpoint 写入之间存在
崩溃窗口，不能承诺外部判断恰好执行一次。

- 控制面声明与绑定：`backend/lens/plugins/decisions.py`。
- 宿主门禁：`lensnode/lensnode/agent_runtime/decision_gates.py`。
- 策略与排序：`lensnode/lensnode/agent_runtime/decision_policy.py`。
- 结果校验：`lensnode/lensnode/decision_contract.py`。
- 默认 Plugin 声明：`plugins/typesafe/plugin.json`。
