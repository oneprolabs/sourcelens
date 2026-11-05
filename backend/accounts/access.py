"""Access-control helpers for role-based platform visibility."""

from __future__ import annotations

from typing import Iterable

from django.contrib.auth.models import Group

FEATURE_DEFINITIONS = (
    {
        'key': 'workspace',
        'label': 'User Workspace',
        'default_path': '/dashboard',
    },
    {
        'key': 'admin_console',
        'label': 'Admin Console',
        'default_path': '/management/users',
    },
    {
        'key': 'run_evaluations',
        'label': 'Agent Evaluation',
        'default_path': '/management/lens/evaluation',
    },
)

FEATURE_KEYS = tuple(item['key'] for item in FEATURE_DEFINITIONS)
FEATURE_KEY_SET = set(FEATURE_KEYS)
FEATURE_ORDER = {key: index for index, key in enumerate(FEATURE_KEYS)}
FEATURE_DEFAULT_PATHS = {
    item['key']: item['default_path']
    for item in FEATURE_DEFINITIONS
}

PLATFORM_DEFINITIONS = FEATURE_DEFINITIONS
PLATFORM_KEYS = FEATURE_KEYS
PLATFORM_KEY_SET = FEATURE_KEY_SET
PLATFORM_ORDER = FEATURE_ORDER
PLATFORM_DEFAULT_PATHS = FEATURE_DEFAULT_PATHS

FEATURE_ALIASES = {
    'llm_console': 'admin_console',
    'task_management_console': 'admin_console',
    'notification_console': 'admin_console',
}

LEGACY_DEFAULT_FEATURES = (
    'workspace',
)


def normalize_feature_keys(values: Iterable[str] | None) -> list[str]:
    """Return a de-duplicated, ordered list of known feature keys."""
    if not values:
        return []

    normalized = []
    seen = set()
    for raw_value in values:
        value = FEATURE_ALIASES.get(
            str(raw_value or '').strip(),
            str(raw_value or '').strip(),
        )
        if not value or value not in FEATURE_KEY_SET or value in seen:
            continue
        normalized.append(value)
        seen.add(value)

    normalized.sort(key=lambda item: FEATURE_ORDER[item])
    return normalized


def normalize_platform_key(value: str | None) -> str:
    """Return a valid platform key or an empty string."""
    raw_platform_key = str(value or '').strip()
    platform_key = FEATURE_ALIASES.get(raw_platform_key, raw_platform_key)
    if platform_key in PLATFORM_KEY_SET:
        return platform_key
    return ''


def serialize_feature_options() -> list[dict[str, str]]:
    """Serialize feature definitions for API clients."""
    return [
        {
            'key': item['key'],
            'label': item['label'],
            'default_path': item['default_path'],
        }
        for item in FEATURE_DEFINITIONS
    ]


def serialize_platform_options() -> list[dict[str, str]]:
    """Serialize platform definitions for API clients."""
    return serialize_feature_options()


def serialize_platforms(platform_keys: Iterable[str]) -> list[dict[str, str]]:
    """Convert platform keys into API payloads."""
    serialized = []
    for platform_key in normalize_feature_keys(platform_keys):
        serialized.append(
            {
                'key': platform_key,
                'label': next(
                    item['label']
                    for item in PLATFORM_DEFINITIONS
                    if item['key'] == platform_key
                ),
                'default_path': PLATFORM_DEFAULT_PATHS[platform_key],
            }
        )
    return serialized


def _normalize_roles(roles) -> list:
    """Return active roles sorted by configured display order."""
    active_roles = [role for role in roles if getattr(role, 'is_active', True)]
    active_roles.sort(key=lambda role: (role.name.lower(), role.pk))
    return active_roles


def _collect_group_roles(groups: Iterable[Group]) -> list:
    """Collect active roles inherited from groups."""
    group_roles = []
    for group in groups:
        prefetched_roles = getattr(group, 'ordered_roles', None)
        if prefetched_roles is None:
            prefetched_roles = group.platform_roles.filter(
                is_active=True
            ).order_by('name', 'id')
        group_roles.extend(prefetched_roles)
    return _normalize_roles(group_roles)


def get_effective_roles(
    user,
    *,
    direct_roles=None,
    groups=None,
) -> list:
    """Return the union of direct user roles and inherited group roles."""
    resolved_direct_roles = direct_roles
    if resolved_direct_roles is None:
        resolved_direct_roles = user.platform_roles.filter(
            is_active=True
        ).order_by('name', 'id')

    resolved_groups = groups
    if resolved_groups is None:
        resolved_groups = user.groups.order_by('name').prefetch_related(
            'platform_roles'
        )

    unique_roles = {}
    for role in _normalize_roles(resolved_direct_roles):
        unique_roles[role.pk] = role
    for role in _collect_group_roles(resolved_groups):
        unique_roles.setdefault(role.pk, role)

    return sorted(
        unique_roles.values(),
        key=lambda role: (role.name.lower(), role.pk),
    )


def get_effective_feature_keys(
    user,
    *,
    effective_roles=None,
) -> list[str]:
    """Return visible feature keys for the given user."""
    # Staff users automatically get all features
    if getattr(user, 'is_staff', False):
        return list(FEATURE_KEYS)

    resolved_roles = effective_roles or get_effective_roles(user)
    feature_keys = []
    for role in resolved_roles:
        feature_keys.extend(normalize_feature_keys(role.visible_features))

    normalized_features = normalize_feature_keys(feature_keys)
    if not normalized_features:
        normalized_features = list(LEGACY_DEFAULT_FEATURES)

    return normalized_features


def _resolve_profile_preferred_platform(user) -> str:
    """Read the preferred platform from profile when available."""
    profile = getattr(user, 'profile', None)
    if profile is None:
        return ''
    return normalize_platform_key(getattr(profile, 'preferred_platform', ''))


def get_preferred_platform(
    user,
    *,
    effective_roles=None,
    feature_keys=None,
) -> str:
    """Resolve the platform to open after login."""
    resolved_roles = effective_roles or get_effective_roles(user)
    resolved_feature_keys = feature_keys or get_effective_feature_keys(
        user,
        effective_roles=resolved_roles,
    )

    profile_platform = _resolve_profile_preferred_platform(user)
    if profile_platform and profile_platform in resolved_feature_keys:
        return profile_platform

    for role in resolved_roles:
        preferred_platform = normalize_platform_key(role.preferred_platform)
        if preferred_platform and preferred_platform in resolved_feature_keys:
            return preferred_platform

    if 'workspace' in resolved_feature_keys:
        return 'workspace'

    if resolved_feature_keys:
        return resolved_feature_keys[0]

    return 'workspace'


def get_access_profile(
    user,
    *,
    direct_roles=None,
    groups=None,
    effective_roles=None,
) -> dict[str, object]:
    """Build the effective access profile for a user."""
    resolved_roles = effective_roles or get_effective_roles(
        user,
        direct_roles=direct_roles,
        groups=groups,
    )
    feature_keys = get_effective_feature_keys(
        user,
        effective_roles=resolved_roles,
    )
    preferred_platform = get_preferred_platform(
        user,
        effective_roles=resolved_roles,
        feature_keys=feature_keys,
    )
    available_platforms = serialize_platforms(feature_keys)
    landing_path = PLATFORM_DEFAULT_PATHS.get(
        preferred_platform,
        '/dashboard',
    )
    return {
        'visible_features': feature_keys,
        'available_platforms': available_platforms,
        'preferred_platform': preferred_platform,
        'landing_path': landing_path,
    }
