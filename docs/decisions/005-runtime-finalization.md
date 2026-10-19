# Unified runtime finalization

Date: 2026-10-19

## Problem

Graph executions applied completion checks, attachment requirements, execution
outcomes and final Decision verdicts in separate blocks of the runtime. The
terminal result could therefore be changed after its execution outcome had
already been calculated. Graph-only review evidence also needs to survive a
forced final synthesis or host replacement of the proposed answer.

## Decision

Keep the existing deepagents loop, frozen capabilities, budgets, checkpoint
recovery and staged attachment publication. Extract final acceptance into
`agent_runtime/finalization.py` rather than rebuilding the agent loop.

After execution, the finalizer refreshes extension evidence, checks completion
and delivery, reviews the selected answer, and resolves the terminal outcome.
It returns a `FinalizedRun` snapshot of the answer, outcome, termination detail,
stop reason and accepted attachment manifest. The existing command manifest
remains the adapter to the backend's final-message publication protocol.

Execution facts and review verdicts enter one outcome resolver. An earlier
execution, coverage or completion failure cannot be erased by a successful
answer review. Verified attachments remain available for partial source
coverage; a failed completion check withdraws publication candidates. Missing
attachments cannot satisfy an explicit artifact request.

During execution, middleware can still request bounded corrections. Final
acceptance does not restart the agent loop or grant additional retry budgets.

The execution stream passes tool material to the evidence middleware even if a
budget or turn limit prevents a first candidate answer. The middleware retains
its latest nonempty bounded tool view outside the graph messages. A changed
answer is reviewed against that material and the current extension evidence;
new observed tool material invalidates an older answer verdict. Truncation and
source-coverage flags remain distinct. This is not an unbounded history store
or proof that all original source data was read.

An unavailable final reviewer returns an inconclusive, partial result while
preserving existing output, rather than dropping the answer or declaring it
verified. Cancellation and hard gateway budget exceptions still propagate;
cancellation is also checked after final review. The publication manifest is
the canonical attachment state when reconciling it with runtime evidence.

## Validation

Characterization tests cover source gaps, incomplete review material,
unsupported answers, rejected completion, missing artifacts, budget wrap-up,
attachment snapshots and cancellation. Existing retrieval, report, delivery,
checkpoint and runtime-contract tests remain regression guards.

Real-model acceptance uses the current devctl source and ego-browser: the same
activity-report question, ordinary document Q&A, and an HTML artifact with a
private scratch draft. Partial coverage is an expected outcome when upstream
data is unavailable; it must not be relabelled as complete.

## Boundaries

This change does not replace deepagents, change provider authorization, promise
complete upstream data, or make natural-language verification a proof of
correctness. The activity-report workflow remains an extension in this
increment. Broader recovery-policy and path-namespace changes require their own
contracts and regression slices.
