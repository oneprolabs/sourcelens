# ADR-001: Platform-level connections for DataSource, Skill, and MCP

## Status

Superseded (2026-10-09 documentation cleanup).

This records the original 2026-07-20 proposal, not the current implementation.
The implemented integration contract is documented in
[Plugin Protocol V1](../plugin-protocol.md), with filesystem package ownership in
[Plugin Package Contract](../spec-plugin-release-lifecycle.md).

## Date

2026-07-20

## Context

SourceLens currently manages GitHub and Feishu credentials through the
DataSource domain. Although `DataSourceCredential` is stored separately, its
fields and lifecycle are still DataSource-specific. Uploaded Skills cannot
declare required parameters or select reusable authorization, while MCP
configuration may also need external secrets.

The platform needs a reusable connection boundary without putting plaintext
secrets into DataSource, Skill, MCP, or persistent Run snapshots.

## Proposed Decision

Introduce a platform-level connection capability with these initial
responsibilities:

- `ConnectionProvider` defines connection schema, accepted authentication
  shapes, validation, capabilities, and runtime authentication behavior.
- `SavedConnection` binds a provider, endpoint/audience, non-secret config,
  encrypted credential version, owner, and grants.
- `DataSourceConnector` remains responsible for source-specific configuration
  and synchronization behavior.
- A versioned Skill capability manifest declares ordinary parameters,
  required providers/capabilities, and accepted authentication modes.
- Consumer bindings store only `SavedConnection` references.
- Persistent Run snapshots store non-secret references and immutable versions,
  never resolved secrets.

For the first version, reuse the complete `SavedConnection` instead of
allowing arbitrary credential material to be rebound to unrelated endpoints.

## Security Constraints

- Uploaded Skill scripts are untrusted by default.
- Raw PATs, passwords, and App Secrets are not default Skill materialization
  modes.
- Standard Skills use narrow, connection-bound Agent Tools where practical.
- Trusted Skills may receive short-lived, audience-bound access tokens.
- LensNode child processes use a minimal explicit environment and never inherit
  platform secrets.
- Endpoint changes, capability expansion, and Skill manifest changes require
  revalidation or reapproval.
- Authorization checks must define the effective actor for interactive Runs,
  shared Assistants, scheduled DataSource tasks, retries, and node execution.

## Alternatives Considered

### Keep credentials under DataSource

Rejected because Skill and MCP would either duplicate credential storage or
depend on the DataSource domain.

### Globally reusable CredentialSet with arbitrary Connection binding

Deferred because rebinding shared secrets to mutable or attacker-controlled
endpoints creates credential-forwarding risks. This may be reconsidered with
strict provider, audience, and approval constraints.

### Build an external Provider plugin project immediately

Deferred because the provider contract, migration path, and security model are
not stable. GitHub and Feishu should initially ship as built-in adapters behind
a stable internal boundary.

### Give every Skill raw environment credentials

Rejected as the default because uploaded scripts can exfiltrate any plaintext
secret they receive. Raw credential access requires an explicit privileged
trust tier and separate approval.

## Consequences

- DataSource becomes the first consumer of a platform capability instead of
  its owner.
- Skill and MCP integration can reuse connection selection, grants, validation,
  audit, and rotation.
- Existing `DataSourceCredential` data requires a staged, reversible migration;
  this is not a simple foreign-key replacement.
- Runtime authorization, LensNode isolation, and Skill trust policies must be
  designed before plaintext credential delivery is implemented.
- Provider plugins, broad database support, and multiple materialization modes
  remain out of the initial scope.

## Unresolved Decisions

- Effective authorization actors and shared Assistant behavior.
- Exact grant operations and ownership scopes.
- Backend, LensNode, or proxy execution location per provider.
- First supported Skill authentication mode.
- SSRF and private-network endpoint policy.
- Credential rotation, revocation, retry, and lease idempotency semantics.
- DataSourceCredential migration and rollback protocol.
