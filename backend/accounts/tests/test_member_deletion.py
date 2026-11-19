"""Permanent member deletion preserves tenant resources and requires reviewed impact."""

import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from accounts.models import AuditLog, Profile, Tenant, TenantApiKey, TenantMembership
from lens.models import Assistant, Message, Run, Session, SharedQA


class MemberDeletionTests(TestCase):
    """Exercise real preview, confirmation, resource transfer and account removal."""

    def setUp(self):
        """Keep the actor and target distinct and the target's resources tenant scoped."""
        self.tenant = Tenant.objects.create(name="Delete members", slug="delete-members")
        self.foreign = Tenant.objects.create(name="Foreign delete", slug="foreign-delete-members")
        self.admin = self.member("delete-admin", role="tenant_admin")
        self.target = self.member("delete-target")
        self.assistant = Assistant.objects.create(
            name="Retained assistant",
            slug="retained-member-assistant",
            tenant=self.tenant,
            owner_user=self.target,
        )
        self.session = Session.objects.create(assistant=self.assistant, tenant=self.tenant, user=self.target)
        question = Message.objects.create(session=self.session, role="user", content="Private question", sequence=1)
        self.run = Run.objects.create(
            tenant=self.tenant,
            session=self.session,
            input_message=question,
            status="done",
        )
        self.share = SharedQA.objects.create(
            assistant=self.assistant,
            run=self.run,
            published_by=self.target,
            token="member-delete-share",
            status="published",
            question="Question",
            answer="Answer",
        )
        self.key, self.raw = TenantApiKey.issue(
            tenant=self.tenant,
            created_by=self.target,
            name="Keep independent key",
            scope="all",
            assistant_uuids=[],
            expires_at=None,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.url = "/api/v1/tenant/members/delete"

    def member(self, name, role="member", tenant=None):
        """Create an explicit tenant member."""
        user = get_user_model().objects.create_user(username=name, email=f"{name}@example.test")
        TenantMembership.objects.create(user=user, tenant=tenant or self.tenant, role=role)
        return user

    def selection(self, *users):
        """Use current member versions as the confirmation selection."""
        rows = self.client.get("/api/v1/tenant/members", {"page_size": 100}).data["results"]
        indexed = {row["id"]: row for row in rows}
        return [{"id": user.pk, "version": indexed[user.pk]["version"]} for user in users]

    def preview(self, *users):
        """Freeze a selection and return its real impact proof."""
        self.selected = self.selection(*(users or [self.target]))
        result = self.client.post(self.url, {"members": self.selected, "preview": True}, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        return result.data

    def confirm(self, preview):
        """Confirm exactly the preview and exact email addresses."""
        emails = {row["id"]: row["email"] for row in preview["members"]}
        rows = [{**row, "confirmation_email": emails[row["id"]]} for row in self.selected]
        return self.client.post(
            self.url,
            {
                "members": rows,
                "preview": False,
                "confirmation": preview["confirmation"],
            },
            format="json",
        )

    def file_fixtures(self):
        """Keep private, published and cached file bytes in isolated test storage."""
        from tempfile import TemporaryDirectory

        from django.conf import settings
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from lens.document_attachments import (
            document_attachment_storage,
            store_document_attachment,
        )
        from lens.models import MessageAttachment, RunOutputFile, SharedQAFile

        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        stores = dict(settings.STORAGES)
        for name in ("default", "deliverables", "document_attachments"):
            stores[name] = {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": media.name},
            }
        override = override_settings(STORAGES=stores)
        override.enable()
        self.addCleanup(override.disable)
        image = MessageAttachment.objects.create(session=self.session, file=SimpleUploadedFile("image.png", b"image"))
        output = RunOutputFile.objects.create(
            tenant=self.tenant,
            assistant=self.assistant,
            session=self.session,
            run=self.run,
            filename="report.txt",
            file=SimpleUploadedFile("report.txt", b"private report"),
        )
        shared = SharedQAFile.objects.create(
            share=self.share,
            kind="output",
            filename="shared.txt",
            file=SimpleUploadedFile("shared.txt", b"published"),
        )
        document = store_document_attachment(
            self.session,
            self.target,
            SimpleUploadedFile("notes.txt", b"private notes", content_type="text/plain"),
        )
        files = [(row.file.storage, row.file.name) for row in (image, output, shared)]
        files.append((document_attachment_storage(), document["storage_name"]))
        return files, document

    def test_file_cleanup_waits_for_commit_and_removes_cached_documents(self):
        """Permanent account removal must not leave private file bytes or cached document metadata."""
        from lens.document_attachments import get_document_attachment

        files, document = self.file_fixtures()
        preview = self.preview()
        impact = preview["members"][0]["impact"]
        self.assertEqual(
            (impact["attachments"], impact["output_files"], impact["shared_files"]),
            (1, 1, 1),
        )
        with patch("lens.tasks.session_workspace_cleanup_task.delay"), self.captureOnCommitCallbacks(execute=True):
            response = self.confirm(preview)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertTrue(all(storage.exists(name) for storage, name in files))
            self.assertIsNotNone(get_document_attachment(document["uuid"]))
        self.assertTrue(all(not storage.exists(name) for storage, name in files))
        self.assertIsNone(get_document_attachment(document["uuid"]))

    def test_new_attachment_requires_a_fresh_review_even_without_session_revision(self):
        """An upload after preview cannot be silently included in a permanent deletion."""
        from lens.models import MessageAttachment

        preview = self.preview()
        attachment = MessageAttachment.objects.create(session=self.session, original_name="later.png")
        self.assertEqual(self.confirm(preview).status_code, 409)
        self.assertTrue(MessageAttachment.objects.filter(pk=attachment.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.target.pk).exists())

    def test_unlinked_foreign_share_does_not_lose_its_last_ownership_evidence(self):
        """Historical shares without runs cannot be assigned to this tenant by their publisher alone."""
        foreign_assistant = Assistant.objects.create(tenant=self.foreign, name="Foreign", slug="foreign-share-owner")
        share = SharedQA.objects.create(
            assistant=foreign_assistant,
            published_by=self.target,
            token="foreign-unlinked",
        )
        preview = self.preview()
        self.assertEqual(preview["members"][0]["skip_reason"], "foreign_history")
        self.assertEqual(preview["members"][0]["impact"], {})
        self.assertEqual(self.confirm(preview).status_code, 200)
        self.assertTrue(SharedQA.objects.filter(pk=share.pk).exists())

    @patch("lens.session_deletion._clean_session_storage")
    def test_preview_is_read_only_and_confirmation_deletes_only_the_member_history(self, cleanup):
        """Keep assistants and machine keys, invalidate shares and remove private history."""
        preview = self.preview()
        row = preview["members"][0]
        self.assertEqual(row["impact"]["assistants"], 1)
        self.assertEqual(row["impact"]["shares"], 1)
        self.assertEqual(row["impact"]["sessions"], 1)
        self.assertEqual(row["impact"]["api_keys"], 1)
        self.assertFalse(AuditLog.objects.filter(action="member.delete").exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.target.pk).exists())
        with self.captureOnCommitCallbacks(execute=True):
            response = self.confirm(preview)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["deleted"], response.data["skipped"]), (1, 0))
        self.assertFalse(get_user_model().objects.filter(pk=self.target.pk).exists())
        self.assertFalse(Session.objects.filter(pk=self.session.pk).exists())
        self.assistant.refresh_from_db()
        self.assertEqual(self.assistant.owner_user_id, self.admin.pk)
        self.assertFalse(SharedQA.objects.filter(pk=self.share.pk).exists())
        self.key.refresh_from_db()
        self.assertEqual(self.key.status, "active")
        self.assertIsNone(self.key.created_by_id)
        self.assertEqual(APIClient().get(f"/api/lens/public/qa/{self.share.token}/").status_code, 404)
        self.assertTrue(AuditLog.objects.filter(action="member.delete", resource_id=str(self.target.pk)).exists())
        cleanup.assert_called_once()

    def test_impact_changes_and_exact_email_are_required(self):
        """No stale resource list or normalized email can authorize permanent deletion."""
        preview = self.preview()
        rows = [{**self.selected[0], "confirmation_email": self.target.email + " "}]
        response = self.client.post(
            self.url,
            {
                "members": rows,
                "preview": False,
                "confirmation": preview["confirmation"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        SharedQA.objects.create(assistant=self.assistant, published_by=self.target, token="new-member-share")
        self.assertEqual(self.confirm(preview).status_code, 409)
        self.assertTrue(get_user_model().objects.filter(pk=self.target.pk).exists())
        self.assertEqual(self.assistant.owner_user_id, self.target.pk)

    def test_self_platform_identity_and_active_work_are_explicitly_skipped(self):
        """Dangerous selections cannot delete the current actor or bypass independent platform authority."""
        preview = self.preview(self.admin, self.target)
        self.assertEqual(
            next(r for r in preview["members"] if r["id"] == self.admin.pk)["skip_reason"],
            "self",
        )
        Run.objects.filter(pk=self.run.pk).update(status="running")
        self.assertEqual(self.preview()["members"][0]["skip_reason"], "active_work")
        Run.objects.filter(pk=self.run.pk).update(status="done")
        Profile.objects.filter(user=self.target).update(platform_role="super_admin")
        self.assertEqual(self.preview()["members"][0]["skip_reason"], "platform_identity")

    def test_foreign_history_is_not_deleted_or_disclosed(self):
        """Even corrupt old associations cannot make the tenant delete another tenant's records."""
        Session.objects.filter(pk=self.session.pk).update(tenant=self.foreign)
        preview = self.preview()
        self.assertEqual(preview["members"][0]["skip_reason"], "foreign_history")
        self.assertEqual(preview["members"][0]["impact"], {})
        response = self.confirm(preview)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["skipped"], 1)
        self.assertTrue(Session.objects.filter(pk=self.session.pk).exists())

    def test_foreign_selection_and_machine_or_delegation_cannot_delete(self):
        """Only explicit current human tenant administrators can inspect or delete members."""
        selected = self.selection(self.target)
        foreign = self.member("delete-foreign", tenant=self.foreign)
        data = {
            "members": selected + [{"id": foreign.pk, "version": "unknown"}],
            "preview": True,
        }
        self.assertEqual(self.client.post(self.url, data, format="json").status_code, 404)
        data["members"] = selected
        self.assertEqual(
            self.client.post(self.url, data, HTTP_X_DELEGATION_ID="support", format="json").status_code,
            403,
        )
        self.client.force_authenticate(self.target)
        self.assertEqual(self.client.post(self.url, data, format="json").status_code, 403)
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.raw}")
        self.assertEqual(self.client.post(self.url, data, format="json").status_code, 403)

    def test_tenant_models_survive_and_foreign_models_block_the_account_removal(self):
        """The package's user CASCADE must not delete models owned by this tenant."""
        from agentcore_metering.adapters.django.models import LLMConfig

        from lens.models import LLMConfigScope

        config = LLMConfig.objects.create(user=self.target, provider="openai")
        owner = LLMConfigScope.objects.create(config=config, tenant=self.foreign)
        self.assertEqual(self.preview()["members"][0]["skip_reason"], "foreign_history")
        owner.tenant = self.tenant
        owner.save()
        preview = self.preview()
        self.assertEqual(preview["members"][0]["impact"]["models"], 1)
        self.assertEqual(self.confirm(preview).status_code, 200)
        config.refresh_from_db()
        self.assertIsNone(config.user_id)
        self.assertEqual(config.ownership.tenant, self.tenant)

    def test_late_dependency_rolls_back_account_resource_transfer_and_link_revocation(
        self,
    ):
        """A dependency race cannot leave a half-deleted batch or run cleanup callbacks."""
        from lens.session_deletion import delete_sessions

        files, document = self.file_fixtures()
        preview = self.preview()

        def fail(queryset):
            """Fail after destructive queries have registered post-commit cleanup."""
            delete_sessions(queryset)
            raise ProtectedError("New dependency", {self.assistant})

        with patch("accounts.services.member_deletion.delete_sessions", side_effect=fail), patch(
            "lens.session_deletion._clean_session_storage"
        ) as cleanup, self.captureOnCommitCallbacks(execute=True):
            response = self.confirm(preview)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertTrue(get_user_model().objects.filter(pk=self.target.pk).exists())
        self.assertTrue(Session.objects.filter(pk=self.session.pk).exists())
        self.assistant.refresh_from_db()
        self.share.refresh_from_db()
        self.assertEqual(self.assistant.owner_user, self.target)
        self.assertEqual(self.share.status, "published")
        self.assertFalse(AuditLog.objects.filter(action="member.delete").exists())
        cleanup.assert_not_called()
        self.assertTrue(all(storage.exists(name) for storage, name in files))
        from lens.document_attachments import get_document_attachment

        self.assertIsNotNone(get_document_attachment(document["uuid"]))

    def test_confirmation_binds_actor_expiry_and_each_member_of_a_mixed_batch(self):
        """Keep self while removing eligible members and reject reuse by another administrator."""
        import time

        other_admin = self.member("delete-second-admin", role="tenant_admin")
        preview = self.preview(self.admin, self.target)
        self.client.force_authenticate(other_admin)
        self.assertEqual(self.confirm(preview).status_code, 409)
        self.client.force_authenticate(self.admin)
        with patch("django.core.signing.time.time", return_value=time.time() + 601):
            self.assertEqual(self.confirm(preview).status_code, 409)
        response = self.confirm(preview)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["deleted"], response.data["skipped"]), (1, 1))
        self.assertEqual(response.data["skipped_items"], [{"id": self.admin.pk, "reason": "self"}])
        self.assertTrue(get_user_model().objects.filter(pk=self.admin.pk).exists())

    def test_old_access_token_is_rejected_but_independent_key_keeps_its_scope(self):
        """Deleting a creator removes human authentication without revoking their tenant machine identity."""
        from rest_framework_simplejwt.tokens import RefreshToken

        access = str(RefreshToken.for_user(self.target).access_token)
        self.assertEqual(self.confirm(self.preview()).status_code, 200)
        caller = APIClient()
        caller.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(caller.get("/api/v1/auth/user").status_code, 401)
        caller.credentials(HTTP_AUTHORIZATION=f"Bearer {self.raw}")
        response = caller.get("/api/lens/assistants/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(str(self.assistant.uuid), [row["uuid"] for row in response.data["results"]])

    def test_private_owner_access_transfers_without_authorizing_other_members(self):
        """Ownership grants tenant-local use and survives deletion as an explicit transfer."""
        observer = self.member("delete-observer")
        Assistant.objects.filter(pk=self.assistant.pk).update(visibility="private")
        caller = APIClient()
        caller.force_authenticate(self.target)
        url = f"/api/lens/assistants/{self.assistant.uuid}/"
        self.assertEqual(caller.get(url).status_code, 200)
        caller.force_authenticate(observer)
        self.assertEqual(caller.get(url).status_code, 404)
        self.assertEqual(self.confirm(self.preview()).status_code, 200)
        membership = TenantMembership.objects.get(user=self.admin)
        membership.role = "member"
        membership.save(update_fields=["role", "updated_at"])
        caller.force_authenticate(get_user_model().objects.get(pk=self.admin.pk))
        self.assertEqual(caller.get(url).status_code, 200)
        caller.force_authenticate(observer)
        self.assertEqual(caller.get(url).status_code, 404)


class MemberDeletionRaceTests(TransactionTestCase):
    """Serialize permanent account removal with requests already holding cached identities."""

    def setUp(self):
        """Prepare a signed deletion preview for an unused assistant owned by the target."""
        self.tenant = Tenant.objects.create(name="Member deletion race", slug="member-deletion-race")
        self.admin = get_user_model().objects.create_user(username="member-race-admin")
        self.target = get_user_model().objects.create_user(username="member-race-target", email="race@example.test")
        TenantMembership.objects.create(user=self.admin, tenant=self.tenant, role="tenant_admin")
        TenantMembership.objects.create(user=self.target, tenant=self.tenant)
        self.assistant = Assistant.objects.create(
            tenant=self.tenant,
            owner_user=self.target,
            name="Race assistant",
            slug="member-race-assistant",
        )
        client = APIClient()
        client.force_authenticate(self.admin)
        rows = client.get("/api/v1/tenant/members").data["results"]
        row = next(row for row in rows if row["id"] == self.target.pk)
        self.url = "/api/v1/tenant/members/delete"
        self.data = {
            "members": [{"id": row["id"], "version": row["version"]}],
            "preview": True,
        }
        preview = client.post(self.url, self.data, format="json")
        self.assertEqual(preview.status_code, 200, preview.data)
        self.data.update(preview=False, confirmation=preview.data["confirmation"])
        self.data["members"][0]["confirmation_email"] = self.target.email

    def race(self, deletion_first):
        """Control both lock orderings using independent PostgreSQL connections."""
        if connection.vendor != "postgresql":
            self.skipTest("Row-lock races require PostgreSQL")
        from accounts.services.member_deletion import _impact
        from lens.assistant_lifecycle import (
            AssistantNotRunnableError,
            create_assistant_session,
        )

        entered, release, second_started, second_finished = (threading.Event() for _ in range(4))
        results, errors = {}, []
        check = Assistant.is_runnable_by

        def pause(function, *args):
            """Hold the first operation after it owns the target user lock."""
            value = function(*args)
            entered.set()
            if not release.wait(8):
                raise TimeoutError("Member deletion race was not released")
            return value

        def request(delete):
            """Use current actor identity and the intentionally cached target identity."""
            close_old_connections()
            if delete != deletion_first:
                second_started.set()
            try:
                if delete:
                    caller = APIClient()
                    caller.force_authenticate(get_user_model().objects.get(pk=self.admin.pk))
                    results["deletion"] = caller.post(self.url, self.data, format="json").status_code
                else:
                    try:
                        results["session"] = create_assistant_session(self.assistant.uuid, self.target).pk
                    except AssistantNotRunnableError:
                        results["session"] = "unavailable"
            except Exception as exc:
                errors.append(exc)
            finally:
                if delete != deletion_first:
                    second_finished.set()
                close_old_connections()

        target = (
            "accounts.services.member_deletion._impact" if deletion_first else "lens.models.Assistant.is_runnable_by"
        )
        function = _impact if deletion_first else check
        with patch(target, side_effect=lambda *args: pause(function, *args), autospec=True):
            first = threading.Thread(target=request, args=(deletion_first,))
            second = threading.Thread(target=request, args=(not deletion_first,))
            first.start()
            try:
                self.assertTrue(entered.wait(8))
                second.start()
                self.assertTrue(second_started.wait(8))
                self.assertFalse(second_finished.wait(0.2))
            finally:
                release.set()
                first.join(10)
                if second.ident is not None:
                    second.join(10)
        self.assertEqual(errors, [])
        self.assertFalse(first.is_alive() or second.is_alive())
        return results

    def test_deletion_wins_and_cached_user_cannot_create_a_new_session(self):
        """A surviving assistant does not let a removed user recreate work."""
        result = self.race(True)
        self.assertEqual(result, {"deletion": 200, "session": "unavailable"})
        self.assertFalse(Session.objects.filter(user_id=self.target.pk).exists())

    def test_session_wins_and_invalidates_the_old_deletion_preview(self):
        """Already admitted work remains and requires a new impact review."""
        result = self.race(False)
        self.assertEqual(result["deletion"], 409)
        self.assertIsInstance(result["session"], int)
        self.assertTrue(get_user_model().objects.filter(pk=self.target.pk).exists())
