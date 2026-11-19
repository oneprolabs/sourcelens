"""Verify the multitenancy migration upgrades existing data."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MultitenancyMigrationTests(TransactionTestCase):
    """Exercise the migrations with records from before tenancy existed."""

    def test_legacy_records_receive_default_tenant(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [target for target in latest if target[0] not in {"accounts", "lens"}]
        before += [("accounts", "0008_agent_token"), ("lens", "0069_evaluation_revisions_and_reliability")]

        try:
            executor.migrate(before)
            old_apps = executor.loader.project_state(before).apps
            user = old_apps.get_model("auth", "User").objects.create(username="legacy-admin", is_staff=True)
            group = old_apps.get_model("auth", "Group").objects.create(name="legacy-group")
            assistant = old_apps.get_model("lens", "Assistant").objects.create(name="Legacy", slug="legacy")
            datasource = old_apps.get_model("lens", "DataSource").objects.create(name="Legacy", source_type="git")
            session = old_apps.get_model("lens", "Session").objects.create(assistant=assistant, user=user)
            dataset = old_apps.get_model("lens", "EvaluationDataset").objects.create(name="Legacy", assistant=assistant)

            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            apps = executor.loader.project_state(latest).apps

            tenant = apps.get_model("accounts", "Tenant").objects.get(slug="default")
            membership = apps.get_model("accounts", "TenantMembership").objects.get(user_id=user.pk)
            self.assertEqual((membership.tenant_id, membership.role), (tenant.pk, "tenant_admin"))
            self.assertEqual(
                apps.get_model("accounts", "Profile").objects.get(user_id=user.pk).platform_role, "super_admin"
            )
            self.assertEqual(
                apps.get_model("accounts", "TenantGroup").objects.get(auth_group_id=group.pk).tenant_id, tenant.pk
            )
            for model, pk in (
                ("Assistant", assistant.pk),
                ("DataSource", datasource.pk),
                ("Session", session.pk),
                ("EvaluationDataset", dataset.pk),
            ):
                self.assertEqual(apps.get_model("lens", model).objects.get(pk=pk).tenant_id, tenant.pk)
        finally:
            MigrationExecutor(connection).migrate(latest)
