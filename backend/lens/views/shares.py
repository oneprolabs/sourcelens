"""Shared Q&A publishing, moderation, and public views."""

import secrets
from uuid import UUID

from django.db.models import F, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import normalize_answer_language
from lens.models import Assistant, SharedQA, SharedQAFile
from lens.public_share import (
    INHERIT,
    LOGIN,
    allows_anonymous_access,
    configured_policy,
    policy_source,
    requires_publisher_choice,
)
from lens.qa_pdf import build_qa_pdf_filename, render_qa_pdf
from lens.serializers import (
    SharedQAAdminDetailSerializer,
    SharedQAAdminSerializer,
    SharedQAListSerializer,
    SharedQAMineSerializer,
    SharedQAPublicSerializer,
)
from lens.shared_qa_files import snapshot_shared_qa_files
from .admin_runs import _admin_safe_int
from .base import BaseAdminViewSet, BaseAuthenticatedViewSet


def _unique_share_token():
    """Generate a collision-free URL-safe share token."""

    for _ in range(5):
        token = secrets.token_urlsafe(16)
        if not SharedQA.objects.filter(token=token).exists():
            return token
    return secrets.token_urlsafe(24)


def _shared_qa_default_title(question, limit=60):
    """Derive a default share title from the question text."""

    text = " ".join((question or "").split())
    return text[:limit] or "Shared Q&A"


class SharedQAViewSet(BaseAuthenticatedViewSet):
    """List, rename, and revoke the current user's own shared Q&As."""

    queryset = SharedQA.objects.all().select_related("published_by")
    serializer_class = SharedQAMineSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self):
        """Restrict to shares published by the current user."""

        return super().get_queryset().filter(
            published_by=self.request.user
        )

    def partial_update(self, request, *args, **kwargs):
        """Let the owner edit the share title and its access mode."""

        share = self.get_object()
        updates = []
        title = (request.data.get("title") or "").strip()
        if title:
            share.title = title[:200]
            updates.append("title")
        if "require_login" in request.data:
            assistant = share.assistant
            if (
                assistant is not None
                and assistant.visibility == Assistant.Visibility.PUBLIC
                and isinstance(request.data.get("require_login"), bool)
            ):
                share.require_login = request.data["require_login"]
                updates.append("require_login")
        if updates:
            share.save(update_fields=[*updates, "updated_at"])
        return Response(SharedQAMineSerializer(share).data)


class SharedQAPolicyView(APIView):
    """Report the link-access policy a publisher inherits for a new share."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        """Return the inherited policy and whether the publisher must choose."""

        assistant = None
        assistant_uuid = (
            request.query_params.get("assistant_uuid") or ""
        ).strip()
        assistant_slug = (
            request.query_params.get("assistant_slug") or ""
        ).strip()
        if assistant_uuid or assistant_slug:
            if assistant_uuid:
                try:
                    lookup = {"uuid": UUID(assistant_uuid)}
                except (AttributeError, TypeError, ValueError):
                    return Response(
                        {"detail": "Assistant not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )
            else:
                lookup = {"slug": assistant_slug}
            assistant = (
                Assistant.objects.filter(**lookup)
                .filter(
                    Q(visibility=Assistant.Visibility.PUBLIC)
                    | Q(access_grants__user=request.user)
                    | Q(access_grants__group__in=request.user.groups.all())
                )
                .distinct()
                .first()
            )
            if assistant is None:
                return Response(
                    {"detail": "Assistant not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if assistant.visibility != Assistant.Visibility.PUBLIC:
                # A private Assistant always needs a signed-in viewer, so no
                # policy can open its share links.
                return Response(
                    {
                        "policy": LOGIN,
                        "source": "private",
                        "requires_choice": False,
                    }
                )
        return Response(
            {
                "policy": configured_policy(assistant) or INHERIT,
                "source": policy_source(assistant) or "",
                "requires_choice": requires_publisher_choice(assistant),
            }
        )


class AdminSharedQAViewSet(BaseAdminViewSet):
    """Admin moderation/curation of shared Q&As."""

    queryset = SharedQA.objects.all().select_related("published_by")
    serializer_class = SharedQAAdminSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_serializer_class(self):
        """Return complete Q&A content only for the detail endpoint."""

        if self.action == "retrieve":
            return SharedQAAdminDetailSerializer
        return SharedQAAdminSerializer

    def get_queryset(self):
        """Filter by listed/status for the moderation queue."""

        queryset = super().get_queryset()
        params = self.request.query_params
        listed = params.get("listed")
        if listed in ("true", "false"):
            queryset = queryset.filter(is_listed=(listed == "true"))
        status_param = (params.get("status") or "").strip()
        if status_param:
            queryset = queryset.filter(status=status_param)
        return queryset


class PublicSharedQAView(APIView):
    """Single shared Q&A by token with assistant visibility rules."""

    permission_classes = [permissions.AllowAny]

    def get(self, request, token):
        """Return one published shared Q&A and bump its view count."""

        share = (
            SharedQA.objects.select_related("assistant", "published_by")
            .filter(token=token, status=SharedQA.Status.PUBLISHED)
            .first()
        )
        if share is None or share.assistant is None:
            return Response(
                {"detail": "Shared Q&A not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        access_error = _shared_qa_access_error(share.assistant, request.user, share=share)
        if access_error is not None:
            return access_error
        snapshot_shared_qa_files(share, strict=False)
        SharedQA.objects.filter(pk=share.pk).update(
            view_count=F("view_count") + 1
        )
        share.refresh_from_db(fields=["view_count"])
        return Response(SharedQAPublicSerializer(share).data)


class PublicSharedQAPdfView(APIView):
    """Download one shared Q&A as a styled text PDF."""

    permission_classes = [permissions.AllowAny]

    def get(self, request, token):
        """Return PDF bytes using the shared page's access rules."""

        share = (
            SharedQA.objects.select_related("assistant")
            .filter(token=token, status=SharedQA.Status.PUBLISHED)
            .first()
        )
        if share is None or share.assistant is None:
            raise Http404
        access_error = _shared_qa_access_error(share.assistant, request.user, share=share)
        if access_error is not None:
            return access_error
        snapshot_shared_qa_files(share, strict=False)
        input_files = share.files.filter(kind=SharedQAFile.Kind.INPUT)
        output_files = share.files.filter(kind=SharedQAFile.Kind.OUTPUT)
        pdf_bytes = render_qa_pdf(
            title=share.title or share.question,
            question=share.question,
            answer=share.answer,
            assistant_name=share.assistant_name,
            published_at=share.published_at,
            input_files=input_files,
            output_files=output_files,
            language_code=getattr(request, "LANGUAGE_CODE", "en"),
        )
        filename = build_qa_pdf_filename(share.title, share.question)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = content_disposition_header(
            True,
            filename,
        )
        response["Cache-Control"] = "private, max-age=0, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class PublicSharedQAFileView(APIView):
    """Serve one immutable file through shared-Q&A authorization."""

    permission_classes = [permissions.AllowAny]

    def get(self, request, token, uuid):
        """Return shared bytes only to eligible authenticated viewers."""

        share = (
            SharedQA.objects.select_related("assistant")
            .filter(token=token, status=SharedQA.Status.PUBLISHED)
            .first()
        )
        if share is None or share.assistant is None:
            raise Http404
        access_error = _shared_qa_access_error(share.assistant, request.user, share=share)
        if access_error is not None:
            return access_error
        snapshot = get_object_or_404(
            SharedQAFile,
            share=share,
            uuid=uuid,
        )
        try:
            file_handle = snapshot.file.open("rb")
        except Exception as exc:
            raise Http404 from exc
        response = FileResponse(
            file_handle,
            as_attachment=True,
            filename=snapshot.filename,
            content_type=snapshot.content_type or "application/octet-stream",
        )
        response["Cache-Control"] = "private, max-age=0, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "sandbox"
        return response


def _shared_qa_visible_to_user(share, user):
    """Return whether a shared Q&A may be read through its token."""

    return bool(
        user and
        user.is_authenticated and
        share.assistant and
        share.assistant.is_accessible_by(user)
    )


def _shared_qa_access_error(assistant, user, share=None):
    """Return an access error response for shared Q&A, or None if allowed.

    A public Assistant publishes its shared Q&A, so its share links open
    without signing in unless the share, the Assistant, or the deployment
    policy requires a signed-in viewer. A private Assistant always needs an
    authenticated viewer that holds access.
    """

    authenticated = bool(user and user.is_authenticated)
    if not authenticated:
        if not allows_anonymous_access(assistant, share=share):
            return Response(
                {
                    "code": "AUTHENTICATION_REQUIRED",
                    "detail": "Sign in to view this shared Q&A.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )
    if assistant.is_accessible_by(user):
        return None
    if not authenticated:
        return Response(
            {
                "code": "AUTHENTICATION_REQUIRED",
                "detail": "Sign in to view this shared Q&A.",
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    return Response(
        {
            "code": "ASSISTANT_ACCESS_DENIED",
            "detail": "You do not have access to this assistant.",
        },
        status=status.HTTP_403_FORBIDDEN,
    )


class PublicSharedQAListView(APIView):
    """Language-matched list of an assistant's curated shared Q&As."""

    permission_classes = [permissions.AllowAny]

    def get(self, request, slug):
        """Return curated, listed shares for one active assistant."""

        assistant = Assistant.objects.filter(
            slug=slug,
            status=Assistant.Status.ACTIVE,
        ).first()
        if assistant is None:
            return Response(
                {"detail": "Assistant not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        access_error = _shared_qa_access_error(assistant, request.user)
        if access_error is not None:
            return access_error
        params = request.query_params
        limit = _admin_safe_int(params.get("limit"), 20, maximum=50)
        offset = _admin_safe_int(params.get("offset"), 0, minimum=0)
        content_language = normalize_answer_language(
            getattr(request, "LANGUAGE_CODE", "en")
        )
        queryset = SharedQA.objects.filter(
            assistant=assistant,
            content_language=content_language,
            is_listed=True,
            status=SharedQA.Status.PUBLISHED,
        ).order_by("-published_at", "-created_at")
        if (
            not (request.user and request.user.is_authenticated)
            and configured_policy(assistant) is None
        ):
            # Without a configured layer the publisher decides per share, so a
            # share marked as sign-in only must not surface its title or
            # excerpt to an anonymous visitor.
            queryset = queryset.exclude(require_login=True)
        total = queryset.count()
        rows = SharedQAListSerializer(
            queryset[offset:offset + limit],
            many=True,
        ).data
        has_more = offset + limit < total
        return Response({
            "assistant": {"name": assistant.name, "slug": assistant.slug},
            "content_language": content_language,
            "results": rows,
            "total": total,
            "next_offset": offset + limit if has_more else None,
        })
