"""Uniform decision seams for one Run: control flow and ranking."""

import json

from langchain.tools import tool
from pydantic import BaseModel, Field

from ..decision_contract import aggregate_ranked, validate_decision_result
from ..plugin_package_loader import (
    PluginPackageLoadError,
    load_runtime_contract,
)
from .decision_gates import DecisionRunner, gate_bindings, run_decision_tool
from .routing import (
    _enforce_route_evidence_invariants,
    _message_needs_retrieval,
    _normalize_route_evidence_capabilities,
    _select_general_chat_route,
)

RANK_TOOL_NAME = "decision_rank"
RANK_SOURCE = "decision_rank"
RANK_MAX_CANDIDATES = 8
RANK_TIMEOUT_S = 3.0
RANK_RUN_BUDGET = 2


class ControlDecisionPolicy:
    """Control-flow decision seam for one Run.

    The runtime always calls these methods; configuration only changes which
    implementation is assembled.  One method maps to exactly one decision
    point and one gate key, so document/code and general_chat semantics never
    share a gate:

    - ``needs_retrieval`` -> gate ``search_needed``       (doc/code only)
    - ``select_route``    -> gate ``evidence_requirement`` (general_chat only)
    """

    def needs_retrieval(self, model, question, history=None):
        """Return whether the message requires workspace retrieval."""

        raise NotImplementedError

    def select_route(self, model, question, **context):
        """Return one route decision, optionally gate-corrected."""

        raise NotImplementedError

    def post_run_checks(self, question, answer, evidence=None):
        """Return advisory post-run gate verdicts; never blocking."""

        return {}


class ModelDecisionPolicy(ControlDecisionPolicy):
    """Default policy: today's primary-model classification, unchanged."""

    def __init__(self, needs_retrieval=None, select_route=None):
        self._needs_retrieval = needs_retrieval or _message_needs_retrieval
        self._select_route = select_route or _select_general_chat_route

    def needs_retrieval(self, model, question, history=None):
        return self._needs_retrieval(model, question, history)

    def select_route(self, model, question, **context):
        return self._select_route(model, question, **context)


class GateDecisionPolicy(ControlDecisionPolicy):
    """Decorator: try a bound gate, then apply its declared fallback.

    Each gate declares one of two fallbacks in the host registry:

    - ``fallback="model"`` — the inner policy keeps today's classifier as the
      default (``search_needed``, ``evidence_requirement``);
    - ``fallback="fixed"`` — no model equivalent exists, so a constant
      fail-safe applies (``evidence_sufficient``, ``answer_supported``).
    """

    def __init__(self, inner, runner, gates):
        self.inner = inner
        self.runner = runner
        self.gates = gates or {}

    def needs_retrieval(self, model, question, history=None):
        value = self.runner.evaluate(
            "search_needed",
            question=question,
            history=history,
        )
        if value is None:
            return self.inner.needs_retrieval(model, question, history)
        threshold = (self.gates.get("search_needed") or {}).get("threshold")
        if not isinstance(threshold, (int, float)):
            return self.inner.needs_retrieval(model, question, history)
        return value >= threshold

    def select_route(self, model, question, **context):
        route = self.inner.select_route(model, question, **context)
        value = self.runner.evaluate(
            "evidence_requirement",
            question=question,
            history=context.get("history"),
            tools=context.get("available_tools"),
        )
        if value is None:
            return route
        proposal = _propose_evidence(
            route,
            value,
            self.gates.get("evidence_requirement") or {},
        )
        if proposal is None:
            return route
        route["evidence_requirement"] = proposal
        route = _normalize_route_evidence_capabilities(
            route,
            context.get("available_tools"),
            has_bound_skills=bool(context.get("has_bound_skills")),
        )
        return _enforce_route_evidence_invariants(route)

    def post_run_checks(self, question, answer, evidence=None):
        """Return advisory post-run gate verdicts; never blocking.

        Only gates the binding actually declares are evaluated, so an
        unbound gate adds no traffic and no events.
        """

        verdicts = {}
        state_text = _post_run_state(question, answer, evidence)
        if "evidence_sufficient" in self.gates:
            threshold = (self.gates.get("evidence_sufficient") or {}).get(
                "threshold"
            )
            value = self.runner.evaluate(
                "evidence_sufficient",
                question=state_text,
            )
            if value is None or not isinstance(threshold, (int, float)):
                verdicts["evidence_sufficient"] = self.runner.default_verdict(
                    "evidence_sufficient"
                )
            else:
                verdicts["evidence_sufficient"] = value >= threshold
        if "answer_supported" in self.gates:
            option = self.runner.evaluate_choice(
                "answer_supported",
                question=state_text,
            )
            verdicts["answer_supported"] = (
                option
                or self.runner.default_verdict("answer_supported")
            )
        return verdicts


def build_decision_policy(
    command,
    config,
    http_client,
    plugin_http_pool=None,
    emit_event=None,
    run_uuid="",
    inner=None,
):
    """Assemble the control-decision seam for one Run.

    The only branch happens here: with no bound gate the inner policy is
    returned unchanged, so every call site stays unconditional.
    """

    policy = inner or ModelDecisionPolicy()
    gates = gate_bindings(command or {})
    if not gates:
        return policy
    runner = DecisionRunner(
        command,
        config,
        http_client,
        plugin_http_pool=plugin_http_pool,
        emit_event=emit_event,
        run_uuid=run_uuid,
    )
    return GateDecisionPolicy(policy, runner, gates)


def _post_run_state(question, answer, evidence=None):
    """Build the bounded post-run gate state from question, answer, evidence."""

    parts = [
        str(question or "").strip(),
        "",
        f"Answer:\n{str(answer or '').strip()}",
    ]
    if isinstance(evidence, dict) and evidence:
        parts.extend(
            [
                "",
                "Runtime evidence:",
                json.dumps(
                    evidence,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )[:2000],
            ]
        )
    return "\n".join(parts).strip()


def _propose_evidence(route, value, config):
    """Map one gate value onto an evidence requirement proposal.

    The gate only proposes the ``evidence_requirement`` field; the route type
    stays owned by the inner policy, and the deterministic invariant is
    re-applied by the caller.
    """

    threshold = config.get("threshold")
    margin = config.get("margin")
    if not isinstance(threshold, (int, float)) or not isinstance(
        margin, (int, float)
    ):
        return None
    if value >= threshold + margin:
        if "artifact_delivery" in (route.get("required_capabilities") or []):
            return "artifact"
        return "tool_result"
    if value <= threshold - margin:
        return "none"
    return None


def analysis_bindings(command):
    """Return bound analysis config keyed by analysis name.

    Each entry carries the frozen Tool identity and rubric resolved by the
    backend at dispatch time.
    """

    index = {}
    for entry in command.get("decision_analyses") or []:
        if not isinstance(entry, dict):
            continue
        analyses = entry.get("analyses")
        if not isinstance(analyses, dict):
            continue
        for key, config in analyses.items():
            if key in index:
                continue
            index[key] = {
                **(config if isinstance(config, dict) else {}),
                "plugin_key": str(entry.get("plugin_key") or ""),
                "plugin_version": str(entry.get("plugin_version") or ""),
                "connection_uuid": str(entry.get("connection_uuid") or ""),
            }
    return index


class NullDecisionRanker:
    """Ranker used when no analysis Decision is bound."""

    def rank(self, decision, candidates, instructions=""):
        """Return None so callers keep their own ordering."""

        return None

    def as_tools(self):
        """Expose no model tool."""

        return []


class DecisionRanker:
    """Rank candidates through one bound analysis Decision."""

    def __init__(
        self,
        command,
        config,
        http_client,
        plugin_http_pool=None,
        emit_event=None,
        run_uuid="",
    ):
        self._command = command or {}
        self._config = config
        self._http_client = http_client
        self._plugin_http_pool = plugin_http_pool
        self._emit = emit_event
        self._run_uuid = str(run_uuid or "")
        self._bindings = analysis_bindings(self._command)
        self._used = 0
        self._cache = {}

    def rank(self, decision, candidates, instructions=""):
        """Return one deterministic ranking, or None when unavailable."""

        binding = self._bindings.get(decision)
        if not binding or not binding.get("tool_key"):
            return None
        if binding.get("kind") != "score":
            return None
        if self._used >= RANK_RUN_BUDGET:
            return None
        bounded = _bounded_candidates(candidates)
        if not bounded:
            return None
        self._used += 1
        entries = []
        failed = []
        for index, candidate in enumerate(bounded):
            result, reason = self._score(
                decision,
                binding,
                candidate,
                instructions,
                index,
            )
            if result is None:
                failed.append({"label": candidate["label"], "reason": reason})
                continue
            entries.append((index, candidate["label"], result))
        if not entries:
            return None
        ranked = aggregate_ranked(entries)
        return {
            "ok": True,
            "decision": decision,
            "ranked": [
                {
                    "label": item.label,
                    "score": item.score,
                    "probability": item.result.value,
                    "legend": item.result.legend,
                }
                for item in ranked
            ],
            "failed": failed,
            "usage": _merged_usage(ranked),
        }

    def as_tools(self):
        """Return the model-facing decision_rank Tool when configured."""

        if not self._bindings:
            return []
        return [build_decision_rank_tool(self)]

    def _score(self, decision, binding, candidate, instructions, index):
        tool_key = str(binding.get("tool_key") or "")
        plugin_key = str(binding.get("plugin_key") or "")
        plugin_version = str(binding.get("plugin_version") or "")
        connection_uuid = str(binding.get("connection_uuid") or "")
        if not plugin_key or not plugin_version or not connection_uuid:
            return None, "unavailable"
        cache_key = (plugin_key, tool_key, candidate["content"], instructions)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached, ""
        try:
            contract = load_runtime_contract(plugin_key, plugin_version)
        except PluginPackageLoadError:
            return None, "error"
        project = getattr(contract, "project_decision", None)
        if not callable(project):
            return None, "error"
        arguments = _score_arguments(binding, candidate["content"], instructions)
        if arguments is None:
            return None, "error"
        encoded, reason = run_decision_tool(
            self._command,
            self._config,
            self._http_client,
            self._plugin_http_pool,
            contract,
            connection_uuid,
            plugin_key,
            plugin_version,
            tool_key,
            arguments,
            f"rank:{decision}:{index}",
            RANK_SOURCE,
            self._tagged_emit,
            RANK_TIMEOUT_S,
        )
        if encoded is None:
            return None, reason
        try:
            payload = json.loads(encoded)
        except (TypeError, ValueError):
            return None, "invalid_response"
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return None, "error"
        result = validate_decision_result(
            project(tool_key, payload),
            binding.get("kind") or "",
        )
        if result is None:
            return None, "invalid_response"
        self._cache[cache_key] = result
        return result, ""

    def _tagged_emit(self, event, payload):
        if isinstance(payload, dict):
            payload = {**payload, "source": RANK_SOURCE}
        if self._emit is not None:
            self._emit(event, payload)


def build_decision_ranker(
    command,
    config,
    http_client,
    plugin_http_pool=None,
    emit_event=None,
    run_uuid="",
):
    """Assemble the ranking seam, or a Null ranker when unconfigured."""

    if not analysis_bindings(command or {}):
        return NullDecisionRanker()
    return DecisionRanker(
        command,
        config,
        http_client,
        plugin_http_pool=plugin_http_pool,
        emit_event=emit_event,
        run_uuid=run_uuid,
    )


def _bounded_candidates(candidates):
    bounded = []
    for candidate in candidates or []:
        if hasattr(candidate, "model_dump"):
            candidate = candidate.model_dump()
        if not isinstance(candidate, dict):
            continue
        label = str(candidate.get("label") or "").strip()[:120]
        content = str(candidate.get("content") or "").strip()
        if not label or not content:
            continue
        bounded.append({"label": label, "content": content})
        if len(bounded) >= RANK_MAX_CANDIDATES:
            break
    return bounded


def _score_arguments(binding, content, instructions):
    rubric = binding.get("rubric")
    if (
        not isinstance(rubric, list)
        or not 2 <= len(rubric) <= 10
        or any(not isinstance(level, str) or not level for level in rubric)
    ):
        return None
    question = str(instructions or binding.get("summary") or "").strip()
    if not question:
        question = (
            "Score the candidate against this ordered rubric: "
            + ", ".join(rubric)
        )
    return {
        "state": content[:100000],
        "instructions": question[:2000],
        "criteria": json.dumps(rubric, ensure_ascii=False),
    }


def _merged_usage(ranked):
    usage = {}
    for item in ranked:
        for key, value in (item.result.usage or {}).items():
            if type(value) is int:
                usage[key] = usage.get(key, 0) + value
    return usage


class _RankCandidate(BaseModel):
    """One candidate accepted by the decision_rank model tool."""

    label: str = Field(
        description="Short stable identifier for the candidate.",
    )
    content: str = Field(description="Full candidate text to score.")


class _RankArguments(BaseModel):
    """Arguments accepted by the decision_rank model tool."""

    decision: str = Field(description="Analysis decision key to rank with.")
    candidates: list[_RankCandidate] = Field(
        description="Candidates to score and order.",
    )
    instructions: str = Field(
        default="",
        description="What 'better' means for this ranking.",
    )


def build_decision_rank_tool(ranker):
    """Create the model-facing decision_rank Tool bound to one ranker."""

    @tool(RANK_TOOL_NAME, args_schema=_RankArguments)
    def decision_rank(decision: str, candidates: list, instructions: str = ""):
        """Rank candidate options with one bound Decision analysis."""

        result = ranker.rank(decision, candidates, instructions)
        if result is None:
            return json.dumps(
                {"ok": False, "error": "DECISION_RANK_UNAVAILABLE"},
                ensure_ascii=False,
            )
        return json.dumps(result, ensure_ascii=False)

    # Deliberately no ``capability_family``: decision_rank is a semantic
    # ranking call, not external evidence, so it must not register as an
    # evidence capability (CapabilityBoundaryMiddleware._capability_for_tool).
    decision_rank.metadata = {
        **(getattr(decision_rank, "metadata", None) or {}),
        "capability": "decision.rank",
        "decision_source": RANK_SOURCE,
    }
    return decision_rank
