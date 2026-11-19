"""Persist task parameters and Beat schedules as one configuration change."""

from agentcore_task.adapters.django import conf
from agentcore_task.adapters.django.models import TaskConfig
from django.conf import settings
from django.db import transaction
from django_celery_beat.models import CrontabSchedule, PeriodicTask

SCHEDULES = {
    "cleanup": ("agentcore-task-cleanup-old-executions", "cleanup_old_task_executions"),
    "mark_timeout": ("agentcore-task-mark-timed-out-executions", "mark_timed_out_task_executions"),
}


def effective_task_config():
    """Read the actual Beat schedule together with worker runtime limits."""
    result = {"timeout_minutes": conf.get_task_timeout_minutes(), "retention_days": conf.get_retention_days()}
    for prefix, (name, _) in SCHEDULES.items():
        task = PeriodicTask.objects.select_related("crontab").filter(name=name).first()
        available = getattr(conf, f"get_{prefix}_enabled")()
        result[f"{prefix}_available"] = available
        result[f"{prefix}_enabled"] = available and bool(task and task.enabled)
        expression = getattr(conf, f"get_{prefix}_crontab")()
        if task and task.crontab_id:
            cron = task.crontab
            expression = f"{cron.minute} {cron.hour} {cron.day_of_month} {cron.month_of_year} {cron.day_of_week}"
        result[f"{prefix}_crontab"] = expression
    return result


@transaction.atomic
def update_task_config(data):
    """Save validated parameters and actual scheduler state together."""
    for key in ("timeout_minutes", "retention_days", "cleanup_crontab", "mark_timeout_crontab"):
        if key in data:
            TaskConfig.objects.update_or_create(scope="global", user=None, key=key, defaults={"value": data[key]})
    for prefix, (name, task_name) in SCHEDULES.items():
        if not {f"{prefix}_crontab", f"{prefix}_enabled"}.intersection(data):
            continue
        task = PeriodicTask.objects.select_for_update().filter(name=name).first()
        if task is None:
            task = PeriodicTask(
                name=name,
                task=f"agentcore_task.adapters.django.tasks.{task_name}",
                queue=getattr(settings, "CELERY_TASK_DEFAULT_QUEUE", "celery"),
                enabled=False,
            )
        if f"{prefix}_crontab" in data or not task.pk:
            expr = data.get(f"{prefix}_crontab", getattr(conf, f"get_{prefix}_crontab")())
            minute, hour, day, month, weekday = expr.split()
            crontab, _ = CrontabSchedule.objects.get_or_create(
                minute=minute,
                hour=hour,
                day_of_month=day,
                month_of_year=month,
                day_of_week=weekday,
                timezone=getattr(settings, "CELERY_TIMEZONE", "UTC"),
            )
            if task.crontab_id != crontab.pk:
                task.crontab = crontab
                task.interval = task.solar = task.clocked = None
                task.last_run_at = None
        if f"{prefix}_enabled" in data:
            task.enabled = data[f"{prefix}_enabled"]
        task.save()
    return effective_task_config()
