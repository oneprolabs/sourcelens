"""Admin access-subject detail views for users and groups."""

from django.contrib.auth.models import Group, User
from django.db.models import Count, Max, Q
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access import get_group_display_name
from accounts.delegation import is_delegated_request
from accounts.permissions import HasRequiredFeature
from lens.assistant_management import assistant_configuration_scope
from lens.models import Assistant, AssistantAccess, Run, Session


def _subject_tenant_id(request):
    """Require tenant membership authority before accessing any subject identity."""

    allowed, tenant_id = assistant_configuration_scope(request)
    if not allowed or is_delegated_request():
        raise PermissionDenied("Member and group access details are managed by the tenant.")
    return tenant_id


def _safe_int(value, default, *, maximum=100):
    """Return a bounded positive integer."""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, 1), maximum)


def _assistant_rows(queryset, *, access_sources):
    """Serialize annotated assistants with their access sources."""

    rows = []
    for assistant in queryset:
        rows.append(
            {
                "uuid": str(assistant.uuid),
                "name": assistant.name,
                "slug": assistant.slug,
                "visibility": assistant.visibility,
                "access_sources": access_sources.get(assistant.pk, []),
                "conversations": assistant.conversation_count,
                "qa_records": assistant.qa_count,
                "last_used_at": (
                    assistant.last_used_at.isoformat()
                    if assistant.last_used_at
                    else None
                ),
            }
        )
    return rows


class AdminUserAccessDetailView(APIView):
    """Return one user's identity, activity, and assistant access."""

    permission_classes = [HasRequiredFeature]
    required_feature = "admin_console"

    def get(self, request, user_id):
        """Return aggregated access and activity for a user."""

        tenant_id = _subject_tenant_id(request)
        try:
            user = User.objects.filter(membership__tenant_id=tenant_id).get(pk=user_id)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        groups = user.groups.filter(tenant_group__tenant_id=tenant_id)
        group_ids = list(groups.values_list("id", flat=True))
        sessions = Session.objects.filter(user=user, tenant_id=tenant_id, assistant__tenant_id=tenant_id)
        session_filter = Q(session__user=user, session__tenant_id=tenant_id)
        run_filter = session_filter & Q(session__run__tenant_id=tenant_id)
        assistant_filter = (
            Q(visibility=Assistant.Visibility.PUBLIC)
            | Q(access_grants__user=user)
            | Q(access_grants__group_id__in=group_ids)
            | Q(session__in=sessions)
        )
        if tenant_id is not None:
            assistant_filter |= Q(visibility=Assistant.Visibility.TENANT)
        assistants = (
            Assistant.objects.filter(assistant_filter, tenant_id=tenant_id)
            .annotate(
                conversation_count=Count(
                    "session",
                    filter=session_filter,
                    distinct=True,
                ),
                qa_count=Count(
                    "session__run",
                    filter=run_filter,
                    distinct=True,
                ),
                last_used_at=Max(
                    "session__run__created_at",
                    filter=run_filter,
                ),
            )
            .distinct()
            .order_by("name", "id")
        )
        assistant_ids = list(assistants.values_list("id", flat=True))
        sources = {assistant_id: [] for assistant_id in assistant_ids}
        for assistant_id in assistant_ids:
            assistant = next(
                item for item in assistants if item.pk == assistant_id
            )
            if assistant.visibility == Assistant.Visibility.PUBLIC:
                sources[assistant_id].append("public")
            elif assistant.visibility == Assistant.Visibility.TENANT:
                sources[assistant_id].append("tenant")
        grants = AssistantAccess.objects.filter(
            assistant_id__in=assistant_ids,
        ).filter(Q(user=user) | Q(group_id__in=group_ids))
        for grant in grants:
            source = "direct" if grant.user_id else "group"
            if source not in sources[grant.assistant_id]:
                sources[grant.assistant_id].append(source)
        used_ids = set(
            sessions.values_list(
                "assistant_id",
                flat=True,
            )
        )
        for assistant_id in used_ids:
            if not sources.get(assistant_id):
                sources[assistant_id] = ["history"]

        conversations = sessions.count()
        runs = Run.objects.filter(session__in=sessions, tenant_id=tenant_id)
        last_active_at = runs.aggregate(value=Max("created_at"))["value"]
        return Response(
            {
                "subject": {
                    "id": user.pk,
                    "username": user.username,
                    "email": user.email,
                    "is_active": user.is_active,
                    "is_staff": user.is_staff,
                    "groups": [
                        {"id": group.pk, "name": get_group_display_name(group)} for group in groups.order_by("name")
                    ],
                },
                "stats": {
                    "assigned_assistants": len(assistant_ids),
                    "conversations": conversations,
                    "qa_records": runs.count(),
                    "last_active_at": (
                        last_active_at.isoformat() if last_active_at else None
                    ),
                },
                "assistants": _assistant_rows(
                    assistants,
                    access_sources=sources,
                ),
            }
        )


class AdminGroupAccessDetailView(APIView):
    """Return one group's members, roles, and assistant assignments."""

    permission_classes = [HasRequiredFeature]
    required_feature = "admin_console"

    def get(self, request, group_id):
        """Return group detail with paginated searchable members."""

        tenant_id = _subject_tenant_id(request)
        try:
            group = (
                Group.objects.filter(tenant_group__tenant_id=tenant_id)
                .prefetch_related("platform_roles")
                .get(pk=group_id)
            )
        except Group.DoesNotExist:
            return Response(
                {"detail": "Group not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        page = _safe_int(request.query_params.get("page"), 1)
        page_size = _safe_int(request.query_params.get("page_size"), 20)
        search = (request.query_params.get("search") or "").strip()
        all_members = group.user_set.filter(membership__tenant_id=tenant_id)
        members = all_members.order_by("username", "id")
        if search:
            members = members.filter(
                Q(username__icontains=search) | Q(email__icontains=search)
            )
        member_count = members.count()
        start = (page - 1) * page_size
        member_rows = [
            {
                "id": user.pk,
                "username": user.username,
                "email": user.email,
                "is_active": user.is_active,
            }
            for user in members[start : start + page_size]
        ]

        session_filter = Q(session__user__in=all_members, session__tenant_id=tenant_id)
        run_filter = session_filter & Q(session__run__tenant_id=tenant_id)
        assistants = (
            Assistant.objects.filter(access_grants__group=group, tenant_id=tenant_id)
            .annotate(
                conversation_count=Count(
                    "session",
                    filter=session_filter,
                    distinct=True,
                ),
                qa_count=Count(
                    "session__run",
                    filter=run_filter,
                    distinct=True,
                ),
                last_used_at=Max(
                    "session__run__created_at",
                    filter=run_filter,
                ),
            )
            .distinct()
            .order_by("name", "id")
        )
        source_map = {assistant.pk: ["group"] for assistant in assistants}
        roles = list(group.platform_roles.filter(is_active=True))
        permission_count = group.permissions.count()
        return Response(
            {
                "subject": {
                    "id": group.pk,
                    "name": get_group_display_name(group),
                    "roles": [
                        {"id": role.pk, "name": role.name}
                        for role in roles
                    ],
                    "permission_count": permission_count,
                },
                "stats": {
                    "members": all_members.count(),
                    "assigned_assistants": len(source_map),
                    "roles": len(roles),
                    "permissions": permission_count,
                },
                "members": {
                    "count": member_count,
                    "page": page,
                    "page_size": page_size,
                    "results": member_rows,
                },
                "assistants": _assistant_rows(
                    assistants,
                    access_sources=source_map,
                ),
            }
        )
