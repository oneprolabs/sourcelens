"""Notification policies, bounded diagnosis and recipient-language behavior."""

import json
from datetime import timedelta
from unittest.mock import patch

from agentcore_notifier.adapters.django.models import NotificationChannel
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from lens.alerts import evaluate_run_alerts, send_alert_delivery
from lens.models import AlertDelivery, AlertRule, AlertSubscription, Assistant, LensNode, Run, Session
from lens.services import create_execution_run


class AlertDiagnosisTests(TestCase):
    """Exercise persisted policies with model calls and external transports stubbed."""

    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="diagnosis-operator", is_staff=True)
        self.user = get_user_model().objects.create_user(username="diagnosis-user", email="person@example.test")
        self.node = LensNode.objects.create(name="diagnosis-node")
        self.assistant = Assistant.objects.create(name="Private assistant", slug="diagnosis", lensnode=self.node)
        self.session = Session.objects.create(assistant=self.assistant, user=self.user)
        self.channel = NotificationChannel.objects.create(name="Local email", channel_type="email", config={})
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def rule(self, mode="direct", language="en-US"):
        rule = AlertRule.objects.create(name="Failure policy", events=["run_failed"], diagnosis_mode=mode)
        AlertSubscription.objects.create(
            rule=rule, channel_uuid=self.channel.uuid, email_recipients=["person@example.test"], language=language
        )
        return rule

    def occurrence(self):
        run = create_execution_run(session=self.session, question="Private question", enqueue=False)
        run.status = Run.Status.FAILED
        run.error = "RUN_TIMEOUT"
        run.finished_at = timezone.now()
        run.save()
        evaluate_run_alerts(run.uuid)
        return run

    def test_policy_and_destination_language_round_trip(self):
        response = self.client.post(
            "/api/lens/admin/alert-rules/",
            {
                "name": "Diagnose timeout",
                "events": ["run_timeout"],
                "diagnosis_mode": "diagnose_first",
                "diagnosis_timeout_seconds": 30,
                "diagnosis_max_tokens": 1024,
                "subscriptions": [
                    {"channel_uuid": str(self.channel.uuid), "email_recipients": ["ops@example.test"], "language": "es"}
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        data = response.data.get("data", response.data)
        self.assertEqual(data["diagnosis_mode"], "diagnose_first")
        self.assertEqual(data["subscriptions"][0]["language"], "es")

    def test_chinese_direct_notification(self):
        self.rule(language="zh-CN")
        self.occurrence()
        with patch("lens.alerts._send_notification", return_value={"success": True}) as send:
            send_alert_delivery(AlertDelivery.objects.get().uuid)
        self.assertIn("问答失败", send.call_args.kwargs["params"]["subject"])
        self.assertIn("触发条件", send.call_args.kwargs["params"]["body"])
        self.assertEqual(AlertDelivery.objects.get().destination["language"], "zh-CN")

    def test_diagnose_first_waits_without_spending_delivery_attempts(self):
        from lens.alert_diagnostics import advance_alert_diagnoses
        from lens.models import AlertDiagnosis

        self.rule("diagnose_first")
        self.occurrence()
        delivery = AlertDelivery.objects.get()
        self.assertEqual(delivery.status, "diagnosing")
        with patch("lens.alerts._send_notification") as send:
            send_alert_delivery(delivery.uuid)
        send.assert_not_called()
        delivery.refresh_from_db()
        self.assertEqual(delivery.attempts, 0)
        AlertDiagnosis.objects.update(status="completed", result={"summary": "The configured execution timed out."})
        advance_alert_diagnoses()
        with patch("lens.alerts._send_notification", return_value={"success": True}) as send:
            send_alert_delivery(delivery.uuid)
        self.assertIn("The configured execution timed out.", send.call_args.kwargs["params"]["body"])

    def test_timeout_falls_back_to_spanish_basic_alert_and_discards_late_work(self):
        from lens.alert_diagnostics import advance_alert_diagnoses, execute_alert_diagnosis
        from lens.models import AlertDiagnosis

        self.rule("diagnose_first", language="es")
        self.occurrence()
        AlertDiagnosis.objects.update(deadline_at=timezone.now() - timedelta(seconds=1))
        advance_alert_diagnoses()
        diagnosis = AlertDiagnosis.objects.get()
        self.assertEqual(diagnosis.status, "timed_out")
        with patch("agentcore_metering.adapters.django.LLMTracker.call_and_track") as model:
            self.assertFalse(execute_alert_diagnosis(diagnosis.uuid))
        model.assert_not_called()
        with patch("lens.alerts._send_notification", return_value={"success": True}) as send:
            send_alert_delivery(AlertDelivery.objects.get().uuid)
        body = send.call_args.kwargs["params"]["body"]
        self.assertIn("Diagnóstico agotado", body)
        self.assertIn("Condiciones", body)

    def test_supplement_is_once_and_only_after_successful_initial_notification(self):
        from lens.alert_diagnostics import advance_alert_diagnoses
        from lens.models import AlertDiagnosis

        self.rule("notify_then_diagnose", language="zh-CN")
        self.occurrence()
        initial = AlertDelivery.objects.get()
        AlertDiagnosis.objects.update(status="completed", result={"summary": "执行达到超时限制。"})
        advance_alert_diagnoses()
        self.assertEqual(AlertDelivery.objects.count(), 1)
        with patch("lens.alerts._send_notification", return_value={"success": True}) as send:
            send_alert_delivery(initial.uuid)
        self.assertNotIn("执行达到超时限制。", send.call_args.kwargs["params"]["body"])
        advance_alert_diagnoses()
        advance_alert_diagnoses()
        supplement = AlertDelivery.objects.get(phase="diagnosis")
        with patch("lens.alerts._send_notification", return_value={"success": True}) as send:
            send_alert_delivery(supplement.uuid)
            send_alert_delivery(supplement.uuid)
        self.assertEqual(send.call_count, 1)
        self.assertIn("执行达到超时限制。", send.call_args.kwargs["params"]["body"])
        self.assertEqual(AlertDelivery.objects.count(), 2)

    def test_supplement_respects_revoked_subscription(self):
        from lens.alert_diagnostics import advance_alert_diagnoses
        from lens.models import AlertDiagnosis

        self.rule("notify_then_diagnose")
        self.occurrence()
        AlertDelivery.objects.update(status="sent")
        AlertDiagnosis.objects.update(status="completed", result={"summary": "Timeout"})
        advance_alert_diagnoses()
        AlertSubscription.objects.update(enabled=False)
        with patch("lens.alerts._send_notification") as send:
            send_alert_delivery(AlertDelivery.objects.get(phase="diagnosis").uuid)
        send.assert_not_called()

    def test_identical_rules_share_one_diagnosis_and_delivery_per_language(self):
        from lens.models import AlertDiagnosis

        self.rule("diagnose_first", "zh-CN")
        self.rule("diagnose_first", "zh-CN")
        self.rule("diagnose_first", "es")
        self.occurrence()
        self.assertEqual(AlertDiagnosis.objects.count(), 2)
        self.assertEqual(AlertDelivery.objects.filter(status="diagnosing").count(), 2)
        self.assertEqual(AlertDelivery.objects.filter(status="suppressed").count(), 1)

    def test_different_diagnosis_policies_and_budgets_are_not_silently_deduplicated(self):
        from lens.models import AlertDiagnosis

        first = self.rule("diagnose_first")
        first.diagnosis_max_tokens = 1024
        first.save()
        self.rule("diagnose_first")
        self.rule("direct")
        self.occurrence()
        self.assertEqual(AlertDelivery.objects.filter(status="pending").count(), 1)
        self.assertEqual(AlertDelivery.objects.filter(status="diagnosing").count(), 2)
        self.assertEqual(AlertDelivery.objects.filter(status="suppressed").count(), 0)
        self.assertEqual(set(AlertDiagnosis.objects.values_list("max_tokens", flat=True)), {1024, 2048})

    def test_diagnosis_survives_multiple_revoked_duplicate_owners(self):
        from lens.alert_diagnostics import advance_alert_diagnoses
        from lens.models import AlertDiagnosis

        for mode in ("diagnose_first", "notify_then_diagnose"):
            with self.subTest(mode=mode), transaction.atomic():
                for _ in range(3):
                    self.rule(mode)
                self.occurrence()
                original = AlertDelivery.objects.exclude(status="suppressed").get()
                duplicates = list(AlertDelivery.objects.filter(status="suppressed"))
                AlertSubscription.objects.filter(
                    pk__in=[original.subscription_id, duplicates[0].subscription_id]
                ).update(enabled=False)
                AlertDiagnosis.objects.update(status="completed", result={"summary": "Preserved diagnosis."})
                advance_alert_diagnoses()
                with patch("lens.alerts._send_notification", return_value={"success": True}) as send:
                    for _ in range(3):
                        send_alert_delivery(AlertDelivery.objects.get(status="pending").uuid)
                    send.assert_called_once()
                    if mode == "diagnose_first":
                        self.assertIn("Preserved diagnosis.", send.call_args.kwargs["params"]["body"])
                    else:
                        advance_alert_diagnoses()
                        advance_alert_diagnoses()
                        supplement = AlertDelivery.objects.get(phase="diagnosis")
                        send_alert_delivery(supplement.uuid)
                        self.assertEqual(send.call_count, 2)
                        self.assertIn("Preserved diagnosis.", send.call_args.kwargs["params"]["body"])
                self.assertEqual(AlertDelivery.objects.filter(phase="initial", status="sent").count(), 1)
                self.assertEqual(AlertDelivery.objects.filter(status="skipped").count(), 2)
                self.assertEqual(AlertDiagnosis.objects.count(), 1)
                transaction.set_rollback(True)

    def test_diagnosis_worker_is_bounded_idempotent_and_omits_private_content(self):
        from lens.alert_diagnostics import execute_alert_diagnosis
        from lens.models import AlertDiagnosis, GlobalSetting

        self.rule("diagnose_first", "es")
        GlobalSetting.objects.create(key="diagnostics.model_ref", value="11111111-1111-1111-1111-111111111111")
        self.occurrence()
        diagnosis = AlertDiagnosis.objects.get()
        result = {
            "summary": "Se agotó el tiempo de ejecución.",
            "severity": "high",
            "confidence": 0.9,
            "events": [],
            "root_cause": {
                "title": "Tiempo agotado",
                "description": "La ejecución agotó el plazo.",
                "evidence_refs": ["E-ALERT"],
            },
            "cause_categories": ["timeout"],
            "recommendations": [{"title": "Revisar", "action": "Revisar el límite.", "evidence_refs": ["E-ALERT"]}],
            "unknowns": [],
        }
        with patch(
            "agentcore_metering.adapters.django.LLMTracker.call_and_track", return_value=(json.dumps(result), {})
        ) as model:
            self.assertTrue(execute_alert_diagnosis(diagnosis.uuid))
            self.assertFalse(execute_alert_diagnosis(diagnosis.uuid))
        self.assertEqual(model.call_count, 1)
        self.assertEqual(model.call_args.kwargs["max_tokens"], 2048)
        self.assertIn("Spanish", model.call_args.kwargs["messages"][0]["content"])
        self.assertNotIn("Private question", model.call_args.kwargs["messages"][1]["content"])
        diagnosis.refresh_from_db()
        self.assertEqual(diagnosis.status, "completed")
        self.assertEqual(AlertDelivery.objects.get().status, "pending")

    def test_broker_failure_leaves_job_recoverable_with_hard_time_limit(self):
        from lens.alert_diagnostics import dispatch_alert_diagnoses
        from lens.models import AlertDiagnosis

        self.rule("diagnose_first")
        self.occurrence()
        with patch("lens.tasks.execute_alert_diagnosis_task.apply_async", side_effect=RuntimeError("offline")):
            dispatch_alert_diagnoses()
        self.assertIsNone(AlertDiagnosis.objects.get().dispatched_at)
        with patch("lens.tasks.execute_alert_diagnosis_task.apply_async") as enqueue:
            dispatch_alert_diagnoses()
            dispatch_alert_diagnoses()
        self.assertEqual(enqueue.call_count, 1)
        self.assertLessEqual(enqueue.call_args.kwargs["soft_time_limit"], 60)
        self.assertLessEqual(enqueue.call_args.kwargs["time_limit"], 65)

    def test_personal_delivery_uses_current_profile_language(self):
        from lens.models import AssistantAccess

        rule = self.rule()
        rule.personal_subscription_enabled = True
        rule.personal_channel_uuid = self.channel.uuid
        rule.save()
        subscription = AlertSubscription.objects.get()
        subscription.personal = True
        subscription.user = self.user
        subscription.save()
        AssistantAccess.objects.create(assistant=self.assistant, user=self.user)
        self.user.profile.language = "es"
        self.user.profile.save()
        self.occurrence()
        with patch("lens.alerts._send_notification", return_value={"success": True}) as send:
            send_alert_delivery(AlertDelivery.objects.get().uuid)
        self.assertIn("Error en consulta", send.call_args.kwargs["params"]["subject"])

    def test_invalid_policies_budgets_and_locales_are_rejected(self):
        cases = [
            {"subject": "message", "topic": "release.ready", "events": ["message_published"]},
            {"evaluation_mode": "running", "events": ["run_slow"], "duration_threshold": 5},
            {"diagnosis_timeout_seconds": 0},
            {"diagnosis_timeout_seconds": 301},
            {"diagnosis_max_tokens": 9000},
            {
                "subscriptions": [
                    {"channel_uuid": str(self.channel.uuid), "email_recipients": ["a@example.test"], "language": "xx"}
                ]
            },
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                response = self.client.post(
                    "/api/lens/admin/alert-rules/",
                    {
                        "name": "Invalid",
                        "events": ["run_failed"],
                        "diagnosis_mode": "diagnose_first",
                        **changes,
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, 400)

    def test_language_change_falls_back_without_mixing_old_diagnosis(self):
        from lens.alert_diagnostics import advance_alert_diagnoses
        from lens.models import AlertDiagnosis

        self.rule("diagnose_first", "zh-CN")
        self.occurrence()
        AlertDiagnosis.objects.update(status="completed", result={"summary": "旧的中文诊断"})
        AlertSubscription.objects.update(language="es")
        advance_alert_diagnoses()
        with patch("lens.alerts._send_notification", return_value={"success": True}) as send:
            send_alert_delivery(AlertDelivery.objects.get().uuid)
        self.assertNotIn("旧的中文诊断", send.call_args.kwargs["params"]["body"])
        self.assertIn("Diagnóstico no disponible", send.call_args.kwargs["params"]["body"])
        self.assertEqual(AlertDelivery.objects.get().destination["language"], "es")

    def test_supplement_skipped_after_policy_or_language_change(self):
        from lens.alert_diagnostics import advance_alert_diagnoses
        from lens.models import AlertDiagnosis

        for changes in ({"language": "es"}, {"policy": "direct"}):
            with self.subTest(changes=changes):
                AlertRule.objects.all().delete()
                self.rule("notify_then_diagnose")
                self.occurrence()
                AlertDelivery.objects.filter(phase="initial").update(status="sent")
                AlertDiagnosis.objects.update(status="completed", result={"summary": "Timeout"})
                advance_alert_diagnoses()
                if "language" in changes:
                    AlertSubscription.objects.update(language=changes["language"])
                else:
                    AlertRule.objects.update(diagnosis_mode="direct")
                supplement = AlertDelivery.objects.filter(phase="diagnosis", status="pending").get()
                with patch("lens.alerts._send_notification") as send:
                    send_alert_delivery(supplement.uuid)
                send.assert_not_called()

    def test_invalid_model_output_releases_basic_alert(self):
        from lens.alert_diagnostics import execute_alert_diagnosis
        from lens.models import AlertDiagnosis, GlobalSetting

        self.rule("diagnose_first")
        GlobalSetting.objects.create(key="diagnostics.model_ref", value="11111111-1111-1111-1111-111111111111")
        self.occurrence()
        with patch("agentcore_metering.adapters.django.LLMTracker.call_and_track", return_value=("{}", {})):
            self.assertFalse(execute_alert_diagnosis(AlertDiagnosis.objects.get().uuid))
        self.assertEqual(AlertDiagnosis.objects.get().status, "failed")
        self.assertEqual(AlertDelivery.objects.get().status, "pending")
        with patch("lens.alerts._send_notification", return_value={"success": True}) as send:
            send_alert_delivery(AlertDelivery.objects.get().uuid)
        self.assertIn("Diagnosis unavailable", send.call_args.kwargs["params"]["body"])

    def test_late_model_exception_is_recorded_as_timeout(self):
        from lens.alert_diagnostics import execute_alert_diagnosis
        from lens.models import AlertDiagnosis, GlobalSetting

        self.rule("diagnose_first")
        GlobalSetting.objects.create(key="diagnostics.model_ref", value="11111111-1111-1111-1111-111111111111")
        self.occurrence()

        def expire(**kwargs):
            AlertDiagnosis.objects.update(deadline_at=timezone.now() - timedelta(seconds=1))
            raise RuntimeError("Provider failure containing private details")

        with patch("agentcore_metering.adapters.django.LLMTracker.call_and_track", side_effect=expire):
            execute_alert_diagnosis(AlertDiagnosis.objects.get().uuid)
        self.assertEqual(AlertDiagnosis.objects.get().status, "timed_out")
        self.assertNotIn("private", AlertDiagnosis.objects.get().error_code)

    def test_task_diagnosis_uses_safe_failure_evidence(self):
        from agentcore_task.adapters.django.models import TaskExecution

        from lens.alert_diagnostics import _diagnostic_evidence
        from lens.alerts import capture_task_alert, process_alert_outbox
        from lens.models import AlertDiagnosis, GlobalSetting

        AlertRule.objects.create(
            name="Processing diagnosis",
            subject="processing",
            events=["processing_failed"],
            diagnosis_mode="diagnose_first",
        )
        rule = AlertRule.objects.get()
        AlertSubscription.objects.create(rule=rule, channel_uuid=self.channel.uuid, email_recipients=["a@example.test"])
        GlobalSetting.objects.create(key="diagnostics.model_ref", value="11111111-1111-1111-1111-111111111111")
        task = TaskExecution.objects.create(
            task_id="diagnose-processing",
            task_name="process",
            module="lens_datasource_conversion",
            status="FAILURE",
            finished_at=timezone.now(),
            metadata={"private": "secret"},
        )
        capture_task_alert(task)
        process_alert_outbox()
        evidence, model = _diagnostic_evidence(AlertDiagnosis.objects.get())
        self.assertEqual(evidence["evidence"]["E-TASK"]["data"]["status"], "FAILURE")
        self.assertNotIn("secret", str(evidence))

    def test_message_translations_render_and_are_part_of_idempotent_content(self):
        from lens.alerts import process_alert_outbox

        rule = AlertRule.objects.create(
            name="Release", subject="message", topic="release.ready", events=["message_published"]
        )
        AlertSubscription.objects.create(
            rule=rule, channel_uuid=self.channel.uuid, email_recipients=["ops@example.test"], language="es"
        )
        payload = {
            "topic": "release.ready",
            "idempotency_key": "release",
            "title": "Release",
            "body": "Ready",
            "translations": {"es": {"title": "Versión", "body": "Lista"}},
        }
        response = self.client.post("/api/lens/admin/alert-messages/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.client.post("/api/lens/admin/alert-messages/", payload, format="json").status_code, 200)
        changed = {**payload, "translations": {"es": {"title": "Versión", "body": "Cambiada"}}}
        self.assertEqual(self.client.post("/api/lens/admin/alert-messages/", changed, format="json").status_code, 409)
        process_alert_outbox()
        with patch("lens.alerts._send_notification", return_value={"success": True}) as send:
            send_alert_delivery(AlertDelivery.objects.get().uuid)
        self.assertEqual(send.call_args.kwargs["params"]["subject"], "[SourceLens] Versión")
        self.assertIn("Lista", send.call_args.kwargs["params"]["body"])

    def test_message_translation_requires_supported_language_and_complete_content(self):
        for translations in ({"ja": {"title": "A", "body": "B"}}, {"es": {"title": "A"}}):
            payload = {
                "topic": "release.ready",
                "idempotency_key": "release",
                "title": "Release",
                "body": "Ready",
                "translations": translations,
            }
            self.assertEqual(
                self.client.post("/api/lens/admin/alert-messages/", payload, format="json").status_code, 400
            )

    def test_oversized_execution_evidence_stays_within_input_budget(self):
        from lens.alert_diagnostics import _diagnostic_evidence
        from lens.models import AlertDiagnosis, GlobalSetting

        self.rule("diagnose_first")
        GlobalSetting.objects.create(key="diagnostics.model_ref", value="11111111-1111-1111-1111-111111111111")
        self.occurrence()
        with patch("lens.run_diagnostics._execution_evidence", return_value={"skills": ["x" * 30000]}):
            evidence, _ = _diagnostic_evidence(AlertDiagnosis.objects.get())
        self.assertLessEqual(len(json.dumps(evidence, ensure_ascii=False)), 24000)
        self.assertIn("execution_over_input_budget", evidence["missing_evidence"])

    def test_locale_keys_survive_camel_case_request_parser(self):
        translations = {
            language: {"title": language, "body": "Translated body"} for language in ("zh-CN", "en-US", "es")
        }
        response = self.client.post(
            "/api/lens/admin/alert-messages/",
            {
                "topic": "release.ready",
                "idempotencyKey": "locale-keys",
                "title": "Release",
                "body": "Ready",
                "translations": translations,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["translations"], translations)

    def test_deleted_subscription_does_not_create_or_repeat_follow_up_work(self):
        from lens.alert_diagnostics import advance_alert_diagnoses
        from lens.models import AlertDiagnosis

        self.rule("notify_then_diagnose")
        self.occurrence()
        AlertDelivery.objects.update(status="sent")
        AlertDiagnosis.objects.update(status="completed", result={"summary": "Timeout"})
        AlertSubscription.objects.all().delete()
        advance_alert_diagnoses()
        advance_alert_diagnoses()
        self.assertEqual(AlertDelivery.objects.count(), 1)
