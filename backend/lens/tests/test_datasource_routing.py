"""Regression coverage for explicit Assistant datasource selection."""

from django.test import TestCase

from lens.datasource_routing import selected_bindings
from lens.models import (
    Assistant,
    AssistantDataSourceBinding,
    DataSource,
)
from lens.serializers import AssistantSerializer


class DatasourceRoutingTests(TestCase):
    """Assistant datasource access must remain explicitly configured."""

    def setUp(self):
        self.assistant = Assistant.objects.create(
            name="Routing Assistant",
            slug="routing-assistant",
            capability=Assistant.Capability.KNOWLEDGE_QA,
            settings={"datasource_routing": "auto"},
        )
        self.active_source = DataSource.objects.create(
            name="Active Source",
            source_type=DataSource.SourceType.GIT,
        )
        self.disabled_source = DataSource.objects.create(
            name="Disabled Source",
            source_type=DataSource.SourceType.GIT,
            status=DataSource.Status.DISABLED,
        )
        AssistantDataSourceBinding.objects.create(
            assistant=self.assistant,
            datasource=self.active_source,
            mount_name="active",
        )
        AssistantDataSourceBinding.objects.create(
            assistant=self.assistant,
            datasource=self.disabled_source,
            mount_name="disabled",
        )

    def test_selected_bindings_returns_every_explicit_binding(self):
        bindings = selected_bindings(self.assistant, "ignore this question")

        self.assertEqual(
            {binding.datasource_id for binding in bindings},
            {self.active_source.pk, self.disabled_source.pk},
        )

    def test_selected_bindings_does_not_expand_empty_selection(self):
        assistant = Assistant.objects.create(
            name="Empty Routing Assistant",
            slug="empty-routing-assistant",
            capability=Assistant.Capability.KNOWLEDGE_QA,
        )

        self.assertEqual(selected_bindings(assistant, "Active Source"), [])

    def test_serializer_normalizes_legacy_datasource_routing_mode(self):
        serializer = AssistantSerializer(
            instance=self.assistant,
            data={
                "settings": {
                    "datasource_routing": "all",
                    "custom_setting": True,
                }
            },
            partial=True,
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(
            serializer.validated_data["settings"],
            {
                "datasource_routing": "selected",
                "custom_setting": True,
            },
        )
