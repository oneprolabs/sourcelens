# GitHub public repositories

GitHub connections support optional personal access tokens. A connection with no
secret version uses anonymous access; an existing disabled, empty, or unreadable
secret never falls back to anonymous access. Leaving the token blank while
editing preserves the stored token.

Connections retain their repository scope. The `*` scope on an anonymous
connection means public repositories, with no account repository discovery.
Explicit scopes still restrict manually entered repositories.

The datasource wizard selects the source type on its first page. Its second page
collects the name, connection, and repositories. No example repository is
preselected. Users can select discovered repositories or enter `owner/repo` or a
GitHub HTTPS URL, with an optional `.git` suffix. Other hosts, embedded credentials,
ports, query strings, and repository subpaths are rejected.

`POST /api/lens/admin/connections/{uuid}/validate-datasource/` normalizes repository
addresses, enforces the connection scope, and checks repository access and the
selected or default ref through a lightweight GitHub commit listing. Results
include the default branch and visibility. Saving an active datasource repeats
this check on the server; disabling an existing datasource does not require
remote access. Advancing the wizard reuses an unchanged successful check.
Changing the selection invalidates the browser's previous result, including any
late response from a previous request.

Anonymous sync still uses execution snapshots and node-bound leases. Material
responses explicitly set `authentication: anonymous` and an empty `value`; an
empty value without this marker is invalid. GitHub generates a Git command with
`auth_scheme: none`, without an access token. Git disables credential helpers,
global credentials, netrc credentials, auth headers, and interactive prompts for this mode.
Backend, bundled plugins, and LensNode must be updated together for anonymous
sync. No database migration is required.

Anonymous GitHub API calls have lower rate limits. GitHub online code search
requires authentication and returns `GITHUB_AUTHENTICATION_REQUIRED` without a
token. Local searches over synchronized files remain available. API validation
checks remote access from the backend; actual clone/fetch also requires GitHub
connectivity from the LensNode.
