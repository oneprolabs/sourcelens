"""Save platform metering policy and actual Beat schedules atomically."""

from agentcore_metering.adapters.django import conf
from agentcore_metering.adapters.django.services.metering_config import set_global_config
from celery.schedules import crontab
from django.conf import settings
from django.db import transaction
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from rest_framework import serializers

SCHEDULES = {
    "cleanup": ("agentcore-metering-cleanup-old-usage", "cleanup.cleanup_old_llm_usage_task"),
    "aggregation": ("agentcore-metering-aggregate", "aggregate.aggregate_llm_usage_series_task"),
}


def effective_config():
    """Report the scheduler's actual configuration."""

    result = {"retention_days": conf.get_retention_days()}
    for prefix, (name, _) in SCHEDULES.items():
        task = PeriodicTask.objects.select_related("crontab").filter(name=name).first()
        expr = getattr(conf, f"get_{prefix}_crontab")()
        if task and task.crontab_id:
            cron = task.crontab
            expr = f"{cron.minute} {cron.hour} {cron.day_of_month} {cron.month_of_year} {cron.day_of_week}"
        result[f"{prefix}_crontab"] = expr
        if prefix == "cleanup":
            available = getattr(settings, "AGENTCORE_METERING_CLEANUP_ENABLED", True)
            result["cleanup_available"] = available
            result["cleanup_enabled"] = available and bool(task and task.enabled)
    return result


class MeteringPolicySerializer(serializers.Serializer):
    """Validate every value before either configuration or schedules change."""

    retention_days = serializers.IntegerField(min_value=1, max_value=3650, required=False)
    cleanup_enabled = serializers.BooleanField(required=False)
    cleanup_crontab = serializers.CharField(max_length=128, required=False)
    aggregation_crontab = serializers.CharField(max_length=128, required=False)

    def validate(self, attrs):
        """Reject unsupported settings and malformed cron expressions."""

        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: "Unsupported setting." for key in unknown})
        if attrs.get("cleanup_enabled") and not getattr(settings, "AGENTCORE_METERING_CLEANUP_ENABLED", True):
            raise serializers.ValidationError({"cleanup_enabled": "Disabled by worker configuration."})
        for key in ("cleanup_crontab", "aggregation_crontab"):
            if key in attrs:
                try:
                    minute, hour, day, month, weekday = attrs[key].split()
                    crontab(minute=minute, hour=hour, day_of_month=day, month_of_year=month, day_of_week=weekday)
                except (ValueError, TypeError, KeyError) as exc:
                    raise serializers.ValidationError({key: "Invalid 5-field cron expression."}) from exc
        return attrs


@transaction.atomic
def update_config(data):
    """Preserve queue, kwargs and run history when a schedule is unchanged."""

    for key, value in data.items():
        set_global_config(key, value)
    for prefix, (name, task_name) in SCHEDULES.items():
        keys = {f"{prefix}_crontab"} | ({"cleanup_enabled"} if prefix == "cleanup" else set())
        if not keys.intersection(data):
            continue
        task = PeriodicTask.objects.select_for_update().filter(name=name).first()
        if task is None:
            task = PeriodicTask(
                name=name,
                task=f"agentcore_metering.adapters.django.tasks.{task_name}",
                enabled=prefix == "aggregation",
                queue=getattr(settings, "CELERY_TASK_DEFAULT_QUEUE", "celery"),
            )
        if f"{prefix}_crontab" in data or not task.pk:
            expression = data.get(f"{prefix}_crontab", getattr(conf, f"get_{prefix}_crontab")())
            minute, hour, day, month, weekday = expression.split()
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute=minute,
                hour=hour,
                day_of_month=day,
                month_of_year=month,
                day_of_week=weekday,
                timezone=getattr(settings, "CELERY_TIMEZONE", "UTC"),
            )
            if task.crontab_id != schedule.pk:
                task.crontab = schedule
                task.interval = task.solar = task.clocked = None
                task.last_run_at = None
        if prefix == "cleanup" and "cleanup_enabled" in data:
            task.enabled = data["cleanup_enabled"]
        task.save()
    return effective_config()
