"""SourceLens owns every notifier route and its access boundary."""

from django.urls import path

from core.views.notification_records import (
    NotificationConfigView,
    NotificationRecordDetailView,
    NotificationRecordListView,
    NotificationRegistrationPollView,
    NotificationRegistrationStartView,
    NotificationSilenceRulesView,
    NotificationStatsView,
    NotificationUsersView,
)
from core.views.notification_targets import (
    TargetBulkDeleteView,
    TargetDetailView,
    TargetListView,
    TargetTestView,
    TargetValidateView,
)

urlpatterns = [
    path("channels/", TargetListView.as_view()),
    path("channels/bulk-delete/", TargetBulkDeleteView.as_view()),
    path("channels/validate/", TargetValidateView.as_view()),
    path("channels/<uuid:uuid>/", TargetDetailView.as_view()),
    path("channels/<uuid:uuid>/test/", TargetTestView.as_view()),
    path("channels/feishu-app/register/start/", NotificationRegistrationStartView.as_view()),
    path("channels/feishu-app/register/poll/", NotificationRegistrationPollView.as_view()),
    path("notification-records/", NotificationRecordListView.as_view()),
    path("notification-records/<uuid:uuid>/", NotificationRecordDetailView.as_view()),
    path("notification-stats/", NotificationStatsView.as_view()),
    path("users/", NotificationUsersView.as_view()),
    path("global/", NotificationConfigView.as_view()),
    path("silence-rules/", NotificationSilenceRulesView.as_view()),
]
