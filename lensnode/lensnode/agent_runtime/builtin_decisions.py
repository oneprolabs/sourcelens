"""Portable decision calls through the authenticated SourceLens gateway."""

import json
import math
import threading
import time
import uuid

import httpx
from langchain.tools import tool
from pydantic import BaseModel, Field, field_validator

from ..decision_contract import DecisionResult


class _ScoreArguments(BaseModel):
    """A bounded assessment with a user-supplied ordered rubric."""

    state: str = Field(min_length=1, max_length=100000, description="Facts to assess; disclose missing evidence.")
    instructions: str = Field(min_length=1, max_length=2000, description="What to assess from these facts.")
    criteria: list[str] = Field(min_length=2, max_length=10, description="Ordered rubric labels, worst to best.")

    @field_validator("criteria")
    @classmethod
    def validate_labels(cls, labels):
        """Reject empty or oversized rubric levels."""

        if any(not label.strip() or len(label) > 1000 for label in labels):
            raise ValueError("Rubric labels must be non-empty and at most 1000 characters.")
        return labels


def build_decision_score_tool(command, config, client, emit_event=None, runtime_state=None):
    """Expose explicit scoring only when the frozen Run authorizes it."""

    selected = command.get("decision_model") or {}
    if not selected.get("model_ref") or (selected.get("score") or {}).get("kind") != "score":
        return None
    binding = {**selected["score"], "model_ref": selected["model_ref"]}
    score_state = runtime_state if runtime_state is not None else {}
    lock = threading.Lock()

    @tool("decision_score", args_schema=_ScoreArguments)
    def decision_score(state: str, instructions: str, criteria: list[str]):
        """Score supplied facts using the selected decision model and a 2–10 level ordered rubric.

        The score is a zero-based rubric position, not proof of completion or confidence.
        On failure disclose that scoring is unavailable; never invent a replacement score.
        """

        with lock:
            used = int(score_state.get("used") or 0)
            if used >= 16:
                return json.dumps({"ok": False, "error": "DECISION_SCORE_UNAVAILABLE", "reason": "budget_exceeded"})
            score_state["used"] = used + 1
            result, reason = call_builtin_decision(
                command, config, client, binding, "score",
                {"state": state, "instructions": instructions, "criteria": json.dumps(criteria, ensure_ascii=False)},
                f"score:{uuid.uuid4().hex}", "decision_score", binding.get("timeout_seconds", 3) + 1, emit=emit_event,
            )
        if result is None:
            return json.dumps({"ok": False, "error": "DECISION_SCORE_UNAVAILABLE", "reason": reason})
        return json.dumps({"ok": True, "score": result.value, "legend": result.legend, "usage": result.usage})

    decision_score.metadata = {"capability": "decision.score", "decision_source": "decision_score"}
    return decision_score


def call_builtin_decision(command, config, client, binding, name, arguments, call_id, source, timeout, emit=None):
    """Return a typed judgment without accessing provider keys or Plugins."""

    detail = {
        "call_id": f"decision:{call_id}",
        "parent_call_id": call_id if source == "decision_gate" else None,
        "name": f"decision.{name}",
        "model_ref": binding["model_ref"],
        "usage_source": "gate_model",
        "source": source,
    }
    started = time.monotonic()
    if emit is not None:
        emit("model.started", detail)
    result, reason, usage = _request_decision(
        command, config, client, binding, name, arguments, call_id, source, timeout
    )
    if emit is not None:
        emit(
            "model.completed" if result is not None else "model.failed",
            {
                **detail,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "usage": usage,
                "value": result.value if result is not None else None,
                "fallback_reason": reason,
            },
        )
    return result, reason


def _request_decision(command, config, client, binding, name, arguments, call_id, source, timeout):
    """Translate a host rubric and validate the portable gateway response."""

    instructions = arguments["instructions"]
    kind = binding["kind"]
    question = {"name": name, "instructions": instructions}
    legend = None
    if kind == "noul":
        question["type"] = "predicate"
        for key, label in (("criteria_true", "True"), ("criteria_false", "False")):
            if arguments.get(key):
                question["instructions"] += f"\n{label}: {arguments[key]}"
    else:
        criteria = json.loads(arguments["criteria"])
        question["type"] = kind
        if kind == "choice":
            question["choices"] = [
                {"value": key, "description": description or key} for key, description in criteria.items()
            ]
            legend = criteria
        else:
            question["levels"] = [{"label": label} for label in criteria]
            legend = {str(index): label for index, label in enumerate(criteria)}
    try:
        response = client.post(
            config.ai_gateway_url.rstrip("/").rsplit("/", 1)[0] + "/decision-gateway/",
            headers={"Authorization": f"Bearer {config.token}"},
            json={
                "run_uuid": command.get("run_uuid"),
                "model_ref": binding["model_ref"],
                "input": arguments["state"],
                "questions": [question],
                "call_id": call_id,
                "source": source,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException:
        return None, "timeout", {}
    except (httpx.HTTPError, ValueError):
        return None, "error", {}
    usage = payload.get("usage") if isinstance(payload, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, list) or len(answers) != 1 or not isinstance(answers[0], dict):
        return None, "invalid_response", usage
    answer = answers[0]
    if answer.get("type") == "refusal":
        return None, "refusal", usage
    if answer.get("name") != name or answer.get("type") != question["type"]:
        return None, "invalid_response", usage
    if kind == "choice":
        value = answer.get("choice")
        if not isinstance(value, str) or value not in legend:
            return None, "invalid_response", usage
    else:
        value = answer.get("probability" if kind == "noul" else "score")
        maximum = 1 if kind == "noul" else len(legend) - 1
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
            return None, "invalid_response", usage
        value = float(value)
    return DecisionResult(kind, value, None, legend, usage), "", usage
