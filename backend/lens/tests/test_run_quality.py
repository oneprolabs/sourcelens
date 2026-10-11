"""Quality failures remain visible after a normally completed process."""

from django.test import TestCase

from lens.models import RunStep
from lens.run_trace import append_run_trace_events
from lens.runtime_events import sanitize_termination_detail
from lens.tests.test_run_trace import RunTraceFixtureMixin
from lens.views.admin_runs import _admin_run_failure_summary


class RunQualitySummaryTests(RunTraceFixtureMixin, TestCase):
    """Expose terminal verification results and recovered tool errors."""

    def setUp(self):
        self.create_trace_fixture()

    def test_protocol_failure_code_is_safe_and_public(self):
        detail = sanitize_termination_detail(
            {
                "reason": "runtime_failure",
                "code": "MODEL_TOOL_PROTOCOL_ERROR",
                "raw": "private content",
            }
        )
        self.assertEqual(detail["code"], "MODEL_TOOL_PROTOCOL_ERROR")
        self.assertNotIn("private content", str(detail))

    def test_runtime_tracked_plugin_failure_is_not_counted_twice(self):
        RunStep.objects.create(
            run=self.run,
            sequence=1,
            step_type=RunStep.StepType.GENERAL_CHAT,
            status=RunStep.Status.DONE,
            detail={
                "events": [
                    {
                        "agent_event": "deepagents.runtime.outcome",
                        "warning_count": 1,
                        "failures": [
                            {
                                "source": "plugin:jira_activity_summary",
                                "tool": "jira_activity_summary",
                                "capability": "tool",
                                "error_type": "tool",
                                "scope": "warning",
                            }
                        ],
                    }
                ]
            },
        )
        append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [
                self.trace_event(
                    sequence=1, event_type="tool.started", call_id="bad", payload={"name": "jira_activity_summary"}
                ),
                self.trace_event(sequence=2, event_type="tool.failed", call_id="bad"),
            ],
        )
        summary = _admin_run_failure_summary(self.run)
        self.assertEqual(summary["warning_count"], 1)
        self.assertEqual(summary["unresolved_failure_count"], 0)

    def test_latest_gate_verdict_replaces_earlier_fallback(self):
        append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [
                self.trace_event(
                    sequence=1,
                    event_type="step.event",
                    payload={
                        "agent_event": "deepagents.decision.gate.done",
                        "gate": "answer_supported",
                        "verdict": "fallback",
                    },
                ),
                self.trace_event(
                    sequence=2,
                    event_type="step.event",
                    payload={
                        "agent_event": "deepagents.decision.gate.done",
                        "gate": "answer_supported",
                        "verdict": "accept",
                    },
                ),
            ],
        )
        self.assertEqual(_admin_run_failure_summary(self.run)["warning_count"], 0)

    def test_unverified_completed_run_has_failure_diagnostics(self):
        append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [
                self.trace_event(
                    event_type="step.event",
                    payload={
                        "agent_event": "deepagents.evidence.verified",
                        "action": "accept_best_effort",
                        "verdicts": {"answer_supported": "unsupported", "evidence_sufficient": True},
                    },
                )
            ],
        )
        summary = _admin_run_failure_summary(self.run)
        self.assertEqual(summary["unresolved_failure_count"], 1)
        self.assertEqual(summary["failures"][0]["error_type"], "verification")
        self.assertTrue(summary["failures"][0]["affects_required_evidence"])

    def test_incomplete_review_is_warning_instead_of_verified_success(self):
        append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [
                self.trace_event(
                    event_type="step.event",
                    payload={
                        "agent_event": "deepagents.evidence.verified",
                        "action": "inconclusive",
                        "verdicts": {"answer_supported": "inconclusive", "evidence_completeness": "incomplete"},
                    },
                )
            ],
        )
        summary = _admin_run_failure_summary(self.run)
        self.assertEqual(summary["warning_count"], 1)
        self.assertEqual(summary["unresolved_failure_count"], 0)
        self.assertFalse(summary["failures"][0]["affects_required_evidence"])

    def test_recovered_tool_error_remains_in_summary_without_leaking_content(self):
        append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [
                self.trace_event(
                    sequence=1,
                    event_type="tool.started",
                    call_id="bad",
                    payload={"name": "execute", "arguments": {"command": "pytest"}},
                ),
                self.trace_event(
                    sequence=2, event_type="tool.failed", call_id="bad", payload={"error": "private content"}
                ),
                self.trace_event(
                    sequence=3,
                    event_type="tool.started",
                    call_id="fixed",
                    payload={"name": "execute", "arguments": {"command": "pytest"}},
                ),
                self.trace_event(sequence=4, event_type="tool.completed", call_id="fixed", payload={}),
            ],
        )
        summary = _admin_run_failure_summary(self.run)
        self.assertEqual(summary["recovered_failure_count"], 1)
        self.assertEqual(summary["unresolved_failure_count"], 0)
        self.assertNotIn("private content", str(summary))

    def test_recheck_then_supported_answer_has_no_verification_failure(self):
        append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [
                self.trace_event(
                    sequence=1,
                    event_type="step.event",
                    payload={
                        "agent_event": "deepagents.evidence.verified",
                        "action": "recheck",
                        "verdicts": {"answer_supported": "unsupported"},
                    },
                ),
                self.trace_event(
                    sequence=2,
                    event_type="step.event",
                    payload={
                        "agent_event": "deepagents.evidence.verified",
                        "action": "accept",
                        "verdicts": {"answer_supported": "supported"},
                    },
                ),
            ],
        )
        summary = _admin_run_failure_summary(self.run)
        self.assertEqual(summary["unresolved_failure_count"], 0)

    def test_unrelated_success_does_not_recover_a_failed_operation(self):
        for arguments in ({"command": "ls"}, None):
            with self.subTest(arguments=arguments):
                self.run.trace_events.all().delete()
                start = {"name": "execute"}
                if arguments is not None:
                    start["arguments"] = arguments
                append_run_trace_events(
                    self.run.uuid,
                    self.node.uuid,
                    [
                        self.trace_event(
                            sequence=1,
                            event_type="tool.started",
                            call_id="bad",
                            payload={"name": "execute", "arguments": {"command": "pytest"}},
                        ),
                        self.trace_event(sequence=2, event_type="tool.failed", call_id="bad"),
                        self.trace_event(sequence=3, event_type="tool.started", call_id="other", payload=start),
                        self.trace_event(sequence=4, event_type="tool.completed", call_id="other"),
                    ],
                )
                summary = _admin_run_failure_summary(self.run)
                self.assertEqual(summary["unresolved_failure_count"], 1)
                self.assertEqual(summary["recovered_failure_count"], 0)
                self.assertNotIn("pytest", str(summary))
