# SourceLens PR Review Policy

This file defines the project-specific review policy consumed by the
SourceLens PR-Agent workflow. The current GitHub Action loads this file alone
from the repository default branch as review context.

The policy describes what the reviewer should assess. It does not replace the
PR-Agent system prompt, GitHub branch protection, CODEOWNERS, required human
approval, or CI.

## Current GitHub Execution

The current implementation is .github/workflows/pr_agent.yml.

- It runs for opened, reopened, ready-for-review, and synchronize pull request
  events.
- It skips draft pull requests, bot-triggered events, and pull requests from
  forks.
- For opened, reopened, and ready-for-review events, PR-Agent runs describe
  before review.
- For synchronize events, PR-Agent runs a full /describe followed by a full
  /review. It is not configured for incremental /review -i behavior.
- PR-Agent updates its persistent review comment instead of creating a new
  top-level review comment on every run.
- The Action does not run project tests, submit native APPROVE or
  REQUEST_CHANGES review events, or merge pull requests.
- The Action sends an operational result notification to Feishu after the
  PR-Agent step. Notification success is not evidence that code verification
  passed.
- Each pull request has one active workflow run. A newer event cancels an
  older in-progress run.
- The review job has a 20-minute timeout.

Automatic describe may update the PR description before review. Generated
description text is supporting context, not a new source of product
requirements. When generated text conflicts with a linked Issue,
maintainer-confirmed discussion, or clearly preserved author requirements,
report the conflict and use the human-authored source.

## Objectives

Every review should cover two dimensions:

1. Requirement compliance: determine whether the implementation covers the
   objective, scope, user scenarios, and acceptance criteria in the linked
   Issue and PR description.
2. Implementation quality: identify concrete correctness, security,
   authorization, protocol, data consistency, deployment, performance, and
   maintainability problems introduced by the pull request.

The terms MUST, MUST NOT, REQUIRED, and BLOCKING in this policy are mandatory.
A mandatory check is not satisfied by a PR-description claim, the presence of
tests, or a successful workflow status. The reviewer must connect the claim to
the supplied implementation and verification evidence.

High implementation quality does not compensate for a missing core
requirement. A pull request should not be required to implement work outside
its documented scope.

All prose published to GitHub by the review workflow must be in English.
Identifiers, paths, logs, and quoted user content may remain in their original
language when an English explanation is included.

## Available Context and Evidence Limits

PR-Agent normally receives:

- The PR title, description, source branch, and commit messages.
- The base-to-head diff, subject to model and diff token limits.
- Linked GitHub Issue content when PR-Agent recognizes an Issue reference in
  the PR description or branch name.
- PR_WORKFLOW.md from the trusted default branch.
- Repository metadata exposed by the GitHub provider.

The current Action does not guarantee that arbitrary discussion comments,
native review state, GitHub check results, CI logs, or manual verification
records are included in the model prompt. Treat them as evidence only when
they are present in the supplied context or quoted in the PR description.

Do not claim that tests, builds, lint, deployment checks, or manual
verification ran without explicit evidence. If evidence is missing, identify
what remains unverified without presenting the missing evidence as a code
defect.

The existing build_and_deploy.yml workflow runs only for version tags or
manual dispatch. It is not a PR validation workflow and is not evidence that
a pull request passed tests, lint, or builds.

## Mandatory Review Protocol

Before reporting a pull request as fully compliant or as having no major
issues, the reviewer MUST:

1. Compare the changed-file count in PR metadata with the files represented in
   the supplied diff. If files or hunks were omitted, clipped, or listed as
   unprocessed, report that the review is incomplete and require human review.
2. Map every concrete linked-Issue requirement and PR acceptance claim to the
   changed implementation and available evidence. Ambiguous requirements must
   require human verification; they must not be silently treated as complete.
3. When an Issue is linked, inspect the target-branch behavior and determine
   whether the reported problem or product gap exists at the code level,
   whether a code change is necessary, whether the changed execution path
   addresses the root cause, and whether the resulting behavior resolves the
   Issue. If the supplied context cannot establish any of these points, require
   human verification and do not issue a green or merge-ready conclusion.
4. Read changed tests first, then trace every changed behavior across its
   input, validation, persistence, shared references, dispatch or transport,
   runtime use, response, and failure cleanup as applicable.
5. For every added, changed, or deleted database migration, compare the entire
   pull request with the current target-branch migration graph and apply the
   Database Migration Focus checks below.
6. Apply every relevant mandatory invariant in this policy, including negative
   and partial-failure scenarios. Happy-path coverage alone is insufficient.
7. State material evidence gaps. A successful Action, author-reported test, or
   manual-verification claim must not be converted into reviewer-verified fact.

If the pull request changes more than one runtime unit or introduces a new
trust boundary, inspecting only the primary implementation file is
insufficient. If the supplied context cannot establish a mandatory invariant,
the reviewer MUST fail closed by requiring human verification, not by assuming
the invariant holds.

A behavior-changing pull request over 1,000 changed lines, or over 20 files
while spanning multiple runtime units, MUST be split before merge unless the
change is generated, mechanical, or documented as technically indivisible and
a maintainer explicitly accepts the review risk. The automated reviewer MUST
not issue a merge-ready conclusion for an oversized exception.

## Requirement Compliance

Use requirement sources in this order:

1. Acceptance criteria and expected behavior in the linked Issue.
2. Decisions explicitly confirmed by a maintainer in Issue or PR discussion.
3. Scope and constraints in the human-authored PR description.
4. Existing behavior and documentation, only to interpret unstated
   compatibility requirements.

If sources conflict, identify the conflict instead of silently choosing one.
If no Issue is linked, use the PR description and state that Issue context is
unavailable. If neither source defines expected behavior sufficiently, mark
the requirement as requiring human verification rather than inventing it.

For each requirement, determine whether it is:

- Fully compliant: implementation and available evidence cover it.
- Not compliant: it is missing, contradicted, or implemented incorrectly.
- Requires human verification: the diff or supplied evidence cannot establish
  the result reliably.

Check that:

- For a linked bug Issue, the target-branch code can produce the reported
  failure, the pull request changes the actual root-cause path, and a
  regression test covers the original trigger. The reviewer must reason
  whether the test would fail before the fix and pass after it without claiming
  it was executed unless explicit execution evidence is supplied.
- For a linked feature Issue, the target branch lacks the required behavior and
  the changed implementation closes that gap across every applicable acceptance
  path.
- A code change is not presented as necessary when the available evidence shows
  that the root cause is data, configuration, deployment, or operation only.
- The change resolves the user-visible problem rather than only modifying
  related code.
- Core acceptance criteria map to implementation and verification evidence.
- A bug fix addresses the reported reproduction path and includes an
  appropriate regression test.
- Required error states, roles, mobile behavior, internationalization,
  concurrency, migrations, or deployment constraints are not omitted.
- The pull request does not introduce unrelated scope or destructive
  refactoring.
- Compatibility and verification claims match the actual diff.

When ticket context exists, requirement gaps belong in PR-Agent's
ticket-compliance section. Without ticket context, report a missing core PR
requirement as Important only when the human-authored description provides
concrete evidence. Keep requirement gaps separate from implementation
defects.

## Review Scope and Comment Behavior

Review only behavior introduced or modified by the pull request. Use current
head line numbers and changed code locations.

The synchronize workflow performs a full base-to-head review. Do not claim
that it reviewed only commits added since the previous review. The persistent
comment prevents duplicate top-level comments, but the current model prompt
does not provide a complete per-finding history. Within the current output,
avoid repeating the same root cause across multiple findings.

Read changed tests before implementation. For cross-component changes, inspect
the changed protocol producer and consumer, persisted state, shared-resource
ownership, permissions, failure cleanup, and deployment compatibility. When a
required counterpart is absent from the supplied context, mark it as requiring
human verification rather than silently skipping the check.

## Finding Priority

The current PR-Agent configuration publishes at most three key implementation
findings. Use those slots for the highest-impact, actionable problems.

A violated mandatory invariant is merge-blocking and MUST use a key-issue slot
unless a higher-severity finding consumes every slot. Closely related failures
may be grouped under one root cause, but their distinct trigger scenarios must
remain explicit. The three-finding limit must never be used to justify an empty
finding list or a positive conclusion.

Prefix the issue header with one of:

- Critical: authorization bypass, credential exposure, arbitrary file or
  command access, data corruption, production startup failure, or a broken
  core execution path.
- Important: a definite functional bug, protocol incompatibility, state race,
  missing protection for critical behavior, resource leak, or significant
  deployment or performance risk.

Do not use key-issue slots for subjective style, optional refactoring, minor
naming, or formatting already reported by automated tools. Every finding must
include:

- A changed file and current-head line or nearest changed symbol.
- The concrete failure scenario and impact.
- A specific, actionable recommendation.
- Any material uncertainty when evidence is incomplete.

An empty key-issues list is correct when there are no concrete Critical or
Important implementation defects. Do not invent findings to fill the output.

## Project Architecture

SourceLens has three primary runtime units:

- backend: Django REST API, authentication and authorization, Assistants,
  Sessions, Runs, data sources, AI Gateway, task scheduling, and
  administration APIs.
- lensnode: a standalone execution node connected through WebSocket. It runs
  Deep Agents, Skills, MCP, document conversion, and data source
  synchronization in a local workspace.
- frontend: Vue 3 and Vite UI for chat, run history, Shared Q&A, and
  administration.

Primary backend apps are accounts, lens, and core. Shared published agentcore
packages provide task, LLM metering, and notification infrastructure.

The answer execution flow is:

    Frontend -> Django API -> Run -> Celery -> Channels WebSocket -> LensNode
             <- SSE state/output <- persisted LensNode events

Data source synchronization has a separate state track involving DataSource,
ScheduledTask, agentcore TaskExecution, Celery dispatch, and LensNode
callbacks. Do not treat TaskExecution as part of every answer Run.

## Backend and API Review Focus

- Keep business logic in models, serializers, and services. Views should
  orchestrate requests and responses.
- Prefer DRF permissions, validation, pagination, and exception handling.
- Check REST request and response compatibility, status codes, pagination,
  OpenAPI implications, and matching frontend consumers.
- Every consumer of a paginated endpoint MUST either follow all required pages
  or request a supported page size that covers the bounded selection use case.
  Unwrapping only the first default page is a functional defect. Verify the
  actual pagination parameter names and maximums rather than assuming them.
- New or changed model fields require migrations. Review existing-data impact,
  reversibility, constraints, indexes, and startup migration behavior.
- Check querysets and object permissions for unauthorized UUID access. Do not
  rely on frontend hiding.
- Review ORM changes for N+1 queries, missing pagination, and unbounded table
  scans.
- For JWT, OAuth, OTP, Role, Permission, Profile, and feature access changes,
  check authentication bypass, user enumeration, replay, rate limits, and
  sensitive-data exposure.
- Store timestamps in UTC and let the frontend present user-local time.
- Treat dependency and lockfile changes as supply-chain changes. Check version
  compatibility, integrity, licensing where relevant, and image build impact.

## Database Migration Focus

Database migrations are a mandatory, blocking review focus. Review them
against the pull request's current target branch, not only against the source
branch or the migration files visible in the pull request.

- A migration version means one Django migration graph node, normally one
  numbered Python migration file. Migration package `__init__.py` files do not
  count as versions.
- Across the entire pull request, each Django application MUST introduce no
  more than one new migration version. Different applications may each add one
  migration version in the same pull request.
- Compare added, changed, and deleted migration files with the target branch.
  A migration already present on the target branch MUST NOT be modified,
  renamed, or deleted by the pull request.
- Compare every new migration name, number, dependency, and resulting leaf node
  with the corresponding application migration graph on the current target
  branch. A migration generated from an outdated source branch is not safe
  merely because its local graph is valid.
- A new migration MUST depend on the current target-branch leaf migration for
  its application. Duplicate names or numbers, parallel leaves, missing
  dependencies, and dependencies that bypass a target-branch migration are
  BLOCKING.
- When the target branch has advanced, require the author to synchronize the
  source branch and regenerate or renumber the migration as appropriate. A
  merge migration added only to preserve an avoidable feature-branch fork does
  not resolve the conflict.
- If the supplied context cannot establish the complete base-to-head migration
  set or the current target-branch migration graph, require human verification
  and do not issue a green or merge-ready conclusion.
- A migration review becomes stale when the target-branch migration graph
  changes. The migration comparison MUST be repeated before merge.
- Migration ordering MUST preserve expand/contract deployment compatibility.
  Review existing-data transformations, reversibility, constraints, indexes,
  locks, and compatibility with both old and new application versions during
  the blue/green observation window.

## Lens Domain and Authorization Focus

- Keep Assistant visibility and user or group grants consistent across list,
  detail, Session, Run, attachments, output files, and Shared Q&A.
- Public tokens and sharing links must be unpredictable and have defined
  ownership, revocation, and expiration behavior.
- Bind Session, Message, Run, RunExecution, and RunStep access to the correct
  user and Assistant.
- Idempotency and concurrency controls must not create duplicate Runs,
  messages, or Celery tasks.
- Execution snapshots must remain consistent for task, directories, Skills,
  MCP, model references, and relevant Assistant settings.
- Validate untrusted attachments, Skill packages, MCP configuration, output
  files, and data source content for size, type, path, filename, extraction,
  and download behavior.
- Store DataSourceCredential secrets encrypted. Never expose plaintext in
  ordinary APIs, logs, exceptions, or task metadata.

## State, Transaction, and Shared-Resource Invariants

- A user-visible save that mutates multiple models or resources MUST be atomic
  at the server boundary. If multiple requests are unavoidable, the design
  MUST provide idempotency and explicit rollback or compensation for every
  partial-failure order. A frontend MUST NOT persist a reusable child resource
  before its parent save without such recovery.
- Validate all replacement data before deleting existing related rows. Create,
  update, binding synchronization, access grants, and derived resources MUST
  not leave partial state when any later step fails.
- Before editing a referenced resource, determine whether it is shared. An edit
  initiated from one Assistant or consumer MUST NOT change another consumer's
  behavior unless shared mutation is the explicit, confirmed product behavior.
  Otherwise use selection, cloning, or copy-on-write semantics.
- Resource creation/editing and resource selection/binding are distinct user
  actions. A binding UI MUST preserve reusable named resources and MUST NOT
  silently turn selection into mutation or per-consumer duplication.
- Enabled, disabled, deleted, and unavailable states MUST have identical
  meaning during save validation, snapshot creation, dispatch validation, and
  runtime resolution. Passing validation and then omitting required runtime
  values is a broken execution path.

## Run, WebSocket, and Streaming Focus

- Answer Run state follows queued to running or streaming, then done, failed,
  or cancelled. Late events must not overwrite terminal states.
- Keep Run, RunExecution, and RunStep states and timestamps consistent.
- Separately keep DataSource, ScheduledTask, and agentcore TaskExecution
  synchronization state consistent.
- Check WebSocket and SSE behavior for duplicates, out-of-order frames,
  reconnection, unknown UUIDs, resets, and terminal events.
- Cancellation must stop or mute control-plane and LensNode work and suppress
  late output.
- Distinguish provider timeout, gateway failure, transport idleness, offline
  nodes, and business failure.
- Persisted final content must remain consistent with streamed accumulation,
  resets, empty reconciliation frames, and output files.

## AI Gateway and agentcore Focus

- LensNode model calls must use the authorized AI Gateway.
- Do not send provider credentials or internal model configuration to
  LensNode or the frontend.
- Validate model references for availability and invocation permission.
- Check streaming, heartbeat, cancellation, provider errors, and retries for
  duplicate or missing metering.
- Keep user, model, Run or task, token, and cost attribution accurate.
- Keep specific agentcore routes ahead of broad administration routes.
- Consume agentcore public interfaces rather than package internals.
- For an agentcore version update, review release changes, migrations,
  compatibility, parent-repository integration, and release order.

## Celery and Startup Focus

- Celery tasks must be discoverable through core/celery.py or its explicit
  imports.
- Register periodic definitions through each app's periodic_tasks.py and the
  project registry.
- Preserve existing django-celery-beat rows so operational edits are not
  overwritten.
- Check task idempotency, retry behavior, locks, timeouts, partial failure,
  queue routing, and structured logging.
- Celery worker and scheduler code does not hot-reload in development.
- The backend startup order is migrate, ensure the configured superuser,
  ensure the default LensNode, register periodic tasks, collectstatic, then
  start the ASGI process.
- Changes to startup defaults must not make development credentials or tokens
  safe-looking or usable as production credentials.

## LensNode Focus

- Change both sides of shared WebSocket message types, fields, defaults, and
  protocol versions, or define a compatible rollout order.
- Review hello, heartbeat, run start, run cancellation, data source
  synchronization, output, event, and completion frames.
- Redact node tokens from URLs, logs, and exception text.
- Prevent traversal through parent segments, symbolic links, and out-of-bound
  absolute paths for workspaces, selected directories, attachments, Skill and
  MCP caches, and deliverables.
- Knowledge Q&A and code analysis are read-only by default. Treat new shell,
  file-write, MCP, or Skill capabilities as trust-boundary changes.
- Treat every LLM-produced tool argument and every Skill package as untrusted.
  A credential-bearing HTTP tool MUST enforce a declarative allowlist of
  methods, routes or route prefixes, and destination scope. A relative URL and
  a configured base URL alone do not authorize arbitrary GET, write, or delete
  operations with privileged credentials.
- URL enforcement MUST cover scheme, host, credentials, redirects, base-path
  escape, and unintended private or control-plane destinations as applicable.
- Response-size limits MUST be enforced while streaming bytes from the network
  and before full buffering, decoding, decompression, JSON parsing, or capture.
  Checking the length of an already-buffered body is not a resource limit.
- Secret redaction MUST cover nested response values, headers, cookies, and
  sensitive key variants across case and camelCase, snake_case, and kebab-case
  separators. Captured session values and substituted secrets must never be
  returned, logged, or included in event summaries.
- Check model and idle timeouts, cancellation signals, threads, semaphores,
  caches, and temporary resources for leaks and late emits.
- Limit document conversion and data source adapters by size, type, timeout,
  and resource consumption.

## Frontend Focus

- Use Vue 3 Composition API and script setup, Pinia for shared state,
  vue-router for routing, and vue-i18n for user-facing copy.
- Handle loading, empty, error, cancellation, retry, and expired-login states
  relevant to the changed flow.
- Do not leak composer text, attachments, or streaming state across Session
  switching and new-session flows.
- Handle streaming disconnection, duplicate events, cancellation, empty
  answers, resets, and terminal states.
- Sanitize Markdown and HTML with DOMPurify. Protect external links, downloads,
  and user content against XSS, dangerous URLs, and tabnabbing.
- Treat frontend permission checks as presentation only; enforce access in the
  backend and represent 401, 403, and 404 correctly.
- Review affected UI on desktop and mobile, in Chinese and English, with long
  and empty content, and for keyboard and screen-reader accessibility.
- Follow the existing AGIOne and agentcore administration style.
- Critical frontend flows need Playwright coverage or a reproducible manual
  verification record.

The current frontend does not implement a global Dark Mode. Do not require
Dark Mode verification unless the pull request introduces or modifies that
capability.

## Deployment and Configuration Focus

- Builds must resolve the declared agentcore package versions from the
  configured Python package index.
- Production builds create backend, frontend, and lensnode images. Shared
  protocol changes require compatible image combinations and rollout order.
- Development Compose uses source mounts and DEV_MODE=1. Production
  configuration must not introduce development mounts or test credentials.
- Tag builds publish both the resolved version and latest tags.
- Review deployment workflow changes for image names, variables, secrets,
  concurrency, failure propagation, health checks, and rollback.
- Never commit environment files, tokens, certificates, OAuth secrets, model
  keys, cloud credentials, or real user data.

## Verification Evidence

Choose the smallest sufficient evidence set for the changed scope. These
commands are evidence expectations for the author or CI; the current
PR-Agent Action does not execute them.

| Change scope | Minimum evidence |
|---|---|
| backend/accounts | pytest backend/accounts/tests |
| backend/lens | pytest backend/lens/tests |
| backend/core or cross-app | pytest or focused Django tests |
| lensnode | pytest lensnode/tests |
| Python style | Black at line length 79 and isort for affected Python roots |
| frontend logic or styles | Non-fixing ESLint and npm run build |
| Critical frontend flows | npm run test:e2e or explicit manual verification |
| Agentcore dependency | Parent compatibility tests and backend image build |
| Docker or deployment | Affected image builds and startup or health checks |
| GitHub workflow | Syntax validation and a safe non-production test run |

Additional expectations:

- npm run lint currently includes --fix. Read-only verification should use an
  equivalent non-fixing ESLint command or confirm that no diff was produced.
- Model changes require migration review and existing-data impact.
- Permission changes should cover relevant anonymous, regular, authorized,
  and administrator branches.
- Run and WebSocket changes should cover success, failure, cancellation,
  duplicate or late events, and offline nodes.
- Bug fixes should include a regression test that fails before the fix.
- If verification cannot run, state the reason and remaining risk.
- Test presence is not evidence of invariant coverage. For changed persistence,
  shared resources, pagination, runtime state, or privileged tools, inspect for
  negative tests covering rollback, cross-consumer isolation, multiple pages,
  disabled resources, unauthorized methods/routes, oversized streaming
  responses, and secret-key variants as applicable.
- A new trust-boundary capability without focused abuse-case tests is an
  Important missing protection even when happy-path tests pass.

## PR-Agent Output and Merge Boundary

Use PR-Agent's native persistent PR Reviewer Guide output. Do not require a
custom AI_REVIEW marker, custom Markdown section layout, or a native GitHub
review event from the current Action.

The output should prioritize:

- Ticket or Issue compliance when a recognized Issue is linked.
- Whether relevant tests were added or changed.
- Up to three Critical or Important implementation findings.
- Concrete security concerns.

If no key issues are found, do not invent optional or style comments. Absence
of findings means only that no merge-blocking defect was identified from the
available diff and context; it does not prove that tests or deployment checks
passed.

The phrases "Fully compliant", "No security concerns identified", and "No
major issues detected" are permitted only after every applicable mandatory
check above is completed with sufficient supplied evidence. If diff coverage,
requirement mapping, a cross-component counterpart, or a mandatory invariant
cannot be established, the output MUST explicitly require human verification
and MUST NOT present a green or merge-ready conclusion.

The automated reviewer never merges a pull request. Human reviewers and
GitHub protection rules make the final approval and merge decision.
