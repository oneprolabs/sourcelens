# ADR-002: Own the LensNode agent runtime contract across deepagents upgrades

## Status

Accepted

## Date

2026-09-17

## Context

LensNode (`lensnode/`) runs the agent loop on the `deepagents` and `langchain`
stacks. `deepagents` is pinned exactly in `lensnode/pyproject.toml`, and the
LangChain bounds (`langchain`, `langchain-core`, `langchain-anthropic`) are
pinned to the matching minor.

The runtime historically relied on behavior that upstream injected implicitly:

- `create_deep_agent` added `TodoListMiddleware` by default, which supplied both
  the `write_todos` tool and its planning prompt. The capability boundary
  (`CapabilityBoundaryMiddleware`) refuses business tools until an initial plan
  has been written, so losing `write_todos` deadlocks every gated run.
- `create_deep_agent` appended its authored `BASE_AGENT_PROMPT` to the caller's
  system prompt.
- Its middleware injected tool-usage prose through `system_prompt` defaults
  (`TASK_SYSTEM_PROMPT`, `FILESYSTEM_SYSTEM_PROMPT`,
  `EXECUTION_SYSTEM_PROMPT`, ...).

`deepagents==0.7.0` removed all three by design: no default
`TodoListMiddleware`, an empty authored base prompt (`BASE_AGENT_PROMPT`
deprecated, removal in `0.9.0`), and `system_prompt=None` defaults that emit no
prose. These changes fail silently — the process starts, but the plan gate
deadlocks and prompt guidance disappears.

The `lensnode` test suite is not run in CI
(`.github/workflows/build_and_deploy.yml` only builds and deploys images), so
this class of regression is not caught automatically.

## Decision

1. LensNode owns its runtime contract explicitly instead of depending on
   upstream prompt injection or default middleware:
   - `agent_runtime/assembly.py` adds `TodoListMiddleware()` to both the main
     agent and the general-purpose subagent.
   - `agent_runtime/system_prompts.py::_agent_operating_guidance()` states the
     behavior and tool-usage guidance LensNode relies on, injected through
     LensNode's own `system_prompt`. LensNode does not use `BASE_AGENT_PROMPT`
     or middleware default prompts.
2. `deepagents` stays pinned exactly, with LangChain bounds pinned to the
   matching minor. Bumping the pin requires a coordinated bump of
   `langchain`, `langchain-core`, and `langchain-anthropic`.
3. `lensnode/tests/test_runtime_contract.py` is the guardrail. It builds the
   real graph the runtime builds and asserts required middleware
   (`TodoListMiddleware`, `FilesystemMiddleware`, `SubAgentMiddleware`),
   required tools (`write_todos` and the filesystem/execute tools), required
   system-prompt markers, and a minimum `deepagents` version. An upgrade that
   drops any of these fails the suite instead of degrading at runtime.
4. Upgrade procedure for `deepagents`: bump the pins and bounds, re-lock
   (`uv lock` / `uv sync`), run the full `lensnode` suite, and treat a
   contract-test failure as a signal to update LensNode-owned code — never to
   reintroduce a dependency on upstream prompt/middleware injection.

## Consequences

- Upgrade regressions surface as failing tests rather than silent behavior
  changes; the `write_todos` plan gate stays functional across versions.
- LensNode carries more prompt text it must maintain and review itself, and the
  exact pin means the LangChain stack is bumped manually.
- The contract test intentionally couples to upstream middleware/tool names;
  when upstream legitimately changes them, both the runtime and the test must be
  updated together.

## Known upstream changes to track

- `0.7.0`: default `TodoListMiddleware` removed, authored base prompt emptied,
  middleware prose removed, `FilesystemBackend`/`LocalShellBackend` default to
  `virtual_mode=True`, a recursive `delete` filesystem tool is exposed.
- `0.9.0`: `BASE_AGENT_PROMPT` is scheduled for removal (unused here).
- `0.7.13`: subagent mode renamed `handoff` to `isolated` (unused here).

## References

- `lensnode/tests/test_runtime_contract.py` — the contract guardrail.
- `lensnode/lensnode/agent_runtime/assembly.py` — explicit middleware assembly.
- `lensnode/lensnode/agent_runtime/system_prompts.py` —
  `_agent_operating_guidance`.
- `lensnode/pyproject.toml` — the pinned runtime dependencies.
