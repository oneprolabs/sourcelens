"""Management portal views for users, groups, and role visibility."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Count, Prefetch, Q
from django.http import Http404
from rest_framework.status import HTTP_200_OK
from rest_framework.response import Response
from rest_framework.status import (
    HTTP_201_CREATED,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
)
from rest_framework.views import APIView

from accounts.access import (
    get_access_profile,
    get_effective_roles,
    normalize_feature_keys,
    normalize_platform_key,
    serialize_feature_options,
    serialize_platform_options,
)
from accounts.models import (
    DEFAULT_ANSWER_LANGUAGE,
    Profile,
    Role,
    normalize_answer_language,
)
from accounts.permissions import HasRequiredFeature
from accounts.services.management_bulk import (
    BulkMutationError,
    delete_groups,
    normalize_bulk_ids,
    set_roles_active,
    set_users_active,
)

User = get_user_model()


def _safe_int(value, default, min_value=1, max_value=100):
    """Parse value to int and clamp to [min_value, max_value]; else default."""
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    if out < min_value:
        return min_value
    if out > max_value:
        return max_value
    return out


def _paginated_payload(items, total, page, page_size):
    """Build paginated response dict (count, page, page_size, results)."""
    return {
        'count': total,
        'page': page,
        'page_size': page_size,
        'results': items,
    }


def _user_payload(u):
    """Build serializable user dict with profile, display_name, and groups."""
    try:
        profile = u.profile
    except Profile.DoesNotExist:
        profile = None
    language = profile.language if profile else DEFAULT_ANSWER_LANGUAGE
    timezone = profile.timezone if profile else 'Asia/Shanghai'
    preferred_platform = (
        normalize_platform_key(profile.preferred_platform)
        if profile else ''
    )
    display_name = (
        f'{u.first_name or ""} {u.last_name or ""}'.strip()
        if (getattr(u, 'first_name', None) or getattr(u, 'last_name', None))
        else (
            getattr(u, 'username', None)
            or getattr(u, 'email', None)
            or str(u.pk)
        )
    )
    ordered_groups = getattr(u, 'ordered_groups', None)
    if ordered_groups is None:
        ordered_groups = u.groups.all().order_by('name')
    groups = [{'id': g.pk, 'name': g.name} for g in ordered_groups]
    direct_roles = getattr(u, 'ordered_roles', None)
    if direct_roles is None:
        direct_roles = u.platform_roles.filter(is_active=True).order_by(
            'name',
            'id',
        )
    effective_roles = get_effective_roles(
        u,
        direct_roles=direct_roles,
        groups=ordered_groups,
    )
    return {
        'id': u.pk,
        'username': (
            getattr(u, 'username', None)
            or getattr(u, 'email', None)
            or str(u.pk)
        ),
        'email': getattr(u, 'email', None) or '',
        'first_name': getattr(u, 'first_name', None) or '',
        'last_name': getattr(u, 'last_name', None) or '',
        'display_name': display_name,
        'is_staff': getattr(u, 'is_staff', False),
        'is_active': getattr(u, 'is_active', True),
        'date_joined': (
            u.date_joined.isoformat()
            if getattr(u, 'date_joined', None)
            else None
        ),
        'language': language,
        'timezone': timezone,
        'preferred_platform': preferred_platform,
        'groups': groups,
        'roles': [_role_summary_payload(role) for role in direct_roles],
        'effective_roles': [
            _role_summary_payload(role)
            for role in effective_roles
        ],
        'access_profile': get_access_profile(
            u,
            direct_roles=direct_roles,
            groups=ordered_groups,
            effective_roles=effective_roles,
        ),
    }


def _user_selector_payload(user):
    """Build the lightweight user payload used by assignment selectors."""
    display_name = (
        f'{user.first_name or ""} {user.last_name or ""}'.strip()
        or user.username
        or user.email
        or str(user.pk)
    )
    return {
        'id': user.pk,
        'username': user.username or user.email or str(user.pk),
        'email': user.email or '',
        'first_name': user.first_name or '',
        'last_name': user.last_name or '',
        'display_name': display_name,
    }


class ManagementUserListView(APIView):
    """
    GET: List all users for management console (with profile and groups).
    POST: Create user (username, email, password, is_staff, group_ids, etc).
    Admin-only.
    """

    permission_classes = [HasRequiredFeature]
    required_feature = 'admin_console'

    def get(self, request):
        page = _safe_int(request.query_params.get('page'), default=1)
        page_size = _safe_int(
            request.query_params.get('page_size'),
            default=20,
            max_value=1000,
        )
        compact = str(
            request.query_params.get('compact') or ''
        ).lower() in {'1', 'true', 'yes'}
        if compact:
            qs = User.objects.only(
                'id',
                'username',
                'email',
                'first_name',
                'last_name',
            ).order_by('id')
        else:
            user_role_prefetch = Prefetch(
                'platform_roles',
                queryset=Role.objects.filter(is_active=True).order_by(
                    'name',
                    'id',
                ),
                to_attr='ordered_roles',
            )
            group_role_prefetch = Prefetch(
                'platform_roles',
                queryset=Role.objects.filter(is_active=True).order_by(
                    'name',
                    'id',
                ),
                to_attr='ordered_roles',
            )
            groups_prefetch = Prefetch(
                'groups',
                queryset=Group.objects.order_by('name').prefetch_related(
                    group_role_prefetch
                ),
                to_attr='ordered_groups',
            )
            qs = User.objects.select_related('profile').prefetch_related(
                groups_prefetch,
                user_role_prefetch,
            ).order_by('id')
        username = request.query_params.get('username')
        if username:
            qs = qs.filter(username=username)
        email = request.query_params.get('email')
        if email:
            qs = qs.filter(email=email)
        search = (request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(username__icontains=search)
                | Q(email__icontains=search)
            )
        total = qs.count()
        start = (page - 1) * page_size
        end = start + page_size
        payload_builder = _user_selector_payload if compact else _user_payload
        items = [payload_builder(u) for u in qs[start:end]]
        return Response(_paginated_payload(items, total, page, page_size))

    def post(self, request):
        username = (request.data.get('username') or '').strip()
        email = (request.data.get('email') or '').strip()
        password = request.data.get('password') or ''
        is_staff = bool(request.data.get('is_staff', False))
        group_ids = request.data.get('group_ids') or []
        role_ids = request.data.get('role_ids') or []
        if not isinstance(group_ids, list):
            group_ids = []
        if not isinstance(role_ids, list):
            role_ids = []
        language = normalize_answer_language(request.data.get('language'))
        timezone = (request.data.get('timezone') or '').strip() or 'Asia/Shanghai'
        preferred_platform = normalize_platform_key(
            request.data.get('preferred_platform')
        )

        if not username:
            return Response(
                {'detail': 'Username is required.', 'code': 'username_required'},
                status=HTTP_400_BAD_REQUEST
            )
        if not password:
            return Response(
                {'detail': 'Password is required.', 'code': 'password_required'},
                status=HTTP_400_BAD_REQUEST
            )
        if User.objects.filter(username=username).exists():
            return Response(
                {
                    'detail': 'A user with that username already exists.',
                    'code': 'username_taken',
                },
                status=HTTP_400_BAD_REQUEST
            )
        if email and User.objects.filter(email=email).exists():
            return Response(
                {
                    'detail': 'A user with that email already exists.',
                    'code': 'email_taken',
                },
                status=HTTP_400_BAD_REQUEST
            )

        user = User.objects.create_user(
            username=username,
            email=email or '',
            password=password,
        )
        if is_staff:
            user.is_staff = True
            user.save(update_fields=['is_staff'])
        if group_ids:
            valid_ids = list(
                Group.objects.filter(pk__in=group_ids).values_list(
                    'pk', flat=True
                )
            )
            if valid_ids:
                user.groups.set(valid_ids)
        if role_ids:
            valid_role_ids = list(
                Role.objects.filter(pk__in=role_ids).values_list('pk', flat=True)
            )
            if valid_role_ids:
                user.platform_roles.set(valid_role_ids)
        try:
            profile = user.profile
        except Profile.DoesNotExist:
            profile = None
        if profile is not None:
            profile.language = language
            profile.timezone = timezone
            profile.preferred_platform = preferred_platform
            profile.save(
                update_fields=['language', 'timezone', 'preferred_platform']
            )
        return Response(_user_payload(user), status=HTTP_201_CREATED)


class ManagementUserDetailView(APIView):
    """PATCH: Update user group and role bindings."""

    permission_classes = [HasRequiredFeature]
    required_feature = 'admin_console'

    def patch(self, request, user_id):
        user = _get_user_or_404(user_id)

        username = request.data.get('username')
        email = request.data.get('email')
        is_staff = request.data.get('is_staff')
        is_active = request.data.get('is_active')
        group_ids = request.data.get('group_ids')
        role_ids = request.data.get('role_ids')
        language = request.data.get('language')
        timezone = request.data.get('timezone')
        preferred_platform = request.data.get('preferred_platform')

        update_fields = []
        if username is not None:
            username = str(username).strip()
            if not username:
                return Response(
                    {
                        'detail': 'Username is required.',
                        'code': 'username_required',
                    },
                    status=HTTP_400_BAD_REQUEST,
                )
            if User.objects.exclude(pk=user.pk).filter(username=username).exists():
                return Response(
                    {
                        'detail': 'A user with that username already exists.',
                        'code': 'username_taken',
                    },
                    status=HTTP_400_BAD_REQUEST,
                )
            user.username = username
            update_fields.append('username')

        if email is not None:
            email = str(email).strip()
            if email and User.objects.exclude(pk=user.pk).filter(email=email).exists():
                return Response(
                    {
                        'detail': 'A user with that email already exists.',
                        'code': 'email_taken',
                    },
                    status=HTTP_400_BAD_REQUEST,
                )
            user.email = email
            update_fields.append('email')

        if is_staff is not None:
            user.is_staff = bool(is_staff)
            update_fields.append('is_staff')

        if is_active is not None:
            user.is_active = bool(is_active)
            update_fields.append('is_active')

        if update_fields:
            user.save(update_fields=update_fields)

        if group_ids is not None:
            if not isinstance(group_ids, list):
                group_ids = []
            valid_group_ids = list(
                Group.objects.filter(pk__in=group_ids).values_list('pk', flat=True)
            )
            user.groups.set(valid_group_ids)

        if role_ids is not None:
            if not isinstance(role_ids, list):
                role_ids = []
            valid_role_ids = list(
                Role.objects.filter(pk__in=role_ids).values_list('pk', flat=True)
            )
            user.platform_roles.set(valid_role_ids)

        profile, _ = Profile.objects.get_or_create(
            user=user,
            defaults={
                'language': DEFAULT_ANSWER_LANGUAGE,
                'timezone': 'Asia/Shanghai',
            },
        )
        profile_update_fields = []
        if language is not None:
            profile.language = normalize_answer_language(language)
            profile_update_fields.append('language')
        if timezone is not None:
            profile.timezone = str(timezone).strip() or 'Asia/Shanghai'
            profile_update_fields.append('timezone')
        if preferred_platform is not None:
            profile.preferred_platform = normalize_platform_key(
                preferred_platform
            )
            profile_update_fields.append('preferred_platform')
        if profile_update_fields:
            profile.save(update_fields=profile_update_fields)

        return Response(_user_payload(user), status=HTTP_200_OK)


class ManagementUserBulkView(APIView):
    """Atomically enable or disable a bounded user selection."""

    permission_classes = [HasRequiredFeature]
    required_feature = 'admin_console'

    def post(self, request):
        is_active = request.data.get('is_active')
        if not isinstance(is_active, bool):
            return Response(
                {'detail': 'is_active must be a boolean.'},
                status=HTTP_400_BAD_REQUEST,
            )
        try:
            user_ids = normalize_bulk_ids(
                request.data.get('user_ids'),
                'user_ids',
            )
            count = set_users_active(
                user_ids,
                is_active,
                request.user.pk,
            )
        except BulkMutationError as exc:
            return Response(
                {'detail': str(exc)},
                status=HTTP_400_BAD_REQUEST,
            )
        return Response({'count': count}, status=HTTP_200_OK)


def _group_payload(g):
    direct_roles = getattr(g, 'ordered_roles', None)
    if direct_roles is None:
        direct_roles = g.platform_roles.filter(is_active=True).order_by(
            'name',
            'id',
        )
    assistant_grant_count = getattr(g, 'assistant_grant_count', None)
    if assistant_grant_count is None:
        assistant_grants = getattr(g, 'assistant_grants', None)
        assistant_grant_count = (
            assistant_grants.count() if assistant_grants is not None else 0
        )
    return {
        'id': g.pk,
        'name': g.name,
        'user_count': getattr(g, 'user_count', g.user_set.count()),
        'permission_count': getattr(
            g,
            'permission_count',
            g.permissions.count(),
        ),
        'assistant_grant_count': assistant_grant_count,
        'roles': [_role_summary_payload(role) for role in direct_roles],
    }


class ManagementGroupListView(APIView):
    """
    GET: List all Django auth groups for management console.
    POST: Create a new group (name).
    Admin-only.
    """

    permission_classes = [HasRequiredFeature]
    required_feature = 'admin_console'

    def get(self, request):
        page = _safe_int(request.query_params.get('page'), default=1)
        page_size = _safe_int(
            request.query_params.get('page_size'),
            default=20,
            max_value=1000,
        )
        compact = str(
            request.query_params.get('compact') or ''
        ).lower() in {'1', 'true', 'yes'}
        if compact:
            qs = Group.objects.order_by('name')
        else:
            role_prefetch = Prefetch(
                'platform_roles',
                queryset=Role.objects.filter(is_active=True).order_by(
                    'name',
                    'id',
                ),
                to_attr='ordered_roles',
            )
            annotations = {
                'user_count': Count('user', distinct=True),
                'permission_count': Count('permissions', distinct=True),
            }
            if hasattr(Group, 'assistant_grants'):
                annotations['assistant_grant_count'] = Count(
                    'assistant_grants',
                    distinct=True,
                )
            qs = (
                Group.objects.annotate(**annotations)
                .prefetch_related(role_prefetch)
                .order_by('name')
            )
        search = (request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(name__icontains=search)
        total = qs.count()
        start = (page - 1) * page_size
        end = start + page_size
        if compact:
            items = [
                {'id': group.pk, 'name': group.name}
                for group in qs[start:end]
            ]
        else:
            items = [_group_payload(group) for group in qs[start:end]]
        return Response(_paginated_payload(items, total, page, page_size))

    def post(self, request):
        name = (request.data.get('name') or '').strip()
        if not name:
            return Response(
                {'detail': 'Group name is required.', 'code': 'name_required'},
                status=HTTP_400_BAD_REQUEST
            )
        if Group.objects.filter(name=name).exists():
            return Response(
                {
                    'detail': 'A group with that name already exists.',
                    'code': 'name_taken',
                },
                status=HTTP_400_BAD_REQUEST
            )
        group = Group.objects.create(name=name)
        user_ids = request.data.get('user_ids')
        if isinstance(user_ids, list) and user_ids:
            valid_user_ids = list(
                User.objects.filter(pk__in=user_ids).values_list(
                    'pk', flat=True
                )
            )
            group.user_set.set(valid_user_ids)
        return Response(_group_payload(group), status=HTTP_201_CREATED)


class ManagementGroupDetailView(APIView):
    """PATCH: Update group name and role bindings."""

    permission_classes = [HasRequiredFeature]
    required_feature = 'admin_console'

    def patch(self, request, group_id):
        group = _get_group_or_404(group_id)
        name = request.data.get('name')
        role_ids = request.data.get('role_ids')

        if name is not None:
            name = str(name).strip()
            if not name:
                return Response(
                    {'detail': 'Group name is required.', 'code': 'name_required'},
                    status=HTTP_400_BAD_REQUEST,
                )
            if Group.objects.exclude(pk=group.pk).filter(name=name).exists():
                return Response(
                    {
                        'detail': 'A group with that name already exists.',
                        'code': 'name_taken',
                    },
                    status=HTTP_400_BAD_REQUEST,
                )
            group.name = name
            group.save(update_fields=['name'])

        if role_ids is not None:
            if not isinstance(role_ids, list):
                role_ids = []
            valid_role_ids = list(
                Role.objects.filter(pk__in=role_ids).values_list('pk', flat=True)
            )
            group.platform_roles.set(valid_role_ids)

        user_ids = request.data.get('user_ids')
        if user_ids is not None:
            if not isinstance(user_ids, list):
                user_ids = []
            valid_user_ids = list(
                User.objects.filter(pk__in=user_ids).values_list(
                    'pk', flat=True
                )
            )
            group.user_set.set(valid_user_ids)

        return Response(_group_payload(group), status=HTTP_200_OK)

    def delete(self, request, group_id):
        """Delete a group.

        AssistantAccess grants made through this group cascade away, so
        members lose any access that depended on this group. Role and
        membership links are removed automatically.
        """

        group = _get_group_or_404(group_id)
        group.delete()
        return Response(status=HTTP_204_NO_CONTENT)


class ManagementGroupBulkDeleteView(APIView):
    """Atomically delete a bounded group selection."""

    permission_classes = [HasRequiredFeature]
    required_feature = 'admin_console'

    def post(self, request):
        try:
            group_ids = normalize_bulk_ids(
                request.data.get('group_ids'),
                'group_ids',
            )
            count = delete_groups(group_ids)
        except BulkMutationError as exc:
            return Response(
                {'detail': str(exc)},
                status=HTTP_400_BAD_REQUEST,
            )
        return Response({'count': count}, status=HTTP_200_OK)


def _role_summary_payload(role):
    """Serialize a role in compact form for nested payloads."""
    return {
        'id': role.pk,
        'name': role.name,
        'visible_features': normalize_feature_keys(role.visible_features),
        'preferred_platform': normalize_platform_key(role.preferred_platform),
        'is_active': role.is_active,
    }


def _role_payload(role):
    """Serialize a role for the management roles page."""
    payload = _role_summary_payload(role)
    payload.update(
        {
            'user_count': getattr(role, 'user_count', role.users.count()),
            'group_count': getattr(role, 'group_count', role.groups.count()),
        }
    )
    return payload


class ManagementRoleListView(APIView):
    """GET/POST role definitions used for visibility control."""

    permission_classes = [HasRequiredFeature]
    required_feature = 'admin_console'

    def get(self, request):
        page = _safe_int(request.query_params.get('page'), default=1)
        page_size = _safe_int(
            request.query_params.get('page_size'),
            default=20,
            max_value=1000,
        )
        qs = Role.objects.annotate(
            user_count=Count('users', distinct=True),
            group_count=Count('groups', distinct=True),
        ).order_by('name', 'id')
        total = qs.count()
        start = (page - 1) * page_size
        end = start + page_size
        items = [_role_payload(role) for role in qs[start:end]]
        payload = _paginated_payload(items, total, page, page_size)
        payload['feature_options'] = serialize_feature_options()
        payload['platform_options'] = serialize_platform_options()
        return Response(payload)

    def post(self, request):
        name = str(request.data.get('name') or '').strip()
        visible_features = normalize_feature_keys(
            request.data.get('visible_features')
        )
        preferred_platform = normalize_platform_key(
            request.data.get('preferred_platform')
        )
        is_active = bool(request.data.get('is_active', True))

        if not name:
            return Response(
                {'detail': 'Role name is required.', 'code': 'name_required'},
                status=HTTP_400_BAD_REQUEST,
            )
        if Role.objects.filter(name=name).exists():
            return Response(
                {
                    'detail': 'A role with that name already exists.',
                    'code': 'name_taken',
                },
                status=HTTP_400_BAD_REQUEST,
            )

        role = Role.objects.create(
            name=name,
            visible_features=visible_features,
            preferred_platform=preferred_platform,
            is_active=is_active,
        )
        return Response(_role_payload(role), status=HTTP_201_CREATED)


class ManagementRoleDetailView(APIView):
    """PATCH role definitions."""

    permission_classes = [HasRequiredFeature]
    required_feature = 'admin_console'

    def patch(self, request, role_id):
        role = _get_role_or_404(role_id)

        name = request.data.get('name')
        visible_features = request.data.get('visible_features')
        preferred_platform = request.data.get('preferred_platform')
        is_active = request.data.get('is_active')

        update_fields = []

        if name is not None:
            name = str(name).strip()
            if not name:
                return Response(
                    {'detail': 'Role name is required.', 'code': 'name_required'},
                    status=HTTP_400_BAD_REQUEST,
                )
            if Role.objects.exclude(pk=role.pk).filter(name=name).exists():
                return Response(
                    {
                        'detail': 'A role with that name already exists.',
                        'code': 'name_taken',
                    },
                    status=HTTP_400_BAD_REQUEST,
                )
            role.name = name
            update_fields.append('name')

        if visible_features is not None:
            role.visible_features = normalize_feature_keys(visible_features)
            update_fields.append('visible_features')

        if preferred_platform is not None:
            role.preferred_platform = normalize_platform_key(preferred_platform)
            update_fields.append('preferred_platform')

        if is_active is not None:
            role.is_active = bool(is_active)
            update_fields.append('is_active')

        if update_fields:
            role.save(update_fields=update_fields)

        return Response(_role_payload(role), status=HTTP_200_OK)


class ManagementRoleBulkView(APIView):
    """Atomically enable or disable a bounded role selection."""

    permission_classes = [HasRequiredFeature]
    required_feature = 'admin_console'

    def post(self, request):
        is_active = request.data.get('is_active')
        if not isinstance(is_active, bool):
            return Response(
                {'detail': 'is_active must be a boolean.'},
                status=HTTP_400_BAD_REQUEST,
            )
        try:
            role_ids = normalize_bulk_ids(
                request.data.get('role_ids'),
                'role_ids',
            )
            count = set_roles_active(role_ids, is_active)
        except BulkMutationError as exc:
            return Response(
                {'detail': str(exc)},
                status=HTTP_400_BAD_REQUEST,
            )
        return Response({'count': count}, status=HTTP_200_OK)


def _get_user_or_404(user_id):
    """Load a user or raise 404."""
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist as exc:
        raise Http404 from exc


def _get_group_or_404(group_id):
    """Load a group or raise 404."""
    try:
        return Group.objects.get(pk=group_id)
    except Group.DoesNotExist as exc:
        raise Http404 from exc


def _get_role_or_404(role_id):
    """Load a role or raise 404."""
    try:
        return Role.objects.get(pk=role_id)
    except Role.DoesNotExist as exc:
        raise Http404 from exc
