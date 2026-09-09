from datetime import timedelta

from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path, include
from django.utils import timezone

from accounts.views import OAuthCallbackRedirectView
from core.views.admin_bulk import (
    LLMConfigBulkView,
    NotificationChannelBulkDeleteView,
)
from .swagger import schema_view, swagger_view, redoc_view


def celery_health(request):
    """Report required queue consumers, broker depth, and beat liveness."""

    del request
    from core.celery import app
    from django_celery_beat.models import PeriodicTask

    required = set(getattr(settings, "CELERY_REQUIRED_QUEUES", ()))
    active_queues = app.control.inspect(timeout=0.5).active_queues() or {}
    consumed = {
        queue["name"]
        for queues in active_queues.values()
        for queue in queues
        if queue.get("name")
    }
    missing = sorted(required - consumed)
    depths = {}
    broker_error = ""
    try:
        import redis

        client = redis.Redis.from_url(settings.CELERY_BROKER_URL)
        depths = {
            queue: int(client.llen(queue))
            for queue in sorted(required)
        }
    except Exception as exc:
        broker_error = type(exc).__name__
    threshold = int(
        getattr(settings, "CELERY_QUEUE_DEPTH_THRESHOLD", 1000)
    )
    overloaded = sorted(
        queue for queue, depth in depths.items() if depth > threshold
    )
    latest_beat = (
        PeriodicTask.objects.filter(enabled=True)
        .exclude(last_run_at__isnull=True)
        .order_by("-last_run_at")
        .values_list("last_run_at", flat=True)
        .first()
    )
    beat_threshold = int(
        getattr(settings, "CELERY_BEAT_LIVENESS_SECONDS", 300)
    )
    beat_stale = latest_beat is None or latest_beat < (
        timezone.now() - timedelta(seconds=beat_threshold)
    )
    healthy = (
        not missing
        and not overloaded
        and not broker_error
        and not beat_stale
    )
    payload = {
        "health": "OK" if healthy else "DEGRADED",
        "required_queues": sorted(required),
        "consumed_queues": sorted(consumed),
        "missing_consumers": missing,
        "queue_depth": depths,
        "queue_depth_threshold": threshold,
        "overloaded_queues": overloaded,
        "beat_last_run_at": latest_beat,
        "beat_liveness_seconds": beat_threshold,
        "beat_stale": beat_stale,
    }
    if broker_error:
        payload["broker_error"] = broker_error
    return JsonResponse(payload, status=200 if healthy else 503)

# Define project URL routing configuration
urlpatterns = [
    # Health check endpoint
    # Used by Docker/Kubernetes for container health monitoring
    # Returns a simple 'OK' response to indicate the application is running
    path('health', lambda _: JsonResponse({'health': 'OK'}, status=200)),
    path('health/celery', celery_health, name='celery-health'),

    # API Schema endpoint
    # Provides the OpenAPI schema in JSON format
    path('api/schema', schema_view, name='schema'),

    # Swagger UI documentation route
    # Displays the API documentation using Swagger UI.
    path('swagger', swagger_view, name='swagger-ui'),

    # ReDoc documentation route
    # Displays the API documentation using ReDoc.
    path('redoc', redoc_view, name='redoc'),

    # Django admin site route
    # Provides access to the Django Admin interface for managing models and
    # data.
    path('admin', admin.site.urls),

    # Authentication routes
    # Includes authentication endpoints provided by custom accounts.urls
    path('', include('accounts.urls')),

    # Task management routes (agentcore-task)
    path('api/v1/tasks/', include('agentcore_task.adapters.django.urls')),

    # Lens MVP API
    path('api/lens/', include('lens.urls')),

    # SourceLens atomic adapters for agentcore admin resources
    path(
        'api/v1/admin/notifications/channels/bulk-delete/',
        NotificationChannelBulkDeleteView.as_view(),
        name='notification_channels_bulk_delete',
    ),
    path(
        'api/v1/admin/llm-config/bulk/',
        LLMConfigBulkView.as_view(),
        name='llm_configs_bulk',
    ),

    # Notifier admin API (agentcore-notifier: must be before admin/ to match)
    path(
        'api/v1/admin/notifications/',
        include('agentcore_notifier.adapters.django.urls')
    ),
    # LLM metering admin API (agentcore-metering package)
    path('api/v1/admin/', include('agentcore_metering.adapters.django.urls')),

    # Custom OAuth callback redirect with JWT tokens
    # This must come BEFORE allauth.urls to intercept the redirect
    path(
        'accounts/oauth/callback/',
        OAuthCallbackRedirectView.as_view(),
        name='oauth_callback_redirect'
    ),

    # Django-allauth OAuth callback routes
    # Required for OAuth provider callbacks (e.g., Google)
    # Even with Headless API, these endpoints are needed for OAuth handshake
    path('accounts/', include('allauth.urls')),

    # Django-allauth Headless API endpoints
    # REST API for frontend-backend separation (allauth >= 65.0.0)
    # Provides authentication APIs without Django form views
    path('_allauth/', include('allauth.headless.urls')),
]
