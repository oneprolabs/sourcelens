"""Judge wiring: real verdicts, schema validation, degradation and calibration."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from lens.evaluation.calibration import judge_gate, summarize_calibration
from lens.evaluation.engine.batch import (
    build_report,
    create_evaluation_run,
    score_case_result,
)
from lens.llm import LensLLMResult
from lens.models import (
    Assistant,
    EvaluationCalibration,
    EvaluationCase,
    EvaluationDataset,
    EvaluationResult,
    GlobalSetting,
    Message,
    Run,
    Session,
)

User = get_user_model()

VALID_JUDGE_OUTPUT = json.dumps(
    {
        "answer": {"verdict": "pass", "rationale": "Matches the reference answer."},
        "evidence": {"verdict": "fail", "rationale": "No citation backs the claim."},
    }
)


class EvaluationJudgeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="judge-operator")
        self.assistant = Assistant.objects.create(
            name="Judge assistant",
            slug="judge-assistant",
            selected_task="knowledge_qa",
        )
        self.dataset = EvaluationDataset.objects.create(
            name="Judge dataset",
            assistant=self.assistant,
            created_by=self.user,
            status=EvaluationDataset.Status.ACTIVE,
        )
        self.case = EvaluationCase.objects.create(
            dataset=self.dataset,
            input="What is the capital of France?",
            expected_answer="Paris",
            expected_sources=["report.md"],
        )

    def _configure(self, model_ref="judge-model"):
        if model_ref is None:
            return
        GlobalSetting.objects.create(key="evaluation.judge_model_ref", value=model_ref)

    def _result_with_run(self, *, status=Run.Status.DONE):
        batch = create_evaluation_run(dataset=self.dataset, requested_by=self.user)
        result = EvaluationResult.objects.get(evaluation_run=batch, case=self.case)
        session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
            source=Session.Source.EVALUATION,
        )
        question = Message.objects.create(session=session, role="user", sequence=1, content=self.case.input)
        answer = Message.objects.create(
            session=session,
            role="assistant",
            sequence=2,
            content="Paris is the capital of France.",
        )
        run = Run.objects.create(
            session=session,
            input_message=question,
            output_message=answer,
            status=status,
            outcome=Run.Outcome.COMPLETED,
            citations=[{"path": "report.md", "start_line": 1, "end_line": 4}],
        )
        result.source_run = run
        result.source_run_uuid = run.uuid
        result.save(update_fields=["source_run", "source_run_uuid"])
        return batch, result, run

    def test_judge_config_is_frozen_into_the_batch_snapshot(self):
        self._configure("judge-model")

        batch = create_evaluation_run(dataset=self.dataset, requested_by=self.user)

        self.assertEqual(
            batch.config_snapshot["judge"]["model_ref"],
            "judge-model",
        )
        self.assertTrue(batch.config_snapshot["judge"]["enabled"])

    @patch("lens.evaluation.judge.run_completion")
    def test_judge_persists_verdicts_that_calibration_can_compare(self, completion):
        self._configure()
        completion.return_value = LensLLMResult(content=VALID_JUDGE_OUTPUT, usage={}, metered=True)
        batch, result, _run = self._result_with_run()

        score_case_result(result)
        result.refresh_from_db()

        self.assertEqual(result.judge_evidence["status"], "ok")
        self.assertEqual(result.judge_evidence["answer"]["verdict"], "pass")
        self.assertEqual(result.judge_evidence["evidence"]["verdict"], "fail")
        self.assertEqual(result.judge_evidence["model"]["model_ref"], "judge-model")
        self.assertEqual(completion.call_args.kwargs["model_ref"], "judge-model")
        self.assertIs(result.passed, False)
        self.assertIs(result.scores["answer_correctness"]["passed"], True)
        self.assertEqual(result.scores["answer_correctness"]["detail"]["method"], "judge")
        self.assertIs(result.scores["evidence_verified"]["passed"], False)
        self.assertEqual(result.scores["evidence_verified"]["detail"]["method"], "judge")
        self.assertIn(
            "evidence_verified_below_threshold",
            [issue["code"] for issue in result.issues],
        )

        EvaluationCalibration.objects.create(
            result=result,
            reviewer=self.user,
            answer_verdict="pass",
            evidence_verdict="pass",
            explanation="Answer matches; evidence gap is real.",
            judge_snapshot=result.judge_evidence,
            context_snapshot={
                "source_run_uuid": str(result.source_run_uuid),
                "output": result.output_snapshot,
                "scores": result.scores,
                "expectation": batch.config_snapshot["case_expectations"][str(self.case.uuid)],
            },
        )
        summary = summarize_calibration([result])

        self.assertEqual(summary["dimensions"]["answer"]["comparable_reviews"], 1)
        self.assertEqual(summary["dimensions"]["answer"]["agreement_rate"], 1.0)
        self.assertEqual(summary["dimensions"]["evidence"]["comparable_reviews"], 1)
        self.assertEqual(summary["dimensions"]["evidence"]["disagreed"], 1)

    @patch("lens.evaluation.judge.run_completion")
    def test_judge_fail_verdict_marks_the_case_failed(self, completion):
        self._configure()
        completion.return_value = LensLLMResult(
            content=json.dumps(
                {
                    "answer": {"verdict": "fail", "rationale": "Wrong city."},
                    "evidence": {"verdict": "pass", "rationale": "Cited."},
                }
            ),
            usage={},
            metered=True,
        )
        _batch, result, _run = self._result_with_run()

        score_case_result(result)
        result.refresh_from_db()

        self.assertIs(result.passed, False)
        self.assertIs(result.scores["answer_correctness"]["passed"], False)
        self.assertEqual(
            [issue["code"] for issue in result.issues],
            ["answer_correctness_below_threshold"],
        )

    @patch("lens.evaluation.judge.run_completion")
    def test_report_exposes_judge_coverage(self, completion):
        self._configure()
        completion.return_value = LensLLMResult(content=VALID_JUDGE_OUTPUT, usage={}, metered=True)
        batch, result, _run = self._result_with_run()

        score_case_result(result)

        report = build_report(batch)
        self.assertTrue(report["judge"]["configured"])
        self.assertEqual(report["judge"]["model_ref"], "judge-model")
        self.assertEqual(report["judge"]["statuses"], {"ok": 1})
        self.assertEqual(report["judge"]["judged_cases"], 1)

    @patch("lens.evaluation.judge.run_completion")
    def test_missing_judge_model_degrades_without_touching_deterministic_scores(self, completion):
        batch, result, _run = self._result_with_run()

        score_case_result(result)
        result.refresh_from_db()

        self.assertEqual(result.judge_evidence["status"], "not_configured")
        self.assertIn("execution_success", result.scores)
        self.assertIsNone(result.passed)
        self.assertFalse(batch.config_snapshot["judge"]["enabled"])
        completion.assert_not_called()

    @patch("lens.evaluation.judge.run_completion")
    def test_invalid_judge_output_is_recorded_and_never_raises(self, completion):
        self._configure()
        completion.return_value = LensLLMResult(content="not-json", usage={}, metered=True)
        _batch, result, _run = self._result_with_run()

        score_case_result(result)
        result.refresh_from_db()

        self.assertEqual(result.judge_evidence["status"], "judge_failed")
        self.assertEqual(
            result.judge_evidence["error_code"],
            "EVALUATION_JUDGE_OUTPUT_INVALID",
        )
        self.assertIn("execution_success", result.scores)

    @patch("lens.evaluation.judge.run_completion")
    def test_judge_call_failure_keeps_the_case_scored(self, completion):
        self._configure()
        completion.side_effect = RuntimeError("provider down")
        _batch, result, _run = self._result_with_run()

        score_case_result(result)
        result.refresh_from_db()

        self.assertEqual(result.judge_evidence["status"], "judge_failed")
        self.assertEqual(
            result.judge_evidence["error_code"],
            "EVALUATION_JUDGE_CALL_FAILED",
        )
        self.assertTrue(result.scores)

    @patch("lens.evaluation.judge.run_completion")
    def test_failed_run_is_not_sent_to_the_judge(self, completion):
        self._configure()
        _batch, result, _run = self._result_with_run(status=Run.Status.FAILED)

        score_case_result(result)
        result.refresh_from_db()

        self.assertEqual(result.judge_evidence["status"], "skipped")
        self.assertEqual(result.judge_evidence["reason"], "run_not_done")
        completion.assert_not_called()
        self.assertIs(result.passed, False)

    @patch("lens.evaluation.judge.run_completion")
    def test_calibration_ignores_reviews_without_a_judge_verdict(self, completion):
        self._configure()
        completion.side_effect = RuntimeError("provider down")
        batch, result, _run = self._result_with_run()
        score_case_result(result)
        result.refresh_from_db()

        EvaluationCalibration.objects.create(
            result=result,
            reviewer=self.user,
            answer_verdict="pass",
            evidence_verdict="pass",
            explanation="Review while the judge was down.",
            judge_snapshot=result.judge_evidence,
            context_snapshot={
                "source_run_uuid": str(result.source_run_uuid),
                "output": result.output_snapshot,
                "scores": result.scores,
                "expectation": batch.config_snapshot["case_expectations"][str(self.case.uuid)],
            },
        )

        summary = summarize_calibration([result])

        self.assertEqual(summary["dimensions"]["answer"]["reviewed_results"], 1)
        self.assertEqual(summary["dimensions"]["answer"]["comparable_reviews"], 0)
        self.assertIsNone(summary["dimensions"]["answer"]["agreement_rate"])

    @patch("lens.evaluation.judge.run_completion")
    @patch("lens.evaluation.engine.batch.load_trace_events")
    def test_deterministic_evidence_wins_over_judge_evidence(self, load_events, completion):
        self._configure()
        completion.return_value = LensLLMResult(content=VALID_JUDGE_OUTPUT, usage={}, metered=True)
        load_events.return_value = [
            SimpleNamespace(
                event_type="deepagents.evidence.verified",
                call_id="verify-1",
                sequence=1,
                payload={"action": "accept"},
            )
        ]
        _batch, result, _run = self._result_with_run()

        score_case_result(result)
        result.refresh_from_db()

        self.assertEqual(result.judge_evidence["evidence"]["verdict"], "fail")
        self.assertIs(result.scores["evidence_verified"]["passed"], True)
        self.assertNotIn("method", result.scores["evidence_verified"]["detail"])
        self.assertNotIn(
            "evidence_verified_below_threshold",
            [issue["code"] for issue in result.issues],
        )

    @patch("lens.evaluation.judge.run_completion")
    def test_secondary_judge_records_cross_check_agreement(self, completion):
        self._configure()
        GlobalSetting.objects.create(
            key="evaluation.judge_secondary_model_ref",
            value="judge-model-2",
        )
        completion.side_effect = [
            LensLLMResult(content=VALID_JUDGE_OUTPUT, usage={}, metered=True),
            LensLLMResult(
                content=json.dumps(
                    {
                        "answer": {"verdict": "pass", "rationale": "Same."},
                        "evidence": {"verdict": "pass", "rationale": "Different."},
                    }
                ),
                usage={},
                metered=True,
            ),
        ]
        _batch, result, _run = self._result_with_run()

        score_case_result(result)
        result.refresh_from_db()

        evidence = result.judge_evidence
        self.assertEqual(evidence["secondary"]["status"], "ok")
        self.assertEqual(evidence["secondary"]["model"]["model_ref"], "judge-model-2")
        self.assertEqual(evidence["inter_judge"]["status"], "ok")
        self.assertIs(evidence["inter_judge"]["answer_agree"], True)
        self.assertIs(evidence["inter_judge"]["evidence_agree"], False)
        self.assertEqual(
            [call.kwargs["model_ref"] for call in completion.call_args_list],
            ["judge-model", "judge-model-2"],
        )

    def test_judge_gate_requires_enough_agreed_samples(self):
        def summary(samples, rate):
            dimension = {"comparable_reviews": samples, "agreement_rate": rate}
            return {"dimensions": {"answer": dimension, "evidence": dict(dimension)}}

        self.assertEqual(judge_gate(summary(0, None), False)["status"], "not_configured")
        self.assertEqual(judge_gate(summary(19, 1.0), True)["status"], "insufficient_samples")
        self.assertEqual(judge_gate(summary(20, 0.75), True)["status"], "below_threshold")
        gate = judge_gate(summary(20, 0.9), True)

        self.assertEqual(gate["status"], "ok")
        self.assertEqual(gate["min_samples"], 20)
        self.assertEqual(gate["min_agreement"], 0.8)
