import uuid

from django.test import TestCase

from lens.evaluation.engine.runner import evaluation_candidate_nodes, evaluation_execution_pool
from lens.models import Assistant, GlobalSetting, LensNode
from lens.serializers import GlobalSettingSerializer


class EvaluationExecutionPoolTests(TestCase):
    def setUp(self):
        self.default = LensNode.objects.create(
            name="Default", pool="default", status="online", enrollment_status="approved"
        )
        self.dedicated = LensNode.objects.create(
            name="Evaluation", pool="evaluation", status="online", enrollment_status="approved"
        )
        self.assistant = Assistant.objects.create(
            name="Assistant", slug="pool-test", lensnode=self.default,
            selected_task="knowledge_qa", agent_model_ref=uuid.uuid4(),
        )

    def test_default_is_used_even_when_dedicated_pool_exists(self):
        self.assertEqual(evaluation_execution_pool(), "default")
        self.assertEqual(evaluation_candidate_nodes(self.assistant), [self.default])

    def test_dedicated_pool_is_used_for_default_bound_assistant(self):
        GlobalSetting.objects.create(key="evaluation.execution_pool", value="evaluation")
        self.assertEqual(evaluation_candidate_nodes(self.assistant), [self.dedicated])
        self.dedicated.status = "offline"
        self.dedicated.save(update_fields=["status"])
        self.assertEqual(evaluation_candidate_nodes(self.assistant), [])

    def test_default_pool_does_not_fall_back_to_dedicated_node(self):
        self.default.status = "offline"
        self.default.save(update_fields=["status"])
        self.assertEqual(evaluation_candidate_nodes(self.assistant), [])

    def test_invalid_pool_is_rejected(self):
        for value in ("other", "", None, {}, ["default"]):
            serializer = GlobalSettingSerializer(data={"key": "evaluation.execution_pool", "value": value})
            self.assertFalse(serializer.is_valid(), value)
