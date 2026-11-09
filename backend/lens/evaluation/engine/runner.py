"""Evaluation node selection and capacity helpers."""

from lens.models import GlobalSetting, LensNode, Run

_ACTIVE_RUN_STATUSES = (
    Run.Status.QUEUED,
    Run.Status.RUNNING,
    Run.Status.STREAMING,
)


def evaluation_execution_pool():
    """Return the explicitly configured execution pool, defaulting to shared nodes."""

    value = GlobalSetting.objects.filter(key="evaluation.execution_pool").values_list("value", flat=True).first()
    return LensNode.Pool.DEFAULT if value is None else value


def evaluation_node_capacity(node):
    """Return the remaining evaluation slots on one LensNode.

    The quota bounds evaluation runs only, regardless of the node pool, so a
    fallback on a shared default node still cannot flood production traffic.
    """

    if node is None:
        return 0
    active = Run.objects.filter(
        lensnode=node,
        source=Run.Source.EVALUATION,
        status__in=_ACTIVE_RUN_STATUSES,
    ).count()
    return max(int(node.evaluation_concurrency_limit or 0) - active, 0)


def evaluation_candidate_nodes(assistant=None):
    """Return online candidates from the configured pool without cross-pool fallback."""

    pool = evaluation_execution_pool()
    online = LensNode.objects.filter(
        pool=pool,
        status=LensNode.Status.ONLINE,
        enrollment_status=LensNode.EnrollmentStatus.APPROVED,
        token_revoked=False,
    )
    if pool == LensNode.Pool.DEFAULT and assistant is not None and assistant.lensnode_id:
        bound = online.filter(pk=assistant.lensnode_id).first()
        return [bound] if bound is not None else []
    if assistant is not None and pool == LensNode.Pool.DEFAULT:
        from lens.services import _compatible_execution_lensnodes

        return [
            node for node in _compatible_execution_lensnodes(assistant, run_source=Run.Source.USER)
            if node.pool == pool
        ]
    if assistant is not None:
        from lens.services import _bound_datasource_lensnode_ids

        datasource_nodes = _bound_datasource_lensnode_ids(assistant)
        if datasource_nodes is not None:
            online = online.filter(pk__in=datasource_nodes)
    nodes = list(online)
    if assistant is not None and assistant.lensnode_id:
        nodes.sort(key=lambda node: node.pk != assistant.lensnode_id)
    return nodes


def evaluation_nodes_with_capacity():
    """Return online nodes that still have a free evaluation slot."""

    return [
        node
        for node in evaluation_candidate_nodes()
        if evaluation_node_capacity(node) > 0
    ]


def select_evaluation_lensnode():
    """Select the least-loaded candidate node or return ``None`` for queueing."""

    nodes = evaluation_nodes_with_capacity()
    if not nodes:
        return None
    return sorted(
        nodes,
        key=lambda node: (-evaluation_node_capacity(node), node.created_at),
    )[0]


def destination_nodes_with_capacity(assistant):
    """Return candidate nodes with capacity, drained and sorted for dispatch."""

    available = [
        node
        for node in evaluation_candidate_nodes(assistant)
        if evaluation_node_capacity(node) > 0
    ]
    return sorted(
        available,
        key=lambda node: (
            0 if node.pool == LensNode.Pool.EVALUATION else 1,
            -evaluation_node_capacity(node),
            node.created_at,
        ),
    )
