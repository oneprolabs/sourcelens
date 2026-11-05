"""Agent Evaluation management API and access probe."""

from django.db.models import Count
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasRunEvaluationsAccess
from lens.models import user_sees_all_assistants
from lens.evaluation.datasets import import_cases, parse_import_payload
from lens.evaluation.engine.batch import (
    build_report,
    cancel_evaluation_run,
    create_evaluation_run,
    retry_failed_cases,
    start_evaluation_run,
)
from lens.evaluation.models import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationMetric,
    EvaluationResult,
    EvaluationRun,
)
from lens.evaluation.serializers import (
    EvaluationCaseSerializer,
    EvaluationDatasetSerializer,
    EvaluationMetricSerializer,
    EvaluationResultDetailSerializer,
    EvaluationResultListSerializer,
    EvaluationRunDetailSerializer,
    EvaluationRunListSerializer,
)


class EvaluationAccessView(APIView):
    """Expose the enabled evaluation surface only to authorized operators."""

    permission_classes = [HasRunEvaluationsAccess]

    def get(self, request):
        """Return the minimal capability payload used by the admin shell."""

        return Response({"enabled": True, "feature": "run_evaluations"})


class EvaluationDatasetViewSet(viewsets.ModelViewSet):
    """Dataset CRUD plus case management and batch launch."""

    permission_classes = [HasRunEvaluationsAccess]
    lookup_field = "uuid"
    serializer_class = EvaluationDatasetSerializer
    queryset = (
        EvaluationDataset.objects.select_related("assistant", "created_by")
        .annotate(case_count=Count("cases"))
        .order_by("-created_at")
    )

    def get_queryset(self):
        queryset = super().get_queryset()
        if not user_sees_all_assistants(self.request.user):
            queryset = queryset.filter(created_by=self.request.user)
        return queryset

    def perform_create(self, serializer):
        """Attribute the dataset to the operator who created it."""

        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["get", "post"], url_path="cases")
    def cases(self, request, uuid=None):
        """List or append cases of one dataset."""

        dataset = self.get_object()
        if request.method == "POST":
            serializer = EvaluationCaseSerializer(
                data=request.data,
                context={"dataset": dataset},
            )
            serializer.is_valid(raise_exception=True)
            case = serializer.save(dataset=dataset)
            return Response(
                EvaluationCaseSerializer(case).data,
                status=status.HTTP_201_CREATED,
            )
        queryset = dataset.cases.all().order_by("created_at", "uuid")
        page = self.paginate_queryset(queryset)
        if page is None:
            return Response(EvaluationCaseSerializer(queryset, many=True).data)
        return self.get_paginated_response(EvaluationCaseSerializer(page, many=True).data)

    @action(detail=True, methods=["post"], url_path="import")
    def import_dataset_cases(self, request, uuid=None):
        """Import cases from JSONL or CSV after validating every row."""

        dataset = self.get_object()
        upload = request.FILES.get("file")
        text = request.data.get("text") or ""
        filename = request.data.get("filename") or ""
        if upload is not None:
            filename = upload.name or ""
            try:
                text = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                return Response(
                    {"errors": [{"line": 0, "error": "file must be UTF-8"}]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        rows, errors = parse_import_payload(text, filename)
        if errors:
            return Response(
                {"errors": errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = import_cases(dataset, rows)
        return Response(result, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="runs")
    def runs(self, request, uuid=None):
        """List batches of one dataset or create a new one."""

        dataset = self.get_object()
        if request.method == "POST":
            case_uuids = request.data.get("case_uuids") or None
            if case_uuids is not None and not isinstance(case_uuids, list):
                return Response(
                    {"detail": "case_uuids must be a list."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                evaluation_run = create_evaluation_run(
                    dataset=dataset,
                    requested_by=request.user,
                    case_uuids=case_uuids,
                    config={"label": str(request.data.get("label") or "")},
                )
            except ValueError as exc:
                return Response(
                    {"detail": str(exc)},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            start_evaluation_run(evaluation_run.uuid)
            return Response(
                EvaluationRunDetailSerializer(evaluation_run).data,
                status=status.HTTP_201_CREATED,
            )
        queryset = EvaluationRun.objects.filter(dataset=dataset)
        if not user_sees_all_assistants(request.user):
            queryset = queryset.filter(requested_by=request.user)
        queryset = queryset.select_related("dataset").order_by("-created_at")
        page = self.paginate_queryset(queryset)
        if page is None:
            return Response(EvaluationRunListSerializer(queryset, many=True).data)
        return self.get_paginated_response(EvaluationRunListSerializer(page, many=True).data)


class EvaluationCaseViewSet(viewsets.ModelViewSet):
    """Update and delete individual evaluation cases."""

    permission_classes = [HasRunEvaluationsAccess]
    lookup_field = "uuid"
    serializer_class = EvaluationCaseSerializer
    http_method_names = ["get", "patch", "put", "delete", "head", "options"]
    queryset = EvaluationCase.objects.select_related("dataset").order_by(
        "created_at",
        "uuid",
    )

    def get_queryset(self):
        """Allow filtering cases by dataset UUID."""

        queryset = super().get_queryset()
        if not user_sees_all_assistants(self.request.user):
            queryset = queryset.filter(dataset__created_by=self.request.user)
        dataset_uuid = self.request.query_params.get("dataset")
        if dataset_uuid:
            queryset = queryset.filter(dataset__uuid=dataset_uuid)
        return queryset


class EvaluationRunViewSet(viewsets.ReadOnlyModelViewSet):
    """Batch inspection, cancellation, retry, results and report."""

    permission_classes = [HasRunEvaluationsAccess]
    lookup_field = "uuid"
    queryset = EvaluationRun.objects.select_related("dataset").order_by("-created_at")

    def get_serializer_class(self):
        """Use the compact serializer for lists."""

        if self.action == "list":
            return EvaluationRunListSerializer
        return EvaluationRunDetailSerializer

    def get_queryset(self):
        """Filter batches by dataset or status from query parameters."""

        queryset = super().get_queryset()
        if not user_sees_all_assistants(self.request.user):
            queryset = queryset.filter(requested_by=self.request.user)
        dataset_uuid = self.request.query_params.get("dataset")
        if dataset_uuid:
            queryset = queryset.filter(dataset__uuid=dataset_uuid)
        run_status = self.request.query_params.get("status")
        if run_status:
            queryset = queryset.filter(status=run_status)
        return queryset

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, uuid=None):
        """Cancel a queued or running batch."""

        evaluation_run = self.get_object()
        if evaluation_run.status in {
            EvaluationRun.Status.COMPLETED,
            EvaluationRun.Status.CANCELLED,
        }:
            return Response(
                {"detail": "Batch is already terminal."},
                status=status.HTTP_409_CONFLICT,
            )
        cancel_evaluation_run(evaluation_run.uuid)
        evaluation_run.refresh_from_db()
        return Response(EvaluationRunDetailSerializer(evaluation_run).data)

    @action(detail=True, methods=["post"], url_path="retry")
    def retry(self, request, uuid=None):
        """Re-run failed and inconclusive cases of a finished batch."""

        evaluation_run = self.get_object()
        try:
            retried = retry_failed_cases(evaluation_run.uuid)
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        evaluation_run.refresh_from_db()
        if retried:
            start_evaluation_run(evaluation_run.uuid)
            evaluation_run.refresh_from_db()
        return Response(
            {
                **EvaluationRunDetailSerializer(evaluation_run).data,
                "retried_cases": retried,
            }
        )

    @action(detail=True, methods=["get"], url_path="results")
    def results(self, request, uuid=None):
        """Return paginated case results, optionally filtered by verdict."""

        evaluation_run = self.get_object()
        queryset = EvaluationResult.objects.filter(evaluation_run=evaluation_run).select_related("case")
        passed = request.query_params.get("passed")
        if passed in {"true", "false", "null"}:
            queryset = queryset.filter(passed=None if passed == "null" else passed == "true")
        queryset = queryset.order_by("created_at", "uuid")
        page = self.paginate_queryset(queryset)
        if page is None:
            return Response(EvaluationResultListSerializer(queryset, many=True).data)
        return self.get_paginated_response(EvaluationResultListSerializer(page, many=True).data)

    @action(detail=True, methods=["get"], url_path="results/(?P<result_uuid>[^/.]+)")
    def result_detail(self, request, uuid=None, result_uuid=None):
        """Return one case result with its frozen evidence snapshot."""

        evaluation_run = self.get_object()
        result = (
            EvaluationResult.objects.select_related("case")
            .filter(evaluation_run=evaluation_run, uuid=result_uuid)
            .first()
        )
        if result is None:
            return Response(
                {"detail": "Result not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(EvaluationResultDetailSerializer(result).data)

    @action(detail=True, methods=["get"], url_path="report")
    def report(self, request, uuid=None):
        """Return the aggregated report for one batch."""

        evaluation_run = self.get_object()
        return Response(build_report(evaluation_run))


class EvaluationMetricViewSet(viewsets.ModelViewSet):
    """Metric configuration used by batch scoring."""

    permission_classes = [HasRunEvaluationsAccess]
    lookup_field = "uuid"
    serializer_class = EvaluationMetricSerializer
    queryset = EvaluationMetric.objects.order_by("name")
