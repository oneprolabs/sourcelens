from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from lens.models import (
    Assistant,
    LensNode,
    Run,
    RunDiagnostic,
    RunDiagnosticEvidence,
    ScheduledTask,
    Session,
)
from lens.services import create_execution_run
from lens.tasks import run_retention_task

User = get_user_model()


class RunRetentionTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="retention-user")
        self.lensnode = LensNode.objects.create(
            name="Retention Node",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        )
        self.assistant = Assistant.objects.create(
            name="Retention Assistant",
            slug="retention-assistant",
            lensnode=self.lensnode,
            selected_task="knowledge_qa",
            agent_model_ref="11111111-1111-1111-1111-111111111111",
            settings={"runtime_mode": "knowledge_qa"},
        )
        self.session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
        )
        self.run = create_execution_run(
            session=self.session,
            question="Old question",
            enqueue=False,
        )
        self.run.status = Run.Status.DONE
        self.run.outcome = Run.Outcome.COMPLETED
        self.run.finished_at = timezone.now() - timedelta(days=40)
        self.run.save(
            update_fields=[
                "status",
                "outcome",
                "finished_at",
                "updated_at",
            ]
        )
        self.evidence = RunDiagnosticEvidence.objects.create(
            run=self.run,
            payload={"answer": "old"},
        )
        self.diagnostic = RunDiagnostic.objects.create(
            run=self.run,
            evidence=self.evidence,
        )

    def test_retention_deletes_runs_with_protected_diagnostic_evidence(self):
        run_retention_task()

        self.assertFalse(Run.objects.filter(pk=self.run.pk).exists())
        self.assertFalse(
            RunDiagnostic.objects.filter(pk=self.diagnostic.pk).exists()
        )
        self.assertFalse(
            RunDiagnosticEvidence.objects.filter(pk=self.evidence.pk).exists()
        )
        record = ScheduledTask.objects.get(
            task_type=ScheduledTask.TaskType.RUN_RETENTION,
            target_type__isnull=True,
            target_id__isnull=True,
        )
        self.assertEqual(record.last_status, ScheduledTask.Status.SUCCESS)
        self.assertGreaterEqual(record.last_metrics.get("deleted", 0), 1)
