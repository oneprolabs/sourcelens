from datetime import datetime, timezone
from unittest import TestCase

from lens.trace_steps import build_trace_steps


def _event(sequence, event_type, timestamp, **overrides):
    event = {
        "event_id": f"event-{sequence}",
        "sequence": sequence,
        "event_type": event_type,
        "timestamp": timestamp,
        "payload": {},
    }
    event.update(overrides)
    return event


class TraceStepBuilderTests(TestCase):
    def setUp(self):
        self.origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.t0 = self.origin.isoformat()

    def test_groups_calls_and_builds_relative_metrics_and_edges(self):
        events = [
            _event(
                1,
                "model.started",
                self.t0,
                call_id="model-1",
                payload={"model_ref": "gpt-test", "messages": [{"role": "user"}]},
            ),
            _event(
                2,
                "model.completed",
                self.t0,
                call_id="model-1",
                payload={
                    "duration_ms": 1200,
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            ),
            _event(
                3,
                "tool.started",
                self.t0,
                call_id="tool-1",
                parent_call_id="model-1",
                payload={"name": "search_workspace", "arguments": {"query": "x"}},
            ),
        ]

        result = build_trace_steps(events, run_start=self.origin)

        self.assertEqual([step["type"] for step in result["steps"]], ["model", "retrieval"])
        model = result["steps"][0]
        self.assertEqual(model["duration_ms"], 1200)
        self.assertEqual(model["start_ms"], 0)
        self.assertEqual(model["tokens"]["total"], 15)
        self.assertEqual(model["tokens"]["input"], 10)
        self.assertEqual(model["details"]["message_count"], 1)
        self.assertNotIn("messages", model["details"])
        self.assertNotIn("events", model)
        self.assertIn({"from": "step_1", "to": "step_2", "kind": "child"}, result["edges"])

    def test_merges_decision_gate_start_and_done_by_payload_call_id(self):
        events = [
            _event(
                1,
                "deepagents.decision.gate.start",
                self.t0,
                payload={"gate": "search_needed", "call_id": "gate:search_needed:1"},
            ),
            _event(
                2,
                "deepagents.decision.gate.done",
                self.t0,
                payload={
                    "gate": "search_needed",
                    "call_id": "gate:search_needed:1",
                    "value": 0.87,
                    "threshold": 0.5,
                    "verdict": "accept",
                },
            ),
        ]

        result = build_trace_steps(events, run_start=self.origin)

        self.assertEqual(len(result["steps"]), 1)
        decision = result["steps"][0]
        self.assertEqual(decision["type"], "decision")
        self.assertEqual(decision["title"], "search_needed")
        self.assertEqual(decision["details"]["value"], 0.87)
        self.assertEqual(decision["details"]["threshold"], 0.5)
        self.assertEqual(decision["summary"], "search_needed → 0.87")

    def test_ignores_internal_noise_and_reports_hidden_count(self):
        events = [
            _event(1, "system.snapshot", self.t0, payload={"messages": []}),
            _event(2, "tools.snapshot", self.t0, payload={"tools": []}),
            _event(3, "checkpoint.saved", self.t0, payload={"checkpoint_id": "c1"}),
            _event(4, "phase.changed", self.t0, payload={"phase": "answer"}),
            _event(
                5,
                "artifact.created",
                self.t0,
                payload={"name": "report.md", "format": "markdown"},
            ),
        ]

        result = build_trace_steps(events, run_start=self.origin)

        self.assertEqual([step["type"] for step in result["steps"]], ["artifact"])
        self.assertEqual(result["hidden_event_count"], 4)

    def test_classifies_save_deliverable_as_artifact_and_evidence_as_retrieval(self):
        events = [
            _event(
                1,
                "tool.completed",
                self.t0,
                call_id="tool-a",
                payload={"name": "save_deliverable", "result": {"path": "a.md"}},
            ),
            _event(
                2,
                "tool.completed",
                self.t0,
                call_id="tool-b",
                payload={"name": "planned_evidence", "metrics": {"result_count": 3}},
            ),
            _event(
                3,
                "tool.completed",
                self.t0,
                call_id="tool-c",
                payload={"name": "run_skill_script", "result": {"status": "ok"}},
            ),
        ]

        result = build_trace_steps(events, run_start=self.origin)

        self.assertEqual(
            [step["type"] for step in result["steps"]],
            ["artifact", "retrieval", "tool"],
        )

    def test_marks_open_calls_running_and_failed_calls_failed(self):
        events = [
            _event(
                1,
                "tool.started",
                self.t0,
                call_id="tool-open",
                payload={"name": "open_shell"},
            ),
            _event(
                2,
                "tool.failed",
                self.t0,
                call_id="tool-fail",
                payload={"name": "failed_shell", "error": "boom"},
            ),
        ]

        result = build_trace_steps(events, run_start=self.origin)

        statuses = {step["title"]: step["status"] for step in result["steps"]}
        self.assertEqual(statuses["open_shell"], "running")
        self.assertEqual(statuses["failed_shell"], "failed")
        failed = next(
            step for step in result["steps"] if step["title"] == "failed_shell"
        )
        self.assertIn("failed", failed["summary"])
