# Optional decision models

Date: 2026-10-12

SourceLens uses `agentcore-metering >= 0.4.0` to configure decision models in
the same model management page as chat models. Assistants can select one
active global model of type `decision`; this does not require a Plugin
connection. The agentcore provider schema remains the source of provider,
model and parameter metadata.

The decision model is optional. With no model selected, SourceLens freezes an
empty decision configuration. LensNode retains `ModelDecisionPolicy` and
`NullDecisionRanker`, creates no built-in decision requests or model events,
and preserves the original routing, evidence handling and candidate ordering.
With a model selected, the existing decision seam performs evidence checks
and optional ranking. Timeouts, refusal and unavailable decisions fall back
to the existing policy.

The host owns gate definitions, thresholds and ranking rubrics. An Assistant
can use automatic gates for its capability or explicitly choose gates and
analyses. Each Run freezes the model UUID and resolved policy, including
delegated Runs. Provider credentials stay in the control plane; LensNode
calls an authenticated gateway authorized against the Run's frozen policy.
The gateway uses `DecisionTracker`, shares the parent Run's token budget,
and correlates metered usage with the Run and decision call. Decision model
events distinguish this usage from agent model calls. Refusals retain usage
without being treated as successful decisions.
New Runs with a selected decision model also authorize the explicit
`decision_score` tool. It accepts supplied facts, assessment instructions and
2–10 ordered rubric labels, and returns a zero-based numeric rubric position.
It uses the selected model verbatim, requires no ranking configuration, and
does not count as retrieved evidence or proof of completion. Unavailable or
refused scores are reported without inventing a rating. The tool permits at
most 16 attempts per Run and retains that counter in checkpoint state.
The Run freezes a scoring timeout from the model configuration, bounded to
1–30 seconds; legacy Run snapshots do not gain scoring authorization.
Skills and workspace guides should request `decision_score` rather than the
retired provider-specific `typesafe_score` tool. Source coverage gaps still
produce a partial outcome even when a report file was delivered successfully.
Built-in ranking scores candidates sequentially to respect the gateway's
shared budget lock. Each candidate retains its own timeout, so a full ranking
window can take longer than one decision call.

Portable predicate answers carry a probability. Choice answers carry a
selected label and score answers carry a numeric rubric position. SourceLens
does not invent probability distributions or confidence for choice and score
answers. Existing Plugin contracts remain supported for frozen legacy Runs.

Migration `lens.0064_builtin_decisions` adds fields without deleting existing
bindings. It migrates usable active TypeSafe connections into global decision
models and copies each Assistant's gate and analysis settings. Retaining
bindings lets the old color keep serving during a blue/green upgrade. The new
color always excludes the retired TypeSafe Plugin from new Run snapshots and
hides its Plugin and connection management entries. Clearing the model selection
cannot re-enable Plugin decisions. The old runtime package is retained only to
finish previously frozen Runs during the transition; users configure one
decision model without a built-in/Plugin distinction.

Upstream API reference:
[agentcore Decisions](https://github.com/cloud2ai/agentcore-metering/blob/v0.4.0/docs/decisions.md).
