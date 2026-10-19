"""Managed alert rules/history and permission-scoped personal subscriptions."""

from django.db import transaction
from django.utils import timezone
from djangorestframework_camel_case.parser import CamelCaseJSONParser
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.permissions import HasRequiredFeature
from core.paginations import APIPagination
from lens.alert_serializers import AlertEventSerializer, AlertRuleSerializer, PersonalAlertSubscriptionSerializer
from lens.alerts import SUBJECT_EVENTS, personal_subscription_allowed
from lens.models import AlertEvent, AlertRule, AlertSubscription

from .base import BaseAuthenticatedViewSet


class AlertPagination(APIPagination):
    """Bound collection sizes for rules, occurrences and personal subscriptions."""

    max_page_size = 100


class AlertRuleViewSet(BaseAuthenticatedViewSet):
    """Manage shared rules and their independently configured destinations."""

    permission_classes = [HasRequiredFeature]
    required_feature = "admin_console"
    queryset = (
        AlertRule.objects.select_related("created_by")
        .prefetch_related("assistants", "datasources", "lensnodes", "subscriptions__users", "subscriptions__groups")
        .order_by("-created_at")
    )
    serializer_class = AlertRuleSerializer
    pagination_class = AlertPagination

    def perform_create(self, serializer):
        """Record the managing operator."""

        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=["get"])
    def catalog(self, request):
        """Publish supported event combinations without a separate client registry."""

        return Response({key: sorted(values) for key, values in SUBJECT_EVENTS.items()})

    @action(detail=False, methods=["get"])
    def channels(self, request):
        """Expose paginated channel choices without staff-only configuration secrets."""

        from agentcore_notifier.adapters.django.models import NotificationChannel

        channels = NotificationChannel.objects.filter(
            is_active=True, channel_type__in=["email", "webhook", "feishu", "wecom", "dingtalk"]
        ).order_by("created_at", "uuid")
        page = self.paginate_queryset(channels)
        return self.get_paginated_response(
            [
                {
                    "uuid": str(channel.uuid),
                    "name": channel.name,
                    "channel_type": channel.channel_type,
                    "is_active": True,
                }
                for channel in page
            ]
        )


class AlertEventViewSet(viewsets.ReadOnlyModelViewSet):
    """Inspect occurrences and per-destination provider outcomes."""

    permission_classes = [HasRequiredFeature]
    required_feature = "admin_console"
    queryset = AlertEvent.objects.select_related("rule", "run").prefetch_related("deliveries__diagnosis")
    serializer_class = AlertEventSerializer
    pagination_class = AlertPagination
    lookup_field = "uuid"

    def get_queryset(self):
        """Filter by subject, event, rule, operation and summary status."""

        queryset = super().get_queryset()
        for name, field in (
            ("event_type", "event_type"),
            ("rule_uuid", "rule__uuid"),
            ("run_uuid", "run__uuid"),
            ("status", "status"),
            ("subject", "subject"),
            ("source_uuid", "source_uuid"),
        ):
            if self.request.query_params.get(name):
                queryset = queryset.filter(**{field: self.request.query_params[name]})
        return queryset

    @action(detail=True, methods=["post"], url_path="retry")
    @transaction.atomic
    def retry(self, request, uuid=None):
        """Retry failed destinations only; successful sends cannot be replayed."""

        event = self.get_object()
        event = AlertEvent.objects.select_for_update().get(pk=event.pk)
        count = event.deliveries.filter(status="failed").update(
            status="pending", attempts=0, next_attempt_at=timezone.now(), error_message=""
        )
        if count:
            from lens.alerts import _wake_dispatcher

            AlertEvent.objects.filter(pk=event.pk).update(status="pending")
            transaction.on_commit(_wake_dispatcher)
        return Response({"retried": count})


class PersonalAlertSubscriptionViewSet(BaseAuthenticatedViewSet):
    """Manage only the current user's published Q&A subscriptions."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PersonalAlertSubscriptionSerializer
    pagination_class = AlertPagination
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        """Never expose another subscriber's identity, channel or destination."""

        return (
            AlertSubscription.objects.filter(user=self.request.user, personal=True)
            .select_related("rule")
            .order_by("-created_at")
        )

    @action(detail=False, methods=["get"])
    def rules(self, request):
        """List published rules visible to this user without private resource scopes."""

        candidates = AlertRule.objects.filter(
            enabled=True, subject__in=["run", "message"], personal_subscription_enabled=True
        ).prefetch_related("assistants")
        candidates = [
            rule
            for rule in candidates
            if personal_subscription_allowed(AlertSubscription(rule=rule, user=request.user, personal=True))
        ]
        page = self.paginate_queryset(candidates)
        rows = [
            {
                "uuid": str(rule.uuid),
                "name": rule.name,
                "events": rule.events,
                "evaluation_mode": rule.evaluation_mode,
                "subject": rule.subject,
                "topic": rule.topic,
            }
            for rule in page
        ]
        return self.get_paginated_response(rows)


class MessagePublicationParser(CamelCaseJSONParser):
    """Preserve BCP 47 locale keys while accepting camelCase publication fields."""

    json_underscoreize = {
        **CamelCaseJSONParser.json_underscoreize,
        "ignore_fields": tuple(CamelCaseJSONParser.json_underscoreize.get("ignore_fields") or ()) + ("translations",),
    }


class MessagePublicationViewSet(viewsets.GenericViewSet):
    """Publish immutable topic messages and inspect every publication, even unmatched ones."""

    permission_classes = [HasRequiredFeature]
    parser_classes = [MessagePublicationParser]
    required_feature = "admin_console"
    pagination_class = AlertPagination
    lookup_field = "uuid"
    http_method_names = ["get", "post", "head", "options"]

    def get_serializer_class(self):
        """Describe publication input and record output for API schema generation."""

        from lens.alert_serializers import MessagePublicationSerializer, MessageRecordSerializer

        return MessagePublicationSerializer if self.action == "create" else MessageRecordSerializer

    def get_queryset(self):
        """Return exact topic matches without exposing business alert snapshots."""

        from lens.models import AlertOutbox

        queryset = AlertOutbox.objects.filter(subject="message").order_by("-created_at")
        topic = self.request.query_params.get("topic")
        return queryset.filter(payload__topic=topic) if topic else queryset

    def list(self, request):
        """Paginate immutable publication records."""

        from lens.alert_serializers import MessageRecordSerializer

        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(MessageRecordSerializer(page, many=True).data)

    def retrieve(self, request, uuid=None):
        """Inspect one recorded message by its public UUID."""

        from lens.alert_serializers import MessageRecordSerializer

        return Response(MessageRecordSerializer(self.get_object()).data)

    def create(self, request):
        """Return the original record for retries and reject conflicting key reuse."""

        from lens.alert_serializers import MessagePublicationSerializer, MessageRecordSerializer
        from lens.messages import PublicationConflict, publish_message

        serializer = MessagePublicationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            publication, created = publish_message(**serializer.validated_data, publisher=request.user)
        except PublicationConflict as error:
            return Response({"detail": str(error)}, status=409)
        return Response(
            {**MessageRecordSerializer(publication).data, "deduplicated": not created}, status=201 if created else 200
        )
