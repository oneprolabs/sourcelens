"""Own every task route so upstream actions cannot bypass platform access."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.views.task_config import PlatformTaskConfigView
from core.views.tasks import TaskExecutionListViewSet

router = DefaultRouter()
router.register("executions", TaskExecutionListViewSet, basename="task-execution")

urlpatterns = [path("config/", PlatformTaskConfigView.as_view()), path("", include(router.urls))]
