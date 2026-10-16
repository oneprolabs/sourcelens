import os

from django.core.exceptions import ImproperlyConfigured
from kombu import Queue

# For production environments, use Redis or RabbitMQ as result backend.
CELERY_BROKER_URL = os.getenv(
    "CELERY_BROKER_URL",
    os.getenv("REDIS_URL", "redis://localhost:6379"),
)

# Use Django database as result backend.
CELERY_RESULT_BACKEND = 'django-db'

# Set the default scheduler for Celery Beat
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

# Celery timezone configuration
# When USE_TZ=True, Celery uses UTC internally but schedules tasks
# according to the specified timezone. This ensures crontab schedules
# are interpreted in the correct timezone (Asia/Shanghai).
CELERY_TIMEZONE = 'Asia/Shanghai'
CELERY_ENABLE_UTC = True


# The CELERY_ACCEPT_CONTENT setting determines the message content types that
# Celery can accept. Setting it to ['json'] means that Celery only accepts JSON
# formatted message content. By default, Celery supports multiple content types
# (such as pickle, json, yaml, msgpack). Using JSON is for security and
# compatibility reasons. JSON format is lightweight, cross-platform, and less
# likely to cause potential security issues (such as pickle deserialization
# vulnerabilities).
CELERY_ACCEPT_CONTENT = ['json']

# The CELERY_TASK_SERIALIZER setting specifies how Celery serializes the task
# message content. Setting it to 'json' means that Celery will serialize the
# task content into JSON format. The purpose of this is to ensure that the task
# content can be transmitted and stored across platforms. JSON is a universal,
# lightweight serialization format that can transfer data between different
# languages and systems. Other optional serialization formats include pickle
# (not recommended, may have security risks), msgpack (more efficient
# compression), and yaml (more readable but less efficient).
CELERY_TASK_SERIALIZER = 'json'

# Dedicated queues for this project to avoid consuming tasks from other apps
# that share the same broker.
CELERY_TASK_DEFAULT_QUEUE = os.getenv("CELERY_TASK_DEFAULT_QUEUE", "backend")
CELERY_TASK_QUEUE_NAMES = [
    name.strip()
    for name in os.getenv("CELERY_TASK_QUEUES", "backend,lens").split(",")
    if name.strip()
]
if CELERY_TASK_DEFAULT_QUEUE not in CELERY_TASK_QUEUE_NAMES:
    CELERY_TASK_QUEUE_NAMES.insert(0, CELERY_TASK_DEFAULT_QUEUE)
CELERY_REQUIRED_QUEUES = {
    name.strip()
    for name in os.getenv("CELERY_REQUIRED_QUEUES", "backend,lens").split(",")
    if name.strip()
}
CELERY_QUEUE_DEPTH_THRESHOLD = int(
    os.getenv("CELERY_QUEUE_DEPTH_THRESHOLD", "1000")
)
CELERY_BEAT_LIVENESS_SECONDS = int(
    os.getenv("CELERY_BEAT_LIVENESS_SECONDS", "300")
)
missing_queues = CELERY_REQUIRED_QUEUES.difference(CELERY_TASK_QUEUE_NAMES)
if missing_queues:
    raise ImproperlyConfigured(
        "CELERY_TASK_QUEUES is missing required queues: "
        + ", ".join(sorted(missing_queues))
    )
CELERY_TASK_QUEUES = tuple(Queue(name) for name in CELERY_TASK_QUEUE_NAMES)

# Prevent task loss in Redis
CELERY_BROKER_TRANSPORT_OPTIONS = {
    'visibility_timeout': 43200,
    'fanout_prefix': True,
    'fanout_patterns': True,
}

# Task execution time limits
# CELERY_TASK_TIME_LIMIT: Hard limit (s); worker killed if exceeded.
# CELERY_TASK_SOFT_TIME_LIMIT: Soft limit (s); raises SoftTimeLimitExceeded.
# When worker receives SIGTERM, it waits for tasks; force-kill if over limit.
# Docker stop_grace_period (600s) should be < CELERY_TASK_TIME_LIMIT.
CELERY_TASK_TIME_LIMIT = 3600
CELERY_TASK_SOFT_TIME_LIMIT = 3000

# Task result expiration (keep results for 7 days)
CELERY_RESULT_EXPIRES = 604800

# Task acknowledgment settings
# When set to True, tasks are acknowledged after execution completes.
# This ensures tasks are not lost if worker crashes during execution.
# Combined with graceful shutdown, this allows workers to finish current tasks
# before stopping (up to CELERY_TASK_TIME_LIMIT seconds).
CELERY_TASK_ACKS_LATE = True

# Prefetch multiplier (configurable based on worker setup)
# For long-running I/O-bound tasks, set to 1
# This ensures fair task distribution and prevents tasks from queuing
# behind long-running ones
CELERY_WORKER_PREFETCH_MULTIPLIER = int(
    os.getenv('CELERY_WORKER_PREFETCH_MULTIPLIER', 1)
)

# Worker concurrency setting
# Note: This setting is for reference/documentation only.
# The actual concurrency is set via command-line argument --concurrency
# in entrypoint.sh, which reads from CELERY_CONCURRENCY environment variable.
# For I/O-bound tasks (network requests, file operations), higher concurrency
# is beneficial. Default is number of CPUs, but for I/O-bound tasks,
# you can set it higher (e.g., 2-4x CPU count)
# Configure via CELERY_CONCURRENCY environment variable in .env file

# Periodic tasks are registered via: python manage.py register_periodic_tasks
# (each app defines its tasks in periodic_tasks.py). No hardcode here.
CELERY_BEAT_SCHEDULE = {}
