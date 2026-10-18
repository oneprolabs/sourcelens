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

Legacy channel fields remain compatible and are migrated into subscriptions.
Existing event records retain their status and details. Non-null additions to existing tables retain database defaults for old writers.
New migrations are additive; future removal of legacy fields requires a later release.

## Implementation plan and acceptance

1. Models, outbox and matching: terminal failures, partial success and timeout
   classification; transaction rollback, duplicate callbacks and retry tests.
2. Destination APIs and delivery: validation, multiple channels, per-destination
   results, cooldown, broker failure recovery, permission revocation tests.
3. Admin rule/event interfaces and personal subscriptions: conditional fields,
   pagination, explicit errors, localized text, no credential exposure.
4. Integration: actual datasource callbacks, Run failure cleanup, periodic
   registration, running threshold sampling, browser acceptance.

Source: `backend/lens/alerts.py`, models and serializers in `backend/lens/`,
views in `backend/lens/views/alerts.py`; UI in `frontend/src/admin/pages/Alerts/`
and user settings. Tests use Django TestCase and Node's built-in test runner.
Follow existing snake_case API fields, Vue composition API and 120-column Python
style. Validate external inputs; never edit vendor packages or send real test
messages to external recipients.

Verification commands (run backend against this worktree's container source):

```sh
python manage.py makemigrations --check --dry-run
python manage.py test lens.tests.test_alerts lens.tests.test_unified_alerts --noinput
cd frontend && npm run test:unit
npm run build
```

Browser acceptance uses ego-browser: create/edit scoped rules, configure multiple
destinations, inspect filtered event/delivery records, subscribe/unsubscribe as a
normal user, and verify conditional inputs and mobile layout. This specification
implements the design approved in the 2026-10-16 conversation.

## Configuration

1. Create an active email or webhook channel in Admin Console → Notifications → Channels.
2. In Alert rules, select synchronization, document processing, Q&A or a message topic; choose events,
   thresholds and optional resource/LensNode scopes. Add one or more destinations.
3. For personal Q&A or message subscriptions, publish the rule and select the personal email channel.
   Users subscribe in Settings → Notifications → Alert subscriptions.
4. Inspect Alert events for each destination's status and retry exhausted failures.

Deploy with migrations through `0066_unified_alerts`, register periodic tasks,
and restart Celery workers and the scheduler. No real external notifications were
sent during acceptance. Existing staff-only scope endpoints retain their permissions;
operators without scope-list access can still use the alert management interface.

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
