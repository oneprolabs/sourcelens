from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("lens", "0067_shared_qa_require_login"),
    ]

    operations = [
        migrations.AddField(
            model_name="lensnode",
            name="pool",
            field=models.CharField(
                choices=[("default", "Default"), ("evaluation", "Evaluation")],
                db_index=True,
                default="default",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="lensnode",
            name="evaluation_concurrency_limit",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="session",
            name="source",
            field=models.CharField(
                choices=[("user", "User"), ("evaluation", "Evaluation")],
                db_default="user",
                db_index=True,
                default="user",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="run",
            name="source",
            field=models.CharField(
                choices=[("user", "User"), ("evaluation", "Evaluation")],
                db_default="user",
                db_index=True,
                default="user",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="EvaluationDataset",
            fields=[
                (
                    "uuid",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("name", models.CharField(max_length=160)),
                ("version", models.PositiveIntegerField(default=1)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("active", "Active"),
                            ("archived", "Archived"),
                        ],
                        default="draft",
                        max_length=16,
                    ),
                ),
                ("config_snapshot", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "assistant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="evaluation_datasets",
                        to="lens.assistant",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_evaluation_datasets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-version"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("assistant", "name", "version"),
                        name="lens_eval_dataset_assistant_name_version_uniq",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="EvaluationCase",
            fields=[
                (
                    "uuid",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("input", models.TextField()),
                ("expected_answer", models.TextField(blank=True, default="")),
                ("expected_facts", models.JSONField(blank=True, default=list)),
                ("expected_sources", models.JSONField(blank=True, default=list)),
                ("tags", models.JSONField(blank=True, default=list)),
                ("config", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "dataset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cases",
                        to="lens.evaluationdataset",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "uuid"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("dataset", "input"),
                        name="lens_eval_case_dataset_input_uniq",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="EvaluationMetric",
            fields=[
                (
                    "uuid",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("name", models.CharField(max_length=120, unique=True)),
                ("category", models.CharField(max_length=32)),
                ("evaluator_type", models.CharField(max_length=32)),
                (
                    "weight",
                    models.DecimalField(decimal_places=4, default=1, max_digits=8),
                ),
                (
                    "threshold",
                    models.DecimalField(
                        blank=True, decimal_places=4, max_digits=8, null=True
                    ),
                ),
                ("config", models.JSONField(blank=True, default=dict)),
                ("enabled", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="EvaluationRun",
            fields=[
                (
                    "uuid",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                ("config_snapshot", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("paused", "Paused"),
                            ("cancelled", "Cancelled"),
                            ("failed", "Failed"),
                            ("completed", "Completed"),
                        ],
                        default="queued",
                        max_length=16,
                    ),
                ),
                ("total_cases", models.PositiveIntegerField(default=0)),
                ("completed_cases", models.PositiveIntegerField(default=0)),
                ("passed_cases", models.PositiveIntegerField(default=0)),
                ("failed_cases", models.PositiveIntegerField(default=0)),
                (
                    "aggregate_score",
                    models.DecimalField(
                        blank=True, decimal_places=4, max_digits=8, null=True
                    ),
                ),
                ("coverage", models.JSONField(blank=True, default=dict)),
                ("error", models.TextField(blank=True, default="")),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "dataset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="evaluation_runs",
                        to="lens.evaluationdataset",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="requested_evaluation_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-uuid"],
                "permissions": [("run_evaluations", "Can run Agent Evaluations")],
            },
        ),
        migrations.CreateModel(
            name="EvaluationResult",
            fields=[
                (
                    "uuid",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        unique=True,
                    ),
                ),
                (
                    "source_run_uuid",
                    models.UUIDField(blank=True, editable=False, null=True),
                ),
                ("output_snapshot", models.TextField(blank=True, default="")),
                ("citations_snapshot", models.JSONField(blank=True, default=list)),
                ("trace_summary", models.JSONField(blank=True, default=dict)),
                ("execution_metrics", models.JSONField(blank=True, default=dict)),
                ("scores", models.JSONField(blank=True, default=dict)),
                ("passed", models.BooleanField(blank=True, null=True)),
                ("issues", models.JSONField(blank=True, default=list)),
                ("judge_evidence", models.JSONField(blank=True, default=dict)),
                ("error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "case",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="results",
                        to="lens.evaluationcase",
                    ),
                ),
                (
                    "evaluation_run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="results",
                        to="lens.evaluationrun",
                    ),
                ),
                (
                    "source_run",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="evaluation_results",
                        to="lens.run",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "uuid"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("evaluation_run", "case"),
                        name="lens_eval_result_run_case_uniq",
                    )
                ],
            },
        ),
    ]
