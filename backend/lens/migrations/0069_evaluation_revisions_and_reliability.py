import hashlib
import json
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

DEFAULT_METRICS = (
    {
        "name": "answer_correctness",
        "category": "correctness",
        "evaluator_type": "answer_correctness",
        "weight": Decimal("0.4000"),
        "threshold": Decimal("1.0000"),
        "config": {},
    },
    {
        "name": "citation_integrity",
        "category": "groundedness",
        "evaluator_type": "citation_integrity",
        "weight": Decimal("0.1500"),
        "threshold": Decimal("0.8000"),
        "config": {},
    },
    {
        "name": "evidence_verified",
        "category": "groundedness",
        "evaluator_type": "groundedness",
        "weight": Decimal("0.1000"),
        "threshold": Decimal("1.0000"),
        "config": {},
    },
    {
        "name": "execution_success",
        "category": "reliability",
        "evaluator_type": "outcome",
        "weight": Decimal("0.1500"),
        "threshold": Decimal("1.0000"),
        "config": {},
    },
    {
        "name": "tool_success_rate",
        "category": "reliability",
        "evaluator_type": "tool_success",
        "weight": Decimal("0.1500"),
        "threshold": Decimal("0.9000"),
        "config": {},
    },
    {
        "name": "latency_budget",
        "category": "efficiency",
        "evaluator_type": "latency",
        "weight": Decimal("0.0500"),
        "threshold": Decimal("0.5000"),
        "config": {"budget_seconds": 600},
    },
)


def backfill_current_revisions(apps, schema_editor):
    """Create the first immutable revision for every existing dataset."""

    Dataset = apps.get_model("lens", "EvaluationDataset")
    Revision = apps.get_model("lens", "EvaluationDatasetRevision")
    Case = apps.get_model("lens", "EvaluationCase")
    for dataset in Dataset.objects.all().iterator():
        cases = [
            {
                "uuid": str(case.uuid),
                "input": case.input,
                "expected_answer": case.expected_answer,
                "expected_facts": case.expected_facts,
                "expected_sources": case.expected_sources,
                "tags": case.tags,
                "config": case.config,
            }
            for case in Case.objects.filter(dataset=dataset).order_by("created_at", "uuid")
        ]
        metadata = {
            "name": dataset.name,
            "status": dataset.status,
            "version": dataset.version,
            "assistant_uuid": str(dataset.assistant.uuid),
            "config_snapshot": dataset.config_snapshot,
        }
        Revision.objects.create(dataset=dataset, revision=1, metadata=metadata, cases=cases)


def backfill_revision_summaries(apps, schema_editor):
    """Populate case counts and content hashes for existing revisions."""

    Revision = apps.get_model("lens", "EvaluationDatasetRevision")
    for revision in Revision.objects.all().iterator():
        payload = json.dumps(
            {"metadata": revision.metadata, "cases": revision.cases},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        revision.case_count = len(revision.cases or [])
        revision.content_hash = hashlib.sha256(payload.encode()).hexdigest()
        revision.save(update_fields=["case_count", "content_hash"])


def seed_default_metrics(apps, schema_editor):
    """Make the built-in metric catalog editable instead of implicit."""

    Metric = apps.get_model("lens", "EvaluationMetric")
    for item in DEFAULT_METRICS:
        Metric.objects.get_or_create(
            name=item["name"],
            defaults={
                "category": item["category"],
                "evaluator_type": item["evaluator_type"],
                "weight": item["weight"],
                "threshold": item["threshold"],
                "config": item["config"],
                "enabled": True,
            },
        )


def preserve_legacy_answer_modes(apps, schema_editor):
    """Keep existing reference answers exact while leaving new cases unchanged."""

    Case = apps.get_model("lens", "EvaluationCase")
    Dataset = apps.get_model("lens", "EvaluationDataset")
    Revision = apps.get_model("lens", "EvaluationDatasetRevision")
    EvaluationRun = apps.get_model("lens", "EvaluationRun")
    changed = set()
    dataset_ids = set()
    for case in Case.objects.exclude(expected_answer="").iterator():
        if not isinstance(case.config, dict):
            continue
        config = case.config
        if "answer_match_mode" in config:
            continue
        case.config = {**config, "answer_match_mode": "exact"}
        case.save(update_fields=["config"])
        changed.add(str(case.pk))
        dataset_ids.add(case.dataset_id)

    for batch in EvaluationRun.objects.filter(status__in=["queued", "running", "paused"]).iterator():
        snapshot = batch.config_snapshot or {}
        expectations = snapshot.get("case_expectations") or {}
        updated = False
        for case_id in changed.intersection(expectations):
            expectation = expectations[case_id]
            if isinstance(expectation, dict) and expectation.get("answer_match_mode") is None:
                expectation["answer_match_mode"] = "exact"
                updated = True
        if updated:
            batch.config_snapshot = snapshot
            batch.save(update_fields=["config_snapshot"])

    for dataset in Dataset.objects.filter(pk__in=dataset_ids).iterator():
        cases = [
            {
                "uuid": str(case.uuid),
                "input": case.input,
                "expected_answer": case.expected_answer,
                "expected_facts": case.expected_facts,
                "expected_sources": case.expected_sources,
                "tags": case.tags,
                "config": case.config,
            }
            for case in Case.objects.filter(dataset_id=dataset.pk).order_by("created_at", "uuid")
        ]
        metadata = {
            "name": dataset.name,
            "status": dataset.status,
            "version": dataset.version,
            "assistant_uuid": str(dataset.assistant_id),
            "config_snapshot": dataset.config_snapshot,
        }
        payload = json.dumps(
            {"metadata": metadata, "cases": cases}, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        previous = Revision.objects.filter(dataset_id=dataset.pk).order_by("-revision").first()
        Revision.objects.create(
            dataset_id=dataset.pk,
            revision=(previous.revision if previous else 0) + 1,
            metadata=metadata,
            cases=cases,
            case_count=len(cases),
            content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        )


class Migration(migrations.Migration):

    dependencies = [
        ("lens", "0068_evaluation_foundation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="evaluationresult",
            name="dispatch_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="evaluationrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("paused", "Paused"),
                    ("cancelled", "Cancelled"),
                    ("failed", "Failed"),
                    ("completed", "Completed"),
                ],
                db_index=True,
                default="queued",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="EvaluationCalibration",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "answer_verdict",
                    models.CharField(
                        choices=[
                            ("pass", "Pass"),
                            ("fail", "Fail"),
                            ("inconclusive", "Inconclusive"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "evidence_verdict",
                    models.CharField(
                        choices=[
                            ("pass", "Pass"),
                            ("fail", "Fail"),
                            ("inconclusive", "Inconclusive"),
                        ],
                        max_length=16,
                    ),
                ),
                ("explanation", models.TextField()),
                ("judge_snapshot", models.JSONField(default=dict)),
                ("context_snapshot", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "result",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="calibrations",
                        to="lens.evaluationresult",
                    ),
                ),
                (
                    "reviewer",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.CreateModel(
            name="EvaluationDatasetRevision",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("revision", models.PositiveIntegerField()),
                ("metadata", models.JSONField(default=dict)),
                ("cases", models.JSONField(default=list)),
                ("case_count", models.PositiveIntegerField(default=0)),
                (
                    "content_hash",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "dataset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="revisions",
                        to="lens.evaluationdataset",
                    ),
                ),
            ],
            options={
                "ordering": ["-revision"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("dataset", "revision"),
                        name="lens_eval_dataset_revision_uniq",
                    )
                ],
            },
        ),
        migrations.RunPython(backfill_current_revisions, migrations.RunPython.noop),
        migrations.RunPython(backfill_revision_summaries, migrations.RunPython.noop),
        migrations.RunPython(seed_default_metrics, migrations.RunPython.noop),
        migrations.RunPython(preserve_legacy_answer_modes, migrations.RunPython.noop),
    ]
