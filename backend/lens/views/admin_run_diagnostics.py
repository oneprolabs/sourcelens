"""Admin API for evidence-backed Run diagnostics."""

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access import get_user_tenant
from accounts.delegation import is_delegated_request
from accounts.permissions import HasRunDiagnosticsAccess
from lens.models import Run, RunDiagnostic
from lens.run_access import CanInspectTenantRuns, can_inspect_run_evidence, scope_run_queryset
from lens.run_diagnostics import (
    RunDiagnosticStateError,
    create_diagnostic_turn,
    create_run_diagnostic,
    serialize_diagnostic,
    serialize_diagnostic_turn,
)


def _get_run(run_uuid, request):
    """Resolve tenant content before checking access to embedded runtime evidence."""
    queryset = Run.objects.select_related("session__assistant").filter(uuid=run_uuid)
    tenant = get_user_tenant(request.user)
    queryset = scope_run_queryset(queryset, tenant.pk if tenant is not None else None)
    run = queryset.first()
    if run is not None and not can_inspect_run_evidence(request, run):
        raise PermissionDenied("Assistant configuration access is required.")
    return run


def _diagnostic_response(diagnostic):
    """Omit directory identities when inspecting tenant runtime under delegation."""
    data = serialize_diagnostic(diagnostic)
    if is_delegated_request():
        data.pop("requested_by", None)
    return data


class AdminRunDiagnosticsView(APIView):
    """List or create diagnostics for one target Run."""

    permission_classes = [HasRunDiagnosticsAccess, CanInspectTenantRuns]

    def get(self, request, run_uuid):
        """Return diagnostics newest first."""

        run = _get_run(run_uuid, request)
        if run is None:
            return Response(
                {"detail": "Run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        diagnostics = (
            RunDiagnostic.objects.filter(run=run)
            .select_related("run", "evidence", "requested_by")
            .prefetch_related("turns")
            .order_by("-created_at")
        )
        return Response([_diagnostic_response(item) for item in diagnostics])

    def post(self, request, run_uuid):
        """Create an idempotent asynchronous diagnosis."""

        run = _get_run(run_uuid, request)
        if run is None:
            return Response(
                {"detail": "Run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            diagnostic, created = create_run_diagnostic(run, request.user)
        except RunDiagnosticStateError as exc:
            return Response(
                {
                    "detail": "Only terminal Runs can be diagnosed.",
                    "code": exc.code,
                },
                status=status.HTTP_409_CONFLICT,
            )
        diagnostic = (
            RunDiagnostic.objects.select_related(
                "run",
                "evidence",
                "requested_by",
            )
            .prefetch_related("turns")
            .get(pk=diagnostic.pk)
        )
        return Response(
            _diagnostic_response(diagnostic),
            status=(status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK),
        )


class AdminRunDiagnosticTurnsView(APIView):
    """Create a controlled question bound to one diagnosis and target Run."""

    permission_classes = [HasRunDiagnosticsAccess, CanInspectTenantRuns]

    def post(self, request, run_uuid, diagnostic_uuid):
        """Queue one bounded follow-up question."""

        run = _get_run(run_uuid, request)
        diagnostic_queryset = RunDiagnostic.objects.select_related("run", "evidence").filter(
            uuid=diagnostic_uuid, run=run
        )
        tenant = get_user_tenant(request.user)
        tenant_id = tenant.pk if tenant is not None else None
        diagnostic_queryset = diagnostic_queryset.filter(run__tenant_id=tenant_id, run__session__tenant_id=tenant_id)
        diagnostic = diagnostic_queryset.first()
        if diagnostic is None:
            return Response(
                {"detail": "Diagnosis not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            turn, created = create_diagnostic_turn(
                diagnostic,
                request.user,
                request.data.get("question"),
            )
        except ValidationError as exc:
            return Response(
                {
                    "detail": exc.messages[0],
                    "code": "INVALID_QUESTION",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RunDiagnosticStateError as exc:
            return Response(
                {
                    "detail": "The diagnosis is not ready for follow-up.",
                    "code": exc.code,
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            serialize_diagnostic_turn(turn),
            status=(status.HTTP_202_ACCEPTED if created else status.HTTP_200_OK),
        )
