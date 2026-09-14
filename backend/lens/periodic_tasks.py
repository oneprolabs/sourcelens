from core.periodic_registry import TASK_REGISTRY

from .models import DataSource, GlobalSetting, ScheduledTask


GLOBAL_PERIODIC_TASKS = {
    ScheduledTask.TaskType.LENSNODE_CLEANUP: {
        "name": "lens-lensnode-cleanup",
        "task": "lens.lensnode_cleanup",
        "setting_key": "lensnode_cleanup.interval_seconds",
        "default_interval": 3600,
    },
    ScheduledTask.TaskType.LENSNODE_HEALTH: {
        "name": "lens-lensnode-health",
        "task": "lens.lensnode_health",
        "setting_key": "lensnode_health.interval_seconds",
        "default_interval": 60,
    },
    ScheduledTask.TaskType.RUN_RETENTION: {
        "name": "lens-run-retention",
        "task": "lens.run_retention",
        "setting_key": "run_retention.interval_seconds",
        "default_interval": 86400,
    },
}


def _get_global_setting_int(key, default):
    """Return a positive integer setting value or a fallback."""

    setting = GlobalSetting.objects.filter(key=key).first()
    if setting is None:
        return default
    try:
        value = int(setting.value)
    except (TypeError, ValueError):
        return default
    return max(value, 1)


def _ensure_global_scheduled_task(task_type):
    """Create the UI mirror row for a global periodic task."""

    task, _ = ScheduledTask.objects.get_or_create(
        task_type=task_type,
        target_type=None,
        target_id=None,
        defaults={"name": task_type, "enabled": True},
    )
    return task


def sync_global_periodic_task(task_type):
    """Sync one global periodic task interval from global settings."""

    meta = GLOBAL_PERIODIC_TASKS[task_type]
    interval = _get_global_setting_int(
        meta["setting_key"],
        meta["default_interval"],
    )

    from django_celery_beat.models import IntervalSchedule, PeriodicTask, PeriodicTasks

    record = _ensure_global_scheduled_task(task_type)
    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=interval,
        period=IntervalSchedule.SECONDS,
    )
    task, created = PeriodicTask.objects.get_or_create(
        name=meta["name"],
        defaults={
            "task": meta["task"],
            "interval": schedule,
            "queue": "lens",
            "enabled": record.enabled,
        },
    )
    if not created and task.interval_id != schedule.id:
        task.interval = schedule
        task.save(update_fields=["interval"])
        PeriodicTasks.update_changed()

    if record.periodic_task_ref != task.id:
        record.periodic_task_ref = task.id
        record.save(update_fields=["periodic_task_ref"])

    return task


def _ensure_source_scheduled_task(datasource):
    """Create the UI mirror row for a datasource periodic task."""

    record, _ = ScheduledTask.objects.get_or_create(
        task_type=ScheduledTask.TaskType.SOURCE_SYNC,
        target_type="datasource",
        target_id=datasource.uuid,
        defaults={
            "name": f"source_sync:{datasource.uuid}",
            "enabled": True,
        },
    )
    return record


def ensure_datasource_periodic_task(datasource):
    """Create missing Celery Beat and UI rows for a datasource sync."""

    if datasource.source_type == DataSource.SourceType.MANAGED_WORKSPACE:
        _disable_datasource_periodic_task(datasource)
        return None
    if datasource.status == DataSource.Status.DISABLED:
        _disable_datasource_periodic_task(datasource)
        return None

    from django_celery_beat.models import PeriodicTask, PeriodicTasks

    schedule_field, schedule = _datasource_schedule(datasource.sync_policy)
    name = f"lens-source-sync-{datasource.uuid}"
    task, created = PeriodicTask.objects.get_or_create(
        name=name,
        defaults={
            "task": "lens.source_sync",
            schedule_field: schedule,
            "args": f'["{datasource.uuid}"]',
            "queue": "lens",
            "enabled": True,
        },
    )
    changed = created
    expected_args = f'["{datasource.uuid}"]'
    update_fields = []
    if not created:
        if task.task != "lens.source_sync":
            task.task = "lens.source_sync"
            update_fields.append("task")
        current_id = getattr(task, f"{schedule_field}_id")
        if current_id != schedule.id:
            task.interval = None
            task.crontab = None
            setattr(task, schedule_field, schedule)
            update_fields.extend(["interval", "crontab"])
        if task.args != expected_args:
            task.args = expected_args
            update_fields.append("args")
        if task.queue != "lens":
            task.queue = "lens"
            update_fields.append("queue")
        if not task.enabled:
            task.enabled = True
            update_fields.append("enabled")
        if update_fields:
            task.save(update_fields=update_fields)
            changed = True
    if changed:
        PeriodicTasks.update_changed()

    record = _ensure_source_scheduled_task(datasource)
    if record.periodic_task_ref != task.id:
        record.periodic_task_ref = task.id
    if not record.enabled:
        record.enabled = True
    record.save(update_fields=["periodic_task_ref", "enabled"])
    return record


def estimate_datasource_next_run(datasource, record=None):
    """Return a best-effort next run time for datasource sync."""

    if datasource.source_type == DataSource.SourceType.MANAGED_WORKSPACE:
        return None
    if datasource.status == DataSource.Status.DISABLED:
        return None

    record = record or ScheduledTask.objects.filter(
        task_type=ScheduledTask.TaskType.SOURCE_SYNC,
        target_type="datasource",
        target_id=datasource.uuid,
    ).first()
    if record is not None and not record.enabled:
        return None

    sync_policy = datasource.sync_policy or {}
    if (sync_policy.get("mode") or "interval") == "crontab":
        return _estimate_crontab_next_run(sync_policy)

    last_run_at = getattr(record, "last_run_at", None) if record else None
    if last_run_at is None:
        return None

    from datetime import timedelta

    interval = max(int(sync_policy.get("interval_seconds") or 3600), 1)
    return last_run_at + timedelta(seconds=interval)


def _estimate_crontab_next_run(sync_policy):
    """Return the next crontab fire time using Celery's cron parser."""

    from datetime import timedelta
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from celery.schedules import crontab
    from django.utils import timezone

    try:
        cron_timezone = ZoneInfo(sync_policy.get("timezone") or "UTC")
    except ZoneInfoNotFoundError:
        cron_timezone = ZoneInfo("UTC")

    try:
        minute, hour, day_of_month, month_of_year, day_of_week = str(
            sync_policy.get("cron") or "0 * * * *"
        ).split()
    except ValueError:
        return None

    now = timezone.now().astimezone(cron_timezone)
    schedule = crontab(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        day_of_week=day_of_week,
        nowfun=lambda: now,
    )
    remaining = schedule.remaining_estimate(now)
    return now + max(remaining, timedelta())


def _datasource_schedule(sync_policy):
    """Return the Celery Beat schedule field and instance."""

    sync_policy = sync_policy or {}
    mode = sync_policy.get("mode") or "interval"
    if mode == "crontab":
        from django_celery_beat.models import CrontabSchedule

        minute, hour, day_of_month, month_of_year, day_of_week = str(
            sync_policy.get("cron") or "0 * * * *"
        ).split()
        schedule, _ = CrontabSchedule.objects.get_or_create(
            minute=minute,
            hour=hour,
            day_of_week=day_of_week,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            timezone=sync_policy.get("timezone") or "UTC",
        )
        return "crontab", schedule

    from django_celery_beat.models import IntervalSchedule

    interval = sync_policy.get("interval_seconds", 3600)
    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=max(int(interval), 1),
        period=IntervalSchedule.SECONDS,
    )
    return "interval", schedule


def _disable_datasource_periodic_task(datasource):
    from django_celery_beat.models import PeriodicTask, PeriodicTasks

    name = f"lens-source-sync-{datasource.uuid}"
    task = PeriodicTask.objects.filter(name=name).first()
    if task is not None and task.enabled:
        task.enabled = False
        task.save(update_fields=["enabled"])
        PeriodicTasks.update_changed()

    ScheduledTask.objects.filter(
        task_type=ScheduledTask.TaskType.SOURCE_SYNC,
        target_type="datasource",
        target_id=datasource.uuid,
    ).update(enabled=False)


def register_periodic_tasks():
    """Register Lens periodic tasks in the project registry."""

    for task_type, meta in GLOBAL_PERIODIC_TASKS.items():
        schedule = _get_global_setting_int(
            meta["setting_key"],
            meta["default_interval"],
        )
        _ensure_global_scheduled_task(task_type)
        TASK_REGISTRY.add(
            name=meta["name"],
            task=meta["task"],
            schedule=schedule,
            queue="lens",
            enabled=True,
        )

    TASK_REGISTRY.add(
        name="lens-document-attachment-cleanup",
        task="lens.document_attachment_cleanup",
        schedule=3600,
        queue="lens",
        enabled=True,
    )

    TASK_REGISTRY.add(
        name="lens-session-title-timeout",
        task="lens.expire_stale_session_titles",
        schedule=60,
        queue="lens",
        enabled=True,
    )

    TASK_REGISTRY.add(
        name="lens-session-workspace-cleanup",
        task="lens.session_workspace_cleanup",
        schedule=3600,
        queue="lens",
        enabled=True,
    )

    datasources = DataSource.objects.filter(
        status=DataSource.Status.ACTIVE,
    ).exclude(source_type=DataSource.SourceType.MANAGED_WORKSPACE)
    for datasource in datasources:
        interval = datasource.sync_policy.get("interval_seconds", 3600)
        _ensure_source_scheduled_task(datasource)
        TASK_REGISTRY.add(
            name=f"lens-source-sync-{datasource.uuid}",
            task="lens.source_sync",
            schedule=interval,
            args=(str(datasource.uuid),),
            queue="lens",
            enabled=True,
        )


def sync_registered_periodic_tasks():
    """Backfill django-celery-beat PeriodicTask IDs into ScheduledTask."""

    from django_celery_beat.models import PeriodicTask

    mappings = [
        (
            ScheduledTask.TaskType.LENSNODE_CLEANUP,
            None,
            None,
            "lens-lensnode-cleanup",
        ),
        (
            ScheduledTask.TaskType.LENSNODE_HEALTH,
            None,
            None,
            "lens-lensnode-health",
        ),
        (
            ScheduledTask.TaskType.RUN_RETENTION,
            None,
            None,
            "lens-run-retention",
        ),
    ]
    for datasource in DataSource.objects.all():
        ensure_datasource_periodic_task(datasource)
        mappings.append(
            (
                ScheduledTask.TaskType.SOURCE_SYNC,
                "datasource",
                datasource.uuid,
                f"lens-source-sync-{datasource.uuid}",
            )
        )

    for task_type, target_type, target_id, name in mappings:
        periodic_task = PeriodicTask.objects.filter(name=name).first()
        if periodic_task is None:
            continue
        ScheduledTask.objects.filter(
            task_type=task_type,
            target_type=target_type,
            target_id=target_id,
        ).update(periodic_task_ref=periodic_task.id)
