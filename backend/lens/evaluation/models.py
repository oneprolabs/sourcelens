"""Persistence models for offline Agent Evaluation runs."""

import uuid

from django.conf import settings
from django.db import models


class EvaluationDataset(models.Model):
    """Versioned collection of prompts and expected evaluation evidence."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, unique=True)
    name = models.CharField(max_length=160)
    assistant = models.ForeignKey(
        "lens.Assistant",
        on_delete=models.PROTECT,
        related_name="evaluation_datasets",
    )
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    config_snapshot = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_evaluation_datasets",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["assistant", "name", "version"],
                name="lens_eval_dataset_assistant_name_version_uniq",
            )
        ]


class EvaluationCase(models.Model):
    """One deterministic input and its expected answer contract."""

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, unique=True)
    dataset = models.ForeignKey(
        EvaluationDataset,
        on_delete=models.CASCADE,
        related_name="cases",
    )
    input = models.TextField()
    expected_answer = models.TextField(blank=True, default="")
    expected_facts = models.JSONField(default=list, blank=True)
    expected_sources = models.JSONField(default=list, blank=True)
    tags = models.JSONField(default=list, blank=True)
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "uuid"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "input"],
                name="lens_eval_case_dataset_input_uniq",
            )
        ]


class EvaluationMetric(models.Model):
    """Configurable weighted dimension used to score an evaluation result."""

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, unique=True)
    name = models.CharField(max_length=120, unique=True)
    category = models.CharField(max_length=32)
    evaluator_type = models.CharField(max_length=32)
    weight = models.DecimalField(max_digits=8, decimal_places=4, default=1)
    threshold = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    config = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class EvaluationRun(models.Model):
    """Batch execution state and immutable runtime configuration snapshot."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        PAUSED = "paused", "Paused"
        CANCELLED = "cancelled", "Cancelled"
        FAILED = "failed", "Failed"
        COMPLETED = "completed", "Completed"

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, unique=True)
    dataset = models.ForeignKey(
        EvaluationDataset,
        on_delete=models.PROTECT,
        related_name="evaluation_runs",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requested_evaluation_runs",
    )
    config_snapshot = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    total_cases = models.PositiveIntegerField(default=0)
    completed_cases = models.PositiveIntegerField(default=0)
    passed_cases = models.PositiveIntegerField(default=0)
    failed_cases = models.PositiveIntegerField(default=0)
    aggregate_score = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    coverage = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-uuid"]
        permissions = [
            ("run_evaluations", "Can run Agent Evaluations"),
        ]


class EvaluationResult(models.Model):
    """One case result with a durable snapshot independent of source Run retention."""

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, unique=True)
    evaluation_run = models.ForeignKey(
        EvaluationRun,
        on_delete=models.CASCADE,
        related_name="results",
    )
    case = models.ForeignKey(
        EvaluationCase,
        on_delete=models.PROTECT,
        related_name="results",
    )
    source_run = models.ForeignKey(
        "lens.Run",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="evaluation_results",
    )
    source_run_uuid = models.UUIDField(null=True, blank=True, editable=False)
    output_snapshot = models.TextField(blank=True, default="")
    citations_snapshot = models.JSONField(default=list, blank=True)
    trace_summary = models.JSONField(default=dict, blank=True)
    execution_metrics = models.JSONField(default=dict, blank=True)
    scores = models.JSONField(default=dict, blank=True)
    passed = models.BooleanField(null=True, blank=True)
    issues = models.JSONField(default=list, blank=True)
    judge_evidence = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "uuid"]
        constraints = [
            models.UniqueConstraint(
                fields=["evaluation_run", "case"],
                name="lens_eval_result_run_case_uniq",
            )
        ]

    def save(self, *args, **kwargs):
        """Retain the source UUID even after the source Run is deleted."""

        if self.source_run_id and not self.source_run_uuid:
            self.source_run_uuid = self.source_run.uuid
        super().save(*args, **kwargs)
