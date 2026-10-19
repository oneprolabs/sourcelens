# Unified alerts and subscriptions

## Objective and contract

Unify datasource synchronization, document processing, and Q&A alerts. Reuse Django,
Celery, PostgreSQL and agentcore-notifier. Users configure scope and conditions,
then independently enable destinations. No external event bus is required.

Rules select one subject (`run`, `sync`, `processing`, `message`), events, optional resource
and LensNode scopes, and positive thresholds. Empty resource scopes mean all
resources. Failure, partial completion, timeout, budget termination and numeric
thresholds have distinct semantics. User cancellation is not failure. A timeout
and generic failure matched by the same rule produce one occurrence with all
matched conditions. Terminal and running evaluations are distinct occurrences.

Rules support terminal evaluation (default) or running threshold reminders.
Running reminders sample persisted LLM metering, including descendant runs, and
elapsed execution time every 60 seconds. They are eventually consistent, do not
enforce execution limits, and do not claim to be the runtime budget counter.
The token limit termination condition uses runtime reasons instead of metering.

Subscriptions belong to a rule and select a channel plus email addresses,
users or groups. Admins manage shared destinations. Personal subscriptions use
the authenticated user's current email and an admin-designated email channel;
users cannot supply addresses, other users, groups or arbitrary channels. Personal
Run notifications cover only that user's runs on currently accessible assistants.
Datasource access remains admin-only, matching existing resource APIs.

## Reliability and history

Persist an outbox occurrence in the business transaction. Celery evaluates it
after commit; a periodic sweep recovers unpublished occurrences and terminal
updates made through bulk operations within the last 24 hours. Rules are matched
against business completion time, so newly created rules do not replay old work.
Late persisted Run usage is rechecked within that recovery window. Running scans
rotate through bounded batches; under more than 100 concurrent runs the interval
can exceed 60 seconds. Evaluation and destination creation are
idempotent. Each delivery has its own claim lease, bounded retries and outcome.
Provider success means accepted by the provider, not proof that a human read it.
External sends are at-least-once across a crash after provider acceptance.

Keep bounded object/rule/destination snapshots, not raw credentials or model
payloads. Disable or revoke destinations before delivery to prevent stale queued
personal notifications. A rule's cooldown suppresses repeated scheduled failures
for the same resource/destination; manual operations bypass cooldown. Keep
suppressed delivery records for audit. Pending delivery rows are the durable queue.

Legacy channel fields remain supported by the rule API. Migration `0065_alert_rules`
creates the complete notification schema directly after `0064_builtin_decisions`.
No alert-data backfill is needed before the first release.

## Configuration

1. Create an active Feishu, WeCom, DingTalk or email target in Admin Console → Notifications → Channels.
2. In Alert rules, select synchronization, document processing, Q&A or a message topic; choose events,
   thresholds and optional resource/LensNode scopes. Add one or more destinations.
3. For personal Q&A or message subscriptions, publish the rule and select the personal email channel.
   Users subscribe in Settings → Notifications → Alert subscriptions.
4. Inspect Alert events for each destination's status and retry exhausted failures.

Deploy with migrations through `0065_alert_rules`, register periodic tasks,
and restart Celery workers and the scheduler.

## Notification targets

Feishu, WeCom and DingTalk use independent `channel_type` values: `feishu`, `wecom`
and `dingtalk`. Existing Webhook targets remain editable. The SourceLens adapter
owns native validation, rendering and sending; it stores native values in existing
notifier string columns without changing the dependency's model choices or schema.
Native sends must use this adapter rather than the dependency's generic
`send_notification` entry point or model-choice validation.

Native targets use alert rule cooldown, deduplication and retry policies. Generic
notifier merge/silence settings apply to legacy Webhook targets. Provider
acknowledgement is required before a delivery becomes sent; ambiguous timeouts can
still cause duplicate external sends because robot APIs provide no idempotency key.

Feishu receives interactive cards; WeCom and DingTalk receive Markdown. Content
uses the destination language, escapes untrusted markup and mentions, and truncates
at UTF-8 boundaries with an ellipsis. Payloads stay below platform limits: 4096
bytes for WeCom, 20 KB for DingTalk and 30 KB for Feishu. Email remains plain text;
supported legacy Webhooks also receive the richer payload.

Robot URLs and signing secrets are write-only; blank edits retain saved values.
Native endpoints require official HTTPS URLs, reject redirects and use bounded
timeouts. Feishu signs the payload; DingTalk signs query parameters; WeCom uses the
robot URL key. Records contain unsigned content, locale, target identity and result
codes, excluding credentials and raw transport exception messages. Explicit test
sends use the same renderer and sender and create notification records.

Platform contracts: [Feishu](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot),
[WeCom](https://developer.work.weixin.qq.com/document/path/91770), and
[DingTalk](https://open.dingtalk.com/document/robots/custom-robot-access).

## On-demand message publication

Business messages use the `message` subject, an exact lowercase topic and the
`message_published` event. Operators publish a bounded plain-text title/body with
an explicit idempotency key. `(topic, idempotency_key)` identifies one immutable
publication: identical retries return the existing record, changed content returns
409. Publication is recorded even when no rule matches and never replays to rules
created later. Only operators with Admin Console access may publish or inspect
publication content. Explicitly published personal message rules are broadcasts
visible to all active users with email; private Run subscriptions retain ownership
and assistant visibility checks.

The existing outbox is the publication record and delivery queue. No separate
message broker or topic registry is introduced. Rules subscribe to exact topics;
users opt in through the existing personal subscription screen. Identical
occurrence/channel/normalized-recipient-set destinations across rules are sent once;
additional matches retain suppressed history referencing the first delivery. Partial
recipient overlap across differently configured destinations is not merged.

Publication API (Admin Console operators only):

```http
POST /api/lens/admin/alert-messages/
Content-Type: application/json

{"topic":"release.ready","idempotency_key":"release-123","title":"Release ready","body":"Ready for review"}
```

Returns 201 for a new message, 200 with `deduplicated=true` for an identical retry,
and 409 if the same key is used for changed content. List/detail endpoints expose
immutable records; `processed_at` means rule matching completed, not sent. Follow
`source_uuid` in `/api/lens/admin/alert-events/?subject=message&source_uuid=...`
for per-destination outcomes. In-process trusted publishers call
`lens.messages.publish_message` with the same validated contract inside their
business transaction. UI entry: Notifications → Message publications.

## Diagnosis workflows and notification languages

Terminal Run, sync and processing rules support `diagnosis_mode`:

- `direct` (default): send the basic alert immediately.
- `diagnose_first`: wait for a validated diagnosis, then include its summary,
  evidence references and recommendations. Failure or deadline expiry releases
  the basic alert with a localized fallback explanation.
- `notify_then_diagnose`: send the basic alert immediately; send one separately
  recorded supplement only after the initial notification was accepted and the
  diagnosis completed successfully. Failed diagnoses produce no supplement.

Creating a diagnosis-enabled rule requires the existing Run diagnostics permission.
Message publications and running threshold reminders use direct delivery. Configure
`diagnostics.model_ref` in global settings; Run diagnosis can also use the execution's
agent model snapshot. Missing model configuration releases a basic alert.

Independent Celery tasks on the existing `lens` queue analyze safe failure codes,
execution budgets and bounded structured step evidence. They omit raw questions,
answers and task logs. Evidence references in model output are validated before
publication. `diagnosis_timeout_seconds` (5–300, default 60) includes queue time;
`diagnosis_max_tokens` (256–8192, default 2048) limits **output** tokens, not total
billable input/output tokens. Input is bounded to 24000 characters. Tasks have soft
and hard execution limits; the dispatcher recovers enqueue failures and expired
jobs. Deadline fallback is eventually dispatched, so a busy worker can delay the
actual notification beyond the configured diagnosis deadline. Late results are
ignored. Matching rules share analyses by occurrence, language and budget. Waiting
for diagnosis does not consume transport attempts.

Managed destinations select `language`: `en-US` (default), `zh-CN`, or `es`.
Personal subscriptions follow the user's current profile language at send time;
unsupported languages fall back to English. Titles, condition labels, field labels,
diagnosis instructions and fallback explanations use that language. Resource names,
rule names and error codes retain their original values. If a recipient changes
language while diagnosis is queued, the initial notification falls back to a basic
alert in the current language; an obsolete-language supplement is skipped.
Identical destinations deduplicate separately by language and initial/supplement
phase. History records the sending language, diagnosis status, validated conclusions
and independent supplement outcome. Sending rechecks subscription access and policy.

Published business messages can optionally include operator-authored translations:

```json
{
  "topic": "release.ready",
  "idempotency_key": "release-123",
  "title": "Release ready",
  "body": "Ready for review",
  "translations": {
    "zh-CN": {"title": "版本就绪", "body": "可以开始验收"},
    "es": {"title": "Versión lista", "body": "Lista para revisión"}
  }
}
```

Each translation requires both title and body. A destination without a translation
receives the original text. Supplied translations are immutable publication content:
changing them under the same topic/business key returns 409. The publication UI
provides optional translation inputs; there is no automatic translation model call.

## Verification

Run backend checks against this worktree's container source using
[devctl](../worktree-development.md):

```sh
./devctl test . -- python manage.py makemigrations --check --dry-run
./devctl test . -- python manage.py test \
  lens.tests.test_alerts lens.tests.test_unified_alerts \
  lens.tests.test_alert_diagnostics lens.tests.test_notification_targets \
  lens.tests.test_alert_migrations --noinput
cd frontend && npm run test:unit
npm run build
```

Use mocked transports or local receivers for automated delivery tests. Browser
verification uses ego-browser to check scoped rules, destinations, delivery history,
personal opt-in, localization and mobile layout. Rendering in each platform's
desktop/mobile client requires separate verification with a configured robot.
