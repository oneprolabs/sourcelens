#!/bin/bash
set -e

# -----------------------------------------------------------------------------
# Project Entrypoint Script
# -----------------------------------------------------------------------------
# This script manages migrations, static collection, and process startup for
# Django, Celery, Gunicorn, etc. Docker Compose handles dependency health.
# -----------------------------------------------------------------------------

# --- Change to project directory ---
cd /opt/backend || { echo "Error: Cannot change to /opt/backend directory"; exit 1; }

# --- Global Variables ---
export PYTHONPATH=/opt/backend
export DJANGO_SETTINGS_MODULE=core.settings

LOG_BASE_DIR="/var/log/gunicorn"
ACCESS_LOG="${LOG_BASE_DIR}/gunicorn_access.log"
ERROR_LOG="${LOG_BASE_DIR}/gunicorn_error.log"
CELERY_LOG="/var/log/celery/celery.log"

WORKERS=${WORKERS:-1}
THREADS=${THREADS:-1}

# --- Ensure log directories exist ---
mkdir -p $LOG_BASE_DIR /var/log/celery
chmod -R 755 $LOG_BASE_DIR /var/log/celery


# --- Logging Helper ---
log() { echo -e "\033[1;36m[entrypoint]\033[0m $*"; }

# --- Django Management Tasks ---
run_startup_init() {
    log "Running SourceLens startup initialization..."
    python manage.py sourcelens_init
}

# --- Process Starters ---
start_gunicorn() {
    # ASGI server. Default: multiple Uvicorn workers (multi-core; serves HTTP,
    # SSE, and the Channels WebSocket). All cross-process state goes through
    # Redis (channel layer + cache) and the DB, so workers scale horizontally.
    # Size via API_WORKERS. Set ASGI_SERVER=daphne to fall back to a single
    # Daphne process if ever needed.
    if [ "${ASGI_SERVER:-uvicorn}" = "daphne" ]; then
        log "Starting Daphne ASGI server (single process)..."
        exec daphne core.asgi:application \
            --bind 0.0.0.0 \
            --port 8000 \
            --ping-interval ${UVICORN_WS_PING_INTERVAL:-45} \
            --ping-timeout ${UVICORN_WS_PING_TIMEOUT:-30} \
            --access-log $ACCESS_LOG
    fi
    log "Starting Uvicorn ASGI workers (API_WORKERS=${API_WORKERS:-3})..."
    exec uvicorn core.asgi:application \
        --host 0.0.0.0 \
        --port 8000 \
        --workers ${API_WORKERS:-3} \
        --loop uvloop \
        --http httptools \
        --limit-max-requests ${UVICORN_MAX_REQUESTS:-10000} \
        --timeout-keep-alive ${UVICORN_KEEPALIVE:-75} \
        --timeout-graceful-shutdown ${UVICORN_GRACEFUL_TIMEOUT:-120} \
        --ws-ping-interval ${UVICORN_WS_PING_INTERVAL:-45} \
        --ws-ping-timeout ${UVICORN_WS_PING_TIMEOUT:-30} \
        --access-log \
        --no-server-header \
        --log-level ${API_LOG_LEVEL:-info}
}

start_wsgi_gunicorn() {
    log "Starting Gunicorn WSGI server..."
    exec gunicorn core.wsgi:application \
        --name backend \
        --bind 0.0.0.0:8000 \
        --workers $WORKERS \
        --threads $THREADS \
        --worker-class gthread \
        --log-level info \
        --access-logfile $ACCESS_LOG \
        --error-logfile $ERROR_LOG
}

start_celery_worker() {
    log "Starting Celery worker..."

    # Get CPU count for default concurrency
    # For I/O-bound tasks, we can use higher concurrency
    CPU_COUNT=$(nproc 2>/dev/null || echo 4)

    # Default concurrency: use CPU count for I/O-bound tasks
    # Can be overridden by CELERY_CONCURRENCY environment variable
    DEFAULT_CONCURRENCY=${CELERY_CONCURRENCY:-$CPU_COUNT}
    REQUIRED_QUEUES=${CELERY_REQUIRED_QUEUES:-backend,lens}
    WORKER_QUEUES=${CELERY_WORKER_QUEUES:-backend,lens}
    for required_queue in ${REQUIRED_QUEUES//,/ }; do
        case ",$WORKER_QUEUES," in
            *",$required_queue,"*) ;;
            *)
                log "Missing required Celery worker queue: $required_queue"
                return 1
                ;;
        esac
    done

    log "Celery worker concurrency: $DEFAULT_CONCURRENCY (CPUs: $CPU_COUNT)"
    log "Celery worker queues: $WORKER_QUEUES"
    log "Graceful shutdown enabled: worker will wait for running tasks to complete (up to stop_grace_period)"

    # Celery worker will gracefully shutdown when receiving SIGTERM:
    # - Stops accepting new tasks
    # - Waits for currently running tasks to complete
    # - Docker stop_grace_period (600s) allows time for tasks to finish
    # - CELERY_TASK_ACKS_LATE=True ensures tasks are only acknowledged after completion
    exec celery -A core worker \
        --loglevel=${CELERY_LOG_LEVEL:-INFO} \
        --concurrency=$DEFAULT_CONCURRENCY \
        --queues=$WORKER_QUEUES \
        --max-tasks-per-child=${CELERY_MAX_TASKS_PER_CHILD:-1000} \
        --max-memory-per-child=${CELERY_MAX_MEMORY_PER_CHILD:-256000} \
        --logfile=/var/log/celery/worker.log
}

start_celery_beat() {
    log "Starting Celery beat with DatabaseScheduler..."
    exec celery -A core beat \
        --scheduler django_celery_beat.schedulers:DatabaseScheduler \
        --loglevel=${CELERY_LOG_LEVEL:-INFO} \
        --logfile=/var/log/celery/beat.log
}

start_flower() {
    log "Starting Flower..."
    exec celery -A core flower \
        --port=${FLOWER_PORT:-5555} \
        --address=0.0.0.0 \
        --broker="${CELERY_BROKER_URL:-${REDIS_URL:-redis://redis:6379/0}}" \
        --loglevel=${CELERY_LOG_LEVEL:-INFO} \
        --logfile=/var/log/celery/flower.log
}

start_development() {
    log "Starting Django development server (runserver)..."
    exec python manage.py runserver 0.0.0.0:8000
}

# --- Main Entrypoint ---
case "$1" in
    gunicorn)
        run_startup_init
        start_gunicorn
        ;;
    wsgi-gunicorn)
        run_startup_init
        start_wsgi_gunicorn
        ;;
    celery)
        start_celery_worker
        ;;
    celery-beat)
        start_celery_beat
        ;;
    flower)
        start_flower
        ;;
    development)
        run_startup_init
        start_development
        ;;
    *)
        exec "$@"
        ;;
esac
