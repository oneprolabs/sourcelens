"""Revoke conversation streams and polling without cancelling their executions."""

from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session as AuthSession
from django.core.cache import cache
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import AgentToken, Profile, Tenant, TenantApiKey, TenantDelegation, TenantMembership
from lens.models import Assistant, LensNode, Message, Run, RunExecution, Session
from lens.services import run_live_cache_key
from lens.views.sessions import RunViewSet


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class RunStreamAuthorityTests(TransactionTestCase):
    """Use real credentials and consume frames across committed authority changes."""

    def setUp(self):
        """Create an owner, tenant, completed answer and its live cache key."""
        self.user = get_user_model().objects.create_user(username="stream-member", password="password")
        self.tenant = Tenant.objects.create(name="Stream tenant", slug="stream-tenant")
        self.membership = TenantMembership.objects.create(user=self.user, tenant=self.tenant)
        self.assistant = Assistant.objects.create(name="Stream assistant", slug="stream-assistant", tenant=self.tenant)
        self.session = Session.objects.create(user=self.user, tenant=self.tenant, assistant=self.assistant)
        question = Message.objects.create(session=self.session, sequence=1, role="user", content="Question")
        answer = Message.objects.create(session=self.session, sequence=2, role="assistant", content="Answer")
        self.run = Run.objects.create(
            session=self.session, tenant=self.tenant, input_message=question, output_message=answer, status="done"
        )
        node = LensNode.objects.create(name="Stream node")
        RunExecution.objects.create(run=self.run, lensnode=node, task="general_chat")
        self.url = f"/api/lens/runs/{self.run.uuid}/stream/"
        self.token = RefreshToken.for_user(self.user).access_token
        self.headers = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}
        self.cache_key = run_live_cache_key(self.run.uuid)
        cache.delete(self.cache_key)

    def tearDown(self):
        """Remove live output independently of database cleanup."""
        cache.delete(self.cache_key)
        super().tearDown()

    def assert_revoked(self, mutate, *, response=None):
        """No cached frame may escape after authority changes or the source disappears."""
        response = response or Client().get(self.url, **self.headers)
        self.assertEqual(response.status_code, 200)

        async def consume():
            iterator = response.streaming_content.__aiter__()
            self.assertIn(b'"type": "sync"', await anext(iterator))
            await sync_to_async(mutate)()
            await cache.aset(self.cache_key, "Answer secret after revocation")
            with self.assertRaises(StopAsyncIteration):
                await anext(iterator)
            await iterator.aclose()

        try:
            async_to_sync(consume)()
        finally:
            response.close()

    def test_inactive_tenant_and_member_block_stream_and_rest_fallback(self):
        """Neither a new connection nor a polling/history request bypasses revocation."""
        cases = ((Tenant, self.tenant.pk, "suspended"), (TenantMembership, self.membership.pk, "disabled"))
        for model, pk, status in cases:
            with self.subTest(model=model.__name__):
                model.objects.filter(pk=pk).update(status=status)
                for url in (self.url, f"/api/lens/runs/{self.run.uuid}/", "/api/lens/sessions/"):
                    response = Client().get(url, **self.headers)
                    self.assertEqual(response.status_code, 403)
                    response.close()
                model.objects.filter(pk=pk).update(status="active")

    def test_open_stream_stops_when_membership_is_disabled(self):
        """Membership changes invalidate a previously authorized connection."""
        self.assert_revoked(lambda: TenantMembership.objects.filter(pk=self.membership.pk).update(status="disabled"))

    def test_open_stream_stops_when_tenant_is_suspended(self):
        """A cached token cannot bypass a tenant suspension."""
        self.assert_revoked(lambda: Tenant.objects.filter(pk=self.tenant.pk).update(status="suspended"))

    def test_open_stream_stops_when_user_is_disabled(self):
        """Reload the account rather than retaining its authenticated object."""
        self.assert_revoked(lambda: get_user_model().objects.filter(pk=self.user.pk).update(is_active=False))

    def test_removed_and_moved_membership_stop_the_stream(self):
        """The original tenant remains fixed for the lifetime of the response."""
        other = Tenant.objects.create(name="Other", slug="other-stream")
        self.assert_revoked(lambda: TenantMembership.objects.filter(pk=self.membership.pk).update(tenant=other))
        TenantMembership.objects.filter(pk=self.membership.pk).update(tenant=self.tenant)
        self.assert_revoked(lambda: TenantMembership.objects.filter(pk=self.membership.pk).delete())

    def test_revocation_does_not_cancel_a_running_execution(self):
        """Read authority changes leave the dispatched work and its status intact."""
        Run.objects.filter(pk=self.run.pk).update(status="running")
        self.assert_revoked(lambda: TenantMembership.objects.filter(pk=self.membership.pk).update(status="disabled"))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "running")

    def test_closing_and_revoking_streams_release_their_event_source(self):
        """The source finally block runs without waiting for garbage collection."""
        for revoke in (False, True):
            with self.subTest(revoke=revoke):
                closed = []

                async def source(run):
                    try:
                        yield {"type": "sync", "status": run.status}
                        yield {"type": "token", "content": "Must not escape"}
                    finally:
                        closed.append(True)

                async def consume():
                    iterator = response._iterator
                    self.assertIn(b'"type": "sync"', await anext(iterator))
                    if revoke:
                        await sync_to_async(TenantMembership.objects.filter(pk=self.membership.pk).update)(
                            status="disabled"
                        )
                        with self.assertRaises(StopAsyncIteration):
                            await anext(iterator)
                    else:
                        await iterator.aclose()

                with patch("lens.views.sessions.stream_run_events_async", source):
                    response = Client().get(self.url, **self.headers)
                    async_to_sync(consume)()
                response.close()
                self.assertEqual(closed, [True])

    def test_changed_run_and_session_scope_stop_the_stream(self):
        """Cached run objects cannot retain changed owner or tenant relationships."""
        other = Tenant.objects.create(name="Other", slug="other-stream")
        other_user = get_user_model().objects.create_user(username="other-stream")
        other_session = Session.objects.create(user=self.user, tenant=self.tenant, assistant=self.assistant)
        for model, pk, changes, restore in (
            (Run, self.run.pk, {"tenant": other}, {"tenant": self.tenant}),
            (Run, self.run.pk, {"session": other_session}, {"session": self.session}),
            (Session, self.session.pk, {"tenant": other}, {"tenant": self.tenant}),
            (Session, self.session.pk, {"user": other_user}, {"user": self.user}),
        ):
            with self.subTest(changes=changes):
                cache.delete(self.cache_key)
                self.assert_revoked(lambda: model.objects.filter(pk=pk).update(**changes))
                model.objects.filter(pk=pk).update(**restore)

    def test_deleted_run_closes_without_an_unhandled_lookup(self):
        """Deletion must close cleanly even when the stream resumes its database poll."""
        self.assert_revoked(lambda: Run.objects.filter(pk=self.run.pk).delete())

    def test_revocation_before_first_frame_leaks_no_snapshot(self):
        """Response creation is not a durable authority grant."""
        response = Client().get(self.url, **self.headers)
        self.assertEqual(response.status_code, 200)
        TenantMembership.objects.filter(pk=self.membership.pk).update(status="disabled")

        async def consume():
            self.assertEqual([chunk async for chunk in response.streaming_content], [])

        async_to_sync(consume)()
        response.close()

    def test_revocation_during_content_loading_cannot_escape_the_send_check(self):
        """Recheck after awaiting a source, not only before starting its read."""
        closed = []

        async def source(run):
            try:
                yield {"type": "sync", "status": run.status}
                await sync_to_async(TenantMembership.objects.filter(pk=self.membership.pk).update)(status="disabled")
                yield {"type": "token", "content": "Must not escape"}
            finally:
                closed.append(True)

        async def consume():
            iterator = response._iterator
            self.assertIn(b'"type": "sync"', await anext(iterator))
            with self.assertRaises(StopAsyncIteration):
                await anext(iterator)

        with patch("lens.views.sessions.stream_run_events_async", source):
            response = Client().get(self.url, **self.headers)
            async_to_sync(consume)()
        response.close()
        self.assertEqual(closed, [True])

    def test_expired_jwt_closes_user_and_admin_streams(self):
        """Token validity must be rechecked after initial authentication."""
        TenantMembership.objects.filter(pk=self.membership.pk).update(role="tenant_admin")
        for url in (self.url, f"/api/lens/admin/runs/{self.run.uuid}/trajectory/stream/"):
            with self.subTest(url=url):
                cache.delete(self.cache_key)
                response = Client().get(url, **self.headers)

                def expire():
                    clock.start()

                clock = patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(days=1))
                try:
                    self.assert_revoked(expire, response=response)
                finally:
                    clock.stop()

    def test_cookie_logout_and_expiry_close_the_stream(self):
        """Session authentication must not survive a deleted or expired login session."""
        for expired in (False, True):
            with self.subTest(expired=expired):
                cache.delete(self.cache_key)
                client = Client()
                client.force_login(self.user)
                key = client.session.session_key
                response = client.get(self.url)

                def revoke():
                    queryset = AuthSession.objects.filter(session_key=key)
                    if expired:
                        queryset.update(expire_date=timezone.now() - timedelta(seconds=1))
                    else:
                        queryset.delete()

                self.assert_revoked(revoke, response=response)

    def test_assistant_disable_preserves_inflight_completion(self):
        """Disabling new execution does not revoke the owner's existing answer."""
        response = Client().get(self.url, **self.headers)

        async def consume():
            iterator = response.streaming_content.__aiter__()
            self.assertIn(b'"type": "sync"', await anext(iterator))
            await sync_to_async(Assistant.objects.filter(pk=self.assistant.pk).update)(
                is_enabled=False, status="archived"
            )
            self.assertIn(b'"type": "done"', await anext(iterator))
            await iterator.aclose()

        async_to_sync(consume)()
        response.close()
        self.assertEqual(Client().get(f"/api/lens/runs/{self.run.uuid}/", **self.headers).status_code, 200)

    def test_delegation_context_survives_response_creation_and_rechecks_expiry(self):
        """Streaming executes after middleware has cleared the request context."""
        platform = get_user_model().objects.create_user(username="stream-platform")
        Profile.objects.update_or_create(user=platform, defaults={"platform_role": "super_admin"})
        Session.objects.filter(pk=self.session.pk).update(user=platform)
        delegation = TenantDelegation.objects.create(
            platform_user=platform, tenant=self.tenant, mode="readonly", expires_at=timezone.now() + timedelta(hours=1)
        )
        token = RefreshToken.for_user(platform).access_token
        response = Client().get(
            self.url, HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_DELEGATION_ID=str(delegation.uuid)
        )
        self.assert_revoked(
            lambda: TenantDelegation.objects.filter(pk=delegation.pk).update(expires_at=timezone.now()),
            response=response,
        )

    def test_restricted_bearer_never_falls_back_to_cookie_identity(self):
        """Native SSE does not add routes to the machine-credential allowlists."""
        _agent, agent_secret = AgentToken.issue(self.user, expires_at=None)
        _key, key_secret = TenantApiKey.issue(
            tenant=self.tenant,
            created_by=self.user,
            name="Stream key",
            scope="all",
            assistant_uuids=[],
            expires_at=None,
        )
        client = Client()
        client.force_login(self.user)
        for secret in (agent_secret, key_secret, "invalid-token"):
            response = client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {secret}")
            self.assertIn(response.status_code, (401, 403))
            response.close()

    def test_drf_stream_entry_rechecks_authority(self):
        """The router action must not remain an unchecked alternative entry point."""
        request = APIRequestFactory().get(self.url, **self.headers)
        response = RunViewSet.as_view({"get": "stream"})(request, uuid=str(self.run.uuid))
        self.assert_revoked(
            lambda: TenantMembership.objects.filter(pk=self.membership.pk).update(status="disabled"), response=response
        )
