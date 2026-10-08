# Public repository datasource connections

Date: 2026-10-08

GitHub and GitLab public datasources accept repository addresses without a
user-managed Connection. The default wizard choice is anonymous public access;
private resources continue to use an explicitly selected authentication Connection.

The host normalizes repository addresses and checks project visibility and access
to the selected ref anonymously before saving. GitLab URLs identify the instance
origin and support nested namespaces; one datasource cannot mix instance origins.
Plain project paths default to GitLab.com or the datasource's existing public endpoint.

Successful saves create or reuse a system-owned Connection for the provider and
origin. Its unique `system_key` makes reuse safe across concurrent saves, and its
stable UUID also bounds host HTTP client reuse during unsaved preflight requests.
These connections have no secret, are excluded from user Connection CRUD, and
never widen a user-managed Connection's allowlist. Preflight does not persist
connections. Snapshots, leases, and invocation audits retain the existing contract.

Anonymous Git synchronization uses `auth_scheme: none` and omits token material;
GitLab anonymous synchronization also disables submodules. Existing credentialed
GitLab synchronization retains its submodule behavior.

Apply migration `0064_connection_system_key` and release the backend, frontend,
and bundled GitLab plugin together. This migration adds a nullable field and does
not rewrite existing user connections or datasources.
