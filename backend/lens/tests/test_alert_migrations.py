"""Exercise alert schema upgrades with historical models and persisted data."""

import uuid

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class UnifiedAlertMigrationTests(TransactionTestCase):
    """Install the complete notification schema without changing existing Runs."""

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.latest_targets = self.executor.loader.graph.leaf_nodes()
        self.addCleanup(self.restore_latest_schema)
        self.executor.migrate([("lens", "0064_builtin_decisions")])
        self.old_apps = self.executor.loader.project_state([("lens", "0064_builtin_decisions")]).apps

    def restore_latest_schema(self):
        """Restore every app before Django flushes the test database."""

        MigrationExecutor(connection).migrate(self.latest_targets)

    def test_upgrade_preserves_existing_runs_and_installs_complete_notifications(self):
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
        target = [("lens", "0065_alert_rules")]
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(target)
        apps = self.executor.loader.project_state(target).apps
        self.assertEqual(apps.get_model("lens", "Assistant").objects.get(pk=assistant.pk).name, "Existing assistant")
        existing_run = apps.get_model("lens", "Run").objects.get(pk=run.pk)
        self.assertEqual((existing_run.status, existing_run.input_message_id), ("failed", message.pk))
        self.assertEqual(apps.get_model("lens", "Message").objects.get(pk=message.pk).content, "Existing question")

        rule_model = apps.get_model("lens", "AlertRule")
        self.assertFalse(rule_model.objects.exists())
        message_rule = rule_model.objects.create(
            name="Release subscription", subject="message", topic="release.ready", events=["message_published"]
        )
        self.assertEqual(message_rule.topic, "release.ready")
        subscription = apps.get_model("lens", "AlertSubscription").objects.create(
            rule=message_rule, channel_uuid=uuid.uuid4(), email_recipients=["ops@example.test"]
        )
        outbox_model = apps.get_model("lens", "AlertOutbox")
        self.assertFalse(outbox_model.objects.exists())
        publication = outbox_model.objects.create(
            source_key="message:release-1:terminal",
            source_uuid="release-1",
            subject="message",
            payload={"topic": "release.ready", "body": "Ready for review"},
        )
        event_model = apps.get_model("lens", "AlertEvent")
        self.assertFalse(event_model.objects.exists())
        event = event_model.objects.create(
            rule=message_rule, subject="message", source_key=publication.source_key, event_type="message_published"
        )
        self.assertIsNone(event.run_id)
        delivery_model = apps.get_model("lens", "AlertDelivery")
        self.assertFalse(delivery_model.objects.exists())
        delivery_model.objects.create(event=event, subscription=subscription, dedupe_key="same-occurrence-destination")
        with self.assertRaises(IntegrityError), transaction.atomic():
            delivery_model.objects.create(event=event, dedupe_key="same-occurrence-destination")
