# TypeSafe AI Plugin

This built-in SourceLens Plugin exposes TypeSafe AI System One as a bounded,
read-only decision capability. The primary Agent model still owns prose,
reasoning, and the workflow; this Plugin only supplies typed judgments that
code can consume. It does not provide datasource synchronization.

The Connection form contains three administrator-supplied fields:

- **Endpoint** — an HTTPS root endpoint, prefilled with
  `https://api.typesafe.ai`.
- **Jev model** — the System One model identifier, prefilled with
  `jev-1.13.0`.
- **API token** — required and stored as an encrypted secret.

The endpoint and model presets remain editable so an administrator can point to
a compatible gateway or model alias, and the endpoint accepts an optional base
path. A connection validation uses the authenticated, non-billable
`GET /v1/models` endpoint and returns the available model names. The runtime
removes credentials from snapshots and sends only the selected typed question to
`/v1/systemone`.

The tools are:

- `typesafe_noul` — one bounded yes/no question, returning a probability.
- `typesafe_choice` — one option from a bounded, mutually exclusive JSON object rubric.
- `typesafe_score` — a probability-weighted position on an ordered JSON array rubric of two to ten levels.

## Judgments

Each tool maps to one System One primitive:

- **Noul** returns a single probability from 0 to 1 and has no confidence field.
  A value near 0.5 means yes and no are similarly likely, not medium intensity.
  One Noul answers one label, so when several labels may each apply, issue one
  Noul per label instead of a single Choice.
- **Choice** selects one option from a defined set. Its distribution compares
  competing, mutually exclusive options and sums to 1. Include a no-match option
  when nothing may fit.
- **Score** returns a probability-weighted position on ordered levels. Score
  levels must describe concrete situations and stand on their own. For graded
  ranking, score every candidate with the same comparable rubric and sort in
  code; a single call does not rank a set.

`confidence` on Choice and Score summarizes distribution concentration, not
overall correctness or permission to act. Typed output guarantees the interface,
not truth: thresholds belong to the caller and should be validated on the
target data. Low confidence need not invalidate a harmless preference choice,
and uncertainty on unused branches can be ignored.

## Arguments

`state` is text or a JSON-encoded object/array string; structured state is
encoded into the string rather than passed as a native object, and multi-part
state should use named fields. `instructions` carries the complete meaning of
the judgment. `criteria` is a JSON-encoded **string**, never a native array or
object: for `choice` its content is a JSON object mapping each option to a
description or `null`; for `score` its content is a JSON array of two to ten
non-empty level descriptions. A `score` result and its probability keys use
zero-based rubric indexes, so map them back to labels with the returned `legend`
instead of adding one.

## Safety

Requests are bounded to prevent oversized state, rubrics, and responses. HTTP
429 and 529 responses use a short capped retry policy; authentication, invalid
input, redirects, and malformed responses fail closed with stable error codes.

The API token is never placed in prompts, Run snapshots, tool results, or audit
payloads.
