from datetime import timedelta

from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from django_celery_results.models import TaskResult

from lens.tasks import cleanup_task_results_task


class TaskResultCleanupTests(TestCase):
    def test_deletes_rows_past_celery_result_expiry_only(self):
        old = TaskResult.objects.create(
            task_id="expired-result",
            task_name="lens.source_sync",
            status="SUCCESS",
        )
        fresh = TaskResult.objects.create(
            task_id="fresh-result",
            task_name="lens.source_sync",
            status="SUCCESS",
        )
        expired_at = timezone.now() - timedelta(
            seconds=int(settings.CELERY_RESULT_EXPIRES) + 60
        )
        TaskResult.objects.filter(pk=old.pk).update(date_created=expired_at)

        cleanup_task_results_task(batch_size=10)

        self.assertFalse(TaskResult.objects.filter(pk=old.pk).exists())
        self.assertTrue(TaskResult.objects.filter(pk=fresh.pk).exists())

    def test_cleanup_is_bounded_by_max_batches(self):
        expired_at = timezone.now() - timedelta(
            seconds=int(settings.CELERY_RESULT_EXPIRES) + 60
        )
        for index in range(5):
            row = TaskResult.objects.create(
                task_id=f"expired-{index}",
                task_name="lens.source_sync",
                status="SUCCESS",
            )
            TaskResult.objects.filter(pk=row.pk).update(date_created=expired_at)

        deleted = cleanup_task_results_task(batch_size=2, max_batches=1)

        self.assertEqual(deleted, 2)
        self.assertEqual(
            TaskResult.objects.filter(task_id__startswith="expired-").count(),
            3,
        )
