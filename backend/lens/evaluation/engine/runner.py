"""Evaluation execution helpers shared by future batch task entrypoints."""

from django.db.models import Count, F, Q

from lens.models import LensNode, Run

_ACTIVE_RUN_STATUSES = (
    Run.Status.QUEUED,
    Run.Status.RUNNING,
    Run.Status.STREAMING,
)


def evaluation_node_capacity(node):
    """Return the remaining evaluation slots on a dedicated LensNode."""

    if node is None or node.pool != LensNode.Pool.EVALUATION:
        return 0
    active = Run.objects.filter(
        lensnode=node,
        source=Run.Source.EVALUATION,
        status__in=_ACTIVE_RUN_STATUSES,
    ).count()
    return max(int(node.evaluation_concurrency_limit) - active, 0)


def evaluation_nodes_with_capacity():
    """Return online, approved evaluation nodes with a positive free quota."""

    nodes = LensNode.objects.filter(
        pool=LensNode.Pool.EVALUATION,
        status=LensNode.Status.ONLINE,
        enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        token_revoked=False,
    ).annotate(
        active_evaluation_runs=Count(
            "runs",
            filter=Q(
                runs__source=Run.Source.EVALUATION,
                runs__status__in=_ACTIVE_RUN_STATUSES,
            ),
        )
    )
    return nodes.filter(active_evaluation_runs__lt=F("evaluation_concurrency_limit"))


def select_evaluation_lensnode():
    """Select the least-loaded dedicated node or return ``None`` for queueing."""

    node = evaluation_nodes_with_capacity().order_by("active_evaluation_runs", "created_at").first()
    return node
