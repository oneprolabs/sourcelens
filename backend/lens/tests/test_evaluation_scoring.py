from types import SimpleNamespace

from django.test import SimpleTestCase

from lens.evaluation.evaluators.citation import citation_integrity_metric
from lens.evaluation.evaluators.groundedness import groundedness_metric
from lens.evaluation.evaluators.outcome import derive_run_quality
from lens.evaluation.evaluators.rule_based import latency_metric, tool_success_metric
from lens.evaluation.evaluators.trace import summarize_trace
from lens.evaluation.scoring import (
    DEFAULT_METRICS,
    aggregate_results,
    metrics_snapshot,
    resolve_metric_configs,
)
from lens.evaluation.snapshots import trace_summary_from_events


class RuleBasedMetricTests(SimpleTestCase):
    def test_tool_success_rate_counts_completed_and_failed_calls(self):
        metric = tool_success_metric({"tools_completed": 3, "tools_failed": 1})

        self.assertEqual(metric["score"], 0.75)
        self.assertEqual(metric["detail"]["total"], 4)

    def test_tool_success_rate_is_inconclusive_without_tool_calls(self):
        metric = tool_success_metric({"tools_completed": 0, "tools_failed": 0})

        self.assertIsNone(metric["score"])
        self.assertEqual(metric["detail"]["reason"], "no_tool_calls")

    def test_latency_score_decays_against_the_budget(self):
        metric = latency_metric(1200, {"budget_seconds": 600})

        self.assertEqual(metric["score"], 0.5)
        self.assertEqual(metric["detail"]["budget_seconds"], 600)

    def test_latency_is_inconclusive_without_duration(self):
        metric = latency_metric(None, {})

        self.assertIsNone(metric["score"])

    def test_tool_success_counts_started_without_terminal_event_as_failure(self):
        metric = tool_success_metric(
            {"tool_started": 4, "tools_completed": 2, "tools_failed": 1}
        )

        self.assertEqual(metric["score"], 0.5)
        self.assertEqual(metric["detail"]["unfinished"], 1)
        self.assertEqual(metric["detail"]["total"], 4)


class OutcomeVerdictTests(SimpleTestCase):
    def _run(self, status, termination_detail=None, outcome=""):
        return SimpleNamespace(
            status=status,
            termination_detail=termination_detail or {},
            outcome=outcome,
            trace_events=[],
        )

    def test_failed_and_cancelled_runs_are_failures(self):
        self.assertIs(derive_run_quality(self._run("failed"))["passed"], False)
        self.assertIs(derive_run_quality(self._run("cancelled"))["passed"], False)

    def test_awaiting_user_input_is_inconclusive(self):
        quality = derive_run_quality(self._run("awaiting_user_input"))

        self.assertIsNone(quality["passed"])
        self.assertEqual(quality["reason"], "awaiting_user_input")


class CitationMetricTests(SimpleTestCase):
    def test_relative_citations_cover_expected_sources(self):
        metric = citation_integrity_metric(
            [
                {
                    "path": "docs/report.md",
                    "start_line": 1,
                    "end_line": 4,
                }
            ],
            expected_sources=["report.md"],
        )

        self.assertEqual(metric["score"], 1.0)
        self.assertFalse(metric["detail"]["existence_checked"])

    def test_absolute_and_parent_paths_are_rejected(self):
        metric = citation_integrity_metric(
            [
                {"path": "/etc/passwd", "start_line": 1, "end_line": 2},
                {"path": "../secret.md", "start_line": 1, "end_line": 2},
                {"path": "docs/report.md", "start_line": 1, "end_line": 2},
            ]
        )

        self.assertAlmostEqual(metric["score"], 1 / 3, places=4)
        self.assertEqual(len(metric["detail"]["invalid"]), 2)

    def test_missing_expected_source_reduces_coverage(self):
        metric = citation_integrity_metric(
            [{"path": "docs/report.md", "start_line": 1, "end_line": 2}],
            expected_sources=["report.md", "budget.csv"],
        )

        self.assertEqual(metric["score"], 0.5)
        self.assertEqual(
            metric["detail"]["missing_expected_sources"],
            ["budget.csv"],
        )

    def test_unresolved_paths_lower_the_score_when_manifest_is_known(self):
        metric = citation_integrity_metric(
            [
                {"path": "docs/report.md", "start_line": 1, "end_line": 2},
                {"path": "docs/ghost.md", "start_line": 1, "end_line": 2},
            ],
            known_paths=["docs/report.md"],
        )

        self.assertEqual(metric["score"], 0.5)
        self.assertTrue(metric["detail"]["existence_checked"])
        self.assertEqual(metric["detail"]["unresolved_paths"], ["docs/ghost.md"])

    def test_no_citations_is_inconclusive(self):
        metric = citation_integrity_metric([])

        self.assertIsNone(metric["score"])
        self.assertEqual(metric["detail"]["reason"], "no_citations")

    def test_no_citations_scores_zero_when_sources_are_expected(self):
        metric = citation_integrity_metric([], expected_sources=["report.md"])

        self.assertEqual(metric["score"], 0.0)
        self.assertEqual(
            metric["detail"]["missing_expected_sources"],
            ["report.md"],
        )


class TraceSummaryTests(SimpleTestCase):
    def test_step_event_verification_is_collected(self):
        summary = summarize_trace(
            [
                {
                    "event_type": "step.event",
                    "payload": {
                        "name": "deepagents.evidence.verified",
                        "action": "recheck",
                    },
                },
                {
                    "event_type": "step.event",
                    "payload": {
                        "name": "deepagents.evidence.verified",
                        "action": "accept",
                    },
                },
            ]
        )

        self.assertEqual(summary["evidence_verifications"], ["recheck", "accept"])

    def test_native_verification_events_are_collected(self):
        summary = summarize_trace(
            [{"event_type": "deepagents.evidence.verified", "payload": {"action": "accept"}}]
        )

        self.assertEqual(summary["evidence_verifications"], ["accept"])


class GroundednessMetricTests(SimpleTestCase):
    def test_missing_verification_events_are_inconclusive(self):
        metric = groundedness_metric({"evidence_verifications": []})

        self.assertIsNone(metric["score"])

    def test_latest_accept_scores_full_marks(self):
        metric = groundedness_metric({"evidence_verifications": ["recheck", "accept"]})

        self.assertEqual(metric["score"], 1.0)

    def test_best_effort_accept_scores_half(self):
        metric = groundedness_metric({"evidence_verifications": ["accept_best_effort"]})

        self.assertEqual(metric["score"], 0.5)

    def test_not_requested_is_inconclusive(self):
        metric = groundedness_metric({"evidence_verifications": ["not_requested"]})

        self.assertIsNone(metric["score"])
        self.assertEqual(metric["detail"]["reason"], "verification_not_requested")

    def test_planned_evidence_is_used_when_no_review_ran(self):
        metric = groundedness_metric(
            {"evidence_verifications": []},
            planned_evidence={"sufficient": False, "gap_categories": ["source"]},
        )

        self.assertEqual(metric["score"], 0.0)
        self.assertEqual(metric["detail"]["source"], "planned_evidence")


class TraceSnapshotTests(SimpleTestCase):
    def test_model_started_payload_is_compacted(self):
        event = SimpleNamespace(
            sequence=1,
            event_type="model.started",
            timestamp=None,
            turn=1,
            step=0,
            payload={
                "model": "deepseek-v4.1-flash",
                "messages": [{"content": "x" * 1000}],
                "tools": [{"name": "search"}],
            },
        )

        summary = trace_summary_from_events([event])

        self.assertEqual(summary["event_count"], 1)
        self.assertEqual(summary["events"][0]["payload"], {"model": "deepseek-v4.1-flash"})
        self.assertFalse(summary["truncated"])
        self.assertEqual(summary["last_sequence"], 1)


class MetricCatalogTests(SimpleTestCase):
    def _row(self, name, enabled=True):
        return SimpleNamespace(
            name=name,
            category="reliability",
            evaluator_type="tool_success",
            weight=1,
            threshold=None,
            config={},
            enabled=enabled,
        )

    def test_disabled_rows_do_not_fall_back_to_defaults(self):
        configs = resolve_metric_configs([self._row("custom", enabled=False)])

        self.assertEqual(configs, [])

    def test_empty_catalog_uses_defaults(self):
        configs = resolve_metric_configs([])

        self.assertEqual(len(configs), len(DEFAULT_METRICS))


class AggregationTests(SimpleTestCase):
    def _result(self, passed, scores):
        return SimpleNamespace(passed=passed, scores=scores)

    def test_weighted_score_is_suppressed_below_case_coverage(self):
        results = [
            self._result(
                True,
                {
                    "execution_success": {"score": 1.0, "passed": True},
                    "citation_integrity": {"score": 1.0, "passed": True},
                },
            ),
            self._result(
                None,
                {
                    "execution_success": {"score": None, "passed": None},
                    "citation_integrity": {"score": 0.5, "passed": False},
                },
            ),
        ]

        summary = aggregate_results(results, metrics_snapshot(DEFAULT_METRICS))

        self.assertEqual(summary["passed_cases"], 1)
        self.assertEqual(summary["inconclusive_cases"], 1)
        self.assertIsNone(summary["aggregate_score"])
        self.assertEqual(summary["coverage"]["case_ratio"], 0.3)
        self.assertEqual(
            summary["coverage"]["aggregate_suppressed"],
            "insufficient_case_coverage",
        )
        self.assertLess(summary["coverage"]["ratio"], 1.0)
        self.assertEqual(
            summary["coverage"]["per_metric"]["execution_success"]["scored_cases"],
            1,
        )

    def test_weighted_score_reports_when_case_coverage_is_sufficient(self):
        results = [
            self._result(
                True,
                {
                    "execution_success": {"score": 1.0, "passed": True},
                    "citation_integrity": {"score": 1.0, "passed": True},
                    "tool_success_rate": {"score": 1.0, "passed": True},
                },
            ),
            self._result(
                True,
                {
                    "execution_success": {"score": 1.0, "passed": True},
                    "citation_integrity": {"score": 0.5, "passed": False},
                    "tool_success_rate": {"score": 1.0, "passed": True},
                },
            ),
        ]

        summary = aggregate_results(results, metrics_snapshot(DEFAULT_METRICS))

        self.assertIsNotNone(summary["aggregate_score"])
        self.assertEqual(summary["coverage"]["scored_cells"], 6)
        self.assertIsNone(summary["coverage"]["aggregate_suppressed"])
        self.assertEqual(
            summary["coverage"]["per_metric"]["execution_success"]["coverage_ratio"],
            1.0,
        )

    def test_no_scored_dimension_leaves_aggregate_empty(self):
        summary = aggregate_results(
            [self._result(None, {"execution_success": {"score": None}})],
            metrics_snapshot(DEFAULT_METRICS),
        )

        self.assertIsNone(summary["aggregate_score"])
        self.assertEqual(summary["coverage"]["ratio"], 0.0)
        self.assertEqual(summary["coverage"]["case_ratio"], 0.0)


class CorrectnessTests(SimpleTestCase):
    def test_reference_facts_require_semantic_review_without_fabricated_verdict(self):
        from unittest.mock import patch

        from lens.evaluation.scoring import evaluate_case_result
        from lens.models import Run

        run = SimpleNamespace(
            status=Run.Status.DONE, outcome="completed", termination_detail={}, trace_events=[],
            output_message_id=1, output_message=SimpleNamespace(content="The answer is not 42"),
            started_at=None, finished_at=None,
        )
        metrics = [{"name": "renamed", "evaluator_type": "outcome", "threshold": 1, "weight": 1}]
        with patch("lens.evaluation.scoring.load_trace_events", return_value=[]):
            for expected, facts, mode, verdict in [
                ("", ["42"], None, None),
                ("The answer is 42", [], None, None),
                ("The answer is 42", [], "exact", False),
                ("The answer is not 42", [], "exact", True),
                ("The answer is not 42", ["42"], "exact", None),
                ("a different answer", ["42"], "exact", False),
                ("", [], None, None),
            ]:
                case = SimpleNamespace(expected_answer=expected, expected_facts=facts, answer_match_mode=mode)
                self.assertEqual(evaluate_case_result(run, case, metrics)["passed"], verdict)

    def test_partial_and_step_limit_are_not_success(self):
        from lens.evaluation.evaluators.outcome import derive_run_quality
        from lens.models import Run

        for reason in ("partial", "step_limit", "blocked"):
            run = SimpleNamespace(status=Run.Status.DONE, termination_detail={"reason": reason},
                                  outcome="", trace_events=[])
            self.assertFalse(derive_run_quality(run)["passed"])


class JudgeVerdictApplicationTests(SimpleTestCase):
    def _evaluation(self, *, passed=None, outcome_passed=True, deterministic=None):
        return {
            "scores": {
                "answer_correctness": {"score": None, "passed": deterministic, "detail": {}},
            },
            "issues": [],
            "passed": passed,
            "outcome_passed": outcome_passed,
        }

    def test_ok_judge_fills_a_semantic_gap(self):
        from lens.evaluation.scoring import apply_judge_verdict

        evaluation = apply_judge_verdict(
            self._evaluation(),
            {
                "status": "ok",
                "answer": {"verdict": "pass", "rationale": "Matches."},
                "model": {"model_ref": "judge-model"},
            },
        )

        self.assertIs(evaluation["passed"], True)
        entry = evaluation["scores"]["answer_correctness"]
        self.assertIs(entry["passed"], True)
        self.assertEqual(entry["score"], 1.0)
        self.assertEqual(entry["detail"]["method"], "judge")

    def test_judge_failure_marks_the_case_failed(self):
        from lens.evaluation.scoring import apply_judge_verdict

        evaluation = apply_judge_verdict(
            self._evaluation(),
            {"status": "ok", "answer": {"verdict": "fail", "rationale": "Wrong."}},
        )

        self.assertIs(evaluation["passed"], False)
        self.assertEqual(
            [issue["code"] for issue in evaluation["issues"]],
            ["answer_correctness_below_threshold"],
        )

    def test_deterministic_verdict_wins_over_the_judge(self):
        from lens.evaluation.scoring import apply_judge_verdict

        evaluation = apply_judge_verdict(
            self._evaluation(passed=True, deterministic=True),
            {"status": "ok", "answer": {"verdict": "fail", "rationale": "Wrong."}},
        )

        self.assertIs(evaluation["passed"], True)
        self.assertIs(evaluation["scores"]["answer_correctness"]["passed"], True)

    def test_non_ok_judge_never_changes_the_evaluation(self):
        from lens.evaluation.scoring import apply_judge_verdict

        for status in ("not_configured", "skipped", "judge_failed"):
            evaluation = apply_judge_verdict(
                self._evaluation(),
                {"status": status},
            )

            self.assertIsNone(evaluation["passed"])
            self.assertIsNone(evaluation["scores"]["answer_correctness"]["passed"])


class ImportFormatTests(SimpleTestCase):
    def test_jsonl_commas_and_csv_are_selected_by_filename(self):
        from lens.evaluation.datasets import parse_import_payload

        rows, errors = parse_import_payload('{"input":"hello","expected_facts":["a","b"]}', 'cases.jsonl')
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["expected_facts"], ["a", "b"])
        rows, errors = parse_import_payload('input,expected_answer\nhello,world\n', 'cases.csv')
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["expected_answer"], "world")
        self.assertTrue(parse_import_payload('{}', 'cases.txt')[1])
