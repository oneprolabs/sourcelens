"""Guard bounded Run reads and preserve existing pagination contracts."""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from lens.models import Run, RunTraceEvent
from lens.services import create_execution_run
from lens.tests.test_run_trace import RunTraceFixtureMixin

User = get_user_model()


class AdminRunReadPerformanceTests(RunTraceFixtureMixin, TestCase):
    """Exercise production API reads without timing-sensitive assertions."""

    def setUp(self):
        """Authenticate an administrator against an isolated trace fixture."""

        self.create_trace_fixture()
        admin = User.objects.create_user(username="run-read-admin", password="x", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(admin)
        self.trajectory_url = f"/api/lens/admin/runs/{self.run.uuid}/trajectory/"

    def create_run(self, **kwargs):
        """Create another execution sharing the fixture's session."""

        return create_execution_run(
            self.run.session,
            "Another execution",
            enqueue=False,
            **kwargs,
        )

    def create_event(self, run, sequence, **kwargs):
        """Persist one deterministic trace record without transport side effects."""

        return RunTraceEvent.objects.create(
            run=run,
            sequence=sequence,
            event_id=uuid.uuid4(),
            timestamp=timezone.now(),
            **kwargs,
        )

    def create_interleaved_trace(self):
        """Give three Run scopes tied ingest times and conflicting event times."""

        child = self.create_run(parent_run=self.run)
        grandchild = self.create_run(parent_run=child)
        now = timezone.now()
        specifications = [
            (self.run, 1, "model.started", "model-1", ""),
            (child, 1, "tool.started", "child-tool", "model-1"),
            (self.run, 2, "tool.completed", "root-tool", "model-1"),
            (grandchild, 1, "tool.completed", "grandchild-tool", "child-tool"),
            (child, 2, "tool.completed", "child-tool", "model-1"),
            (self.run, 3, "model.completed", "model-1", ""),
        ]
        events = []
        for index, (run, sequence, event_type, call_id, parent_call_id) in enumerate(specifications, start=1):
            event = self.create_event(
                run,
                sequence,
                uuid=uuid.UUID(int=index),
                event_type=event_type,
                call_id=call_id,
                parent_call_id=parent_call_id,
                payload={"marker": index, "content": "x" * 65536},
            )
            RunTraceEvent.objects.filter(pk=event.pk).update(
                created_at=now,
                timestamp=now - timedelta(seconds=index),
            )
            events.append(event)
        return events

    def test_run_count_does_not_aggregate_off_page_trace_events(self):
        """Keep historical traces outside the parent Run count query."""

        off_page = self.create_run()
        for sequence in range(1, 13):
            self.create_event(
                off_page,
                sequence,
                event_type="tool.started",
                call_id=f"off-page-{sequence}",
                payload={"large_result": "x" * 65536},
            )
        Run.objects.filter(pk=self.run.pk).update(created_at=timezone.now() + timedelta(seconds=1))

        with CaptureQueriesContext(connection) as captured:
            response = self.client.get("/api/lens/admin/runs/", {"page_size": 1})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 2)
        self.assertEqual(response.data["results"][0]["uuid"], str(self.run.uuid))
        count_queries = [
            query["sql"] for query in captured if query["sql"].lstrip().upper().startswith("SELECT COUNT(")
        ]
        self.assertTrue(count_queries, "The list must calculate its total from the filtered Run queryset.")
        for sql in count_queries:
            self.assertNotIn(RunTraceEvent._meta.db_table, sql, "Counting Runs must not join their event history.")

    def test_run_list_preserves_distinct_tool_counts_retries_and_unfiltered_status_summary(self):
        """Retain distinct call counts and summary scope after page-local aggregation."""

        Run.objects.filter(pk=self.run.pk).update(status=Run.Status.FAILED)
        off_page = self.create_run()
        Run.objects.filter(pk=off_page.pk).update(status=Run.Status.FAILED)
        for _index in range(2):
            retry = self.create_run(retry_of_run=self.run)
            Run.objects.filter(pk=retry.pk).update(status=Run.Status.DONE)
        self.create_run(parent_run=self.run)
        specifications = [
            ("tool.started", "tool-1", {}),
            ("tool.completed", "tool-1", {}),
            ("subtool.started", "subtool-1", {}),
            ("step.event", "plugin-1", {"name": "tool.plugin.start"}),
            ("step.event", "plugin-1", {"name": "tool.plugin.done"}),
            ("step.event", "done-without-start", {"name": "tool.plugin.done"}),
            ("tool.started", "", {}),
            ("model.completed", "model-1", {}),
        ]
        for sequence, (event_type, call_id, payload) in enumerate(specifications, start=1):
            self.create_event(self.run, sequence, event_type=event_type, call_id=call_id, payload=payload)
        Run.objects.filter(pk=self.run.pk).update(created_at=timezone.now() + timedelta(seconds=1))

        response = self.client.get("/api/lens/admin/runs/", {"page_size": 1, "status": "failed"})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], 2)
        self.assertEqual(response.data["summary"]["total"], 4)
        self.assertEqual(response.data["summary"]["failed"], 2)
        self.assertEqual(response.data["summary"]["done"], 2)
        row = response.data["results"][0]
        self.assertEqual(row["uuid"], str(self.run.uuid))
        self.assertEqual(row["tool_call_count"], 3)
        self.assertEqual(row["retry_count"], 2)

    def test_trajectory_only_materializes_payloads_from_the_requested_page(self):
        """Decode payload objects only for events returned on the current page."""

        events = self.create_interleaved_trace()
        loaded_payloads = []
        original_from_db = RunTraceEvent.from_db

        def record_payloads(db, field_names, values):
            """Observe model hydration without changing its field values."""

            event = original_from_db(db, field_names, values)
            if "payload" in field_names:
                loaded_payloads.append(event.pk)
            return event

        with patch.object(RunTraceEvent, "from_db", side_effect=record_payloads):
            response = self.client.get(self.trajectory_url, {"after_sequence": 2, "page_size": 2})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([row["ingest_sequence"] for row in response.data["results"]], [3, 4])
        self.assertEqual(set(loaded_payloads), {events[2].pk, events[3].pk})
        self.assertEqual(len(loaded_payloads), 2)
        self.assertEqual(response.data["stream_sequence"], 6)
        self.assertEqual(response.data["summary"]["event_count"], 6)

    def test_trajectory_page_stops_at_its_stream_cursor_during_ingestion(self):
        """Newly ingested events belong to the next stream update, not this page."""

        for sequence in range(1, 3):
            self.create_event(self.run, sequence, event_type="tool.started", call_id=f"tool-{sequence}")
        original_from_db = RunTraceEvent.from_db
        inserted = False

        def ingest_after_cursor_read(db, field_names, values):
            """Simulate a new durable event after the initial cursor has been read."""

            nonlocal inserted
            event = original_from_db(db, field_names, values)
            if "payload" not in field_names and not inserted:
                inserted = True
                self.create_event(self.run, 3, event_type="tool.started", call_id="new-tool")
            return event

        with patch.object(RunTraceEvent, "from_db", side_effect=ingest_after_cursor_read):
            response = self.client.get(self.trajectory_url)

        self.assertTrue(inserted)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["stream_sequence"], 2)
        self.assertEqual(response.data["total"], 2)
        self.assertEqual([row["event_sequence"] for row in response.data["results"]], [1, 2])

    def test_trajectory_filters_keep_global_ingest_sequences_across_descendants(self):
        """Filter interleaved descendants without renumbering global ingest order."""

        self.create_interleaved_trace()
        filters = {
            "after_sequence": 1,
            "page_size": 1,
            "category": "tool",
            "event_type": "tool.completed",
            "call_id": "model-1",
        }

        first = self.client.get(self.trajectory_url, filters)
        second = self.client.get(self.trajectory_url, {**filters, "after_sequence": 3})

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data["total"], 2)
        self.assertEqual(first.data["results"][0]["ingest_sequence"], 3)
        self.assertEqual(first.data["results"][0]["event_sequence"], 2)
        self.assertEqual(first.data["next_after_sequence"], 3)
        self.assertTrue(first.data["has_more"])
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(second.data["total"], 1)
        self.assertEqual(second.data["results"][0]["ingest_sequence"], 5)
        self.assertEqual(second.data["results"][0]["trace_run_role"], "child")
        self.assertEqual(second.data["next_after_sequence"], 5)
        self.assertFalse(second.data["has_more"])
        self.assertEqual(first.data["summary"], second.data["summary"])
        self.assertEqual(first.data["stream_cursor"], second.data["stream_cursor"])
        self.assertEqual(first.data["revision"], second.data["revision"])

    def test_default_trajectory_and_exhausted_page_keep_summary_and_stream_checkpoint(self):
        """Retain unfiltered summary and stream checkpoints at the end of pagination."""

        events = self.create_interleaved_trace()

        response = self.client.get(self.trajectory_url)
        exhausted = self.client.get(self.trajectory_url, {"after_sequence": 6})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["page_size"], 200)
        self.assertEqual(response.data["after_sequence"], 0)
        self.assertEqual(response.data["total"], 6)
        self.assertEqual(response.data["next_after_sequence"], 6)
        self.assertFalse(response.data["has_more"])
        self.assertEqual([row["uuid"] for row in response.data["results"]], [str(event.pk) for event in events])
        self.assertEqual([row["sequence"] for row in response.data["results"]], [1, 1, 2, 1, 2, 3])
        for index, row in enumerate(response.data["results"], start=1):
            self.assertEqual(row["ingest_sequence"], index)
            self.assertEqual(row["event_sequence"], row["sequence"])
            self.assertEqual(row["payload"], {"marker": index, "content": "x" * 65536})
        self.assertEqual(response.data["summary"]["event_count"], 6)
        self.assertEqual(response.data["summary"]["categories"], {"model": 2, "tool": 4})
        self.assertEqual(response.data["summary"]["model_calls"], 1)
        self.assertEqual(response.data["summary"]["tool_calls"], 3)
        self.assertEqual(exhausted.status_code, 200, exhausted.data)
        self.assertEqual(exhausted.data["results"], [])
        self.assertEqual(exhausted.data["total"], 0)
        self.assertIsNone(exhausted.data["next_after_sequence"])
        self.assertFalse(exhausted.data["has_more"])
        self.assertEqual(exhausted.data["summary"], response.data["summary"])
        self.assertEqual(exhausted.data["stream_cursor"], response.data["stream_cursor"])
        self.assertEqual(exhausted.data["stream_sequence"], response.data["stream_sequence"])
        self.assertEqual(exhausted.data["revision"], response.data["revision"])

    def test_trajectory_search_preserves_python_payload_text_matching(self):
        """Keep Python representation searches compatible for legacy payloads."""

        first = self.create_event(self.run, 1, event_type="tool.completed", payload={"ok": False, "result": None})
        self.create_event(self.run, 2, event_type="tool.completed", payload={"ok": True, "result": "ordinary"})

        for keyword in ("'ok': false", "NONE"):
            with self.subTest(keyword=keyword):
                response = self.client.get(self.trajectory_url, {"q": keyword, "page_size": 1})

                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data["total"], 1)
                self.assertEqual(response.data["results"][0]["uuid"], str(first.uuid))
                self.assertEqual(response.data["results"][0]["ingest_sequence"], 1)
                self.assertEqual(response.data["summary"]["event_count"], 2)
