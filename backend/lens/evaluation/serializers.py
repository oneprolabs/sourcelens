"""Serializers for the Agent Evaluation management API."""

from rest_framework import serializers

from lens.evaluation.models import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationMetric,
    EvaluationResult,
    EvaluationRun,
)
from lens.evaluation.scoring import EVALUATION_SCORING_VERSION
from lens.models import Assistant

CASE_LIST_FIELDS = (
    "uuid",
    "input",
    "expected_answer",
    "expected_facts",
    "expected_sources",
    "tags",
    "config",
    "created_at",
    "updated_at",
)


class EvaluationCaseSerializer(serializers.ModelSerializer):
    """One evaluation case with its expected answer contract."""

    class Meta:
        model = EvaluationCase
        fields = CASE_LIST_FIELDS
        read_only_fields = ("uuid", "created_at", "updated_at")

    def validate_input(self, value):
        """Reject blank inputs before they reach the unique constraint."""

        text = (value or "").strip()
        if not text:
            raise serializers.ValidationError("input is required")
        return text

    def validate(self, attrs):
        """Reject duplicate inputs inside one dataset."""

        dataset = attrs.get("dataset") or self.context.get("dataset") or getattr(self.instance, "dataset", None)
        text = attrs.get("input") or getattr(self.instance, "input", "")
        if dataset is not None and text:
            duplicates = dataset.cases.filter(input=text)
            if self.instance is not None:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                raise serializers.ValidationError({"input": "duplicate input in this dataset"})
        return attrs


class EvaluationDatasetSerializer(serializers.ModelSerializer):
    """Versioned dataset with its assistant binding and case count."""

    assistant = serializers.SlugRelatedField(
        slug_field="uuid",
        queryset=Assistant.objects.all(),
    )
    assistant_name = serializers.CharField(source="assistant.name", read_only=True)
    created_by_email = serializers.CharField(
        source="created_by.email",
        read_only=True,
        allow_null=True,
    )
    case_count = serializers.SerializerMethodField()

    class Meta:
        model = EvaluationDataset
        fields = (
            "uuid",
            "name",
            "assistant",
            "assistant_name",
            "version",
            "status",
            "case_count",
            "config_snapshot",
            "created_by",
            "created_by_email",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "uuid",
            "version",
            "created_by",
            "created_by_email",
            "case_count",
            "created_at",
            "updated_at",
        )
        validators = []

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        fields["assistant"].queryset = (
            Assistant.objects.visible_to(request.user) if request else Assistant.objects.none()
        )
        return fields

    def get_case_count(self, obj):
        """Return the annotated count or count the relation on create."""

        annotated = getattr(obj, "case_count", None)
        if annotated is not None:
            return annotated
        return obj.cases.count()

    def create(self, validated_data):
        """Create the next version of a named dataset."""

        assistant = validated_data["assistant"]
        name = validated_data["name"]
        latest = (
            EvaluationDataset.objects.filter(assistant=assistant, name=name)
            .order_by("-version")
            .values_list("version", flat=True)
            .first()
        )
        validated_data["version"] = (latest or 0) + 1
        return super().create(validated_data)


class EvaluationMetricSerializer(serializers.ModelSerializer):
    """Configurable weighted dimension used to score results."""

    def validate(self, attrs):
        evaluator = attrs.get("evaluator_type", getattr(self.instance, "evaluator_type", None))
        weight = attrs.get("weight", getattr(self.instance, "weight", 0))
        threshold = attrs.get("threshold", getattr(self.instance, "threshold", None))
        config = attrs.get("config", getattr(self.instance, "config", {}))
        if evaluator not in {"outcome", "tool_success", "citation_integrity", "groundedness", "latency"}:
            raise serializers.ValidationError({"evaluator_type": "Unsupported evaluator type."})
        if weight is not None and weight < 0:
            raise serializers.ValidationError({"weight": "Must be non-negative."})
        if threshold is not None and not 0 <= threshold <= 1:
            raise serializers.ValidationError({"threshold": "Must be between 0 and 1."})
        if not isinstance(config, dict):
            raise serializers.ValidationError({"config": "Must be an object."})
        if evaluator == "latency" and "budget_seconds" in config:
            budget = config["budget_seconds"]
            if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget <= 0:
                raise serializers.ValidationError({"config": "budget_seconds must be positive."})
        return attrs

    class Meta:
        model = EvaluationMetric
        fields = (
            "uuid",
            "name",
            "category",
            "evaluator_type",
            "weight",
            "threshold",
            "config",
            "enabled",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("uuid", "created_at", "updated_at")


class EvaluationResultListSerializer(serializers.ModelSerializer):
    """One case result without the full frozen trace payload."""

    case_uuid = serializers.UUIDField(source="case.uuid", read_only=True)
    case_input = serializers.SerializerMethodField()
    source_run = serializers.UUIDField(source="source_run_uuid", read_only=True)
    output_excerpt = serializers.SerializerMethodField()

    class Meta:
        model = EvaluationResult
        fields = (
            "uuid",
            "case_uuid",
            "case_input",
            "source_run",
            "passed",
            "scores",
            "issues",
            "execution_metrics",
            "output_excerpt",
            "error",
            "created_at",
            "updated_at",
        )

    def get_case_input(self, obj):
        """Show the input captured for the batch, with a legacy fallback."""

        expectations = (obj.evaluation_run.config_snapshot or {}).get("case_expectations", {})
        return (expectations.get(str(obj.case_id)) or {}).get("input", obj.case.input)

    def get_output_excerpt(self, obj):
        """Return a bounded excerpt so list payloads stay small."""

        return (obj.output_snapshot or "")[:600]


class EvaluationResultDetailSerializer(EvaluationResultListSerializer):
    """One case result with the full frozen snapshot and trace summary."""

    baseline = serializers.SerializerMethodField()

    def get_baseline(self, obj):
        """Return the baseline frozen when this evaluation batch was created."""

        expectations = (obj.evaluation_run.config_snapshot or {}).get("case_expectations", {})
        return (expectations.get(str(obj.case_id)) or {}).get("baseline")

    class Meta(EvaluationResultListSerializer.Meta):
        fields = EvaluationResultListSerializer.Meta.fields + (
            "output_snapshot",
            "citations_snapshot",
            "trace_summary",
            "judge_evidence",
            "baseline",
        )


class _EvaluationRunBaseSerializer(serializers.ModelSerializer):
    """Shared run fields for list and detail payloads."""

    dataset_uuid = serializers.UUIDField(source="dataset.uuid", read_only=True)
    dataset_name = serializers.CharField(source="dataset.name", read_only=True)
    dataset_version = serializers.IntegerField(
        source="dataset.version",
        read_only=True,
    )
    progress = serializers.SerializerMethodField()
    legacy_overall = serializers.SerializerMethodField()

    class Meta:
        model = EvaluationRun
        fields = (
            "uuid",
            "dataset_uuid",
            "dataset_name",
            "dataset_version",
            "status",
            "total_cases",
            "completed_cases",
            "passed_cases",
            "failed_cases",
            "aggregate_score",
            "coverage",
            "progress",
            "legacy_overall",
            "error",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_progress(self, obj):
        """Return completion as a 0-100 integer for the UI."""

        total = obj.total_cases or 0
        if total <= 0:
            return 0
        return min(int((obj.completed_cases or 0) * 100 / total), 100)

    def get_legacy_overall(self, obj):
        """Return True when the batch was scored under an older version."""

        snapshot = obj.config_snapshot or {}
        return str(snapshot.get("scoring_version") or "") != EVALUATION_SCORING_VERSION


class EvaluationRunListSerializer(_EvaluationRunBaseSerializer):
    """Batch summary used by the experiments table."""


class EvaluationRunDetailSerializer(_EvaluationRunBaseSerializer):
    """Batch detail including the frozen configuration snapshot."""

    class Meta(_EvaluationRunBaseSerializer.Meta):
        fields = _EvaluationRunBaseSerializer.Meta.fields + ("config_snapshot",)
        read_only_fields = fields
