"""Register recovery for persisted account notification outboxes."""


def register_periodic_tasks():
    """Create the recovery schedule while preserving operator modifications."""
    from core.periodic_registry import TASK_REGISTRY

    TASK_REGISTRY.add(
        name="accounts-dispatch-pending-api-key-notices",
        task="accounts.tasks.dispatch_pending_api_key_notices_task",
        schedule=60,
        queue="backend",
    )
