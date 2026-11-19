"""Atomic bulk mutations for the management console."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction

from accounts.models import Profile, Role

User = get_user_model()

MAX_BULK_ITEMS = 100


def legacy_users():
    """Return accounts outside tenant and platform management."""
    return User.objects.filter(membership__isnull=True, is_superuser=False).exclude(
        profile__platform_role=Profile.PlatformRole.SUPER_ADMIN
    )


def legacy_groups():
    """Keep tenant groups and mixed legacy groups out of the old admin API."""
    return (
        Group.objects.filter(tenant_group__isnull=True)
        .exclude(user__in=User.objects.exclude(pk__in=legacy_users()))
        .exclude(assistant_grants__assistant__tenant__isnull=False)
    )


def legacy_roles():
    """Prevent changes to roles that still reach tenant or platform accounts."""
    return Role.objects.exclude(users__in=User.objects.exclude(pk__in=legacy_users())).exclude(
        groups__in=Group.objects.exclude(pk__in=legacy_groups())
    )


class BulkMutationError(ValueError):
    """Reject a bulk mutation before any resource is changed."""


def normalize_bulk_ids(raw_ids, field_name):
    """Validate a bounded, unique list of positive integer identifiers."""

    if not isinstance(raw_ids, list) or not raw_ids:
        raise BulkMutationError(f"{field_name} must be a non-empty list.")
    if len(raw_ids) > MAX_BULK_ITEMS:
        raise BulkMutationError(
            f"{field_name} cannot contain more than {MAX_BULK_ITEMS} items."
        )

    normalized = []
    for raw_id in raw_ids:
        if isinstance(raw_id, bool):
            raise BulkMutationError(f"{field_name} contains an invalid id.")
        try:
            value = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise BulkMutationError(
                f"{field_name} contains an invalid id."
            ) from exc
        if value <= 0:
            raise BulkMutationError(f"{field_name} contains an invalid id.")
        normalized.append(value)

    if len(set(normalized)) != len(normalized):
        raise BulkMutationError(f"{field_name} contains duplicate ids.")
    return normalized


def _locked_objects(queryset, object_ids, label):
    objects = list(
        queryset.select_for_update(of=("self",))
        .filter(pk__in=object_ids)
        .order_by("pk")
    )
    if len(objects) != len(object_ids):
        raise BulkMutationError(f"One or more {label} no longer exist.")
    return objects


@transaction.atomic
def set_users_active(user_ids, is_active, actor_id):
    """Set every selected user status or change none of them."""

    users = _locked_objects(legacy_users(), user_ids, "users")
    if not is_active and actor_id in user_ids:
        raise BulkMutationError("You cannot disable your own account.")
    User.objects.filter(pk__in=[user.pk for user in users]).update(
        is_active=is_active
    )
    return len(users)


@transaction.atomic
def set_roles_active(role_ids, is_active):
    """Set every selected role status or change none of them."""

    roles = _locked_objects(legacy_roles(), role_ids, "roles")
    Role.objects.filter(pk__in=[role.pk for role in roles]).update(
        is_active=is_active
    )
    return len(roles)


@transaction.atomic
def delete_groups(group_ids):
    """Delete every selected group or preserve the complete selection."""

    groups = _locked_objects(legacy_groups(), group_ids, "groups")
    Group.objects.filter(pk__in=[group.pk for group in groups]).delete()
    return len(groups)
