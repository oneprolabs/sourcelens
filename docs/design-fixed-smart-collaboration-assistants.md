# Smart Assistants

## Objective

Allow an administrator to create a reusable Smart Assistant. Users select it
from the normal Assistant list and
chat at the normal Assistant URL, while each Session runs through the existing
Smart Collaboration coordinator and a member set chosen by the administrator.

The existing ad-hoc Smart Collaboration entry at `/lens/chat` remains
available. It continues to let a user choose a participant range per Session.

### Current state

- A normal Assistant owns one execution capability and creates a direct
  Session.
- Ad-hoc Smart Collaboration is a Session mode. It creates a hidden system
  coordinator from `lens.smart_collaboration.model_ref` and stores a
  user-selected list in `Session.allowed_assistant_uuids`.
- The runtime already snapshots the coordinator model, routing mode, allowed
  member UUIDs, and complete subagent definitions into each Run.
- The admin Assistant wizard cannot persist a collaboration team.

### User stories

1. As an administrator, I can create a Smart Assistant and select its member
   Assistants.
2. As a user, I can select that Assistant like any other Assistant and create
   Sessions without choosing the team again.
3. As an operator, I can inspect a Session or Run and see the fixed Assistant
   and the exact member snapshot used for that conversation.
4. As an administrator, I can edit the team without changing historical
   Sessions.

## Design decisions

### Assistant mode is independent from execution capability

An Assistant has a product-level `mode`:

- `direct` is the default and preserves every existing Assistant.
- `smart` identifies an administrator-managed collaboration Assistant.

The persisted `routing_mode` field remains as a compatibility column and is
exposed alongside the new `mode` API field during migration. Mode behavior is
implemented polymorphically (`DirectAssistantMode` and `SmartAssistantMode`)
so callers do not infer product semantics from `capability`.

`capability` describes the low-level execution primitive for direct Assistants.
Smart mode may internally use `general_chat` as its coordinator primitive, but
it is not a General Chat Assistant in the product model and does not inherit
General Chat's Skill, MCP, LensNode, workspace, or multimodal requirements.

Add a directed, self-referential many-to-many relation for collaboration
members. A Smart Assistant is the coordinator definition; its
members are ordinary, non-system, direct Assistants.

This does not add `orchestrator` back to Assistant capabilities. Direct
capabilities remain `general_chat`, `code_analysis`, or `knowledge_qa`;
Smart mode delegates to its configured team through the internal coordinator.

### Sessions snapshot the team

Creating a Session with a Smart Assistant will:

1. verify that the coordinator and every member are active and accessible to
   the user;
2. create the Session with the fixed Assistant as `session.assistant`;
3. set `Session.routing_mode=smart`;
4. copy the current member UUIDs to `Session.allowed_assistant_uuids`.

Later edits to Assistant membership affect only new Sessions. Existing
Sessions and Run snapshots are unchanged.

The existing ad-hoc Smart Collaboration flow still uses the hidden system
Assistant and the global model setting. It remains the only Smart Session whose
participant range a user can edit.

### Users cannot change a Session's team membership

For a non-system Assistant with `routing_mode=smart`, PATCH requests that try
to change `Session.allowed_assistant_uuids` are rejected. The chat composer
shows the configured participants as a read-only summary.

Per-message `@Assistant` selection remains available and may select one or
more Assistants inside the Session snapshot. It does not alter the
Session range.

### Security and nesting

- Smart membership never grants access to a member Assistant.
  A user must be able to access every member when a Session is created and
  when a Run is started.
- A Smart Assistant cannot include itself, a system Assistant,
  an archived Assistant, or another `routing_mode=smart` Assistant.
- At least one member is required.
- Duplicate member UUIDs are rejected at the API boundary.
- Existing direct and ad-hoc Smart Collaboration permissions remain intact.

## API contract

No endpoint is added. `POST/PATCH /api/lens/assistants/` gains additive fields:

```json
{
  "mode": "smart",
  "collaboration_member_uuids": [
    "11111111-1111-1111-1111-111111111111",
    "22222222-2222-2222-2222-222222222222"
  ]
}
```

`routing_mode: "smart"` is accepted for backwards compatibility and is
returned with the same value while clients migrate to `mode`.

Assistant responses gain:

```json
{
  "routing_mode": "smart",
  "collaboration_members": [
    {
      "uuid": "11111111-1111-1111-1111-111111111111",
      "name": "Code Advisor",
      "capability": "code_analysis",
      "status": "active"
    }
  ]
}
```

`collaboration_member_uuids` is write-only.
`collaboration_members` is read-only and ordered by Assistant name. Existing
clients that omit the new fields continue to create direct Assistants.

Creating a Smart Session uses the existing payload:

```json
{
  "assistant_uuid": "33333333-3333-3333-3333-333333333333"
}
```

The response remains the existing Session representation and includes
`routing_mode=smart`, `allowed_assistant_uuids`, and `routing_assistants`.

Validation uses the project's existing DRF field-error shape. Invalid member
configuration is a `400`; inaccessible Assistants remain a `403` at Session or
Run creation.

## Backend changes

### Data model

- `Assistant.routing_mode`: indexed choice field, default `direct`.
- `Assistant.collaboration_members`: directed self many-to-many field,
  symmetrical false, blank at the database layer.
- One additive migration; no data migration is required.

### Serializer and lifecycle

- Extend `AssistantSerializer` with the new input/output contract and boundary
  validation.
- Synchronize member relations inside the existing create/update transactions.
- Smart coordinators use the internal `general_chat` primitive, do not require
  a Skill, and do not require a bound LensNode. Their own model, analysis
  rounds, token profile, prompt settings, visibility, and access grants remain
  configurable.
- `create_assistant_session` branches on Assistant routing mode and freezes the
  member UUIDs for Smart Assistants.
- `_session_assistant_is_runnable` accepts either the existing hidden system
  coordinator or an accessible Smart Assistant.
- `SessionSerializer` rejects participant-range updates for Smart Sessions.
- `smart_collaboration_assistants` excludes Smart Assistants so
  delegation cannot nest.

### Code style

Follow the existing Django service/serializer split and English docstrings:

```python
def fixed_collaboration_members(assistant, user):
    """Return active Smart members that remain accessible to the user."""

    members = assistant.collaboration_members.filter(
        routing_mode=Assistant.RoutingMode.DIRECT,
        status=Assistant.Status.ACTIVE,
        is_system=False,
    )
    return [member for member in members if member.is_accessible_by(user)]
```

Views continue to handle HTTP concerns only; validation and business rules stay
in serializers and lifecycle services.

## Frontend changes

### Admin Assistant wizard

- Add an Assistant Mode choice: Direct or Smart.
- When Smart is selected:
  - force execution capability to General Chat;
  - show a searchable multi-select of active direct Assistants;
  - require at least one member before continuing;
  - hide LensNode, workspace directory, Skill, MCP, and multimodal controls
    that apply to direct execution;
  - retain a Workspace Guide for coordinator context; execution workspace
    configuration remains with each Direct Assistant;
  - keep coordinator model, analysis depth, token profile, prompt context,
    visibility, and access controls.
- The Assistant list and detail drawer label the mode and show the configured
  member count/names.

The page continues to use AdminLayout, BaseDrawer, BaseSelect, BaseButton,
design tokens, responsive cards/tables, and localized text in Chinese, English,
and Spanish.

### Chat

- Treat either the existing `LensSmartChat` route or a selected Assistant with
  `routing_mode=smart` as a Smart Collaboration conversation.
- Keep separate flags for ad-hoc Smart Collaboration and Smart Assistants so
  Session creation sends the correct payload.
- Initialize a Smart conversation's pre-Session participant summary from
  `collaboration_members`.
- Render the participant picker read-only for a Smart Assistant.
- Keep `@Assistant` chips and runtime assistant activity unchanged.
- Exclude Smart Assistants from routing and mention candidates.

## Project structure

Expected implementation files:

```text
backend/lens/models.py
backend/lens/migrations/<next>_assistant_fixed_collaboration.py
backend/lens/serializers.py
backend/lens/assistant_lifecycle.py
backend/lens/views/assistants.py
backend/lens/views/sessions.py
backend/lens/tests/test_api.py
frontend/src/pages/lens/Assistants.vue
frontend/src/pages/lens/AssistantFormDrawerDirectEnvironment.vue
frontend/src/pages/lens/AssistantDetailDrawer.vue
frontend/src/pages/lens/Chat.vue
frontend/src/pages/lens/components/ParticipatingAssistantsPicker.vue
frontend/src/admin/locales/{en,es,zh-CN}.json
frontend/src/locales/{en,es,zh-CN}.json
frontend/tests/<focused tests>.test.js
```

No new frontend dependency or endpoint is required.

## Testing strategy

### Backend integration tests

- create and update a Smart Assistant;
- reject empty, duplicate, system, self, archived, and nested members;
- create a Smart Session without the global Smart Collaboration setting;
- snapshot members into the Session and Run;
- reject user edits to a Smart Session range;
- preserve editable ranges for the ad-hoc system coordinator;
- preserve all existing direct Assistant and Smart Collaboration tests.

### Frontend tests

- admin payload contains routing mode and member UUIDs;
- wizard gating requires a member for Smart mode;
- chat distinguishes ad-hoc Smart Collaboration and Smart Assistant payloads;
- configured participants are read-only while `@Assistant` remains available;
- all new localization keys exist in Chinese, English, and Spanish.

### Browser acceptance

Use `ego-browser`, not Playwright, to verify:

1. an administrator creates a Smart Assistant with two members;
2. it appears in the Assistant list and switcher;
3. opening it shows the configured member summary without edit controls;
4. a new Session receives the configured members;
5. `@Assistant` can select a subset for one message;
6. desktop and 390-pixel mobile layouts have no horizontal overflow.

## Commands

```shell
# Backend focused tests
docker exec sourcelens-api-dev \
  python manage.py test lens.tests.test_api --keepdb --verbosity 1

# Frontend unit tests
cd frontend && npm test

# Frontend lint and production build
cd frontend && npm run lint
cd frontend && npm run build

# Diff hygiene
git diff --check
```

If a migration is added, restart `sourcelens-api-dev` once so the development
entrypoint applies it. Ordinary API code remains hot-reloaded; worker code is
not expected to change.

## Boundaries

### Always

- Preserve existing ad-hoc Smart Collaboration behavior and URLs.
- Keep new API fields additive and default existing Assistants to direct.
- Snapshot membership at Session creation.
- Recheck member status and access before execution.
- Add failing tests before behavioral implementation.
- Use localized UI text and existing design tokens/components.

### Ask first

- Remove or replace the existing `/lens/chat` ad-hoc entry.
- Allow nested collaboration Assistants.
- Let a Smart Assistant bypass a member Assistant's access grants.
- Retroactively update existing Sessions after membership edits.

### Never

- Store member configuration only in frontend state.
- Resolve members by mutable names or slugs in runtime snapshots.
- Expose the hidden system coordinator in Assistant lists.
- Modify agentcore packages for this feature.
- Run Playwright for acceptance unless explicitly requested.

## Success criteria

- Administrators can create and edit a Smart Assistant from
  the existing Assistant wizard.
- Users can select it from the normal Assistant switcher and start a chat.
- New Sessions automatically use exactly the configured accessible member set.
- Users cannot edit the configured member range.
- Per-message multi-Assistant mentions work inside that range.
- Historical Sessions and Runs retain their original member snapshots.
- Existing direct Assistants and ad-hoc Smart Collaboration continue to pass
  regression tests.
- Backend tests, frontend tests, lint, build, diff checks, and ego-browser
  acceptance pass.

## Review decisions requested

Before implementation, confirm these two product decisions:

1. Keep the existing ad-hoc Smart Collaboration entry alongside Smart
   Assistants.
2. Membership edits affect only new Sessions; existing Sessions keep their
   original member snapshot.
