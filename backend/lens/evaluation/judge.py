"""LLM Judge labels for answer correctness and evidence support."""

import json
import logging
import re

from django.utils import timezone

from lens.llm import run_completion
from lens.models import GlobalSetting

logger = logging.getLogger(__name__)

JUDGE_MODEL_SETTING = "evaluation.judge_model_ref"
JUDGE_PROMPT_VERSION = "1"
JUDGE_NODE_NAME = "evaluation_judge"
JUDGE_VERDICTS = {"pass", "fail", "inconclusive"}
MAX_ANSWER_CHARS = 12000
MAX_REFERENCE_CHARS = 4000
MAX_CITATIONS = 50
MAX_RATIONALE_CHARS = 2000
MAX_RAW_RESPONSE_CHARS = 4000

_FENCE_RE = re.compile(r"```[A-Za-z0-9_+-]*\s*\n(.*?)```", re.DOTALL)

SYSTEM_PROMPT = """You are an evaluation judge for a source-grounded assistant.

Return one JSON object and nothing else:
{"answer": {"verdict": "pass|fail|inconclusive", "rationale": "..."},
 "evidence": {"verdict": "pass|fail|inconclusive", "rationale": "..."}}

Rules:
- Judge answer correctness against the question, the expected answer, the
  expected facts, and any reference baseline answer. Facts may be expressed in
  different words; do not require literal overlap.
- Judge evidence support from the citations and the expected sources. A pass
  requires every material claim in the answer to be backed by a cited source.
- Use "inconclusive" when the reference material is missing or insufficient to
  decide. Never guess or infer facts that are not in the input.
- Keep each rationale under 80 words.
"""


class JudgeOutputError(ValueError):
    """Raised when the judge response does not match the required schema."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def judge_model_ref():
    """Return the configured Judge model reference, or an empty string."""

    setting = GlobalSetting.objects.filter(key=JUDGE_MODEL_SETTING).first()
    value = setting.value if setting is not None else ""
    return value.strip() if isinstance(value, str) else ""


def judge_config():
    """Return the frozen Judge configuration for one evaluation batch."""

    model_ref = judge_model_ref()
    return {
        "model_ref": model_ref,
        "prompt_version": JUDGE_PROMPT_VERSION,
        "enabled": bool(model_ref),
    }


def _bounded(value, limit):
    """Return one bounded string so judge prompts stay predictable."""

    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def _reference_payload(case):
    """Return the frozen case expectation without the live dataset row."""

    baseline = getattr(case, "baseline", None)
    payload = {
        "question": _bounded(getattr(case, "input", ""), MAX_REFERENCE_CHARS),
        "expected_answer": _bounded(
            getattr(case, "expected_answer", ""),
            MAX_REFERENCE_CHARS,
        ),
        "expected_facts": [
            _bounded(item, 500)
            for item in (getattr(case, "expected_facts", None) or [])
        ][:20],
        "expected_sources": [
            _bounded(item, 500)
            for item in (getattr(case, "expected_sources", None) or [])
        ][:50],
        "reference_baseline": None,
    }
    if isinstance(baseline, dict) and str(baseline.get("answer") or "").strip():
        payload["reference_baseline"] = {
            "answer": _bounded(baseline["answer"], MAX_REFERENCE_CHARS),
        }
    return payload


def _citation_payload(citations):
    """Return only the citation fields the Judge needs."""

    rows = []
    for item in citations or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "path": _bounded(item.get("path"), 400),
                "start_line": item.get("start_line"),
                "end_line": item.get("end_line"),
            }
        )
        if len(rows) >= MAX_CITATIONS:
            break
    return rows


def build_judge_input(*, case, output, citations, execution_metrics):
    """Build the bounded JSON payload sent to the Judge."""

    metrics = execution_metrics or {}
    return {
        "reference": _reference_payload(case),
        "actual_answer": _bounded(output, MAX_ANSWER_CHARS),
        "citations": _citation_payload(citations),
        "execution": {
            "status": metrics.get("status"),
            "tool_calls": metrics.get("tool_calls"),
            "tools_failed": metrics.get("tools_failed"),
            "total_tokens": metrics.get("total_tokens"),
            "duration_seconds": metrics.get("duration_seconds"),
        },
    }


def _parse_json_object(content):
    """Parse one JSON object, tolerating a markdown code fence wrapper."""

    raw = str(content or "").strip()
    candidates = [raw]
    fence = _FENCE_RE.search(raw)
    if fence:
        candidates.insert(0, fence.group(1).strip())
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise JudgeOutputError("EVALUATION_JUDGE_OUTPUT_INVALID")


def _dimension(parsed, name):
    """Validate one Judge dimension against the calibration contract."""

    value = parsed.get(name)
    if not isinstance(value, dict):
        raise JudgeOutputError("EVALUATION_JUDGE_OUTPUT_INVALID")
    verdict = str(value.get("verdict") or "").strip().lower()
    if verdict not in JUDGE_VERDICTS:
        raise JudgeOutputError("EVALUATION_JUDGE_OUTPUT_INVALID")
    return {
        "verdict": verdict,
        "rationale": _bounded(value.get("rationale"), MAX_RATIONALE_CHARS),
    }


def _model_provenance(model_ref):
    """Return the Judge model identity stored with each result."""

    return {"model_ref": model_ref, "prompt_version": JUDGE_PROMPT_VERSION}


def judge_result(
    *,
    case,
    output,
    citations,
    execution_metrics,
    model_ref=None,
    user_id=None,
):
    """Return Judge evidence for one evaluated case.

    A missing model, an empty answer, a call failure, or invalid output never
    raises: the deterministic scores stay authoritative and the Judge status
    makes the gap visible instead of silently marking the case correct.
    """

    if model_ref is None:
        model_ref = judge_model_ref()
    model_ref = str(model_ref or "").strip()
    if not model_ref:
        return {
            "status": "not_configured",
            "error_code": "EVALUATION_JUDGE_NOT_CONFIGURED",
        }
    if not str(output or "").strip():
        return {
            "status": "skipped",
            "reason": "empty_answer",
            "model": _model_provenance(model_ref),
        }
    payload = build_judge_input(
        case=case,
        output=output,
        citations=citations,
        execution_metrics=execution_metrics,
    )
    try:
        completion = run_completion(
            model_ref=model_ref,
            system=SYSTEM_PROMPT,
            user=json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            node_name=JUDGE_NODE_NAME,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning(
            "evaluation judge call failed: model=%s error=%s",
            model_ref,
            type(exc).__name__,
        )
        return {
            "status": "judge_failed",
            "error_code": "EVALUATION_JUDGE_CALL_FAILED",
            "error": type(exc).__name__,
            "model": _model_provenance(model_ref),
        }
    try:
        parsed = _parse_json_object(completion.content)
        answer = _dimension(parsed, "answer")
        evidence = _dimension(parsed, "evidence")
    except JudgeOutputError as exc:
        logger.warning(
            "evaluation judge rejected output: model=%s code=%s",
            model_ref,
            exc.code,
        )
        return {
            "status": "judge_failed",
            "error_code": exc.code,
            "raw_response": _bounded(completion.content, MAX_RAW_RESPONSE_CHARS),
            "model": _model_provenance(model_ref),
            "usage": completion.usage or {},
        }
    return {
        "status": "ok",
        "answer": answer,
        "evidence": evidence,
        "model": _model_provenance(model_ref),
        "usage": completion.usage or {},
        "judged_at": timezone.now().isoformat(),
    }
