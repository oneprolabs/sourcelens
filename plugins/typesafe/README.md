# TypeSafe AI Plugin

This built-in SourceLens Plugin exposes TypeSafe AI System One as a bounded,
read-only decision capability. It does not replace the primary chat model and
does not provide datasource synchronization.

The Connection form contains three administrator-supplied fields:

- **Endpoint** — an HTTPS root endpoint, prefilled with
  `https://api.typesafe.ai`.
- **Jev model** — the model identifier, prefilled with `jev-1.13.0`.
- **API token** — required and stored as an encrypted secret.

The endpoint and model presets remain editable so an administrator can point to
a compatible gateway or model alias. The runtime removes credentials from
snapshots and sends only the selected typed question to `/v1/systemone`.

The tools are:

- `typesafe_noul` for a yes/no probability.
- `typesafe_choice` for one option from a JSON object rubric.
- `typesafe_score` for an ordered JSON array rubric with two to ten levels.

Requests are bounded to prevent oversized state, rubrics, and responses. HTTP
429 and 529 responses use a short capped retry policy; authentication, invalid
input, redirects, and malformed responses fail closed with stable error codes.

The API token is never placed in prompts, Run snapshots, tool results, or audit
payloads.
