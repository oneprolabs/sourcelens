"""Keep catalog ownership and tenant opt-ins consistent at bind and dispatch time."""

from django.db.models import Q


def usable_catalog_resources(queryset, tenant):
    """Return enabled private resources and official resources opted into by this tenant."""
    queryset = queryset.filter(enabled=True)
    if tenant is None:
        return queryset.filter(tenant__isnull=True)
    return queryset.filter(
        Q(tenant=tenant) | Q(tenant__isnull=True, tenant_entries__tenant=tenant, tenant_entries__enabled=True)
    ).distinct()
