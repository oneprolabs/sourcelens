from types import SimpleNamespace

from django.test import SimpleTestCase

from lens.evaluation.evaluators.citation import citation_integrity_metric
from lens.evaluation.evaluators.groundedness import groundedness_metric
from lens.evaluation.evaluators.rule_based import latency_metric, tool_success_metric
from lens.evaluation.evaluators.trace import summarize_trace
from lens.evaluation.scoring import (
    DEFAULT_METRICS,
    aggregate_results,
    metrics_snapshot,
)


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

    def test_planned_evidence_is_used_when_no_review_ran(self):
        metric = groundedness_metric(
            {"evidence_verifications": []},
            planned_evidence={"sufficient": False, "gap_categories": ["source"]},
        )

        self.assertEqual(metric["score"], 0.0)
        self.assertEqual(metric["detail"]["source"], "planned_evidence")


class AggregationTests(SimpleTestCase):
    def _result(self, passed, scores):
        return SimpleNamespace(passed=passed, scores=scores)

    def test_weighted_score_ignores_missing_dimensions(self):
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
        self.assertIsNotNone(summary["aggregate_score"])
        self.assertLess(summary["coverage"]["ratio"], 1.0)
        self.assertEqual(
            summary["coverage"]["per_metric"]["execution_success"]["scored_cases"],
            1,
        )

    def test_no_scored_dimension_leaves_aggregate_empty(self):
        summary = aggregate_results(
            [self._result(None, {"execution_success": {"score": None}})],
            metrics_snapshot(DEFAULT_METRICS),
        )

        self.assertIsNone(summary["aggregate_score"])
        self.assertEqual(summary["coverage"]["ratio"], 0.0)


class CorrectnessTests(SimpleTestCase):
    def test_expected_answer_and_facts_and_renamed_outcome(self):
        from unittest.mock import patch

        from lens.evaluation.scoring import evaluate_case_result
        from lens.models import Run

        run = SimpleNamespace(
            status=Run.Status.DONE, outcome="completed", termination_detail={}, trace_events=[],
            output_message_id=1, output_message=SimpleNamespace(content="The answer is 42"),
            started_at=None, finished_at=None,
        )
        metrics = [{"name": "renamed", "evaluator_type": "outcome", "threshold": 1, "weight": 1}]
        with patch("lens.evaluation.scoring.load_trace_events", return_value=[]):
            for expected, facts, verdict in [
                ("The answer is 42", ["42"], True),
                ("wrong", [], False),
                ("", ["missing"], False),
                ("", [], None),
            ]:
                case = SimpleNamespace(expected_answer=expected, expected_facts=facts)
                self.assertEqual(evaluate_case_result(run, case, metrics)["passed"], verdict)

    def test_partial_and_step_limit_are_not_success(self):
        from lens.evaluation.evaluators.outcome import derive_run_quality
        from lens.models import Run

        for reason in ("partial", "step_limit", "blocked"):
            run = SimpleNamespace(status=Run.Status.DONE, termination_detail={"reason": reason},
                                  outcome="", trace_events=[])
            self.assertFalse(derive_run_quality(run)["passed"])


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
