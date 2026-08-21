from contextlib import contextmanager
from datetime import timedelta
import hashlib
from importlib import import_module
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from agentcore_task.adapters.django.models import TaskExecution
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from django_celery_beat.models import PeriodicTask

from core.asgi import application
from core.management.commands.register_periodic_tasks import (
    discover_and_register,
)
from core.periodic_registry import TASK_REGISTRY
from lens.consumers import LensNodeConsumer
from lens.datasource_services import (
    DataSourceDispatchError,
    dispatch_datasource_conversion_async,
    dispatch_datasource_sync_async,
)
from lens.execution import execute_answer_run
from lens.lensnode_auth import issue_lensnode_token
from lens.models import (
    Assistant,
    AssistantSkill,
    DataSource,
    GlobalSetting,
    LensNode,
    Message,
    MessageAttachment,
    Run,
    RunOutputFile,
    RunStep,
    ScheduledTask,
    Session,
    Skill,
)
from lens.periodic_tasks import (
    ensure_datasource_periodic_task,
    register_periodic_tasks,
)
from lens.runtime_events import (
    public_step_detail,
    sanitize_runtime_event,
    sanitize_termination_detail,
)
from lens.serializers import MessageSerializer, RunSerializer
from lens.services import (
    _build_sync_event,
    _step_sequence,
    build_clarification_continuation_question,
    append_lensnode_output,
    build_run_history,
    build_run_history_artifacts,
    create_execution_run,
    create_run_execution_snapshot,
    dispatch_run_to_lensnode,
    finish_lensnode_run,
    rewrite_query,
    run_timeout_for_rounds,
)
from lens.tasks import (
    acquire_datasource_lock,
    cleanup_stale_datasource_sync_tasks,
    complete_datasource_conversion_task,
    complete_datasource_sync_task,
    datasource_conversion_task,
    datasource_lock,
    lensnode_health_task,
    register_datasource_conversion_task,
    reconcile_orphaned_datasource_conversions,
    register_datasource_sync_task,
    release_datasource_lock,
    source_sync_task,
)

User = get_user_model()

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    },
}

TEST_CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    },
}


@override_settings(CACHES=TEST_CACHES, CHANNEL_LAYERS=TEST_CHANNEL_LAYERS)
class LensServiceTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="lens-user",
            email="lens-user@example.com",
            password="pass12345",
        )
        self.lensnode = LensNode.objects.create(
            name="Local LensNode",
            status=LensNode.Status.ONLINE,
            enrollment_status=LensNode.EnrollmentStatus.APPROVED,
            workspace_path="/workspace",
            available_dirs=[{"path": "/workspace/repo"}],
            tasks=[
                {
                    "name": "knowledge_qa",
                    "description": "Answer code questions",
                }
            ],
        )
        self.assistant = Assistant.objects.create(
            name="Code Advisor",
            slug="code-advisor",
            lensnode=self.lensnode,
            selected_task="knowledge_qa",
            selected_dirs=[{"path": "/workspace/repo"}],
        )
        self.datasource = DataSource.objects.create(
            name="Repo Cache",
            source_type="git",
            lensnode=self.lensnode,
            config={"repo_url": "https://example.com/repo.git"},
            sync_policy={"interval_seconds": 3600},
            target_path="/workspace/repo-cache",
        )
        self.session = Session.objects.create(
            assistant=self.assistant,
            user=self.user,
            title="",
        )

    def test_run_timeout_is_independent_of_execution_strategy(self):
        for agent_rounds in ("flash", "fast", "balanced", "deep", "max"):
            with self.subTest(agent_rounds=agent_rounds):
                self.assertEqual(
                    run_timeout_for_rounds(agent_rounds),
                    3600,
                )

    def test_explicit_language_request_overrides_profile_language(self):
        self.user.profile.language = "en-US"
        self.user.profile.save(update_fields=["language"])

        run = create_execution_run(
            session=self.session,
            question="请用中文回答，可以吗？",
            enqueue=False,
        )

        self.assertEqual(
            run.execution.runtime_snapshot["answer_language"],
            "zh-CN",
        )

    def test_follow_up_inherits_latest_resolved_language(self):
        self.user.profile.language = "en-US"
        self.user.profile.save(update_fields=["language"])
        first = create_execution_run(
            session=self.session,
            question="Please answer in Chinese.",
            enqueue=False,
        )

        follow_up = create_execution_run(
            session=self.session,
            question="继续",
            enqueue=False,
        )

        self.assertEqual(
            first.execution.runtime_snapshot["answer_language"],
            "zh-CN",
        )
        self.assertEqual(
            follow_up.execution.runtime_snapshot["answer_language"],
            "zh-CN",
        )

    def test_retry_inherits_original_run_language(self):
        self.user.profile.language = "en-US"
        self.user.profile.save(update_fields=["language"])
        original = create_execution_run(
            session=self.session,
            question="请用中文回答。",
            enqueue=False,
        )
        self.user.profile.language = "en-US"
        self.user.profile.save(update_fields=["language"])

        retry = create_execution_run(
            session=self.session,
            question="Retry this answer in English.",
            retry_of_run=original,
            enqueue=False,
        )

        self.assertEqual(
            retry.execution.runtime_snapshot["answer_language"],
            "zh-CN",
        )

    def test_unrelated_message_language_does_not_override_profile(self):
        self.user.profile.language = "en-US"
        self.user.profile.save(update_fields=["language"])

        run = create_execution_run(
            session=self.session,
            question="请分析这份中文文档。",
            enqueue=False,
        )

        self.assertEqual(
            run.execution.runtime_snapshot["answer_language"],
            "en-US",
        )

    def test_timeout_migration_backfills_from_current_assistant(self):
        self.assistant.agent_rounds = Assistant.AgentRounds.MAX
        self.assistant.save(update_fields=["agent_rounds"])
        run = create_execution_run(
            session=self.session,
            question="Analyze everything",
            enqueue=False,
        )
        run.execution.agent_rounds = None
        run.execution.run_timeout_s = None
        run.execution.save(update_fields=["agent_rounds", "run_timeout_s"])
        migration = import_module(
            "lens.migrations."
            "0028_runexecution_agent_rounds_runexecution_run_timeout_s"
        )

        migration.backfill_run_timeout_snapshots(apps, None)
        run.execution.refresh_from_db()

        self.assertEqual(run.execution.agent_rounds, "max")
        self.assertEqual(run.execution.run_timeout_s, 3600)

    def test_run_step_sequences_are_distinct_for_structured_steps(self):
        step_types = [
            RunStep.StepType.QUERY_REWRITE,
            RunStep.StepType.MULTIMODAL,
            RunStep.StepType.RETRIEVAL,
            RunStep.StepType.GENERAL_CHAT,
            RunStep.StepType.ANSWER,
            RunStep.StepType.STREAM,
        ]

        sequences = [_step_sequence(step_type) for step_type in step_types]

        self.assertEqual(len(sequences), len(set(sequences)))

    def test_create_execution_run_creates_queued_run_with_lensnode(self):
        run = create_execution_run(
            session=self.session,
            question="How does SSE work?",
            idempotency_key="real-run",
            enqueue=False,
        )

        self.assertEqual(run.status, "queued")
        self.assertEqual(run.lensnode, self.lensnode)
        self.assertEqual(run.input_message.role, Message.Role.USER)
        self.assertEqual(run.output_message.role, Message.Role.ASSISTANT)
        self.assertEqual(self.session.message_set.count(), 2)
        self.assertEqual(run.execution.status, "queued")
        self.assertEqual(run.execution.agent_rounds, "balanced")
        self.assertEqual(run.execution.run_timeout_s, 3600)

    def test_build_run_history_returns_prior_turns_and_skips_empty(self):
        run1 = create_execution_run(
            session=self.session, question="q1", enqueue=False
        )
        run1.output_message.content = "a1"
        run1.output_message.save(update_fields=["content"])
        run1.status = Run.Status.DONE
        run1.outcome = Run.Outcome.COMPLETED
        run1.save(update_fields=["status", "outcome"])
        # second turn left unanswered -> its empty answer must be skipped
        create_execution_run(
            session=self.session, question="q2", enqueue=False
        )
        current = create_execution_run(
            session=self.session, question="q3", enqueue=False
        )

        history = build_run_history(current)

        self.assertEqual(
            history,
            [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "q2"},
            ],
        )

    def test_build_run_history_artifacts_returns_trusted_deliverable(self):
        from django.core.files.base import ContentFile

        content = b"# Original report\nFull source content"
        prior = create_execution_run(
            session=self.session,
            question="Create the report",
            enqueue=False,
        )
        prior.status = Run.Status.DONE
        prior.outcome = Run.Outcome.COMPLETED
        prior.save(update_fields=["status", "outcome"])
        output = RunOutputFile(
            run=prior,
            message=prior.output_message,
            session=self.session,
            assistant=self.assistant,
            filename="report.md",
            content_type="text/markdown",
            byte_size=len(content),
            content_hash=hashlib.sha256(content).hexdigest(),
        )
        output.file.save(
            "report.md",
            ContentFile(content),
            save=False,
        )
        output.save()
        current = create_execution_run(
            session=self.session,
            question="Translate the previous file",
            enqueue=False,
        )

        try:
            artifacts = build_run_history_artifacts(current)
        finally:
            output.file.delete(save=False)

        self.assertEqual(
            artifacts,
            [
                {
                    "uuid": str(output.uuid),
                    "filename": "report.md",
                    "content_type": "text/markdown",
                    "byte_size": len(content),
                    "content_hash": hashlib.sha256(content).hexdigest(),
                    "source_run_uuid": str(prior.uuid),
                }
            ],
        )

    def test_build_run_history_artifacts_excludes_blocked_run(self):
        prior = create_execution_run(
            session=self.session,
            question="Create the report",
            enqueue=False,
        )
        prior.status = Run.Status.DONE
        prior.outcome = Run.Outcome.BLOCKED
        prior.save(update_fields=["status", "outcome"])
        RunOutputFile.objects.create(
            run=prior,
            message=prior.output_message,
            session=self.session,
            assistant=self.assistant,
            filename="partial.md",
            content_type="text/markdown",
            byte_size=10,
            content_hash="a" * 64,
        )
        current = create_execution_run(
            session=self.session,
            question="Continue",
            enqueue=False,
        )

        self.assertEqual(build_run_history_artifacts(current), [])

    def test_build_run_history_skips_capability_unavailable_answer(self):
        blocked = create_execution_run(
            session=self.session,
            question="Create an order",
            enqueue=False,
        )
        blocked.output_message.content = "Capability unavailable"
        blocked.output_message.save(update_fields=["content"])
        finish_lensnode_run(
            blocked.uuid,
            Run.Status.DONE,
            outcome=Run.Outcome.BLOCKED,
            termination_detail={
                "reason": "capability_unavailable",
                "capability": "skill",
            },
        )
        current = create_execution_run(
            session=self.session,
            question="List orders",
            enqueue=False,
        )

        history = build_run_history(current)

        self.assertEqual(
            history,
            [{"role": "user", "content": "Create an order"}],
        )

    def test_build_run_history_ignores_blocked_run_without_output(self):
        answered = create_execution_run(
            session=self.session,
            question="Explain order states",
            enqueue=False,
        )
        answered.output_message.content = "Order states explained"
        answered.output_message.save(update_fields=["content"])
        answered.status = Run.Status.DONE
        answered.outcome = Run.Outcome.COMPLETED
        answered.save(update_fields=["status", "outcome"])
        blocked = create_execution_run(
            session=self.session,
            question="Create an order",
            enqueue=False,
        )
        blocked.status = Run.Status.DONE
        blocked.outcome = Run.Outcome.BLOCKED
        blocked.termination_detail = {"reason": "capability_unavailable"}
        blocked.save(update_fields=["status", "outcome", "termination_detail"])
        blocked.output_message.delete()
        current = create_execution_run(
            session=self.session,
            question="Continue",
            enqueue=False,
        )

        history = build_run_history(current)

        self.assertEqual(
            history,
            [
                {"role": "user", "content": "Explain order states"},
                {"role": "assistant", "content": "Order states explained"},
                {"role": "user", "content": "Create an order"},
            ],
        )

    def test_build_run_history_excludes_untrusted_assistant_outputs(self):
        cases = [
            (Run.Status.DONE, Run.Outcome.BLOCKED),
            (Run.Status.FAILED, Run.Outcome.BLOCKED),
            (Run.Status.CANCELLED, Run.Outcome.PARTIAL),
        ]
        expected = []
        for index, (status, outcome) in enumerate(cases, start=1):
            prior = create_execution_run(
                session=self.session,
                question=f"question {index}",
                enqueue=False,
            )
            prior.output_message.content = f"partial answer {index}"
            prior.output_message.save(update_fields=["content"])
            prior.status = status
            prior.outcome = outcome
            prior.save(update_fields=["status", "outcome"])
            expected.append({"role": "user", "content": f"question {index}"})
        current = create_execution_run(
            session=self.session,
            question="follow up",
            enqueue=False,
        )

        history = build_run_history(current)

        self.assertEqual(history, expected)

    def test_build_run_history_collapses_retry_chain_to_latest_attempt(self):
        first = create_execution_run(
            session=self.session,
            question="same question",
            enqueue=False,
        )
        first.status = Run.Status.DONE
        first.outcome = Run.Outcome.BLOCKED
        first.save(update_fields=["status", "outcome"])
        second = create_execution_run(
            session=self.session,
            question="same question",
            retry_of_run=first,
            enqueue=False,
        )
        second.status = Run.Status.FAILED
        second.outcome = Run.Outcome.BLOCKED
        second.save(update_fields=["status", "outcome"])
        third = create_execution_run(
            session=self.session,
            question="same question",
            retry_of_run=second,
            enqueue=False,
        )
        third.output_message.content = "fresh completed answer"
        third.output_message.save(update_fields=["content"])
        third.status = Run.Status.DONE
        third.outcome = Run.Outcome.COMPLETED
        third.save(update_fields=["status", "outcome"])
        current = create_execution_run(
            session=self.session,
            question="follow up",
            enqueue=False,
        )

        history = build_run_history(current)

        self.assertEqual(
            history,
            [
                {"role": "user", "content": "same question"},
                {"role": "assistant", "content": "fresh completed answer"},
            ],
        )

    def test_retry_history_stops_before_the_original_run(self):
        context = create_execution_run(
            session=self.session,
            question="context question",
            enqueue=False,
        )
        context.output_message.content = "context answer"
        context.output_message.save(update_fields=["content"])
        context.status = Run.Status.DONE
        context.outcome = Run.Outcome.COMPLETED
        context.save(update_fields=["status", "outcome"])
        original = create_execution_run(
            session=self.session,
            question="regenerate this",
            enqueue=False,
        )
        original.output_message.content = "original answer"
        original.output_message.save(update_fields=["content"])
        original.status = Run.Status.DONE
        original.outcome = Run.Outcome.COMPLETED
        original.save(update_fields=["status", "outcome"])
        later = create_execution_run(
            session=self.session,
            question="later question",
            enqueue=False,
        )
        later.output_message.content = "later answer"
        later.output_message.save(update_fields=["content"])
        later.status = Run.Status.DONE
        later.outcome = Run.Outcome.COMPLETED
        later.save(update_fields=["status", "outcome"])
        retry = create_execution_run(
            session=self.session,
            question="regenerate this",
            retry_of_run=original,
            enqueue=False,
        )

        history = build_run_history(retry)

        self.assertEqual(
            history,
            [
                {"role": "user", "content": "context question"},
                {"role": "assistant", "content": "context answer"},
            ],
        )

    def test_retry_history_resolves_a_long_chain_in_one_query(self):
        original = create_execution_run(
            session=self.session,
            question="original",
            enqueue=False,
        )
        previous = original
        for index in range(5):
            previous = create_execution_run(
                session=self.session,
                question=f"retry {index}",
                retry_of_run=previous,
                enqueue=False,
            )
        current = create_execution_run(
            session=self.session,
            question="latest retry",
            retry_of_run=previous,
            enqueue=False,
        )

        # One query walks the retry chain; the second reads the admin-tunable
        # history budget from GlobalSetting (fixed cost, not N+1).
        with self.assertNumQueries(2):
            history = build_run_history(current)

        self.assertEqual(history, [])

    def test_build_run_history_preserves_manual_identical_turns(self):
        for answer in ("first answer", "second answer"):
            prior = create_execution_run(
                session=self.session,
                question="same text",
                enqueue=False,
            )
            prior.output_message.content = answer
            prior.output_message.save(update_fields=["content"])
            prior.status = Run.Status.DONE
            prior.outcome = Run.Outcome.COMPLETED
            prior.save(update_fields=["status", "outcome"])
        current = create_execution_run(
            session=self.session,
            question="follow up",
            enqueue=False,
        )

        history = build_run_history(current)

        self.assertEqual(
            history,
            [
                {"role": "user", "content": "same text"},
                {"role": "assistant", "content": "first answer"},
                {"role": "user", "content": "same text"},
                {"role": "assistant", "content": "second answer"},
            ],
        )

    def test_history_limits_apply_after_untrusted_outputs_are_filtered(self):
        completed = create_execution_run(
            session=self.session,
            question="trusted question",
            enqueue=False,
        )
        completed.output_message.content = "trusted answer"
        completed.output_message.save(update_fields=["content"])
        completed.status = Run.Status.DONE
        completed.outcome = Run.Outcome.COMPLETED
        completed.save(update_fields=["status", "outcome"])
        for index in range(4):
            blocked = create_execution_run(
                session=self.session,
                question=f"blocked question {index}",
                enqueue=False,
            )
            blocked.output_message.content = "x" * 8000
            blocked.output_message.save(update_fields=["content"])
            blocked.status = Run.Status.DONE
            blocked.outcome = Run.Outcome.BLOCKED
            blocked.save(update_fields=["status", "outcome"])
        current = create_execution_run(
            session=self.session,
            question="follow up",
            enqueue=False,
        )

        history = build_run_history(current)

        self.assertEqual(
            history[0:2],
            [
                {"role": "user", "content": "trusted question"},
                {"role": "assistant", "content": "trusted answer"},
            ],
        )
        self.assertEqual(
            [item["content"] for item in history[2:]],
            [f"blocked question {index}" for index in range(4)],
        )

    def test_history_budget_override_from_globalsetting(self):
        GlobalSetting.objects.create(
            key="lens.history_budget",
            value={
                "pairs": 1,
                "message_chars": 300,
                "total_chars": 2000,
            },
            description="",
        )
        for index in range(3):
            prior = create_execution_run(
                session=self.session,
                question=f"prior question {index} " + "x" * 2000,
                enqueue=False,
            )
            prior.output_message.content = f"prior answer {index}"
            prior.output_message.save(update_fields=["content"])
            prior.status = Run.Status.DONE
            prior.outcome = Run.Outcome.COMPLETED
            prior.save(update_fields=["status", "outcome"])
        current = create_execution_run(
            session=self.session,
            question="follow up",
            enqueue=False,
        )

        history = build_run_history(current)

        # message_chars=300 caps the long question, pairs=1 keeps only
        # the newest turn.
        self.assertEqual(
            history,
            [
                {
                    "role": "user",
                    "content": "prior question 2 " + "x" * (300 - 17),
                },
                {"role": "assistant", "content": "prior answer 2"},
            ],
        )

    def test_history_budget_clamps_invalid_globalsetting(self):
        GlobalSetting.objects.create(
            key="lens.history_budget",
            value={"pairs": "nope", "message_chars": -5, "total_chars": []},
            description="",
        )
        prior = create_execution_run(
            session=self.session,
            question="x" * 4000,
            enqueue=False,
        )
        prior.output_message.content = "answer"
        prior.output_message.save(update_fields=["content"])
        prior.status = Run.Status.DONE
        prior.outcome = Run.Outcome.COMPLETED
        prior.save(update_fields=["status", "outcome"])
        current = create_execution_run(
            session=self.session,
            question="follow up",
            enqueue=False,
        )

        history = build_run_history(current)

        # message_chars clamps up to 200 (never a negative slice); pairs
        # falls back to the default of 5.
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(len(history[0]["content"]), 200)

    def test_rewrite_query_passthrough_without_preprocess_model(self):
        run = create_execution_run(
            session=self.session, question="how to deploy?", enqueue=False
        )

        result = rewrite_query(run)

        self.assertFalse(result["rewritten"])
        self.assertEqual(result["question"], "how to deploy?")

    @patch("lens.services.run_completion")
    def test_rewrite_query_uses_preprocess_model(self, mock_completion):
        import uuid

        from lens.llm import LensLLMResult

        self.assistant.preprocess_model_ref = uuid.uuid4()
        self.assistant.save(update_fields=["preprocess_model_ref"])
        mock_completion.return_value = LensLLMResult(
            content="AGIOne 单机部署 步骤", usage={}, metered=True
        )
        run = create_execution_run(
            session=self.session, question="它怎么装", enqueue=False
        )

        result = rewrite_query(run)

        self.assertTrue(mock_completion.called)
        self.assertTrue(result["rewritten"])
        self.assertEqual(result["question"], "AGIOne 单机部署 步骤")

    def test_lens_smoke_test_command_passes(self):
        call_command("lens_smoke_test")

    def test_create_execution_run_reuses_idempotent_run(self):
        first = create_execution_run(
            session=self.session,
            question="How does SSE work?",
            idempotency_key="real-run",
            enqueue=False,
        )
        second = create_execution_run(
            session=self.session,
            question="How does SSE work?",
            idempotency_key="real-run",
            enqueue=False,
        )

        self.assertEqual(first.uuid, second.uuid)
        self.assertEqual(self.session.message_set.count(), 2)

    def test_create_execution_run_persists_retry_relationship(self):
        first = create_execution_run(
            session=self.session,
            question="How does SSE work?",
            idempotency_key="first-run",
            enqueue=False,
        )

        retry = create_execution_run(
            session=self.session,
            question="How does SSE work?",
            idempotency_key="retry-run",
            retry_of_run=first,
            enqueue=False,
        )

        self.assertEqual(retry.retry_of_run, first)
        self.assertEqual(self.session.message_set.count(), 4)

    def test_awaiting_resume_run_counts_toward_assistant_concurrency(self):
        self.assistant.max_concurrency = 1
        self.assistant.save(update_fields=["max_concurrency"])
        awaiting = create_execution_run(
            session=self.session,
            question="First",
            enqueue=False,
        )
        awaiting.status = Run.Status.RUNNING
        awaiting.resume_by = timezone.now() + timedelta(hours=1)
        awaiting.save(update_fields=["status", "resume_by"])
        queued = create_execution_run(
            session=self.session,
            question="Second",
            enqueue=False,
        )

        with patch("lens.tasks.enqueue_answer_run_task") as enqueue:
            execute_answer_run(queued, dispatch=False)

        queued.refresh_from_db()
        self.assertEqual(queued.status, Run.Status.QUEUED)
        enqueue.assert_called_once_with(queued.uuid, 0, countdown=3)

    def test_execute_answer_run_creates_execution_snapshot(self):
        self.assistant.agent_rounds = "max"
        self.assistant.token_budget_profile = "deep"
        self.assistant.save(
            update_fields=["agent_rounds", "token_budget_profile"]
        )
        run = create_execution_run(
            session=self.session,
            question="How does SSE work?",
            idempotency_key="real-run",
            enqueue=False,
        )

        execute_answer_run(run, dispatch=False)
        run.refresh_from_db()

        self.assertEqual(run.status, "done")
        self.assertEqual(run.steps.count(), 3)
        self.assertTrue(run.output_message.content)
        self.assertEqual(run.execution.task, "knowledge_qa")
        self.assertEqual(
            run.execution.target_dirs, [{"path": "/workspace/repo"}]
        )
        self.assertEqual(run.execution.agent_rounds, "max")
        self.assertEqual(run.execution.run_timeout_s, 3600)
        self.assertEqual(run.execution.token_budget_profile, "deep")
        self.assertEqual(run.execution.token_budget_max_tokens, 500000)
        self.assertEqual(
            run.execution.token_budget_final_reserve_tokens,
            75000,
        )

    def test_execute_answer_run_creates_missing_legacy_snapshot(self):
        run = create_execution_run(
            session=self.session,
            question="How does SSE work?",
            enqueue=False,
        )
        run.execution.delete()
        run.refresh_from_db()

        execute_answer_run(run, dispatch=False)
        run.refresh_from_db()

        self.assertEqual(run.status, Run.Status.DONE)
        self.assertEqual(run.execution.status, "completed")

    def test_dispatch_refreshes_skill_package_snapshot(self):
        skill = Skill.objects.create(
            name="Packaged Skill",
            slug="packaged-skill",
            package_hash="sha256:old",
        )
        AssistantSkill.objects.create(
            assistant=self.assistant,
            skill=skill,
        )
        run = create_execution_run(
            session=self.session,
            question="Use the Skill",
            enqueue=False,
        )
        self.assertEqual(
            run.execution.loaded_skills[0]["package_hash"],
            "sha256:old",
        )
        skill.package_hash = "sha256:new"
        skill.save(update_fields=["package_hash"])

        execute_answer_run(run, dispatch=False)
        run.refresh_from_db()

        self.assertEqual(
            run.execution.loaded_skills[0]["package_hash"],
            "sha256:new",
        )

    @patch("lens.services.async_to_sync")
    @patch("lens.services.get_channel_layer")
    @patch("lens.services.build_run_history_artifacts")
    def test_dispatch_includes_general_chat_history_artifacts(
        self,
        build_artifacts,
        get_channel_layer,
        mock_async_to_sync,
    ):
        build_artifacts.return_value = [
            {
                "uuid": "artifact-1",
                "filename": "report.md",
                "content_type": "text/markdown",
                "byte_size": 42,
                "content_hash": "a" * 64,
                "source_run_uuid": "prior-run",
            }
        ]
        sender = mock_async_to_sync.return_value
        self.assistant.selected_task = "general_chat"
        self.assistant.save(update_fields=["selected_task"])
        run = create_execution_run(
            session=self.session,
            question="Translate the previous file",
            enqueue=False,
        )

        dispatch_run_to_lensnode(run, "Translate the previous file")

        payload = sender.call_args.args[1]["payload"]
        self.assertEqual(
            payload["history_artifacts"],
            build_artifacts.return_value,
        )
        build_artifacts.assert_called_once_with(run)

    @patch("lens.services.async_to_sync")
    @patch("lens.services.get_channel_layer")
    def test_dispatch_preserves_clarification_context(
        self,
        get_channel_layer,
        mock_async_to_sync,
    ):
        sender = mock_async_to_sync.return_value
        parent = create_execution_run(
            session=self.session,
            question="Why did the deployment fail?",
            enqueue=False,
        )
        parent.status = Run.Status.AWAITING_USER_INPUT
        parent.termination_detail = {
            "reason": "needs_user_input",
            "request": {
                "request_id": "clarification-1",
                "question": "Which environment should I inspect?",
                "reason": "ambiguous_scope",
                "answer_type": "text",
            },
        }
        parent.save(update_fields=["status", "termination_detail"])
        continuation = create_execution_run(
            session=self.session,
            question="Production",
            retry_of_run=parent,
            enqueue=False,
        )

        dispatch_run_to_lensnode(continuation, "Production")

        payload = sender.call_args.args[1]["payload"]
        self.assertEqual(
            payload["question"],
            "Original user request:\n"
            "Why did the deployment fail?\n\n"
            "Clarification question:\n"
            "Which environment should I inspect?\n"
            "User clarification:\n"
            "Production",
        )

    def test_dispatch_preserves_multiple_clarification_answers(self):
        first = create_execution_run(
            session=self.session,
            question="Why did the deployment fail?",
            enqueue=False,
        )
        first.status = Run.Status.AWAITING_USER_INPUT
        first.termination_detail = {
            "reason": "needs_user_input",
            "request": {
                "request_id": "clarification-1",
                "question": "Which environment should I inspect?",
                "reason": "ambiguous_scope",
                "answer_type": "text",
            },
        }
        first.save(update_fields=["status", "termination_detail"])
        second = create_execution_run(
            session=self.session,
            question="Production",
            retry_of_run=first,
            enqueue=False,
        )
        second.status = Run.Status.AWAITING_USER_INPUT
        second.termination_detail = {
            "reason": "needs_user_input",
            "request": {
                "request_id": "clarification-2",
                "question": "Which service should I inspect?",
                "reason": "ambiguous_target",
                "answer_type": "text",
            },
        }
        second.save(update_fields=["status", "termination_detail"])
        third = create_execution_run(
            session=self.session,
            question="API gateway",
            retry_of_run=second,
            enqueue=False,
        )

        question = build_clarification_continuation_question(
            third,
            "API gateway",
        )

        self.assertEqual(
            question,
            "Original user request:\n"
            "Why did the deployment fail?\n\n"
            "Clarification question:\n"
            "Which environment should I inspect?\n"
            "User clarification:\n"
            "Production\n\n"
            "Clarification question:\n"
            "Which service should I inspect?\n"
            "User clarification:\n"
            "API gateway",
        )

    def test_dispatch_preserves_original_after_many_clarifications(self):
        current = create_execution_run(
            session=self.session,
            question="Why did the deployment fail?",
            enqueue=False,
        )
        for index in range(6):
            current.status = Run.Status.AWAITING_USER_INPUT
            current.termination_detail = {
                "reason": "needs_user_input",
                "request": {
                    "request_id": f"clarification-{index}",
                    "question": f"Clarification question {index}",
                    "reason": "missing_input",
                    "answer_type": "text",
                },
            }
            current.save(update_fields=["status", "termination_detail"])
            current = create_execution_run(
                session=self.session,
                question=f"Answer {index}",
                retry_of_run=current,
                enqueue=False,
            )

        question = build_clarification_continuation_question(
            current,
            "Answer 5",
        )

        self.assertIn(
            "Original user request:\nWhy did the deployment fail?",
            question,
        )
        self.assertNotIn("Clarification question 0", question)
        self.assertIn("Clarification question 1", question)
        self.assertIn("User clarification:\nAnswer 5", question)

    def test_dispatch_preserves_long_clarification_answer(self):
        parent = create_execution_run(
            session=self.session,
            question="Which deployment should I inspect?",
            enqueue=False,
        )
        parent.status = Run.Status.AWAITING_USER_INPUT
        parent.termination_detail = {
            "reason": "needs_user_input",
            "request": {
                "request_id": "clarification-1",
                "question": "Provide the deployment identifier.",
                "reason": "missing_input",
                "answer_type": "text",
            },
        }
        parent.save(update_fields=["status", "termination_detail"])
        answer = "A" * 2501
        continuation = create_execution_run(
            session=self.session,
            question=answer,
            retry_of_run=parent,
            enqueue=False,
        )

        question = build_clarification_continuation_question(
            continuation,
            answer,
        )

        self.assertIn(answer, question)

    def test_dispatch_preserves_long_original_request(self):
        original = "Investigate this deployment in detail: " + ("A" * 2501)
        parent = create_execution_run(
            session=self.session,
            question=original,
            enqueue=False,
        )
        parent.status = Run.Status.AWAITING_USER_INPUT
        parent.termination_detail = {
            "reason": "needs_user_input",
            "request": {
                "request_id": "clarification-1",
                "question": "Which environment should I inspect?",
                "reason": "ambiguous_scope",
                "answer_type": "text",
            },
        }
        parent.save(update_fields=["status", "termination_detail"])
        continuation = create_execution_run(
            session=self.session,
            question="Production",
            retry_of_run=parent,
            enqueue=False,
        )

        question = build_clarification_continuation_question(
            continuation,
            "Production",
        )

        self.assertIn(original, question)

    def test_dispatch_preserves_clarification_for_attachment_only_request(
        self,
    ):
        attachment = MessageAttachment.objects.create(
            session=self.session,
            uploaded_by=self.user,
            kind=MessageAttachment.Kind.IMAGE,
            original_name="error.png",
            mime_type="image/png",
            byte_size=7,
        )
        parent = create_execution_run(
            session=self.session,
            question="",
            enqueue=False,
            attachment_uuids=[str(attachment.uuid)],
        )
        parent.status = Run.Status.AWAITING_USER_INPUT
        parent.termination_detail = {
            "reason": "needs_user_input",
            "request": {
                "request_id": "clarification-1",
                "question": "Which image issue should I inspect?",
                "reason": "ambiguous_scope",
                "answer_type": "text",
            },
        }
        parent.save(update_fields=["status", "termination_detail"])
        continuation = create_execution_run(
            session=self.session,
            question="The deployment error",
            retry_of_run=parent,
            enqueue=False,
        )

        question = build_clarification_continuation_question(
            continuation,
            "The deployment error",
        )

        self.assertIn("(attachment-only request)", question)
        self.assertIn(
            "Clarification question:\nWhich image issue should I inspect?",
            question,
        )
        self.assertIn("User clarification:\nThe deployment error", question)

    @patch("lens.services.attachment_data_url")
    @patch("lens.services.async_to_sync")
    @patch("lens.services.get_channel_layer")
    def test_dispatch_includes_images_for_final_agent(
        self,
        get_channel_layer,
        mock_async_to_sync,
        mock_attachment_data_url,
    ):
        sender = mock_async_to_sync.return_value
        mock_attachment_data_url.return_value = (
            "data:image/png;base64,encoded"
        )
        self.assistant.multimodal_model_ref = uuid4()
        self.assistant.save(update_fields=["multimodal_model_ref"])
        attachment = MessageAttachment.objects.create(
            session=self.session,
            uploaded_by=self.user,
            kind=MessageAttachment.Kind.IMAGE,
            original_name="error.png",
            mime_type="image/png",
            byte_size=7,
        )
        run = create_execution_run(
            session=self.session,
            question="What is wrong?",
            enqueue=False,
            attachment_uuids=[str(attachment.uuid)],
        )

        dispatch_run_to_lensnode(run, "What is wrong?")

        payload = sender.call_args.args[1]["payload"]
        self.assertEqual(
            payload["image_data_urls"],
            ["data:image/png;base64,encoded"],
        )
        self.assertEqual(
            payload["agent_model_ref"],
            str(self.assistant.multimodal_model_ref),
        )

    @patch("lens.services.async_to_sync")
    @patch("lens.services.get_channel_layer")
    def test_dispatch_sends_assistant_profile_token_budget(
        self,
        get_channel_layer,
        mock_async_to_sync,
    ):
        sender = mock_async_to_sync.return_value
        self.assistant.token_budget_profile = "deep"
        self.assistant.save(update_fields=["token_budget_profile"])
        run = create_execution_run(
            session=self.session,
            question="Analyze everything",
            enqueue=False,
        )
        execution = run.execution

        self.assistant.token_budget_profile = "standard"
        self.assistant.save(update_fields=["token_budget_profile"])
        dispatch_run_to_lensnode(run, "Analyze everything")

        payload = sender.call_args.args[1]["payload"]
        self.assertEqual(
            payload["token_budget"],
            {
                "profile": "deep",
                "max_tokens": execution.token_budget_max_tokens,
                "final_reserve_tokens": (
                    execution.token_budget_final_reserve_tokens
                ),
            },
        )
        self.assertEqual(execution.token_budget_profile, "deep")
        self.assertEqual(payload["trace_context"]["trace_id"], run.uuid.hex)
        self.assertEqual(
            len(payload["trace_context"]["root_observation_id"]),
            32,
        )

    @patch("lens.services.async_to_sync")
    @patch("lens.services.get_channel_layer")
    def test_dispatch_sends_unlimited_profile_budget(
        self,
        get_channel_layer,
        mock_async_to_sync,
    ):
        sender = mock_async_to_sync.return_value
        self.assistant.token_budget_profile = "unlimited"
        self.assistant.save(update_fields=["token_budget_profile"])
        run = create_execution_run(
            session=self.session,
            question="Analyze without a token cap",
            enqueue=False,
        )

        dispatch_run_to_lensnode(run, "Analyze without a token cap")

        payload = sender.call_args.args[1]["payload"]
        self.assertEqual(
            payload["token_budget"],
            {
                "profile": "unlimited",
                "max_tokens": 0,
                "final_reserve_tokens": 0,
            },
        )
        self.assertEqual(run.execution.token_budget_profile, "unlimited")

    @patch("lens.services.async_to_sync")
    @patch("lens.services.get_channel_layer")
    def test_dispatch_uses_run_timeout_execution_snapshot(
        self,
        get_channel_layer,
        mock_async_to_sync,
    ):
        sender = mock_async_to_sync.return_value
        self.assistant.agent_rounds = "max"
        self.assistant.save(update_fields=["agent_rounds"])
        run = create_execution_run(
            session=self.session,
            question="Analyze everything",
            enqueue=False,
        )
        run.started_at = timezone.now() - timedelta(minutes=5)
        run.save(update_fields=["started_at"])
        execution = run.execution

        self.assistant.agent_rounds = "flash"
        self.assistant.save(update_fields=["agent_rounds"])
        with patch(
            "lens.services.timezone.now",
            return_value=run.started_at + timedelta(minutes=5),
        ):
            dispatch_run_to_lensnode(run, "Analyze everything")

        payload = sender.call_args.args[1]["payload"]
        self.assertEqual(payload["agent_rounds"], "max")
        self.assertNotIn("max_agent_turns", payload)
        self.assertEqual(payload["run_timeout_s"], 3600)
        self.assertEqual(payload["remaining_run_timeout_s"], 3300)
        self.assertEqual(execution.agent_rounds, "max")
        self.assertEqual(execution.run_timeout_s, 3600)

    @patch("lens.services.async_to_sync")
    @patch("lens.services.get_channel_layer")
    def test_dispatch_uses_model_and_settings_runtime_snapshot(
        self,
        get_channel_layer,
        mock_async_to_sync,
    ):
        sender = mock_async_to_sync.return_value
        self.assistant.agent_model_ref = "11111111-1111-1111-1111-111111111111"
        self.assistant.multimodal_model_ref = (
            "22222222-2222-2222-2222-222222222222"
        )
        self.assistant.settings = {"runtime_mode": "original"}
        self.user.profile.language = "zh-CN"
        self.user.profile.save(update_fields=["language"])
        self.assistant.save(
            update_fields=[
                "agent_model_ref",
                "multimodal_model_ref",
                "settings",
            ]
        )
        run = create_execution_run(
            session=self.session,
            question="Analyze with frozen configuration",
            enqueue=False,
        )

        self.assistant.agent_model_ref = "33333333-3333-3333-3333-333333333333"
        self.assistant.multimodal_model_ref = None
        self.assistant.settings = {"runtime_mode": "changed"}
        self.user.profile.language = "en-US"
        self.user.profile.save(update_fields=["language"])
        self.assistant.save(
            update_fields=[
                "agent_model_ref",
                "multimodal_model_ref",
                "settings",
            ]
        )
        dispatch_run_to_lensnode(run, "Analyze frozen configuration")

        payload = sender.call_args.args[1]["payload"]
        self.assertEqual(
            payload["agent_model_ref"],
            "11111111-1111-1111-1111-111111111111",
        )
        self.assertEqual(
            payload["vision_model_ref"],
            "22222222-2222-2222-2222-222222222222",
        )
        self.assertEqual(payload["answer_language"], "zh-CN")
        self.assertEqual(payload["settings"], {"runtime_mode": "original"})

    @patch("lens.services.async_to_sync")
    @patch("lens.services.get_channel_layer")
    def test_dispatch_uses_answer_language_runtime_snapshot(
        self,
        get_channel_layer,
        mock_async_to_sync,
    ):
        sender = mock_async_to_sync.return_value
        self.user.profile.language = "zh-CN"
        self.user.profile.save(update_fields=["language"])
        run = create_execution_run(
            session=self.session,
            question="Analyze frozen language",
            enqueue=False,
        )

        self.user.profile.language = "en-US"
        self.user.profile.save(update_fields=["language"])
        dispatch_run_to_lensnode(run, "Analyze frozen language")

        payload = sender.call_args.args[1]["payload"]
        self.assertEqual(
            run.execution.runtime_snapshot["answer_language"],
            "zh-CN",
        )
        self.assertEqual(payload["answer_language"], "zh-CN")

    def test_execute_answer_run_fails_when_lensnode_offline(self):
        self.lensnode.status = LensNode.Status.OFFLINE
        self.lensnode.save(update_fields=["status"])
        run = create_execution_run(
            session=self.session,
            question="How does SSE work?",
            enqueue=False,
        )

        with self.assertRaises(Exception):
            execute_answer_run(run, dispatch=False)

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.FAILED)
        self.assertEqual(run.error, "LENSNODE_OFFLINE")

    def test_finish_lensnode_run_does_not_overwrite_cancelled_run(self):
        run = create_execution_run(
            session=self.session,
            question="What does this project do?",
            enqueue=False,
        )
        run.status = Run.Status.CANCELLED
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])

        finish_lensnode_run(run.uuid, Run.Status.DONE)

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.CANCELLED)
        self.assertEqual(run.output_message.content, "")

    def test_finish_lensnode_run_persists_business_outcome(self):
        run = create_execution_run(
            session=self.session,
            question="Use the configured Skill",
            enqueue=False,
        )

        finish_lensnode_run(
            run.uuid,
            Run.Status.DONE,
            outcome=Run.Outcome.BLOCKED,
            termination_detail={
                "reason": "capability_unavailable",
                "capability": "skill",
            },
        )

        run.refresh_from_db()
        self.assertEqual(run.outcome, Run.Outcome.BLOCKED)
        self.assertEqual(
            run.termination_detail,
            {
                "reason": "capability_unavailable",
                "capability": "skill",
            },
        )

    def test_sync_event_exposes_safe_runtime_fields_only(self):
        run = create_execution_run(
            session=self.session,
            question="Plan this task",
            enqueue=False,
        )
        RunStep.objects.create(
            run=run,
            step_type=RunStep.StepType.GENERAL_CHAT,
            sequence=3,
            status=RunStep.Status.RUNNING,
            detail={
                "secret": "do-not-expose",
                "events": [
                    {
                        "agent_event": "tool.call_skill_api.invoke",
                        "activity": "running_tool",
                        "summary": "Authorization: secret-token",
                    },
                    {
                        "agent_event": "workflow.route.selected",
                        "activity": "running",
                        "event_type": "route.selected",
                        "visibility": "user",
                        "payload": {
                            "route": "plan_execute",
                            "complexity": "complex",
                            "evidence_requirement": "none",
                        },
                    },
                    {
                        "agent_event": "workflow.plan.updated",
                        "activity": "running",
                        "event_type": "plan.updated",
                        "visibility": "user",
                        "payload": {
                            "revision": 1,
                            "steps": [
                                {
                                    "id": "step-1",
                                    "title": "Inspect configuration",
                                    "status": "in_progress",
                                    "secret": "hidden",
                                }
                            ],
                            "secret": "hidden",
                        },
                    },
                    {
                        "agent_event": "workflow.stage.updated",
                        "activity": "running",
                        "event_type": "stage.updated",
                        "visibility": "user",
                        "payload": {
                            "id": "fetch-orders",
                            "title": "Fetch order data",
                            "status": "in_progress",
                            "summary": "Fetched 93 orders",
                            "order": 2,
                            "revision": 4,
                            "secret": "hidden",
                        },
                    },
                ],
            },
        )
        run.resume_by = timezone.now() + timedelta(hours=1)
        run.save(update_fields=["resume_by"])

        event = _build_sync_event(run)
        detail = event["steps"][0]["detail"]

        self.assertEqual(event["resume_by"], run.resume_by.isoformat())
        self.assertNotIn("secret", str(detail))
        self.assertNotIn("Authorization", str(detail))
        self.assertEqual(
            detail["events"][2]["payload"]["steps"][0]["title"],
            "Inspect configuration",
        )
        self.assertEqual(
            detail["events"][1]["payload"]["evidence_requirement"],
            "none",
        )
        self.assertEqual(
            detail["events"][3]["payload"],
            {
                "id": "fetch-orders",
                "title": "Fetch order data",
                "status": "in_progress",
                "summary": "Fetched 93 orders",
                "order": 2,
                "revision": 4,
            },
        )

    def test_route_event_rejects_unknown_contract_values(self):
        event = sanitize_runtime_event(
            {
                "agent_event": "workflow.route.selected",
                "activity": "running",
                "event_type": "route.selected",
                "visibility": "user",
                "payload": {
                    "intent": "secret-intent",
                    "complexity": "oversized",
                    "route": "arbitrary-route",
                    "evidence_requirement": "secret-token",
                    "required_capabilities": ["skill", "secret-token"],
                },
            }
        )

        self.assertEqual(
            event["payload"],
            {"required_capabilities": ["skill"]},
        )

    def test_capability_unavailable_route_is_public(self):
        event = sanitize_runtime_event(
            {
                "event_type": "route.selected",
                "visibility": "user",
                "payload": {
                    "intent": "action",
                    "complexity": "simple",
                    "route": "capability_unavailable",
                    "evidence_requirement": "tool_result",
                    "required_capabilities": ["skill"],
                },
            }
        )

        self.assertEqual(
            event["payload"]["route"],
            "capability_unavailable",
        )

    def test_execution_failure_event_is_distinct(self):
        event = sanitize_runtime_event(
            {
                "event_type": "execution.failed",
                "visibility": "user",
                "payload": {
                    "reason": "execution_failed",
                    "capability": "skill",
                    "error_type": "transient",
                },
            }
        )

        self.assertEqual(event["event_type"], "execution.failed")
        self.assertEqual(event["payload"]["reason"], "execution_failed")
        self.assertEqual(event["payload"]["error_type"], "transient")

    def test_verification_failure_event_is_distinct(self):
        event = sanitize_runtime_event(
            {
                "event_type": "verification.failed",
                "visibility": "user",
                "payload": {
                    "reason": "evidence_unavailable",
                    "capability": "skill",
                    "error_type": "verification",
                },
            }
        )

        self.assertEqual(event["event_type"], "verification.failed")
        self.assertEqual(
            event["payload"]["reason"],
            "evidence_unavailable",
        )
        self.assertEqual(event["payload"]["error_type"], "verification")

    def test_runtime_event_rejects_unknown_phase_and_status_values(self):
        phase_event = sanitize_runtime_event(
            {
                "agent_event": "secret-token",
                "activity": "Authorization: secret-token",
                "event_type": "phase.changed",
                "visibility": "user",
                "payload": {"phase": "secret-token"},
            }
        )
        plan_event = sanitize_runtime_event(
            {
                "event_type": "plan.updated",
                "visibility": "user",
                "payload": {
                    "steps": [
                        {
                            "id": "step-1",
                            "title": "Inspect configuration",
                            "status": "secret-token",
                        }
                    ]
                },
            }
        )

        self.assertNotIn("secret-token", str(phase_event))
        self.assertEqual(phase_event["payload"], {})
        self.assertEqual(
            plan_event["payload"]["steps"][0]["status"],
            "pending",
        )

    def test_stage_event_bounds_public_fields(self):
        event = sanitize_runtime_event(
            {
                "event_type": "stage.updated",
                "visibility": "user",
                "payload": {
                    "id": "stage-" + ("x" * 100),
                    "title": "T" * 300,
                    "status": "completed",
                    "summary": "S" * 300,
                    "order": 99,
                    "revision": "7",
                    "secret": "hidden",
                },
            }
        )

        self.assertEqual(len(event["payload"]["id"]), 64)
        self.assertEqual(len(event["payload"]["title"]), 240)
        self.assertEqual(len(event["payload"]["summary"]), 240)
        self.assertEqual(event["payload"]["status"], "completed")
        self.assertEqual(event["payload"]["order"], 12)
        self.assertEqual(event["payload"]["revision"], 7)
        self.assertNotIn("secret", str(event))

    def test_stage_event_rejects_invalid_status(self):
        event = sanitize_runtime_event(
            {
                "event_type": "stage.updated",
                "visibility": "user",
                "payload": {
                    "id": "fetch-orders",
                    "title": "Fetch order data",
                    "status": "secret-token",
                },
            }
        )

        self.assertEqual(event["payload"], {})
        self.assertNotIn("secret-token", str(event))

    def test_document_progress_event_exposes_only_bounded_counts(self):
        event = sanitize_runtime_event(
            {
                "event_type": "document.progress",
                "visibility": "user",
                "payload": {
                    "revision": "7",
                    "stage": "recognizing_images",
                    "document_index": 1,
                    "document_total": 2,
                    "image_completed": 3,
                    "image_total": 5,
                    "filename": "private-report.docx",
                    "secret": "hidden",
                },
            }
        )

        self.assertEqual(event["event_type"], "document.progress")
        self.assertEqual(
            event["payload"],
            {
                "revision": 7,
                "stage": "recognizing_images",
                "document_index": 1,
                "document_total": 2,
                "image_completed": 3,
                "image_total": 5,
            },
        )
        self.assertNotIn("private-report.docx", str(event))
        self.assertNotIn("hidden", str(event))

    def test_document_progress_event_rejects_unknown_stage(self):
        event = sanitize_runtime_event(
            {
                "event_type": "document.progress",
                "visibility": "user",
                "payload": {
                    "revision": 1,
                    "stage": "reading_private_content",
                },
            }
        )

        self.assertEqual(event["payload"], {})

    def test_order_query_activity_exposes_safe_real_parameters(self):
        event = sanitize_runtime_event(
            {
                "agent_event": "tool.run_skill_script.start",
                "activity": "running_tool",
                "runtime_scope": "general_chat",
                "invocation_id": "activity-123",
                "skill": "license-cli",
                "artifact": "income",
                "args_redacted": [
                    "--profile",
                    "default",
                    "order",
                    "list",
                    "--start",
                    "2026-07-20T00:00:00+08:00",
                    "--end",
                    "2026-07-26T23:59:59+08:00",
                    "--token",
                    "[REDACTED]",
                ],
            }
        )

        self.assertEqual(event["event_type"], "activity.recorded")
        self.assertEqual(event["visibility"], "user")
        self.assertEqual(
            event["payload"],
            {
                "id": "activity-123",
                "kind": "query_orders",
                "stage_kind": "order_query",
                "status": "in_progress",
                "start_date": "2026-07-20",
                "end_date": "2026-07-26",
            },
        )
        self.assertNotIn("token", str(event).lower())
        self.assertNotIn("profile", str(event).lower())
        self.assertNotIn("run_skill_script", str(event))

    def test_completed_tool_activity_preserves_only_pairing_fields(self):
        event = sanitize_runtime_event(
            {
                "agent_event": "tool.run_skill_script.done",
                "activity": "running_tool",
                "runtime_scope": "general_chat",
                "invocation_id": "activity-123",
                "stdout_ref": "/large_tool_results/private.txt",
                "summary": "license-cli/income · rc=0",
            }
        )

        self.assertEqual(
            event["payload"],
            {
                "id": "activity-123",
                "kind": "querying_data",
                "stage_kind": "data_query",
                "status": "completed",
            },
        )
        self.assertNotIn("stdout", str(event))

    def test_order_detail_and_command_help_have_real_activity_kinds(self):
        detail = sanitize_runtime_event(
            {
                "agent_event": "tool.run_skill_script.start",
                "activity": "running_tool",
                "runtime_scope": "general_chat",
                "invocation_id": "detail-123",
                "args_redacted": [
                    "--profile",
                    "default",
                    "order",
                    "get",
                    "ORDER-123",
                ],
            }
        )
        command_help = sanitize_runtime_event(
            {
                "agent_event": "tool.run_skill_script.start",
                "activity": "running_tool",
                "runtime_scope": "general_chat",
                "invocation_id": "help-123",
                "args_redacted": ["order", "get", "--help"],
            }
        )

        self.assertEqual(detail["payload"]["kind"], "get_order_detail")
        self.assertEqual(
            detail["payload"]["order_ref"],
            "ORDER-123",
        )
        self.assertEqual(
            command_help["payload"]["kind"],
            "reading_order_commands",
        )

    def test_order_list_by_code_exposes_only_safe_order_reference(self):
        event = sanitize_runtime_event(
            {
                "agent_event": "tool.run_skill_script.start",
                "activity": "running_tool",
                "runtime_scope": "general_chat",
                "invocation_id": "lookup-123",
                "args_redacted": [
                    "--profile",
                    "default",
                    "order",
                    "list",
                    "--code",
                    "HWINSTAD2025071509",
                    "--token",
                    "[REDACTED]",
                ],
            }
        )

        self.assertEqual(
            event["payload"]["order_ref"],
            "HWINSTAD2025071509",
        )
        self.assertNotIn("profile", str(event).lower())
        self.assertNotIn("token", str(event).lower())

    def test_order_reference_rejects_non_identifier_arguments(self):
        event = sanitize_runtime_event(
            {
                "agent_event": "tool.run_skill_script.start",
                "activity": "running_tool",
                "runtime_scope": "general_chat",
                "invocation_id": "lookup-unsafe",
                "args_redacted": [
                    "order",
                    "get",
                    "../../private-order",
                ],
            }
        )

        self.assertNotIn("order_ref", event["payload"])

    def test_structured_analysis_activity_exposes_allowlisted_operation(self):
        event = sanitize_runtime_event(
            {
                "agent_event": "tool.analyze_structured_output.start",
                "activity": "running_tool",
                "runtime_scope": "general_chat",
                "invocation_id": "analysis-123",
                "operation": "count",
                "input_ref": "/large_tool_results/private.txt",
            }
        )

        self.assertEqual(
            event["payload"],
            {
                "id": "analysis-123",
                "kind": "count_results",
                "stage_kind": "result_analysis",
                "status": "in_progress",
            },
        )
        self.assertNotIn("input_ref", str(event))

    def test_non_general_chat_tool_event_keeps_original_public_shape(self):
        event = sanitize_runtime_event(
            {
                "agent_event": "tool.run_skill_script.start",
                "activity": "running_tool",
                "invocation_id": "activity-123",
                "args_redacted": ["order", "list"],
            }
        )

        self.assertEqual(
            event,
            {
                "agent_event": "tool.run_skill_script.start",
                "activity": "running_tool",
            },
        )

    def test_general_chat_model_round_is_not_user_visible(self):
        event = sanitize_runtime_event(
            {
                "agent_event": "model.round.start",
                "runtime_scope": "general_chat",
                "invocation_id": "model-round-2",
                "round": 2,
                "summary": "private model reasoning",
            }
        )

        self.assertIsNone(event)

    def test_order_help_is_preparation_not_a_business_query(self):
        event = sanitize_runtime_event(
            {
                "agent_event": "tool.run_skill_script.start",
                "runtime_scope": "general_chat",
                "invocation_id": "order-list-help",
                "args_redacted": ["order", "list", "--help"],
            }
        )

        self.assertEqual(
            event["payload"],
            {
                "id": "order-list-help",
                "kind": "reading_order_commands",
                "stage_kind": "preparation",
                "status": "in_progress",
            },
        )

    def test_order_view_is_an_order_detail_operation(self):
        event = sanitize_runtime_event(
            {
                "agent_event": "tool.run_skill_script.start",
                "runtime_scope": "general_chat",
                "invocation_id": "order-view",
                "args_redacted": [
                    "order",
                    "view",
                    "HWINSTAD2025071509",
                ],
            }
        )

        self.assertEqual(event["payload"]["kind"], "get_order_detail")
        self.assertEqual(
            event["payload"]["order_ref"],
            "HWINSTAD2025071509",
        )

    def test_general_chat_does_not_guess_summary_before_later_tools(self):
        detail = public_step_detail(
            {
                "events": [
                    {
                        "agent_event": "tool.run_skill_script.start",
                        "runtime_scope": "general_chat",
                        "invocation_id": "order-query",
                        "args_redacted": [
                            "order",
                            "get",
                            "HWINSTAD2025071509",
                        ],
                    },
                    {
                        "agent_event": "tool.run_skill_script.done",
                        "runtime_scope": "general_chat",
                        "invocation_id": "order-query",
                    },
                    {
                        "agent_event": "model.round.start",
                        "runtime_scope": "general_chat",
                    },
                    {
                        "agent_event": "tool.run_skill_script.start",
                        "runtime_scope": "general_chat",
                        "invocation_id": "order-help",
                        "args_redacted": ["order", "--help"],
                    },
                ]
            }
        )

        kinds = [
            item["payload"]["kind"]
            for item in detail["events"]
            if item.get("event_type") == "activity.recorded"
        ]

        self.assertNotIn("summarizing_results", kinds)

    def test_general_chat_public_path_uses_real_operation_stages(self):
        detail = public_step_detail(
            {
                "events": [
                    {
                        "agent_event": "tool.run_skill_script.start",
                        "runtime_scope": "general_chat",
                        "invocation_id": "tool-version",
                        "args_redacted": ["version"],
                    },
                    {
                        "agent_event": "tool.run_skill_script.done",
                        "runtime_scope": "general_chat",
                        "invocation_id": "tool-version",
                    },
                    {
                        "agent_event": "tool.run_skill_script.start",
                        "runtime_scope": "general_chat",
                        "invocation_id": "auth-status",
                        "args_redacted": ["auth", "status"],
                    },
                    {
                        "agent_event": "tool.run_skill_script.done",
                        "runtime_scope": "general_chat",
                        "invocation_id": "auth-status",
                    },
                    {
                        "agent_event": "tool.run_skill_script.start",
                        "runtime_scope": "general_chat",
                        "invocation_id": "auth-login",
                        "args_redacted": ["auth", "login"],
                    },
                    {
                        "agent_event": "tool.run_skill_script.done",
                        "runtime_scope": "general_chat",
                        "invocation_id": "auth-login",
                    },
                    {
                        "agent_event": "tool.run_skill_script.start",
                        "runtime_scope": "general_chat",
                        "invocation_id": "order-query",
                        "args_redacted": [
                            "order",
                            "list",
                            "--code",
                            "HWINSTAD2025071509",
                        ],
                    },
                    {
                        "agent_event": "tool.run_skill_script.done",
                        "runtime_scope": "general_chat",
                        "invocation_id": "order-query",
                    },
                    {
                        "agent_event": "model.round.start",
                        "runtime_scope": "general_chat",
                        "invocation_id": "model-round-4",
                        "round": 4,
                    },
                    {
                        "agent_event": "deepagents.runtime.done",
                        "runtime_scope": "general_chat",
                        "answer_chars": 120,
                    },
                ]
            }
        )

        activities = [
            item["payload"]
            for item in detail["events"]
            if item.get("event_type") == "activity.recorded"
        ]

        self.assertEqual(
            [item["kind"] for item in activities],
            [
                "checking_tool",
                "checking_tool",
                "checking_authentication",
                "checking_authentication",
                "authenticating",
                "authenticating",
                "query_orders",
                "query_orders",
                "summarizing_results",
            ],
        )
        self.assertEqual(
            [item["stage_kind"] for item in activities],
            [
                "preparation",
                "preparation",
                "preparation",
                "preparation",
                "preparation",
                "preparation",
                "order_query",
                "order_query",
                "result_analysis",
            ],
        )
        self.assertEqual(activities[-1]["status"], "completed")
        self.assertEqual(
            activities[-1]["order_ref"],
            "HWINSTAD2025071509",
        )

    def test_general_chat_summary_completes_without_order_assumptions(self):
        detail = public_step_detail(
            {
                "events": [
                    {
                        "agent_event": "tool.run_skill_script.start",
                        "runtime_scope": "general_chat",
                        "invocation_id": "ticket-query",
                        "args_redacted": ["ticket", "search", "INC-123"],
                    },
                    {
                        "agent_event": "tool.run_skill_script.done",
                        "runtime_scope": "general_chat",
                        "invocation_id": "ticket-query",
                    },
                    {
                        "agent_event": "model.round.start",
                        "runtime_scope": "general_chat",
                        "invocation_id": "model-round-2",
                        "round": 2,
                    },
                    {
                        "agent_event": "deepagents.runtime.done",
                        "runtime_scope": "general_chat",
                        "answer_chars": 120,
                    },
                ]
            }
        )

        activities = [
            item["payload"]
            for item in detail["events"]
            if item.get("event_type") == "activity.recorded"
        ]

        self.assertEqual(activities[0]["kind"], "querying_data")
        self.assertNotIn("order_ref", activities[0])
        self.assertEqual(
            activities[-1],
            {
                "id": "summarize-results",
                "kind": "summarizing_results",
                "stage_kind": "result_analysis",
                "status": "completed",
            },
        )

    def test_message_history_replays_inferred_general_chat_summary(self):
        run = create_execution_run(
            session=self.session,
            question="Find ticket INC-123",
            enqueue=False,
        )
        RunStep.objects.create(
            run=run,
            step_type=RunStep.StepType.GENERAL_CHAT,
            sequence=3,
            status=RunStep.Status.DONE,
            detail={
                "events": [
                    {
                        "agent_event": ("tool.run_skill_script.start"),
                        "runtime_scope": "general_chat",
                        "invocation_id": "ticket-query",
                        "args_redacted": [
                            "ticket",
                            "search",
                            "INC-123",
                        ],
                    },
                    {
                        "agent_event": ("tool.run_skill_script.done"),
                        "runtime_scope": "general_chat",
                        "invocation_id": "ticket-query",
                    },
                    {
                        "agent_event": "model.round.start",
                        "runtime_scope": "general_chat",
                    },
                    {
                        "agent_event": "deepagents.runtime.done",
                        "runtime_scope": "general_chat",
                        "answer_chars": 120,
                    },
                ]
            },
        )

        thinking = MessageSerializer(run.output_message).data["thinking"]
        activities = [
            item["payload"]
            for item in thinking["steps"]
            if item.get("event_type") == "activity.recorded"
        ]

        self.assertEqual(
            activities[-1],
            {
                "id": "summarize-results",
                "kind": "summarizing_results",
                "stage_kind": "result_analysis",
                "status": "completed",
            },
        )

    def test_termination_detail_uses_fixed_public_contract(self):
        detail = sanitize_termination_detail(
            {
                "reason": "evidence_unavailable",
                "trigger": "soft_deadline",
                "capability": "mcp",
                "error_type": "secret-token",
                "tool": "Authorization: secret-token",
                "recovery": "Authorization: secret-token",
                "code": "secret-token",
            }
        )

        self.assertEqual(
            detail,
            {
                "reason": "evidence_unavailable",
                "trigger": "soft_deadline",
                "capability": "mcp",
            },
        )
        self.assertNotIn("secret-token", str(detail))

    def test_termination_detail_exposes_only_text_clarification_request(self):
        detail = sanitize_termination_detail(
            {
                "reason": "needs_user_input",
                "request": {
                    "request_id": "clarification-1",
                    "question": "Which environment should I inspect?",
                    "reason": "ambiguous_scope",
                    "answer_type": "text",
                    "internal": "do not expose",
                },
            }
        )

        self.assertEqual(
            detail,
            {
                "reason": "needs_user_input",
                "request": {
                    "request_id": "clarification-1",
                    "question": "Which environment should I inspect?",
                    "reason": "ambiguous_scope",
                    "answer_type": "text",
                },
            },
        )

    def test_termination_detail_rejects_non_text_clarification_request(self):
        detail = sanitize_termination_detail(
            {
                "reason": "needs_user_input",
                "request": {
                    "request_id": "clarification-1",
                    "question": "Choose an environment",
                    "reason": "ambiguous_scope",
                    "answer_type": "choice",
                },
            }
        )

        self.assertEqual(detail, {"reason": "needs_user_input"})

    def test_run_serializer_hides_runtime_credentials(self):
        run = create_execution_run(
            session=self.session,
            question="Use the connector",
            enqueue=False,
        )
        execution = create_run_execution_snapshot(run)
        execution.loaded_mcps = [
            {
                "mcp_uuid": "mcp-1",
                "mcp_name": "Orders",
                "transport": "streamable_http",
                "endpoint": "https://example.test/mcp",
                "config": {"headers": {"Authorization": "secret-token"}},
            }
        ]
        execution.save(update_fields=["loaded_mcps"])
        run.refresh_from_db()

        payload = RunSerializer(run).data

        self.assertNotIn("secret-token", str(payload))
        self.assertEqual(payload["execution"]["agent_rounds"], "balanced")
        self.assertEqual(payload["execution"]["run_timeout_s"], 3600)
        self.assertNotIn("endpoint", payload["execution"]["loaded_mcps"][0])
        self.assertEqual(
            payload["execution"]["loaded_mcps"][0]["mcp_name"],
            "Orders",
        )

    def test_append_lensnode_output_ignores_terminal_run(self):
        run = create_execution_run(
            session=self.session,
            question="What does this project do?",
            enqueue=False,
        )
        run.status = Run.Status.CANCELLED
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])

        append_lensnode_output(run.uuid, content_delta="late answer")

        run.refresh_from_db()
        self.assertEqual(run.output_message.content, "")

    def test_lensnode_websocket_hello_output_and_done(self):
        token = issue_lensnode_token(self.lensnode)

        async_to_sync(self._exercise_lensnode_websocket)(token)

        self.lensnode.refresh_from_db()
        self.assertEqual(self.lensnode.protocol_version, "v1")
        self.lensnode.status = LensNode.Status.ONLINE
        self.lensnode.save(update_fields=["status"])

        run = create_execution_run(
            session=self.session,
            question="How does SSE work?",
            enqueue=False,
        )
        execute_answer_run(run, dispatch=True)

        async_to_sync(self._exercise_lensnode_run_frames)(token, run)

        run.refresh_from_db()
        self.assertEqual(run.status, Run.Status.DONE)
        self.assertEqual(run.output_message.content, "answer")

    def test_lensnode_websocket_rejects_revoked_token(self):
        token = issue_lensnode_token(self.lensnode)
        self.lensnode.token_revoked = True
        self.lensnode.save(update_fields=["token_revoked"])
        communicator = WebsocketCommunicator(
            application,
            f"/ws/lens/lensnodes/?token={token}",
        )

        connected, _ = async_to_sync(communicator.connect)()

        self.assertFalse(connected)

    async def _exercise_lensnode_websocket(self, token):
        """Connect a LensNode and send a hello frame in one event loop."""

        communicator = WebsocketCommunicator(
            application,
            f"/ws/lens/lensnodes/?token={token}",
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()
        await communicator.send_json_to(
            {
                "type": "hello",
                "protocol_version": "v1",
                "agent_version": "1.0.0",
                "workspace_path": "/workspace",
                "available_dirs": [{"path": "/workspace/repo"}],
                "tasks": [{"name": "knowledge_qa"}],
                "labels": {"region": "local"},
            }
        )
        self.assertEqual(
            (await communicator.receive_json_from())["type"],
            "hello_ack",
        )
        await communicator.disconnect()

    async def _exercise_lensnode_run_frames(self, token, run):
        """Connect a LensNode and send run result frames in one event loop."""

        communicator = WebsocketCommunicator(
            application,
            f"/ws/lens/lensnodes/?token={token}",
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()
        await communicator.send_json_to(
            {
                "type": "run_event",
                "run_uuid": str(run.uuid),
                "step_type": "retrieval",
                "status": "done",
                "detail": {"hits": 2},
            }
        )
        await communicator.send_json_to(
            {
                "type": "run_output",
                "run_uuid": str(run.uuid),
                "content_delta": "answer",
            }
        )
        await communicator.send_json_to(
            {
                "type": "run_done",
                "run_uuid": str(run.uuid),
                "status": "done",
            }
        )
        self.assertEqual(
            await communicator.receive_json_from(),
            {
                "type": "run_done_ack",
                "run_uuid": str(run.uuid),
            },
        )
        await communicator.disconnect()

    async def _exercise_lensnode_conversion_done(self, token, task_id):
        """Send a managed conversion completion frame over the node socket."""

        communicator = WebsocketCommunicator(
            application,
            f"/ws/lens/lensnodes/?token={token}",
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()
        await communicator.send_json_to(
            {
                "type": "datasource_convert_done",
                "task_id": task_id,
                "status": "success",
                "conversion_summary": {
                    "total": 1,
                    "success": 1,
                    "failed": 0,
                    "skipped": 0,
                    "unsupported": 0,
                },
            }
        )
        await communicator.disconnect()

    def test_source_sync_task_dispatches_without_waiting_for_result(self):
        with patch("lens.tasks.dispatch_datasource_sync_async") as dispatch:
            dispatch.return_value = "request-1"
            synced = source_sync_task(str(self.datasource.uuid))
            self.datasource.refresh_from_db()

            record = ScheduledTask.objects.get(
                task_type="source_sync",
                target_type="datasource",
                target_id=self.datasource.uuid,
            )
            self.assertEqual(synced, 0)
            self.assertEqual(self.datasource.status, "active")
            self.assertIsNone(self.datasource.last_synced_at)
            self.assertEqual(record.last_status, "running")
            self.assertEqual(record.last_metrics, {})
            task = TaskExecution.objects.get(module="lens_datasource")
            self.assertEqual(task.task_name, "datasource_sync:Repo Cache")
            self.assertEqual(task.status, "STARTED")
            self.assertEqual(task.metadata["type"], "datasource")
            self.assertEqual(
                task.metadata["completion_source"],
                "lensnode_callback",
            )
            self.assertEqual(
                task.metadata["datasource_sync_request_id"],
                "request-1",
            )
            self.assertEqual(task.metadata["source_type"], "git")
            self.assertEqual(
                task.metadata["repo_url"],
                "https://example.com/repo.git",
            )
            self.assertEqual(task.metadata["lensnode_name"], "Local LensNode")
            self.assertEqual(
                task.metadata["target_path"],
                "/workspace/repo-cache",
            )
            self.assertEqual(task.metadata["conversion"], {})
            self.assertFalse(task.metadata["conversion_enabled"])
            self.assertEqual(
                task.metadata["sync_policy"],
                {"interval_seconds": 3600},
            )
            self.assertEqual(task.metadata["sync_interval_seconds"], 3600)
            steps = task.metadata.get("steps") or []
            self.assertGreaterEqual(len(steps), 2)
            self.assertEqual(steps[0]["name"], "prepare")
            self.assertEqual(steps[-1]["name"], "dispatch")

    def test_managed_workspace_sync_is_blocked_at_task_and_dispatch_layers(
        self,
    ):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )

        with patch("lens.tasks.dispatch_datasource_sync_async") as dispatch:
            self.assertEqual(source_sync_task(str(datasource.uuid)), 0)

        dispatch.assert_not_called()
        self.assertFalse(
            ScheduledTask.objects.filter(target_id=datasource.uuid).exists()
        )
        self.assertFalse(
            TaskExecution.objects.filter(
                metadata__datasource_uuid=str(datasource.uuid)
            ).exists()
        )
        with self.assertRaises(DataSourceDispatchError):
            dispatch_datasource_sync_async(
                datasource,
                task_id="managed-sync",
            )

    def test_managed_workspace_conversion_dispatches_without_sync_adapter(
        self,
    ):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )

        with patch("lens.datasource_services._send_lensnode_command") as send:
            request_id = dispatch_datasource_conversion_async(
                datasource,
                task_id="managed-conversion",
                conversion={"document": True},
                force=True,
            )

        self.assertTrue(request_id)
        payload = send.call_args.args[1]
        self.assertEqual(payload["type"], "datasource_convert")
        self.assertEqual(payload["source_type"], "managed_workspace")
        self.assertEqual(payload["conversion"], {"document": True})
        self.assertTrue(payload["force"])
        self.assertNotIn("config", payload)
        self.assertNotIn("sync_policy", payload)

    def test_managed_workspace_conversion_task_is_callback_completed(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )
        register_datasource_conversion_task(
            datasource,
            "managed-conversion",
            {"document": True},
            created_by=self.user,
        )

        with patch(
            "lens.tasks.dispatch_datasource_conversion_async"
        ) as dispatch:
            dispatch.return_value = "conversion-request"
            result = datasource_conversion_task(
                str(datasource.uuid),
                {"document": True},
                False,
                "managed-conversion",
            )

        self.assertEqual(result, 0)
        task = TaskExecution.objects.get(task_id="managed-conversion")
        datasource.refresh_from_db()
        self.assertEqual(task.status, "STARTED")
        self.assertEqual(
            task.metadata["datasource_conversion_request_id"],
            "conversion-request",
        )
        self.assertEqual(datasource.last_conversion_status, "STARTED")

    def test_managed_workspace_conversion_waits_for_lensnode_heavy_slot(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )
        register_datasource_conversion_task(
            datasource,
            "queued-conversion",
            {"document": True},
        )

        with (
            patch(
                "lens.tasks.acquire_lensnode_heavy_work_slot",
                return_value=None,
            ),
            patch("lens.tasks._schedule_queued_conversion") as schedule,
        ):
            datasource_conversion_task(
                str(datasource.uuid),
                {"document": True},
                False,
                "queued-conversion",
            )

        task = TaskExecution.objects.get(task_id="queued-conversion")
        datasource.refresh_from_db()
        self.assertEqual(task.status, "PENDING")
        self.assertEqual(task.metadata["queue_state"], "QUEUED")
        self.assertEqual(datasource.last_conversion_status, "PENDING")
        schedule.assert_called_once()

    def test_reconnect_reconciles_orphaned_conversion_and_releases_owners(
        self,
    ):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
            last_conversion_status="STARTED",
        )
        task = register_datasource_conversion_task(
            datasource,
            "orphaned-conversion",
            {"document": True},
        )
        task.status = "STARTED"
        task.metadata.update(
            {
                "lensnode_connection_id": "old-connection",
                "lock_token": task.task_id,
                "heavy_work_slot": "0",
            }
        )
        task.save(update_fields=["status", "metadata"])
        cache.set(
            f"lens:datasource-sync:{datasource.uuid}",
            task.task_id,
        )
        cache.set(
            f"lens:heavy-work:{self.lensnode.uuid}:0",
            task.task_id,
        )

        count = reconcile_orphaned_datasource_conversions(
            self.lensnode.uuid,
            "new-connection",
        )

        task.refresh_from_db()
        datasource.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(task.status, "FAILURE")
        self.assertEqual(task.error, "DATASOURCE_CONVERSION_ORPHANED")
        self.assertTrue(task.metadata["recovery_retryable"])
        self.assertEqual(datasource.last_conversion_status, "FAILURE")
        self.assertIsNone(cache.get(f"lens:datasource-sync:{datasource.uuid}"))
        self.assertIsNone(
            cache.get(f"lens:heavy-work:{self.lensnode.uuid}:0")
        )

    def test_complete_managed_workspace_conversion_persists_summary(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )
        register_datasource_conversion_task(
            datasource,
            "managed-conversion",
            {"document": True},
        )

        complete_datasource_conversion_task(
            "managed-conversion",
            {
                "status": "success",
                "conversion_summary": {
                    "total": 3,
                    "success": 1,
                    "failed": 1,
                    "skipped": 1,
                    "unsupported": 0,
                    "items": [
                        {
                            "path": "report.docx",
                            "status": "converted",
                            "reason": "",
                        }
                    ],
                },
            },
        )

        task = TaskExecution.objects.get(task_id="managed-conversion")
        datasource.refresh_from_db()
        self.assertEqual(task.status, "SUCCESS")
        self.assertEqual(task.result["overall_status"], "succeeded")
        self.assertEqual(task.result["conversion_summary"]["failed"], 1)
        self.assertEqual(datasource.last_conversion_status, "SUCCESS")
        self.assertIsNotNone(datasource.last_conversion_at)

    def test_lensnode_conversion_done_frame_completes_task(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )
        task = register_datasource_conversion_task(
            datasource,
            "conversion-frame",
            {"document": True},
        )
        token = issue_lensnode_token(self.lensnode)

        async_to_sync(self._exercise_lensnode_conversion_done)(
            token,
            task.task_id,
        )

        task.refresh_from_db()
        self.assertEqual(task.status, "SUCCESS")
        self.assertEqual(task.result["overall_status"], "succeeded")
        self.assertEqual(task.result["conversion_summary"]["success"], 1)

    def test_late_conversion_callback_does_not_override_cancellation(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
            last_conversion_status="REVOKED",
            last_conversion_at=timezone.now(),
        )
        task = register_datasource_conversion_task(
            datasource,
            "cancelled-conversion",
            {"document": True},
        )
        task.status = "REVOKED"
        task.finished_at = timezone.now()
        task.save(update_fields=["status", "finished_at"])

        complete_datasource_conversion_task(
            task.task_id,
            {
                "status": "success",
                "conversion_summary": {"success": 1},
            },
        )

        task.refresh_from_db()
        datasource.refresh_from_db()
        self.assertEqual(task.status, "REVOKED")
        self.assertEqual(datasource.last_conversion_status, "REVOKED")
        self.assertEqual(
            task.metadata["conversion_summary"]["success"],
            1,
        )

    def test_revoked_conversion_is_not_dispatched_when_celery_starts_late(
        self,
    ):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
            last_conversion_status="REVOKED",
            last_conversion_at=timezone.now(),
        )
        task = register_datasource_conversion_task(
            datasource,
            "revoked-conversion",
            {"document": True},
        )
        task.status = "REVOKED"
        task.finished_at = timezone.now()
        task.save(update_fields=["status", "finished_at"])

        with patch(
            "lens.tasks.dispatch_datasource_conversion_async"
        ) as dispatch:
            result = datasource_conversion_task(
                str(datasource.uuid),
                {"document": True},
                False,
                task.task_id,
            )

        self.assertEqual(result, 0)
        dispatch.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.status, "REVOKED")

    def test_conversion_cancelled_during_dispatch_stays_revoked(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )
        task = register_datasource_conversion_task(
            datasource,
            "cancelled-during-dispatch",
            {"document": True},
        )

        def dispatch(*args, **kwargs):
            del args, kwargs
            execution = TaskExecution.objects.get(task_id=task.task_id)
            execution.status = "REVOKED"
            execution.finished_at = timezone.now()
            execution.save(update_fields=["status", "finished_at"])
            return "late-conversion-request"

        with (
            patch(
                "lens.tasks.dispatch_datasource_conversion_async",
                side_effect=dispatch,
            ),
            patch(
                "lens.services."
                "cancel_datasource_conversion_on_lensnode"
            ) as cancel,
        ):
            datasource_conversion_task(
                str(datasource.uuid),
                {"document": True},
                False,
                task.task_id,
            )

        task.refresh_from_db()
        self.assertEqual(task.status, "REVOKED")
        cancel.assert_called_once_with(self.lensnode, task.task_id)

    def test_conversion_callback_maps_safe_cancellation_to_revoked(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )
        register_datasource_conversion_task(
            datasource,
            "cancelled-by-node",
            {"document": True},
        )

        complete_datasource_conversion_task(
            "cancelled-by-node",
            {
                "status": "cancelled",
                "error": "DATASOURCE_CONVERSION_CANCELLED",
            },
        )

        task = TaskExecution.objects.get(task_id="cancelled-by-node")
        datasource.refresh_from_db()
        self.assertEqual(task.status, "REVOKED")
        self.assertEqual(task.result["overall_status"], "cancelled")
        self.assertEqual(datasource.last_conversion_status, "REVOKED")

    def test_conversion_progress_persists_lifecycle_counts(self):
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
        )
        register_datasource_conversion_task(
            datasource,
            "live-managed-conversion",
            {"document": True},
        )

        LensNodeConsumer._record_datasource_sync_event(
            "live-managed-conversion",
            {
                "step": "conversion_progress",
                "status": "running",
                "category": "conversion",
                "message": "Converted 1/3 datasource files.",
                "progress_total": 3,
                "progress_current": 1,
                "progress_percent": 33,
                "summary": {
                    "total": 3,
                    "waiting": 1,
                    "active": 1,
                    "succeeded": 1,
                    "failed": 0,
                    "skipped": 0,
                    "unsupported": 0,
                },
                "current_file": "report.docx",
                "current_status": "converted",
            },
        )

        task = TaskExecution.objects.get(task_id="live-managed-conversion")
        summary = task.metadata["conversion_summary"]
        self.assertEqual(task.metadata["progress_percent"], 33)
        self.assertEqual(summary["waiting"], 1)
        self.assertEqual(summary["active"], 1)
        self.assertEqual(summary["succeeded"], 1)

    def test_source_sync_task_reuses_registered_task_id(self):
        task_id = "manual-sync"
        register_datasource_sync_task(
            self.datasource,
            task_id,
            "manual",
            created_by=self.user,
            metadata={"celery_task_id": "celery-sync"},
        )

        with patch("lens.tasks.dispatch_datasource_sync_async") as dispatch:
            dispatch.return_value = "request-1"
            synced = source_sync_task(
                str(self.datasource.uuid),
                "manual",
                task_id,
            )

        self.assertEqual(synced, 0)
        self.assertEqual(
            TaskExecution.objects.filter(module="lens_datasource").count(),
            1,
        )
        task = TaskExecution.objects.get(task_id=task_id)
        self.assertEqual(task.status, "STARTED")
        self.assertEqual(task.created_by, self.user)
        self.assertEqual(task.metadata["celery_task_id"], "celery-sync")
        dispatch.assert_called_once_with(
            self.datasource,
            task_id=task_id,
            trigger="manual",
        )

    def test_datasource_sync_task_metadata_includes_conversion_policy(self):
        self.datasource.sync_policy = {
            "interval_seconds": 3600,
            "conversion": {
                "document": True,
                "image": False,
            },
        }
        self.datasource.save(update_fields=["sync_policy"])

        task = register_datasource_sync_task(
            self.datasource,
            "conversion-sync",
            "manual",
        )

        self.assertTrue(task.metadata["conversion_enabled"])
        self.assertEqual(
            task.metadata["conversion"],
            {
                "document": True,
                "image": False,
            },
        )

    def test_datasource_sync_dispatch_includes_max_workers(self):
        GlobalSetting.objects.create(
            key="lens.datasource_sync.workers",
            value=8,
            description="",
        )

        with patch("lens.datasource_services._send_lensnode_command") as send:
            dispatch_datasource_sync_async(
                self.datasource,
                task_id="task-1",
                trigger="manual",
            )

        payload = send.call_args.args[1]
        self.assertEqual(payload["max_workers"], 8)

    def test_complete_datasource_sync_task_updates_records(self):
        with patch("lens.tasks.dispatch_datasource_sync_async") as dispatch:
            dispatch.return_value = "request-1"
            source_sync_task(str(self.datasource.uuid))

        task = TaskExecution.objects.get(module="lens_datasource")
        complete_datasource_sync_task(
            task.task_id,
            {
                "status": "success",
                "synced": 1,
                "files": 3,
                "target_path": self.datasource.target_path,
            },
        )

        self.datasource.refresh_from_db()
        record = ScheduledTask.objects.get(
            task_type="source_sync",
            target_type="datasource",
            target_id=self.datasource.uuid,
        )
        task.refresh_from_db()
        self.assertIsNotNone(self.datasource.last_synced_at)
        self.assertEqual(record.last_status, "success")
        self.assertEqual(
            record.last_metrics,
            {
                "synced": 1,
                "files": 3,
                "folders": 0,
                "failed": 0,
                "scanned": 0,
                "changed": 1,
                "skipped": 0,
                "deleted": 0,
                "documents": 0,
                "by_extension": {},
                "by_type": {},
                "repository_summaries": [],
                "failed_repositories": [],
                "partial_success": False,
                "target_path": self.datasource.target_path,
            },
        )
        self.assertEqual(task.status, "SUCCESS")
        self.assertEqual(task.metadata["progress_percent"], 100)

    def test_complete_datasource_sync_task_preserves_zero_changed(self):
        with patch("lens.tasks.dispatch_datasource_sync_async") as dispatch:
            dispatch.return_value = "request-1"
            source_sync_task(str(self.datasource.uuid))

        task = TaskExecution.objects.get(module="lens_datasource")
        complete_datasource_sync_task(
            task.task_id,
            {
                "status": "success",
                "synced": 4,
                "changed": 0,
                "skipped": 4,
                "files": 4,
                "target_path": self.datasource.target_path,
            },
        )

        record = ScheduledTask.objects.get(
            task_type="source_sync",
            target_type="datasource",
            target_id=self.datasource.uuid,
        )
        task.refresh_from_db()
        self.assertEqual(record.last_metrics["changed"], 0)
        self.assertEqual(task.result["changed"], 0)
        self.assertEqual(task.metadata["sync_summary"]["changed"], 0)

    def test_complete_datasource_sync_task_keeps_summaries_separate(self):
        with patch("lens.tasks.dispatch_datasource_sync_async") as dispatch:
            dispatch.return_value = "request-1"
            source_sync_task(str(self.datasource.uuid))

        task = TaskExecution.objects.get(module="lens_datasource")
        complete_datasource_sync_task(
            task.task_id,
            {
                "status": "success",
                "synced": 1,
                "changed": 1,
                "files": 1,
                "details": {
                    "changed": [
                        {
                            "path": "README.md",
                            "name": "README.md",
                            "status": "synced",
                        }
                    ]
                },
                "changed_items": [
                    {
                        "path": "README.md",
                        "name": "README.md",
                        "status": "synced",
                    }
                ],
                "conversion_summary": {
                    "candidates": 1,
                    "converted": 1,
                    "items": [
                        {
                            "path": "README.md",
                            "name": "README.md",
                            "status": "converted",
                        }
                    ],
                },
                "target_path": self.datasource.target_path,
            },
        )

        task.refresh_from_db()
        self.assertIn("conversion_summary", task.metadata)
        self.assertNotIn(
            "conversion_summary",
            task.metadata["sync_summary"],
        )
        self.assertNotIn("conversion_summary", task.result)
        self.assertNotIn("details", task.result)
        self.assertNotIn("changed_items", task.metadata["sync_summary"])
        self.assertNotIn("changed_items", task.result)
        self.assertEqual(
            task.metadata["sync_summary"]["details"]["changed"][0]["path"],
            "README.md",
        )

    def test_datasource_sync_event_updates_realtime_sync_details(self):
        TaskExecution.objects.create(
            task_id="live-sync",
            task_name="datasource_sync:Repo Cache",
            module="lens_datasource",
            status="STARTED",
            metadata={
                "datasource_uuid": str(self.datasource.uuid),
                "sync_summary": {"changed": 1},
            },
        )

        LensNodeConsumer._record_datasource_sync_event(
            "live-sync",
            {
                "step": "item_done",
                "status": "done",
                "message": "Downloaded README.md.",
                "kind": "file",
                "item_name": "README.md",
                "file": "README.md",
                "file_extension": "md",
            },
        )

        task = TaskExecution.objects.get(task_id="live-sync")
        details = task.metadata["sync_summary"]["details"]
        self.assertEqual(details["changed"][0]["path"], "README.md")
        self.assertEqual(details["success"][0]["name"], "README.md")

    def test_datasource_sync_event_updates_realtime_conversion_details(self):
        TaskExecution.objects.create(
            task_id="live-conversion",
            task_name="datasource_sync:Repo Cache",
            module="lens_datasource",
            status="STARTED",
            metadata={
                "datasource_uuid": str(self.datasource.uuid),
                "conversion_summary": {"converted": 1},
            },
        )

        LensNodeConsumer._record_datasource_sync_event(
            "live-conversion",
            {
                "step": "conversion_progress",
                "status": "running",
                "category": "conversion",
                "message": "Converted 1/1 datasource files.",
                "summary": {"converted": 1, "success": 1},
                "current_file": "README.md",
                "current_status": "converted",
                "current_stats": {
                    "chars": 120,
                    "cost": {"model_calls": 1, "total_tokens": 30},
                },
            },
        )

        task = TaskExecution.objects.get(task_id="live-conversion")
        details = task.metadata["conversion_summary"]["details"]
        self.assertEqual(details["converted"][0]["path"], "README.md")
        self.assertEqual(details["model_calls"][0]["stats"]["chars"], 120)

    def test_source_sync_task_marks_invalid_source_failed(self):
        with patch("lens.tasks.dispatch_datasource_sync_async") as dispatch:
            dispatch.side_effect = RuntimeError("LENS_SOURCE_CONFIG_INVALID")
            with self.assertRaises(RuntimeError):
                source_sync_task(str(self.datasource.uuid))

        self.datasource.refresh_from_db()
        record = ScheduledTask.objects.get(
            task_type="source_sync",
            target_type="datasource",
            target_id=self.datasource.uuid,
        )
        self.assertEqual(self.datasource.status, "active")
        self.assertEqual(
            self.datasource.last_error,
            "LENS_SOURCE_CONFIG_INVALID",
        )
        self.assertEqual(record.last_status, "failed")

    def test_complete_datasource_sync_task_marks_failure(self):
        with patch("lens.tasks.dispatch_datasource_sync_async") as dispatch:
            dispatch.return_value = "request-1"
            source_sync_task(str(self.datasource.uuid))

        task = TaskExecution.objects.get(module="lens_datasource")
        complete_datasource_sync_task(
            task.task_id,
            {
                "status": "failed",
                "error": "LENS_SOURCE_CONFIG_INVALID",
            },
        )

        self.datasource.refresh_from_db()
        record = ScheduledTask.objects.get(
            task_type="source_sync",
            target_type="datasource",
            target_id=self.datasource.uuid,
        )
        task.refresh_from_db()
        self.assertEqual(
            self.datasource.last_error,
            "LENS_SOURCE_CONFIG_INVALID",
        )
        self.assertEqual(record.last_status, "failed")
        self.assertEqual(task.status, "FAILURE")

    def test_source_sync_task_rejects_concurrent_sync(self):
        # Simulate a real in-flight sync: a running task owns the lock. The
        # orphan-reclaim must keep its hands off an owned lock, so a second
        # sync is rejected as busy. (A bare lock with no owning task is now
        # treated as orphaned and reclaimable, so it would not be rejected.)
        owner_token = "owner-sync"
        acquire_datasource_lock(self.datasource.uuid, token=owner_token)
        TaskExecution.objects.create(
            task_id=owner_token,
            task_name="datasource_sync:Repo Cache",
            module="lens_datasource",
            status="STARTED",
            metadata={
                "datasource_uuid": str(self.datasource.uuid),
                "lock_token": owner_token,
            },
        )
        try:
            synced = source_sync_task(
                str(self.datasource.uuid), task_id="rejected-sync"
            )
        finally:
            release_datasource_lock(self.datasource.uuid, token=owner_token)

        self.datasource.refresh_from_db()
        record = ScheduledTask.objects.get(
            task_type="source_sync",
            target_type="datasource",
            target_id=self.datasource.uuid,
        )
        task = TaskExecution.objects.get(task_id="rejected-sync")
        self.assertEqual(synced, 0)
        self.assertEqual(self.datasource.status, "active")
        self.assertEqual(record.last_status, "running")
        self.assertEqual(record.last_error, "LENS_SOURCE_SYNC_BUSY")
        self.assertEqual(task.status, "REVOKED")
        self.assertEqual(task.error, "LENS_SOURCE_SYNC_BUSY")
        self.assertEqual(task.metadata["progress_step"], "lock")
        self.assertEqual(
            task.metadata["progress_message"],
            "LENS_SOURCE_SYNC_BUSY",
        )

    def test_cleanup_stale_datasource_sync_releases_lock(self):
        GlobalSetting.objects.create(
            key="lens.datasource_sync.timeout_s",
            value="1",
        )
        task = TaskExecution.objects.create(
            task_id="stale-sync",
            task_name="datasource_sync:Repo Cache",
            module="lens_datasource",
            status="STARTED",
            started_at=timezone.now() - timedelta(seconds=2),
            metadata={
                "datasource_uuid": str(self.datasource.uuid),
                "lock_token": "stale-sync",
            },
        )
        acquire_datasource_lock(
            self.datasource.uuid,
            token="stale-sync",
            ttl_s=60,
        )

        with patch(
            "lens.services.cancel_datasource_sync_on_lensnode"
        ) as cancel:
            result = cleanup_stale_datasource_sync_tasks()

        task.refresh_from_db()
        record = ScheduledTask.objects.get(
            task_type="source_sync",
            target_type="datasource",
            target_id=self.datasource.uuid,
        )
        self.assertEqual(result["failed"], 1)
        self.assertEqual(task.status, "FAILURE")
        self.assertEqual(task.error, "LENS_SOURCE_SYNC_TIMEOUT")
        self.assertEqual(record.last_status, "failed")
        self.assertEqual(record.last_error, "LENS_SOURCE_SYNC_TIMEOUT")
        cancel.assert_called_once_with(self.lensnode, "stale-sync")

        acquire_datasource_lock(
            self.datasource.uuid,
            token="new-sync",
            ttl_s=60,
        )
        release_datasource_lock(self.datasource.uuid, token="new-sync")

    def test_cleanup_stale_datasource_conversion_releases_lock(self):
        GlobalSetting.objects.create(
            key="lens.datasource_sync.timeout_s",
            value="1",
        )
        GlobalSetting.objects.create(
            key="lens.datasource_conversion.timeout_s",
            value="1",
        )
        datasource = DataSource.objects.create(
            name="Managed Snapshot",
            source_type=DataSource.SourceType.MANAGED_WORKSPACE,
            lensnode=self.lensnode,
            target_path="/workspace/restores/finance",
            last_conversion_status="STARTED",
        )
        task = TaskExecution.objects.create(
            task_id="stale-conversion",
            task_name="datasource_convert:Managed Snapshot",
            module="lens_datasource_conversion",
            status="STARTED",
            started_at=timezone.now() - timedelta(seconds=2),
            metadata={
                "datasource_uuid": str(datasource.uuid),
                "lock_token": "stale-conversion",
            },
        )
        acquire_datasource_lock(
            datasource.uuid,
            token="stale-conversion",
            ttl_s=60,
        )

        with patch(
            "lens.services.cancel_datasource_conversion_on_lensnode"
        ) as cancel:
            result = cleanup_stale_datasource_sync_tasks()

        task.refresh_from_db()
        datasource.refresh_from_db()
        self.assertEqual(result["failed"], 0)
        self.assertEqual(task.status, "CANCELLING")
        self.assertEqual(task.error, "")
        self.assertEqual(datasource.last_conversion_status, "CANCELLING")
        self.assertIsNone(datasource.last_conversion_at)
        cancel.assert_called_once_with(self.lensnode, "stale-conversion")
        self.assertFalse(
            ScheduledTask.objects.filter(
                target_type="datasource",
                target_id=datasource.uuid,
            ).exists()
        )

        complete_datasource_conversion_task(
            task.task_id,
            {
                "status": "cancelled",
                "error": "DATASOURCE_CONVERSION_TIMEOUT",
                "completion_reason": "DATASOURCE_CONVERSION_TIMEOUT",
                "stop_confirmation_source": "lensnode_callback",
            },
        )

        task.refresh_from_db()
        datasource.refresh_from_db()
        self.assertEqual(task.status, "REVOKED")
        self.assertEqual(task.error, "DATASOURCE_CONVERSION_TIMEOUT")
        self.assertEqual(datasource.last_conversion_status, "REVOKED")
        self.assertIsNotNone(datasource.last_conversion_at)

        acquire_datasource_lock(
            datasource.uuid,
            token="new-conversion",
            ttl_s=60,
        )
        release_datasource_lock(
            datasource.uuid,
            token="new-conversion",
        )

    def test_startup_cleanup_keeps_fresh_datasource_sync_running(self):
        GlobalSetting.objects.create(
            key="lens.datasource_sync.timeout_s",
            value="60",
        )
        task = TaskExecution.objects.create(
            task_id="fresh-sync",
            task_name="datasource_sync:Repo Cache",
            module="lens_datasource",
            status="STARTED",
            started_at=timezone.now(),
            metadata={
                "datasource_uuid": str(self.datasource.uuid),
                "lock_token": "fresh-sync",
                "completion_source": "lensnode_callback",
            },
        )
        acquire_datasource_lock(
            self.datasource.uuid,
            token="fresh-sync",
            ttl_s=60,
        )

        with patch(
            "lens.services.cancel_datasource_sync_on_lensnode"
        ) as cancel:
            result = cleanup_stale_datasource_sync_tasks(startup=True)

        task.refresh_from_db()
        self.assertEqual(result["failed"], 0)
        self.assertEqual(task.status, "STARTED")
        cancel.assert_not_called()
        self.assertFalse(
            release_datasource_lock(
                self.datasource.uuid,
                token="other-sync",
            )
        )
        release_datasource_lock(self.datasource.uuid, token="fresh-sync")

    def test_cleanup_releases_completed_datasource_sync_lock(self):
        GlobalSetting.objects.create(
            key="lens.datasource_sync.timeout_s",
            value="60",
        )
        TaskExecution.objects.create(
            task_id="timed-out-sync",
            task_name="datasource_sync:Repo Cache",
            module="lens_datasource",
            status="FAILURE",
            finished_at=timezone.now(),
            metadata={
                "datasource_uuid": str(self.datasource.uuid),
                "lock_token": "timed-out-sync",
            },
        )
        acquire_datasource_lock(
            self.datasource.uuid,
            token="timed-out-sync",
            ttl_s=60,
        )

        with patch(
            "lens.services.cancel_datasource_sync_on_lensnode"
        ) as cancel:
            result = cleanup_stale_datasource_sync_tasks()

        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["locks_released"], 1)
        cancel.assert_called_once_with(self.lensnode, "timed-out-sync")
        acquire_datasource_lock(
            self.datasource.uuid,
            token="new-sync",
            ttl_s=60,
        )
        release_datasource_lock(self.datasource.uuid, token="new-sync")

    def test_acquire_datasource_lock_recovers_completed_owner_lock(self):
        TaskExecution.objects.create(
            task_id="completed-sync",
            task_name="datasource_sync:Repo Cache",
            module="lens_datasource",
            status="REVOKED",
            metadata={
                "datasource_uuid": str(self.datasource.uuid),
                "lock_token": "completed-sync",
            },
        )
        acquire_datasource_lock(
            self.datasource.uuid,
            token="completed-sync",
            ttl_s=60,
        )

        acquire_datasource_lock(
            self.datasource.uuid,
            token="new-sync",
            ttl_s=60,
        )
        release_datasource_lock(self.datasource.uuid, token="new-sync")

    def test_acquire_datasource_lock_recovers_ownerless_lock(self):
        acquire_datasource_lock(
            self.datasource.uuid,
            token="missing-owner",
            ttl_s=60,
        )

        acquire_datasource_lock(
            self.datasource.uuid,
            token="new-sync",
            ttl_s=60,
        )
        release_datasource_lock(self.datasource.uuid, token="new-sync")

    def test_source_sync_task_dispatches_feishu_datasource(self):
        self.datasource.source_type = DataSource.SourceType.FEISHU
        self.datasource.config = {
            "app_token": "app-token",
            "doc_ids": ["doc-1", "doc-2"],
        }
        self.datasource.save(update_fields=["source_type", "config"])

        with patch("lens.tasks.dispatch_datasource_sync_async") as dispatch:
            dispatch.return_value = "request-1"
            synced = source_sync_task(str(self.datasource.uuid))

        self.assertEqual(synced, 0)
        dispatch.assert_called_once()

    def test_lensnode_health_marks_stale_lensnodes_offline(self):
        self.lensnode.status = LensNode.Status.ONLINE
        self.lensnode.last_heartbeat_at = timezone.now() - timedelta(
            seconds=120
        )
        self.lensnode.save(update_fields=["status", "last_heartbeat_at"])

        marked = lensnode_health_task()

        self.lensnode.refresh_from_db()
        self.assertEqual(marked, 1)
        self.assertEqual(self.lensnode.status, LensNode.Status.OFFLINE)

    def test_register_periodic_tasks_adds_lens_entries(self):
        TASK_REGISTRY.clear()

        register_periodic_tasks()

        self.assertEqual(
            ScheduledTask.objects.filter(
                task_type="lensnode_cleanup",
                target_type=None,
            ).count(),
            1,
        )
        self.assertEqual(
            ScheduledTask.objects.filter(
                task_type="lensnode_health",
                target_type=None,
            ).count(),
            1,
        )
        self.assertGreaterEqual(len(TASK_REGISTRY), 4)

    def test_register_periodic_tasks_uses_global_interval_settings(self):
        GlobalSetting.objects.create(
            key="lensnode_cleanup.interval_seconds",
            value=1800,
        )
        GlobalSetting.objects.create(
            key="lensnode_health.interval_seconds",
            value=120,
        )
        GlobalSetting.objects.create(
            key="run_retention.interval_seconds",
            value=7200,
        )

        TASK_REGISTRY.clear()
        discover_and_register()

        cleanup = PeriodicTask.objects.get(name="lens-lensnode-cleanup")
        health = PeriodicTask.objects.get(name="lens-lensnode-health")
        retention = PeriodicTask.objects.get(name="lens-run-retention")

        self.assertEqual(cleanup.interval.every, 1800)
        self.assertEqual(health.interval.every, 120)
        self.assertEqual(retention.interval.every, 7200)

    def test_datasource_periodic_task_updates_existing_beat_row(self):
        record = ensure_datasource_periodic_task(self.datasource)
        task = PeriodicTask.objects.get(pk=record.periodic_task_ref)
        task.enabled = False
        task.save(update_fields=["enabled"])

        self.datasource.sync_policy = {"interval_seconds": 120}
        self.datasource.save(update_fields=["sync_policy", "updated_at"])

        ensure_datasource_periodic_task(self.datasource)

        task.refresh_from_db()
        self.assertTrue(task.enabled)
        self.assertEqual(task.interval.every, 120)
        self.assertEqual(task.interval.period, "seconds")
        self.assertEqual(task.task, "lens.source_sync")
        self.assertEqual(task.args, f'["{self.datasource.uuid}"]')
        self.assertEqual(task.queue, "lens")

    def test_datasource_periodic_task_supports_crontab_policy(self):
        self.datasource.sync_policy = {
            "mode": "crontab",
            "cron": "0 2 * * *",
            "timezone": "Asia/Shanghai",
        }
        self.datasource.save(update_fields=["sync_policy", "updated_at"])

        record = ensure_datasource_periodic_task(self.datasource)

        task = PeriodicTask.objects.get(pk=record.periodic_task_ref)
        self.assertIsNone(task.interval_id)
        self.assertIsNotNone(task.crontab_id)
        self.assertEqual(task.crontab.minute, "0")
        self.assertEqual(task.crontab.hour, "2")
        self.assertEqual(str(task.crontab.timezone), "Asia/Shanghai")

    def test_discover_and_register_reconciles_datasource_beat_row(self):
        record = ensure_datasource_periodic_task(self.datasource)
        task = PeriodicTask.objects.get(pk=record.periodic_task_ref)
        task.enabled = False
        task.save(update_fields=["enabled"])
        self.datasource.sync_policy = {"interval_seconds": 120}
        self.datasource.save(update_fields=["sync_policy", "updated_at"])

        discover_and_register()

        task.refresh_from_db()
        self.assertTrue(task.enabled)
        self.assertEqual(task.interval.every, 120)

    def test_discover_and_register_backfills_periodic_task_refs(self):
        discover_and_register()

        task_types = {
            "lensnode_cleanup",
            "lensnode_health",
            "run_retention",
            "source_sync",
        }
        tasks = ScheduledTask.objects.filter(task_type__in=task_types)
        self.assertTrue(tasks.exists())
        self.assertFalse(tasks.filter(periodic_task_ref__isnull=True).exists())

    def _local_git_repo(self):
        """Create a temporary git repo for source sync tests."""

        import shutil
        import subprocess
        import tempfile

        @contextmanager
        def repo_context():
            root = Path(tempfile.mkdtemp(prefix="lens-git-source-"))
            try:
                subprocess.run(
                    ["git", "init", "-b", "main"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    ["git", "config", "user.email", "lens@example.com"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    ["git", "config", "user.name", "Lens Test"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                (root / "README.md").write_text("hello lens\n")
                subprocess.run(
                    ["git", "add", "README.md"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    ["git", "commit", "-m", "Initial commit"],
                    cwd=root,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                yield str(root)
            finally:
                shutil.rmtree(root, ignore_errors=True)

        return repo_context()

    def _target_path(self):
        """Create an empty target path value for source sync tests."""

        import shutil
        import tempfile

        @contextmanager
        def target_context():
            root = Path(tempfile.mkdtemp(prefix="lens-target-"))
            target = root / "cache"
            try:
                yield str(target)
            finally:
                shutil.rmtree(root, ignore_errors=True)

        return target_context()
