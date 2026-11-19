"""Own all model administration URLs so vendor endpoints cannot bypass scope."""

from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.audit import record_audit
from accounts.permissions import HasPlatformSuperAdmin
from core.services.model_config import MeteringPolicySerializer, effective_config, update_config
from core.views.model_usage import ModelStatsView, ModelUsageView, ModelUsersView
from core.views.models import (
    ModelBulkView,
    ModelCatalogView,
    ModelDecisionTestView,
    ModelDetailView,
    ModelListView,
    ModelProvidersView,
    ModelTestCallView,
    ModelTestView,
    RetiredUserModelsView,
)


class PlatformMeteringConfigView(APIView):
    """Retention and aggregation schedules remain platform operations."""

    permission_classes = [HasPlatformSuperAdmin]

    def get(self, request):
        """Read effective global retention and schedules."""

        return Response(effective_config())

    def patch(self, request):
        """Persist validated scheduler and policy changes together."""

        serializer = MeteringPolicySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = update_config(serializer.validated_data)
        record_audit(
            action="metering_config.update", request=request, actor=request.user, resource_type="metering_config"
        )
        return Response(data)


urlpatterns = [
    path("users/", ModelUsersView.as_view()),
    path("token-stats/", ModelStatsView.as_view()),
    path("llm-usage/", ModelUsageView.as_view()),
    path("metering-config/", PlatformMeteringConfigView.as_view()),
    path("llm-config/bulk/", ModelBulkView.as_view()),
    path("llm-config/all/", ModelListView.as_view()),
    path("llm-config/", ModelListView.as_view()),
    path("llm-config/providers/", ModelProvidersView.as_view()),
    path("llm-config/models/", ModelCatalogView.as_view()),
    path("llm-config/test/", ModelTestView.as_view()),
    path("llm-config/test-call/", ModelTestCallView.as_view()),
    path("llm-config/decision-test", ModelDecisionTestView.as_view()),
    path("llm-config/users/", RetiredUserModelsView.as_view()),
    path("llm-config/users/<int:user_id>/", RetiredUserModelsView.as_view()),
    path("llm-config/<uuid:config_ref>/", ModelDetailView.as_view()),
    path("llm-config/<int:config_ref>/", ModelDetailView.as_view()),
]
