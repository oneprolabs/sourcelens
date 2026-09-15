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
