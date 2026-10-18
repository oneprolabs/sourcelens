"""Exercise alert schema upgrades with historical models and persisted data."""

import uuid

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class UnifiedAlertMigrationTests(TransactionTestCase):
    """The unified migration must include message publication and preserve old alerts."""

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.latest_targets = self.executor.loader.graph.leaf_nodes()
        self.addCleanup(self.restore_latest_schema)
        self.executor.migrate([("lens", "0065_alert_rules")])
        self.old_apps = self.executor.loader.project_state([("lens", "0065_alert_rules")]).apps

    def restore_latest_schema(self):
        """Restore every app before Django flushes the test database."""

        MigrationExecutor(connection).migrate(self.latest_targets)

    def test_upgrade_preserves_legacy_history_and_installs_message_deduplication(self):
        user = self.old_apps.get_model(settings.AUTH_USER_MODEL).objects.create(username="migration-user")
        assistant = self.old_apps.get_model("lens", "Assistant").objects.create(
            name="Existing assistant", slug="legacy"
        )
        session = self.old_apps.get_model("lens", "Session").objects.create(assistant=assistant, user=user)
        message = self.old_apps.get_model("lens", "Message").objects.create(
            session=session, role="user", sequence=1, content="Existing question"
        )
        run = self.old_apps.get_model("lens", "Run").objects.create(
            session=session, input_message=message, status="failed", finished_at=timezone.now()
        )
        channel_uuid = uuid.uuid4()
        rule = self.old_apps.get_model("lens", "AlertRule").objects.create(
            name="Existing alert",
            events=["run_failed"],
            channel_uuid=channel_uuid,
            email_recipients=["ops@example.test"],
        )
        old_event = self.old_apps.get_model("lens", "AlertEvent")
        sent = old_event.objects.create(rule=rule, run=run, event_type="run_failed", status="dispatched")
        failed = old_event.objects.create(
            rule=rule, run=run, event_type="token_exceeded", status="failed", error_message="Historical failure"
        )

        target = [("lens", "0066_unified_alerts")]
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(target)
        apps = self.executor.loader.project_state(target).apps
        event_model = apps.get_model("lens", "AlertEvent")
        sent = event_model.objects.get(pk=sent.pk)
        failed = event_model.objects.get(pk=failed.pk)
        self.assertEqual(sent.status, "dispatched")
        self.assertEqual((failed.status, failed.error_message), ("failed", "Historical failure"))
        self.assertEqual(sent.source_key, f"run:{run.uuid}:terminal")
        self.assertNotEqual(sent.source_key, failed.source_key)
        self.assertEqual(sent.rule_snapshot["name"], "Existing alert")
        subscription = apps.get_model("lens", "AlertSubscription").objects.get(rule_id=rule.pk)
        self.assertEqual(subscription.channel_uuid, channel_uuid)
        self.assertEqual(subscription.email_recipients, ["ops@example.test"])
        self.assertFalse(apps.get_model("lens", "AlertOutbox").objects.exists())
        delivery_model = apps.get_model("lens", "AlertDelivery")
        self.assertFalse(delivery_model.objects.exists())
        self.assertEqual(apps.get_model("lens", "Assistant").objects.get(pk=assistant.pk).name, "Existing assistant")

        rule_model = apps.get_model("lens", "AlertRule")
        self.assertEqual(rule_model.objects.get(pk=rule.pk).topic, "")
        message_rule = rule_model.objects.create(
            name="Release subscription", subject="message", topic="release.ready", events=["message_published"]
        )
        self.assertEqual(message_rule.topic, "release.ready")
        delivery_model.objects.create(event_id=sent.pk, dedupe_key="same-occurrence-destination")
        with self.assertRaises(IntegrityError), transaction.atomic():
            delivery_model.objects.create(event_id=sent.pk, dedupe_key="same-occurrence-destination")
