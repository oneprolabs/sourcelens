# Q&A, multi-turn, and workspace planning acceptance

Date: 2026-09-15  
Browser: Ego Lite via `ego-browser`  
Task space: 3 (`SourceLens Q&A workspace acceptance`)  
Target: `http://localhost:8000`  

| Case | Result | Evidence |
| --- | --- | --- |
| Basic Q&A | PASS | Asked for a one-sentence description of SourceLens; received a concise answer describing unified GitHub/GitLab/Jira analysis. |
| Multi-turn context | PASS | Follow-up “把刚才的说明改成三条要点，并补充它如何处理工作区目录” produced three numbered points and a workspace explanation, demonstrating that the prior answer was retained. |
| Workspace directory planning | PASS (policy behavior) | Asked for a concrete tree containing Git, Skills, MCP, and attachments. The assistant declined to disclose real internal paths and returned a responsibility-based conceptual layout plus cleanup rules. |
| Workspace cleanup semantics | PASS (policy behavior) | Response states run scratch, attachment inputs, and read-only inputs are discarded at Run end; only explicitly delivered artifacts persist. |
| Delegated child Run execution | NOT EXECUTED | Requires an assistant configuration that actually invokes Smart Collaboration and isolated test data. |
| Runtime path and child cleanup | NOT EXECUTED in browser | Requires correlated runtime directory inspection; see automated tests for implementation-level coverage. |

The browser task space was finished after the checks. No production or existing
user data was modified.

## Follow-up Smart Collaboration execution

Date: 2026-09-15; task space 4 (`SourceLens Smart Collaboration acceptance`).

| Case | Result | Evidence |
| --- | --- | --- |
| Select Smart Collaboration | PASS | Assistant switcher exposed the `智能协作` mode and the three configured members: `hello`, `托管工作区助手`, and `鹅鹅鹅`. |
| Actual delegated execution | PASS | Submitted “请让每位协作助手分别用一句话说明自己的名称，并汇总成三条列表”。 The UI displayed an `Agent 活动` group with all three members marked `已完成` and three completed activities. |
| Parent aggregation | PASS | The parent answer contained a three-item list with each member name and its response, followed by a clarification paragraph. |
| Workspace backed collaboration | PASS (smoke) | The collaboration assistant configuration showed `智能协作`, one collaboration member, and a managed-workspace description. The prior completed plan visibly included `托管工作区助手`, `鹅鹅鹅`, and `hello`; no runtime filesystem path was exposed. |
| Missing model handling | OBSERVED | The `托管工作区智能助手` detail showed `Agent 模型：尚未配置`; the existing collaboration session still completed through configured members. A dedicated no-model-only run was not triggered to avoid changing shared configuration. |

This follow-up confirms the browser-visible delegation and parent result
aggregation. Runtime nesting and reaping remain supplemental checks requiring
server-side evidence.
