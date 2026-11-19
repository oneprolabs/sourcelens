"""Persist notification retention policy and its actual cleanup schedule together."""

from agentcore_notifier.adapters.django import conf
from agentcore_notifier.adapters.django.services import notification_config
from celery.schedules import crontab
from django.conf import settings
from django.db import transaction
from django_celery_beat.models import CrontabSchedule, PeriodicTask
from rest_framework import serializers

CLEANUP_TASK = "agentcore-notifier-cleanup-old-records"


def effective_config():
    """Read actual Beat state instead of reporting a detached config switch."""

    config = dict(notification_config.get_config("global") or {})
    config["retention_days"] = conf.get_retention_days()
    task = PeriodicTask.objects.select_related("crontab").filter(name=CLEANUP_TASK).first()
    available = getattr(settings, "AGENTCORE_NOTIFIER_CLEANUP_ENABLED", True)
    config["cleanup_available"] = available
    config["cleanup_enabled"] = available and bool(task and task.enabled)
    config["cleanup_crontab"] = conf.get_cleanup_crontab()
    if task and task.crontab_id:
        cron = task.crontab
        config["cleanup_crontab"] = (
            f"{cron.minute} {cron.hour} {cron.day_of_month} {cron.month_of_year} {cron.day_of_week}"
        )
    return config


class NotificationConfigSerializer(serializers.Serializer):
    """Validate supported policy fields before either configuration or Beat writes."""

    retention_days = serializers.IntegerField(min_value=1, max_value=3650, required=False)
    cleanup_enabled = serializers.BooleanField(required=False)
    cleanup_crontab = serializers.CharField(max_length=128, required=False)

    def validate(self, attrs):
        """Reject unsupported fields and invalid cron expressions."""

        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: "Unsupported setting." for key in unknown})
        if attrs.get("cleanup_enabled") and not getattr(settings, "AGENTCORE_NOTIFIER_CLEANUP_ENABLED", True):
            raise serializers.ValidationError({"cleanup_enabled": "Disabled by worker configuration."})
        if "cleanup_crontab" in attrs:
            parts = attrs["cleanup_crontab"].split()
            try:
                if len(parts) != 5:
                    raise ValueError
                minute, hour, day, month, weekday = parts
                crontab(minute=minute, hour=hour, day_of_month=day, month_of_year=month, day_of_week=weekday)
            except (ValueError, TypeError, KeyError) as error:
                raise serializers.ValidationError({"cleanup_crontab": "Invalid 5-field cron expression."}) from error
        return attrs


@transaction.atomic
def update_config(data):
    """Preserve unrelated policy keys, cron runtime state and scheduler options."""

    value = dict(notification_config.get_config("global") or {})
    value.update(data)
    notification_config.set_config("global", value)
    if {"cleanup_enabled", "cleanup_crontab"}.intersection(data):
        task = PeriodicTask.objects.select_for_update().filter(name=CLEANUP_TASK).first()
        if task is None:
            task = PeriodicTask(
                name=CLEANUP_TASK,
                task="agentcore_notifier.adapters.django.tasks.cleanup.cleanup_old_notification_records_task",
                enabled=False,
                queue=getattr(settings, "CELERY_TASK_DEFAULT_QUEUE", "celery"),
            )
        if "cleanup_crontab" in data or not task.pk:
            minute, hour, day, month, weekday = data.get("cleanup_crontab", conf.get_cleanup_crontab()).split()
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
        if "cleanup_enabled" in data:
            task.enabled = data["cleanup_enabled"]
        task.save()
    return effective_config()
