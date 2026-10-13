# GitHub activity summary repository selectors

`github_activity_summary` accepts up to 50 unique selectors in `repositories`:

| Selector | Selection |
| --- | --- |
| `owner/repository` | One repository. Explicit unauthorized requests are rejected. |
| `owner/*` | Accessible, authorized repositories belonging to that owner. |
| `*` | All repositories within the Connection's authorized enumeration scope. |

Selectors are case-insensitive and overlapping expansions are deduplicated.
Single-repository tools still require an explicit `owner/repository`.

## Authorization and discovery

The Tool snapshot intersects the Run's frozen GitHub scope with the Connection's
current scope. Broadening a Connection does not broaden an admitted Run, and
tightening a Connection restricts subsequent Tool snapshots.

A finite Connection scope is expanded locally. When the effective scope is `*`,
the Plugin discovers repositories with the leased credential: `owner/*` uses
`/orgs/{owner}/repos`, while `*` uses `/user/repos`. An organization lookup returning
404 falls back to `/users/{owner}/repos` for public repositories and, when
authenticated, adds owner-filtered `/user/repos` results to preserve private
access. Mixed `*` and `owner/*` selectors are discovered separately and merged,
so explicitly selected external public owners remain included. All paths stay on GitHub's fixed
API origin and discovered names are validated before activity requests.

Anonymous `owner/*` enumerates public organization repositories, or public user
repositories after the organization lookup returns 404. Anonymous `*` works with
a finite Connection scope. With an unrestricted scope it is rejected with
`GITHUB_AUTHENTICATION_REQUIRED` and an owner-selector recovery hint, because there
is no bounded enumeration of every public repository on GitHub.

## Coverage and limits

The Plugin handles discovery pagination and activity batches itself. Each
discovery selector can read at most 20 pages of 100 repositories, and a summary
can query at most 500 repositories, in batches of 50 with up to eight concurrent
resource requests. Existing per-resource activity limits still apply.

The response retains `repositories` and adds:

- `coverage.requested_selectors`: the normalized requested scope.
- `coverage.resolved_repositories`: the concrete repositories actually queried.
- `coverage.discovery_complete`: whether repository discovery completed.
- `coverage.repositories_truncated` and top-level `repositories_truncated`: whether
  the repository limit or incomplete discovery prevented full selection coverage.
- `coverage.reasons`: `repository_limit`, `discovery_page_limit`,
  `discovery_stalled`, or `discovery_failed` when applicable.
- `coverage.discovery_errors`: stable errors attributed to discovery selectors.
- `coverage.limits`: repository and discovery-page limits.
- `source_coverage_complete`: false for incomplete discovery, omitted repositories,
  per-resource truncation or failed activity requests.

Discovery and per-repository failures preserve available results. A successful
empty selection is distinct from an empty result after failed discovery. The
evidence ledger and Run outcome checks retain these coverage limitations.

GitHub selector and retrieval instructions live in the Plugin Tool description
and input schema. Its runtime registers a Plugin-owned report workflow through the generic Agent
extension protocol. Only model-visible tools in the Assistant's frozen binding
participate. Report recognition, routing, saved evidence, and delivery policies
live under `plugins/_shared`; the core runtime contains no report workflow.

Report agents should issue one summary for the requested scope. A rejected
`TOOL_ARGUMENTS_INVALID` request has retrieved no evidence: correct the selectors
and retry once. A returned partial summary remains the retrieval boundary;
disclose its limits instead of scanning repositories individually.

Backend and LensNode must be upgraded together. No database migration is required.
