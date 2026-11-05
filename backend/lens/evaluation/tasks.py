"""Celery entrypoints for offline Agent Evaluation batches."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="lens.evaluation_start", queue="lens")
def start_evaluation_task(evaluation_run_uuid):
    """Create case Runs and dispatch the first wave of a batch."""

    from .engine.batch import start_evaluation_run

    evaluation_run = start_evaluation_run(evaluation_run_uuid)
    return str(evaluation_run.uuid)


@shared_task(name="lens.evaluation_case_finalize", queue="lens")
def finalize_evaluation_case_task(run_uuid):
    """Score one finished case Run and advance its batch."""

    from .engine.batch import finalize_case_result

    result = finalize_case_result(run_uuid)
    return str(result.uuid) if result is not None else ""


@shared_task(name="lens.evaluation_reconcile", queue="lens")
def reconcile_evaluation_runs_task():
    """Advance queued and stale batches without a user request."""

    from .engine.batch import reconcile_evaluation_runs

    return reconcile_evaluation_runs()


def schedule_evaluation_case_finalize(run_uuid):
    """Enqueue case scoring after the terminal Run transaction commits."""

    try:
        finalize_evaluation_case_task.delay(str(run_uuid))
    except Exception:
        logger.exception(
            "evaluation: failed to enqueue finalize for run %s",
            run_uuid,
        )
