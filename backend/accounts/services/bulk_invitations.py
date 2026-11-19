"""Reviewed invitation batches with per-recipient validation, authority and delivery outcomes."""

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.db.models.functions import Lower
from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied

from accounts.audit import record_audit
from accounts.models import Tenant, TenantGroup, TenantMembership
from accounts.services.member_management import require_member_authority
from accounts.services.tenant_invitations import deliver_invitation, prepare_invitation

CONFIRMATION_SALT = "accounts.bulk-invitation.v1"
CONFIRMATION_TIMEOUT = 600


class BulkInvitationInput(serializers.Serializer):
    """Bound the envelope while letting bad recipient rows produce individual feedback."""

    entries = serializers.ListField(child=serializers.JSONField(allow_null=True), min_length=1, max_length=100)
    confirmation = serializers.CharField(max_length=512, required=False, trim_whitespace=False)


class InvitationReviewChanged(APIException):
    """Require a new preview after the input, identity or confirmation lifetime changes."""

    status_code = 409
    default_detail = "INVITATION_REVIEW_CHANGED"


class InvitationEntry(serializers.Serializer):
    """Validate a single recipient without accepting platform roles or foreign groups."""

    email = serializers.EmailField(max_length=254)
    role = serializers.ChoiceField(choices=TenantMembership.Role.choices, default="member")
    group_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=100, default=list)


def current_invitation_tenant(request):
    """Resolve live membership without trusting a cached User relation."""
    tenant_id = TenantMembership.objects.filter(user_id=request.user.pk).values_list("tenant_id", flat=True).first()
    tenant = Tenant.objects.filter(pk=tenant_id).first()
    if tenant is None:
        raise PermissionDenied("MEMBER_ADMIN_AUTHORITY_CHANGED")
    require_member_authority(request, tenant)
    return tenant


def _rows(tenant, entries):
    """Classify user-supplied rows without disclosing existing account metadata."""
    groups = set(TenantGroup.objects.filter(tenant=tenant).values_list("auth_group_id", flat=True))
    emails = [str(row.get("email", "")).strip().lower() for row in entries if isinstance(row, dict)]
    existing = set(
        get_user_model()
        .objects.annotate(canonical=Lower("email"))
        .filter(canonical__in=emails)
        .values_list("canonical", flat=True)
    )
    seen, items, valid = set(), [], []
    for index, entry in enumerate(entries):
        raw = entry if isinstance(entry, dict) else {}
        email = raw.get("email", "")
        email = email.strip().lower() if isinstance(email, str) else ""
        item = {"index": index, "email": email[:254], "status": "invalid", "reason": "invalid_email"}
        serializer = InvitationEntry(data=entry)
        if serializer.is_valid():
            row = {**serializer.validated_data, "email": email}
            if email in seen:
                item.update(status="duplicate", reason="duplicate")
            elif not set(row["group_ids"]).issubset(groups):
                item["reason"] = "invalid_groups"
            elif email in existing:
                item.update(status="existing", reason="email_unavailable")
            else:
                item.update(status="valid", reason="")
                valid.append((index, row))
        else:
            item["reason"] = (
                "invalid_role"
                if "role" in serializer.errors
                else ("invalid_groups" if "group_ids" in serializer.errors else "invalid_email")
            )
        seen.add(email)
        items.append(item)
    return items, valid


def _input_digest(entries):
    """Hash the exact submitted envelope without placing recipient addresses in the proof."""
    import hashlib
    import json

    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _proof(request, tenant, entries):
    """Bind confirmation to the same actor, tenant and explicit per-recipient configuration."""
    return {"actor": request.user.pk, "tenant": str(tenant.pk), "input": _input_digest(entries)}


def preview_invitations(*, request, entries):
    """Preview validation and availability without creating accounts or invitation credentials."""
    tenant = current_invitation_tenant(request)
    items, valid = _rows(tenant, entries)
    return {
        "items": items,
        "counts": {
            status: sum(item["status"] == status for item in items)
            for status in ("valid", "invalid", "existing", "duplicate")
        },
        "confirmation": signing.dumps(
            {**_proof(request, tenant, entries), "valid": [index for index, _ in valid]},
            salt=CONFIRMATION_SALT,
            compress=True,
        ),
    }


def invite_members(*, request, entries, confirmation, allow_send):
    """Create only new reviewed recipients and send outside their database transactions."""
    tenant = current_invitation_tenant(request)
    try:
        proof = signing.loads(confirmation, salt=CONFIRMATION_SALT, max_age=CONFIRMATION_TIMEOUT)
    except signing.BadSignature:
        raise InvitationReviewChanged() from None
    reviewed = proof.pop("valid", []) if isinstance(proof, dict) else []
    if proof != _proof(request, tenant, entries):
        raise InvitationReviewChanged()
    items, valid = _rows(tenant, entries)
    valid = [(index, row) for index, row in valid if index in reviewed]
    for item in items:
        if item["status"] == "valid" and item["index"] not in reviewed:
            item.update(status="skipped", reason="review_changed")
    result = {"sent": 0, "skipped": len(items) - len(valid), "failed": 0, "items": items}
    for index, row in valid:
        item = items[index]
        try:
            with transaction.atomic():
                locked = Tenant.objects.select_for_update().get(pk=tenant.pk)
                require_member_authority(request, locked)
                current, eligible = _rows(locked, [row])
                if not eligible:
                    item.update(status="skipped", reason=current[0]["reason"])
                elif not allow_send():
                    item.update(status="failed", reason="rate_limited")
                else:
                    member, token = prepare_invitation(locked, **row, request=request, allow_existing=False)
            if item["status"] == "valid":
                sent = deliver_invitation(member, token)
                item.update(
                    status="sent" if sent else "failed",
                    reason="" if sent else "delivery_failed",
                    member_id=member.user_id,
                )
                record_audit(
                    action="member.bulk_invite",
                    request=request,
                    tenant=locked,
                    actor=request.user,
                    resource_type="member",
                    resource_id=str(member.user_id),
                    result="success" if sent else "failed",
                    metadata={"role": member.role, "delivered": sent},
                )
        except serializers.ValidationError as failure:
            reason = "invalid_groups" if "group_ids" in failure.detail else "email_unavailable"
            item.update(status="skipped", reason=reason)
        except PermissionDenied:
            item.update(status="failed", reason="authority_changed")
        result[item["status"]] += 1
    for item in items:
        if item["status"] in {"invalid", "existing", "duplicate"}:
            item["status"] = "skipped"
    return result
