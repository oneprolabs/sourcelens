"""Agent Evaluation management API and access probe."""

import uuid as uuid_module

from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import HasRequiredFeature, HasRunEvaluationsAccess
from lens.evaluation.comparison import compare_batches
from lens.evaluation.datasets import import_cases, parse_import_payload
from lens.evaluation.engine.batch import (
    assistant_can_run_evaluation,
    build_report,
    cancel_evaluation_run,
    create_evaluation_run,
    retry_failed_cases,
    start_evaluation_run,
)
from lens.evaluation.models import (
    EvaluationCalibration,
    EvaluationCase,
    EvaluationDataset,
    EvaluationMetric,
    EvaluationResult,
    EvaluationRun,
)
from lens.evaluation.revisions import baseline_metadata, record_dataset_revision
from lens.evaluation.scoring import DEFAULT_METRICS, ensure_default_metrics
from lens.evaluation.serializers import (
    EvaluationCaseSerializer,
    EvaluationDatasetSerializer,
    EvaluationMetricSerializer,
    EvaluationResultDetailSerializer,
    EvaluationResultListSerializer,
    EvaluationRunDetailSerializer,
    EvaluationRunListSerializer,
)
from lens.models import Message, Run, user_sees_all_assistants


class EvaluationAccessView(APIView):
    """Expose the enabled evaluation surface only to authorized operators."""

    permission_classes = [HasRunEvaluationsAccess]

    def get(self, request):
        """Return the minimal capability payload used by the admin shell."""

        return Response({"enabled": True, "feature": "run_evaluations"})


class EvaluationDatasetViewSet(viewsets.ModelViewSet):
    """Dataset CRUD plus case management and batch launch."""

    permission_classes = [HasRunEvaluationsAccess]
    required_feature = "admin_console"
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
        assistant_slug = self.request.query_params.get("assistant_slug")
        if assistant_slug:
            queryset = queryset.filter(assistant__slug=assistant_slug)
        return queryset

    def perform_create(self, serializer):
        """Attribute the dataset to the operator who created it."""

        with transaction.atomic():
            dataset = serializer.save(created_by=self.request.user)
            record_dataset_revision(dataset)

    def perform_update(self, serializer):
        """Capture dataset metadata edits in the revision history."""

        with transaction.atomic():
            EvaluationDataset.objects.select_for_update().get(pk=serializer.instance.pk)
            dataset = serializer.save()
            record_dataset_revision(dataset)

    @action(detail=True, methods=["get"], url_path="revisions")
    def revisions(self, request, uuid=None):
        """List immutable dataset snapshots, optionally selecting one revision."""

        dataset = self.get_object()
        rows = dataset.revisions.all()
        revision = request.query_params.get("revision")
        if revision:
            if not revision.isdecimal():
                return Response({"detail": "Invalid revision."}, status=status.HTTP_400_BAD_REQUEST)
            row = get_object_or_404(rows, revision=int(revision))
            return Response(
                {
                    "revision": row.revision,
                    "created_at": row.created_at,
                    "metadata": row.metadata,
                    "cases": row.cases,
                }
            )
        summaries = rows.only("revision", "created_at", "case_count")
        return Response(
            [
                {
                    "revision": row.revision,
                    "created_at": row.created_at,
                    "case_count": row.case_count,
                }
                for row in summaries
            ]
        )

    def destroy(self, request, *args, **kwargs):
        """Reject deletion of a dataset that already produced evaluation runs."""

        dataset = self.get_object()
        if dataset.evaluation_runs.exists():
            return Response(
                {"detail": "Dataset has evaluation runs and cannot be deleted."},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    @transaction.atomic
    @action(detail=True, methods=["get", "post"], url_path="cases")
    def cases(self, request, uuid=None):
        """List or append cases of one dataset."""

        dataset = self.get_object()
        if request.method == "POST":
            dataset = EvaluationDataset.objects.select_for_update().get(pk=dataset.pk)
            serializer = EvaluationCaseSerializer(
                data=request.data,
                context={"dataset": dataset},
            )
            serializer.is_valid(raise_exception=True)
            case = serializer.save(dataset=dataset)
            record_dataset_revision(dataset)
            return Response(
                EvaluationCaseSerializer(case).data,
                status=status.HTTP_201_CREATED,
            )
        queryset = dataset.cases.all().order_by("created_at", "uuid")
        page = self.paginate_queryset(queryset)
        if page is None:
            return Response(EvaluationCaseSerializer(queryset, many=True).data)
        return self.get_paginated_response(EvaluationCaseSerializer(page, many=True).data)

    @transaction.atomic
    @action(detail=True, methods=["post"], url_path="baseline")
    def baseline(self, request, uuid=None):
        """Create a case from an authorized, standalone completed Run."""

        dataset = self.get_object()
        if not HasRequiredFeature().has_permission(request, self):
            return Response({"detail": "Run access required."}, status=status.HTTP_403_FORBIDDEN)
        try:
            run_uuid = uuid_module.UUID(str(request.data.get("run_uuid") or ""))
        except (TypeError, ValueError):
            return Response({"detail": "Invalid Run UUID."}, status=status.HTTP_400_BAD_REQUEST)
        run = get_object_or_404(
            Run.objects.select_related("session", "input_message", "output_message"), uuid=run_uuid
        )
        if run.session.assistant_id != dataset.assistant_id:
            return Response(
                {"detail": "Run and dataset assistants must match."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (
            run.parent_run_id
            or run.status != Run.Status.DONE
            or run.outcome != Run.Outcome.COMPLETED
            or not run.output_message_id
            or not run.output_message.content.strip()
        ):
            return Response(
                {"detail": "A completed Run with an answer is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        execution_snapshot = getattr(getattr(run, "execution", None), "runtime_snapshot", {}) or {}
        if (
            Message.objects.filter(
                session=run.session,
                role=Message.Role.USER,
                sequence__lt=run.input_message.sequence,
            ).exists()
            or run.input_message.attachments.exists()
            or execution_snapshot.get("session_attachment_uuids")
            or execution_snapshot.get("document_attachment_count")
        ):
            return Response(
                {"detail": "Runs requiring previous conversation or attachments are not supported."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        dataset = EvaluationDataset.objects.select_for_update().get(pk=dataset.pk)
        baseline = {
            "metadata": baseline_metadata(run),
            "run_uuid": str(run.uuid),
            "question": run.input_message.content,
            "answer": run.output_message.content,
            "citations": run.citations,
            "created_at": run.created_at.isoformat(),
        }
        serializer = EvaluationCaseSerializer(
            data={"input": run.input_message.content, "config": {"baseline": baseline}},
            context={"dataset": dataset},
        )
        serializer.is_valid(raise_exception=True)
        case = serializer.save(dataset=dataset)
        record_dataset_revision(dataset)
        return Response(EvaluationCaseSerializer(case).data, status=status.HTTP_201_CREATED)

    @transaction.atomic
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
        dataset = EvaluationDataset.objects.select_for_update().get(pk=dataset.pk)
        result = import_cases(dataset, rows)
        if result["created"]:
            record_dataset_revision(dataset)
        return Response(result, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="runs")
    def runs(self, request, uuid=None):
        """List batches of one dataset or create a new one."""

        dataset = self.get_object()
        if request.method == "POST":
            if not assistant_can_run_evaluation(dataset.assistant):
                return Response(
                    {"detail": "LENSNODE_EVALUATION_NODE_UNAVAILABLE"},
                    status=status.HTTP_409_CONFLICT,
                )
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

    def perform_update(self, serializer):
        """Atomically update an example and capture its new dataset revision."""

        with transaction.atomic():
            dataset = EvaluationDataset.objects.select_for_update().get(pk=serializer.instance.dataset_id)
            serializer.save()
            record_dataset_revision(dataset)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        """Reject deletion of a case that is referenced by evaluation results."""

        case = self.get_object()
        EvaluationDataset.objects.select_for_update().get(pk=case.dataset_id)
        if case.results.exists():
            return Response(
                {"detail": "Case has evaluation results and cannot be deleted."},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        """Atomically delete an example and capture its new dataset revision."""

        with transaction.atomic():
            dataset = EvaluationDataset.objects.select_for_update().get(pk=instance.dataset_id)
            instance.delete()
            record_dataset_revision(dataset)


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

    @action(detail=True, methods=["get"], url_path="compare")
    def compare(self, request, uuid=None):
        """Compare authorized batch results with explicit limitations."""

        left = self.get_object()
        try:
            other_uuid = uuid_module.UUID(str(request.query_params.get("other") or ""))
        except (TypeError, ValueError):
            return Response({"detail": "Invalid comparison batch UUID."}, status=status.HTTP_400_BAD_REQUEST)
        right = get_object_or_404(self.get_queryset(), uuid=other_uuid)
        return Response(compare_batches(left, right))

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

    @action(detail=True, methods=["get", "post"], url_path="results/(?P<result_uuid>[^/.]+)/calibrations")
    def calibrations(self, request, uuid=None, result_uuid=None):
        """Append independent human verdicts without changing the model result."""

        batch = self.get_object()
        result = get_object_or_404(EvaluationResult, evaluation_run=batch, uuid=result_uuid)
        if request.method == "POST":
            if not result.scores:
                return Response({"detail": "Result has not been scored."}, status=status.HTTP_409_CONFLICT)
            allowed = EvaluationCalibration.Verdict.values
            answer = request.data.get("answer_verdict")
            evidence = request.data.get("evidence_verdict")
            explanation = request.data.get("explanation")
            if (
                answer not in allowed
                or evidence not in allowed
                or not isinstance(explanation, str)
                or not explanation.strip()
            ):
                return Response(
                    {"detail": "Two verdicts and an explanation are required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if len(explanation) > 4000:
                return Response({"detail": "Explanation is too long."}, status=status.HTTP_400_BAD_REQUEST)
            EvaluationCalibration.objects.create(
                result=result, reviewer=request.user, answer_verdict=answer, evidence_verdict=evidence,
                explanation=explanation.strip(), judge_snapshot=result.judge_evidence or {},
                context_snapshot={
                    "source_run_uuid": str(result.source_run_uuid) if result.source_run_uuid else None,
                    "output": result.output_snapshot,
                    "scores": result.scores,
                    "expectation": (batch.config_snapshot or {}).get("case_expectations", {}).get(str(result.case_id)),
                },
            )
        rows = result.calibrations.select_related("reviewer").all()
        return Response([
            {"id": row.pk, "answer_verdict": row.answer_verdict, "evidence_verdict": row.evidence_verdict,
             "explanation": row.explanation, "reviewer": row.reviewer.username if row.reviewer else None,
             "judge_snapshot": row.judge_snapshot, "context_snapshot": row.context_snapshot,
             "created_at": row.created_at} for row in rows
        ], status=status.HTTP_201_CREATED if request.method == "POST" else status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="report")
    def report(self, request, uuid=None):
        """Return the aggregated report for one batch."""

        evaluation_run = self.get_object()
        return Response(build_report(evaluation_run))


class EvaluationMetricViewSet(viewsets.ModelViewSet):
    """Metric configuration used by batch scoring."""

    DEFAULT_METRIC_NAMES = {item["name"] for item in DEFAULT_METRICS}

    permission_classes = [HasRunEvaluationsAccess]
    lookup_field = "uuid"
    serializer_class = EvaluationMetricSerializer
    queryset = EvaluationMetric.objects.order_by("name")

    def get_queryset(self):
        """Keep the built-in catalog materialized for every list request."""

        ensure_default_metrics()
        return super().get_queryset()

    def destroy(self, request, *args, **kwargs):
        """Reject deletion of a built-in metric; disable it instead."""

        metric = self.get_object()
        if metric.name in self.DEFAULT_METRIC_NAMES:
            return Response(
                {"detail": "Built-in metrics cannot be deleted; disable them instead."},
                status=status.HTTP_409_CONFLICT,
            )
        return super().destroy(request, *args, **kwargs)
