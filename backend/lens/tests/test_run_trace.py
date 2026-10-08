import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from lens.consumers import LensNodeConsumer
from lens.models import Assistant, LensNode, Run, RunTraceEvent, Session
from lens.run_trace import RunTraceValidationError, append_run_trace_events
from lens.services import create_execution_run, dispatch_run_to_lensnode
from lens.views.admin_runs import stream_admin_run_trajectory_events_async

User = get_user_model()


class RunTraceFixtureMixin:
    """Build a run assigned to one approved LensNode."""

    def create_trace_fixture(self):
        self.user = User.objects.create_user(
            username=f"trace-user-{uuid.uuid4()}",
            password="x",
        )
        self.node = LensNode.objects.create(
            name=f"Trace node {uuid.uuid4()}",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )
        assistant = Assistant.objects.create(
            name=f"Trace assistant {uuid.uuid4()}",
            slug=f"trace-{uuid.uuid4()}",
            lensnode=self.node,
            selected_task="general_chat",
        )
        session = Session.objects.create(
            assistant=assistant,
            user=self.user,
        )
        self.run = create_execution_run(
            session,
            "Explain this run",
            enqueue=False,
        )

    def trace_event(self, sequence=1, **overrides):
        event = {
            "event_id": str(uuid.uuid4()),
            "sequence": sequence,
            "attempt": 1,
            "event_type": "model.completed",
            "timestamp": timezone.now().isoformat(),
            "call_id": "model-call-1",
            "payload": {
                "model": "test-model",
                "duration_ms": 125,
                "ttft_ms": 20,
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        }
        event.update(overrides)
        return event


class RunTraceServiceTests(RunTraceFixtureMixin, TransactionTestCase):
    def setUp(self):
        self.create_trace_fixture()

    def test_appends_ordered_events_and_replays_idempotently(self):
        first = self.trace_event(sequence=1)
        second = self.trace_event(
            sequence=2,
            event_type="tool.started",
            call_id="tool-call-1",
            parent_call_id="model-call-1",
            payload={"name": "search", "arguments": {"q": "trace"}},
        )

        result = append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [first, second],
        )
        replay = append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [first, second],
        )

        self.assertEqual(result.inserted_count, 2)
        self.assertEqual(result.duplicate_count, 0)
        self.assertEqual(replay.inserted_count, 0)
        self.assertEqual(replay.duplicate_count, 2)
        self.assertEqual(
            list(
                RunTraceEvent.objects.filter(run=self.run).values_list(
                    "sequence", flat=True
                )
            ),
            [1, 2],
        )

    def test_rejects_conflicting_event_id_transactionally(self):
        existing = self.trace_event(sequence=1)
        append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [existing],
        )
        valid = self.trace_event(sequence=2)
        conflict = self.trace_event(
            sequence=3,
            event_id=existing["event_id"],
            event_type="tool.failed",
        )

        with self.assertRaises(RunTraceValidationError):
            append_run_trace_events(
                self.run.uuid,
                self.node.uuid,
                [valid, conflict],
            )

        self.assertEqual(self.run.trace_events.count(), 1)

    def test_rejects_sequence_conflict_and_wrong_node(self):
        first = self.trace_event(sequence=1)
        append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [first],
        )
        other_node = LensNode.objects.create(
            name="Other trace node",
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )

        with self.assertRaises(RunTraceValidationError):
            append_run_trace_events(
                self.run.uuid,
                self.node.uuid,
                [self.trace_event(sequence=1)],
            )
        with self.assertRaises(RunTraceValidationError):
            append_run_trace_events(
                self.run.uuid,
                other_node.uuid,
                [self.trace_event(sequence=2)],
            )

    def test_rejects_sequence_gap_transactionally(self):
        append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [self.trace_event(sequence=1)],
        )

        with self.assertRaises(RunTraceValidationError):
            append_run_trace_events(
                self.run.uuid,
                self.node.uuid,
                [self.trace_event(sequence=3)],
            )

        self.assertEqual(self.run.trace_events.count(), 1)

    def test_validates_external_event_shape(self):
        invalid = self.trace_event(
            sequence=0,
            event_type="Model Completed",
            payload=["not", "an", "object"],
        )

        with self.assertRaises(RunTraceValidationError) as captured:
            append_run_trace_events(
                self.run.uuid,
                self.node.uuid,
                [invalid],
            )

        self.assertIn("sequence", str(captured.exception))
        self.assertEqual(self.run.trace_events.count(), 0)

    def test_rejects_non_finite_json_numbers(self):
        invalid = self.trace_event(
            sequence=1,
            payload={"duration_ms": float("nan")},
        )

        with self.assertRaises(RunTraceValidationError):
            append_run_trace_events(
                self.run.uuid,
                self.node.uuid,
                [invalid],
            )

        self.assertEqual(self.run.trace_events.count(), 0)

    def test_websocket_frame_returns_durable_cursor_ack(self):
        consumer = LensNodeConsumer()
        consumer.lensnode = self.node
        consumer.send_json = AsyncMock()

        async_to_sync(consumer._handle_run_trace_events)(
            {
                "run_uuid": str(self.run.uuid),
                "events": [self.trace_event(sequence=1)],
            }
        )

        response = consumer.send_json.await_args.args[0]
        self.assertEqual(response["type"], "run_trace_events_ack")
        self.assertEqual(response["last_sequence"], 1)
        self.assertEqual(response["inserted_count"], 1)

    @patch("lens.services.async_to_sync")
    @patch("lens.services.get_channel_layer")
    def test_resume_dispatch_uses_persisted_trace_cursor(
        self,
        get_channel_layer,
        mock_async_to_sync,
    ):
        del get_channel_layer
        append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [
                self.trace_event(sequence=sequence, attempt=2)
                for sequence in range(1, 10)
            ],
        )

        dispatch_run_to_lensnode(
            self.run,
            "Explain this run",
            resume=True,
        )

        payload = mock_async_to_sync.return_value.call_args.args[1]["payload"]
        self.assertEqual(payload["trace_cursor"], 9)
        self.assertEqual(payload["trace_attempt"], 3)


class AdminRunTrajectoryAPITests(RunTraceFixtureMixin, TestCase):
    def setUp(self):
        self.create_trace_fixture()
        self.admin = User.objects.create_user(
            username="trace-admin",
            password="x",
            is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_admin_reads_filtered_cursor_page_with_summary(self):
        started_at = timezone.now() - timedelta(seconds=1)
        events = [
            self.trace_event(
                sequence=1,
                event_type="model.started",
                timestamp=started_at.isoformat(),
                payload={"model": "test-model", "input": "full prompt"},
            ),
            self.trace_event(sequence=2),
            self.trace_event(
                sequence=3,
                event_type="tool.completed",
                call_id="tool-call-1",
                payload={
                    "name": "search",
                    "result": {"answer": "needle"},
                    "duration_ms": 40,
                },
            ),
        ]
        append_run_trace_events(self.run.uuid, self.node.uuid, events)

        response = self.client.get(
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/",
            {
                "after_sequence": 1,
                "page_size": 1,
                "category": "model",
                "q": "test-model",
            },
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["results"][0]["sequence"], 2)
        self.assertEqual(response.data["next_after_sequence"], 2)
        self.assertEqual(response.data["summary"]["event_count"], 3)
        self.assertEqual(response.data["summary"]["model_calls"], 1)
        self.assertEqual(response.data["summary"]["tool_calls"], 1)
        self.assertEqual(response.data["summary"]["total_tokens"], 15)
        self.assertGreaterEqual(
            response.data["summary"]["duration_ms"],
            900,
        )

    def test_admin_pages_parent_and_child_events_with_one_global_cursor(self):
        child_assistant = Assistant.objects.create(
            name="Delegated trace assistant",
            slug=f"delegated-trace-{uuid.uuid4()}",
            lensnode=self.node,
            selected_task="general_chat",
        )
        child_session = Session.objects.create(
            assistant=child_assistant,
            user=self.user,
        )
        child_run = create_execution_run(
            child_session,
            "Inspect the delegated run",
            enqueue=False,
            parent_run=self.run,
        )
        started_at = timezone.now() - timedelta(seconds=3)
        append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [
                self.trace_event(
                    sequence=1,
                    timestamp=started_at.isoformat(),
                ),
                self.trace_event(
                    sequence=2,
                    timestamp=(started_at + timedelta(seconds=2)).isoformat(),
                ),
            ],
        )
        append_run_trace_events(
            child_run.uuid,
            self.node.uuid,
            [
                self.trace_event(
                    sequence=1,
                    timestamp=(started_at + timedelta(seconds=1)).isoformat(),
                )
            ],
        )

        first_page = self.client.get(
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/",
            {"page_size": 2},
        )
        second_page = self.client.get(
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/",
            {
                "page_size": 2,
                "after_sequence": first_page.data["next_after_sequence"],
            },
        )

        self.assertEqual(first_page.status_code, 200, first_page.data)
        self.assertEqual(
            [event["ingest_sequence"] for event in first_page.data["results"]],
            [1, 2],
        )
        self.assertTrue(first_page.data["has_more"])
        self.assertEqual(second_page.status_code, 200, second_page.data)
        self.assertEqual(
            [event["ingest_sequence"] for event in second_page.data["results"]],
            [3],
        )
        self.assertEqual(
            second_page.data["results"][0]["trace_run_role"],
            "child",
        )
        self.assertEqual(
            second_page.data["results"][0]["sequence"],
            1,
        )
        self.assertFalse(second_page.data["has_more"])
        self.assertEqual(
            first_page.data["summary"]["run_progress"],
            [
                {
                    "run_uuid": str(self.run.uuid),
                    "role": "parent",
                    "assistant_name": self.run.session.assistant.name,
                    "status": self.run.status,
                    "outcome": self.run.outcome,
                    "started_at": None,
                    "finished_at": None,
                    "duration_ms": None,
                    "event_count": 2,
                    "task": self.run.input_message.content,
                },
                {
                    "run_uuid": str(child_run.uuid),
                    "role": "child",
                    "assistant_name": child_assistant.name,
                    "status": child_run.status,
                    "outcome": child_run.outcome,
                    "started_at": None,
                    "finished_at": None,
                    "duration_ms": None,
                    "event_count": 1,
                    "task": "Inspect the delegated run",
                    "attempt_count": 1,
                    "attempts": [
                        {
                            "run_uuid": str(child_run.uuid),
                            "attempt": 1,
                            "retry_of_run_uuid": None,
                            "assistant_name": child_assistant.name,
                            "status": child_run.status,
                            "outcome": child_run.outcome,
                            "started_at": None,
                            "finished_at": None,
                            "duration_ms": None,
                            "event_count": 1,
                            "task": "Inspect the delegated run",
                        }
                    ],
                },
            ],
        )

        child_response = self.client.get(
            f"/api/lens/admin/runs/{child_run.uuid}/trajectory/"
        )
        self.assertEqual(child_response.status_code, 200, child_response.data)
        self.assertEqual(
            [
                (item["role"], item["assistant_name"])
                for item in child_response.data["summary"]["run_progress"]
            ],
            [
                ("parent", self.run.session.assistant.name),
                ("child", child_assistant.name),
            ],
        )

    def test_admin_reports_child_progress_before_trace_events_exist(self):
        child_assistant = Assistant.objects.create(
            name="Queued delegated assistant",
            slug=f"queued-delegated-{uuid.uuid4()}",
            lensnode=self.node,
            selected_task="general_chat",
        )
        child_session = Session.objects.create(
            assistant=child_assistant,
            user=self.user,
        )
        child_run = create_execution_run(
            child_session,
            "Wait for delegated execution",
            enqueue=False,
            parent_run=self.run,
        )

        response = self.client.get(
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["summary"]["event_count"], 0)
        self.assertEqual(
            response.data["summary"]["run_progress"][1]["run_uuid"],
            str(child_run.uuid),
        )
        self.assertEqual(
            response.data["summary"]["run_progress"][1]["event_count"],
            0,
        )

    def test_admin_groups_legacy_explicit_delegations_as_attempts(self):
        child_assistant = Assistant.objects.create(
            name="OfficeCli",
            slug=f"office-cli-{uuid.uuid4()}",
            lensnode=self.node,
            selected_task="general_chat",
        )
        snapshot = dict(self.run.execution.runtime_snapshot)
        snapshot.update(
            {
                "routing_assistant_uuid": str(child_assistant.uuid),
                "routing_question": "整理成 ppt",
            }
        )
        self.run.execution.runtime_snapshot = snapshot
        self.run.execution.save(update_fields=["runtime_snapshot"])
        child_runs = []
        for index in range(2):
            child_session = Session.objects.create(
                assistant=child_assistant,
                user=self.user,
            )
            child_run = create_execution_run(
                child_session,
                f"Prepare presentation attempt {index + 1}",
                enqueue=False,
                parent_run=self.run,
            )
            child_run.status = (
                Run.Status.FAILED if index == 0 else Run.Status.DONE
            )
            child_run.save(update_fields=["status"])
            child_runs.append(child_run)

        response = self.client.get(
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/"
        )

        self.assertEqual(response.status_code, 200, response.data)
        children = [
            item
            for item in response.data["summary"]["run_progress"]
            if item["role"] == "child"
        ]
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["assistant_name"], "OfficeCli")
        self.assertEqual(children[0]["attempt_count"], 2)
        self.assertEqual(
            [item["run_uuid"] for item in children[0]["attempts"]],
            [str(item.uuid) for item in child_runs],
        )
        self.assertEqual(children[0]["status"], Run.Status.DONE)

    def test_non_admin_cannot_read_trajectory(self):
        client = APIClient()
        client.force_authenticate(self.user)

        response = client.get(f"/api/lens/admin/runs/{self.run.uuid}/trajectory/")

        self.assertEqual(response.status_code, 403)

    def test_trajectory_response_exposes_stable_stream_checkpoint(self):
        append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [self.trace_event(sequence=1)],
        )

        response = self.client.get(
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["stream_cursor"])
        self.assertTrue(response.data["revision"])


class AdminRunTrajectoryStreamTests(RunTraceFixtureMixin, TransactionTestCase):
    def setUp(self):
        self.create_trace_fixture()

    def test_stream_resumes_after_checkpoint_without_replaying_events(self):
        append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [self.trace_event(sequence=1)],
        )
        admin = User.objects.create_user(
            username="trace-stream-admin",
            password="x",
            is_staff=True,
        )
        client = APIClient()
        client.force_authenticate(admin)
        checkpoint = client.get(
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/"
        ).data

        async def consume():
            stream = stream_admin_run_trajectory_events_async(
                self.run.uuid,
                cursor=checkpoint["stream_cursor"],
                revision=checkpoint["revision"],
                poll_interval=0,
                terminal_quiet_polls=1,
            )
            sync_event = await anext(stream)
            await sync_to_async(append_run_trace_events)(
                self.run.uuid,
                self.node.uuid,
                [self.trace_event(sequence=2)],
            )
            append_event = await anext(stream)
            await stream.aclose()
            return sync_event, append_event

        sync_event, append_event = async_to_sync(consume)()

        self.assertEqual(sync_event["type"], "sync")
        self.assertEqual(sync_event["events"], [])
        self.assertEqual(
            sync_event["previous_revision"],
            checkpoint["revision"],
        )
        self.assertEqual(append_event["type"], "append")
        self.assertEqual(
            [item["event_id"] for item in append_event["events"]],
            [str(self.run.trace_events.get(sequence=2).event_id)],
        )
        self.assertNotEqual(
            append_event["revision"],
            append_event["previous_revision"],
        )

    def test_stream_endpoint_requires_admin_console_access(self):
        client = Client()
        client.force_login(self.user)

        response = client.get(
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/stream/"
        )

        self.assertEqual(response.status_code, 403)

    def test_stream_endpoint_rejects_an_invalid_cursor(self):
        admin = User.objects.create_user(
            username="invalid-cursor-trace-stream-admin",
            password="x",
            is_staff=True,
        )
        client = Client()
        client.force_login(admin)

        response = client.get(
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/stream/",
            {"cursor": "not-a-cursor"},
        )

        self.assertEqual(response.status_code, 400)

    def test_stream_endpoint_rejects_a_cursor_from_another_run(self):
        other_run = create_execution_run(
            self.run.session,
            "Inspect another trace",
            enqueue=False,
        )
        append_run_trace_events(
            other_run.uuid,
            self.node.uuid,
            [self.trace_event(sequence=1)],
        )
        admin = User.objects.create_user(
            username="cross-run-cursor-trace-stream-admin",
            password="x",
            is_staff=True,
        )
        api_client = APIClient()
        api_client.force_authenticate(admin)
        other_checkpoint = api_client.get(
            f"/api/lens/admin/runs/{other_run.uuid}/trajectory/"
        ).data
        client = Client()
        client.force_login(admin)

        response = client.get(
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/stream/",
            {"cursor": other_checkpoint["stream_cursor"]},
        )

        self.assertEqual(response.status_code, 400)

    def test_stream_discovers_delegated_runs_created_after_sync(self):
        def create_child_event():
            child_assistant = Assistant.objects.create(
                name="Live delegated trace assistant",
                slug=f"live-delegated-trace-{uuid.uuid4()}",
                lensnode=self.node,
                selected_task="general_chat",
            )
            child_session = Session.objects.create(
                assistant=child_assistant,
                user=self.user,
            )
            child_run = create_execution_run(
                child_session,
                "Inspect the live delegated run",
                enqueue=False,
                parent_run=self.run,
            )
            append_run_trace_events(
                child_run.uuid,
                self.node.uuid,
                [self.trace_event(sequence=1)],
            )
            return child_run

        async def consume():
            stream = stream_admin_run_trajectory_events_async(
                self.run.uuid,
                poll_interval=0,
            )
            sync_event = await anext(stream)
            child_run = await sync_to_async(create_child_event)()
            append_event = await anext(stream)
            await stream.aclose()
            return sync_event, append_event, child_run

        sync_event, append_event, child_run = async_to_sync(consume)()

        self.assertEqual(sync_event["events"], [])
        self.assertEqual(append_event["type"], "append")
        self.assertEqual(
            append_event["events"][0]["trace_run_uuid"],
            str(child_run.uuid),
        )
        self.assertEqual(append_event["events"][0]["trace_run_role"], "child")

    def test_stream_advances_cursor_when_filters_hide_new_events(self):
        append_run_trace_events(
            self.run.uuid,
            self.node.uuid,
            [self.trace_event(sequence=1)],
        )
        admin = User.objects.create_user(
            username="filtered-trace-stream-admin",
            password="x",
            is_staff=True,
        )
        client = APIClient()
        client.force_authenticate(admin)
        checkpoint = client.get(
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/"
        ).data

        async def consume():
            stream = stream_admin_run_trajectory_events_async(
                self.run.uuid,
                cursor=checkpoint["stream_cursor"],
                revision=checkpoint["revision"],
                filters={"category": "tool"},
                display_sequence=checkpoint["stream_sequence"],
                poll_interval=0,
            )
            sync_event = await anext(stream)
            await sync_to_async(append_run_trace_events)(
                self.run.uuid,
                self.node.uuid,
                [self.trace_event(sequence=2)],
            )
            append_event = await anext(stream)
            await stream.aclose()
            return sync_event, append_event

        sync_event, append_event = async_to_sync(consume)()

        self.assertEqual(sync_event["events"], [])
        self.assertEqual(append_event["type"], "append")
        self.assertEqual(append_event["events"], [])
        self.assertNotEqual(append_event["cursor"], sync_event["cursor"])
        self.assertEqual(append_event["sequence"], 2)

    def test_terminal_stream_endpoint_emits_sync_and_done(self):
        self.run.status = Run.Status.DONE
        self.run.finished_at = timezone.now()
        self.run.save(update_fields=["status", "finished_at", "updated_at"])
        admin = User.objects.create_user(
            username="terminal-trace-stream-admin",
            password="x",
            is_staff=True,
        )
        client = Client()
        client.force_login(admin)

        response = client.get(
            f"/api/lens/admin/runs/{self.run.uuid}/trajectory/stream/",
            HTTP_ACCEPT="text/event-stream",
        )

        async def collect():
            chunks = []
            async for chunk in response.streaming_content:
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8")

        body = async_to_sync(collect)()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response["Content-Type"].startswith("text/event-stream")
        )
        self.assertIn('"type": "sync"', body)
        self.assertIn('"type": "done"', body)
