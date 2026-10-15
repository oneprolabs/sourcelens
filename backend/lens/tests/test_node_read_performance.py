"""Regression coverage for bounded LensNode list reads."""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from lens.models import Assistant, DataSource, DataSourceItem, LensNode, Message, Run, RunTraceEvent, Session


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class LensNodeReadPerformanceTests(TestCase):
    """Keep list costs bounded while retaining storage and workload semantics."""

    def setUp(self):
        """Authenticate an administrator and prepare one node and session."""

        self.user = get_user_model().objects.create_user(username="node-performance-admin", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.node = LensNode.objects.create(name="A node", status=LensNode.Status.ONLINE)
        assistant = Assistant.objects.create(name="Node assistant", slug="node-performance", lensnode=self.node)
        self.session = Session.objects.create(assistant=assistant, user=self.user)
        self.message = Message.objects.create(session=self.session, role=Message.Role.USER, sequence=1)

    def _run(self, node=None, **kwargs):
        """Create persisted workload without dispatching a background task."""

        return Run.objects.create(
            session=self.session,
            input_message=self.message,
            lensnode=node or self.node,
            **kwargs,
        )

    def _event(self, run, sequence, payload, event_type="model.completed"):
        """Record a realistic model observation with a unique sequence."""

        return RunTraceEvent.objects.create(
            run=run,
            event_id=uuid.uuid4(),
            sequence=sequence,
            event_type=event_type,
            timestamp=timezone.now(),
            payload=payload,
        )

    def _list(self, **params):
        """Fetch the public admin list contract."""

        response = self.client.get("/api/lens/admin/lensnodes/", {"page_size": 100, **params})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_list_query_count_is_bounded_as_nodes_datasources_and_items_grow(self):
        """Adding related resources must not add one query per resource."""

        datasource = DataSource.objects.create(name="Initial", source_type="git", lensnode=self.node)
        DataSourceItem.objects.create(datasource=datasource, name="Initial item", source_type="git", storage_key="a")
        with CaptureQueriesContext(connection) as small:
            initial = self._list()
        self.assertEqual(len(initial["results"]), 1)

        nodes = [self.node] + [LensNode.objects.create(name=f"Node {index}") for index in range(4)]
        for node in nodes:
            for index in range(3):
                datasource = DataSource.objects.create(name=f"Datasource {index}", source_type="git", lensnode=node)
                DataSourceItem.objects.bulk_create([
                    DataSourceItem(
                        datasource=datasource,
                        name=f"Item {item_index}",
                        source_type="git",
                        storage_key=str(item_index),
                        storage_usage={"total_bytes": 10},
                    )
                    for item_index in range(4)
                ])

        with CaptureQueriesContext(connection) as large:
            expanded = self._list()

        self.assertEqual(len(expanded["results"]), 5)
        self.assertEqual(sum(len(row["datasources"]) for row in expanded["results"]), 16)
        self.assertLessEqual(
            len(large),
            len(small) + 1,
            f"List queries grew from {len(small)} to {len(large)} for five nodes and sixteen datasources",
        )

    def test_token_totals_preserve_integer_coercion_and_ignore_unrelated_events(self):
        """Legacy token values remain tolerant and isolated by node and event type."""

        run = self._run()
        values = [17, "13", 3.9, True, -20, "-5", "bad", "2.5", None, {}, [], 0, 10**30, " +9 "]
        for sequence, value in enumerate(values, start=1):
            self._event(run, sequence, {"usage": {"total_tokens": value}})
        self._event(run, 20, {"usage": None})
        self._event(run, 21, {})
        self._event(run, 22, {"usage": {"total_tokens": 999}}, event_type="model.started")
        other = LensNode.objects.create(name="B node")
        self._event(self._run(node=other), 1, {"usage": {"total_tokens": 5000}})

        data = self._list()
        rows = {row["uuid"]: row for row in data["results"]}

        self.assertEqual(rows[str(self.node.uuid)]["total_tokens"], 10**30 + 43)
        self.assertEqual(rows[str(other.uuid)]["total_tokens"], 5000)

    def test_token_summary_does_not_transfer_complete_event_payloads(self):
        """A token-only list must not read model prompts and outputs from JSON."""

        self._event(
            self._run(),
            1,
            {"usage": {"total_tokens": 8}, "output": "x" * 100_000},
        )

        with CaptureQueriesContext(connection) as captured:
            data = self._list()

        self.assertEqual(data["results"][0]["total_tokens"], 8)
        trace_queries = [query["sql"] for query in captured if '"lens_runtraceevent"' in query["sql"]]
        self.assertTrue(trace_queries)
        for sql in trace_queries:
            projection = sql.partition(" FROM ")[0].removeprefix("SELECT ")
            self.assertNotRegex(
                projection,
                r'(?:^|,\s*)"lens_runtraceevent"\."payload"(?:\s+AS\s+"[^"]+")?(?:\s*,|\s*$)',
            )

    def test_storage_usage_and_fleet_workload_remain_correct_when_paginated(self):
        """Active item sums, stored fallback, and fleet-wide totals stay intact."""

        measured = DataSource.objects.create(
            name="Measured", source_type="git", lensnode=self.node, storage_usage={"total_bytes": 999}
        )
        for key, status, usage in [
            ("first", "active", {"raw_bytes": "10", "derived_bytes": 2, "total_bytes": 12}),
            ("second", "active", {"raw_bytes": 20, "derived_bytes": 3, "total_bytes": 23}),
            ("empty", "active", {}),
            ("disabled", "disabled", {"total_bytes": 999}),
        ]:
            DataSourceItem.objects.create(
                datasource=measured, name=key, source_type="git", storage_key=key, status=status, storage_usage=usage
            )
        fallback = DataSource.objects.create(
            name="Fallback",
            source_type="git",
            lensnode=self.node,
            storage_usage={"raw_bytes": 5, "derived_bytes": 1, "total_bytes": 6, "status": "partial"},
        )
        DataSourceItem.objects.create(datasource=fallback, name="Unmeasured", source_type="git", storage_key="empty")
        empty = DataSource.objects.create(name="Empty", source_type="git", lensnode=self.node)
        DataSource.objects.create(name="Disabled", source_type="git", lensnode=self.node, status="disabled")
        draining = LensNode.objects.create(name="B draining", status=LensNode.Status.DRAINING)
        self._run(status=Run.Status.QUEUED)
        self._run(status=Run.Status.RUNNING, resume_by=timezone.now() + timedelta(minutes=10))
        self._run(status=Run.Status.STREAMING, resume_by=timezone.now() - timedelta(minutes=10))
        self._run(status=Run.Status.DONE)
        self._run(status=Run.Status.FAILED)
        self._run(node=draining, status=Run.Status.QUEUED)

        data = self._list(page_size=1)
        row = data["results"][0]
        sources = {source["uuid"]: source for source in row["datasources"]}

        self.assertEqual(row["uuid"], str(self.node.uuid))
        self.assertEqual(len(sources), 3)
        self.assertEqual(
            sources[str(measured.uuid)]["storage_usage"],
            {"raw_bytes": 30, "derived_bytes": 5, "total_bytes": 35, "status": "complete"},
        )
        self.assertEqual(sources[str(fallback.uuid)]["storage_usage"], fallback.storage_usage)
        self.assertEqual(sources[str(empty.uuid)]["storage_usage"], {})
        for field, expected in {
            "active_run_count": 2,
            "queued_run_count": 1,
            "awaiting_resume_count": 1,
            "total_run_count": 5,
            "succeeded_run_count": 1,
            "failed_run_count": 1,
        }.items():
            self.assertEqual(row[field], expected, field)
        self.assertIsNotNone(row["last_run_at"])
        self.assertEqual(
            data["fleet_summary"],
            {
                "total": 2,
                "online": 1,
                "unresponsive": 0,
                "offline": 0,
                "draining": 1,
                "active_runs": 2,
                "queued_runs": 2,
                "awaiting_resume": 1,
            },
        )
