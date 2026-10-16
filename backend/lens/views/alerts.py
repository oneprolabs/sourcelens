"""Admin alert rule CRUD and triggered alert event views."""

from rest_framework import viewsets

from accounts.permissions import HasRequiredFeature
from core.paginations import APIPagination
from lens.models import AlertEvent, AlertRule
from lens.serializers import AlertEventSerializer, AlertRuleSerializer
from .base import BaseAuthenticatedViewSet


class AlertPagination(APIPagination):
    """Bounded pagination for alert collections."""

    max_page_size = 100


class AlertRuleViewSet(BaseAuthenticatedViewSet):
    """Manage alert rules that notify on terminal QA Run events."""

    permission_classes = [HasRequiredFeature]
    required_feature = "admin_console"
    queryset = AlertRule.objects.select_related("created_by").prefetch_related(
        "assistants"
    )
    serializer_class = AlertRuleSerializer
    pagination_class = AlertPagination

    def perform_create(self, serializer):
        """Stamp the creating operator on new rules."""

        serializer.save(created_by=self.request.user)


class AlertEventViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only list of triggered alert events."""

    permission_classes = [HasRequiredFeature]
    required_feature = "admin_console"
    queryset = AlertEvent.objects.select_related(
        "rule",
        "run",
        "run__session",
        "run__session__assistant",
        "run__session__user",
    )
    serializer_class = AlertEventSerializer
    pagination_class = AlertPagination
    lookup_field = "uuid"

    def get_queryset(self):
        """Apply optional event/rule/run/status filters."""

        queryset = super().get_queryset()
        params = self.request.query_params
        event_type = params.get("event_type")
        if event_type:
            queryset = queryset.filter(event_type=event_type)
        rule_uuid = params.get("rule_uuid")
        if rule_uuid:
            queryset = queryset.filter(rule__uuid=rule_uuid)
        run_uuid = params.get("run_uuid")
        if run_uuid:
            queryset = queryset.filter(run__uuid=run_uuid)
        status = params.get("status")
        if status:
            queryset = queryset.filter(status=status)
        return queryset
