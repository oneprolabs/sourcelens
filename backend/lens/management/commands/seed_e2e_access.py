"""Seed/teardown deterministic fixtures for access-control E2E tests.

Dev-only. Creates a set of namespaced users, a group, two assistants
(public + private with grants) and a shared Q&A on each, then prints a
JSON summary on stdout for the Playwright global setup to consume.
"""
import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from lens.models import (
    Assistant,
    AssistantAccess,
    LensNode,
    Run,
    Session,
    SharedQA,
)

User = get_user_model()

PASSWORD = "e2ePass123!"
GROUP_NAME = "e2e_group"
USERS = {
    "admin": {"username": "e2e_admin", "is_staff": True},
    "user": {"username": "e2e_user", "is_staff": False},
    "authuser": {"username": "e2e_authuser", "is_staff": False},
    "groupuser": {"username": "e2e_groupuser", "is_staff": False},
}
PUBLIC_SLUG = "e2e-public"
PRIVATE_SLUG = "e2e-private"
PUBLIC_SHARE_TOKEN = "e2e-share-public"
PRIVATE_SHARE_TOKEN = "e2e-share-private"


class Command(BaseCommand):
    help = "Seed or tear down access-control E2E fixtures (dev only)."

    def add_arguments(self, parser):
        parser.add_argument("--teardown", action="store_true")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Refusing to run with DEBUG=False.")
        if options["teardown"]:
            self._teardown()
            self.stdout.write(json.dumps({"teardown": True}))
            return
        self.stdout.write(json.dumps(self._setup()))

    def _setup(self):
        lensnode = self._pick_lensnode()
        task = self._lensnode_task(lensnode)
        path = self._lensnode_dir(lensnode)

        users = {}
        user_ids = {}
        for role, spec in USERS.items():
            user, _ = User.objects.get_or_create(username=spec["username"])
            user.email = f"{spec['username']}@example.com"
            user.is_staff = spec["is_staff"]
            user.set_password(PASSWORD)
            user.save()
            users[role] = spec["username"]
            user_ids[role] = user.id

        group, _ = Group.objects.get_or_create(name=GROUP_NAME)
        group.user_set.add(
            User.objects.get(username=USERS["groupuser"]["username"])
        )

        public = self._upsert_assistant(
            PUBLIC_SLUG, "E2E Public", lensnode, task, path,
            Assistant.Visibility.PUBLIC,
        )
        private = self._upsert_assistant(
            PRIVATE_SLUG, "E2E Private", lensnode, task, path,
            Assistant.Visibility.PRIVATE,
        )

        private.access_grants.all().delete()
        AssistantAccess.objects.create(
            assistant=private,
            user=User.objects.get(username=USERS["authuser"]["username"]),
        )
        AssistantAccess.objects.create(assistant=private, group=group)

        self._upsert_share(PUBLIC_SHARE_TOKEN, public)
        self._upsert_share(PRIVATE_SHARE_TOKEN, private)

        return {
            "password": PASSWORD,
            "users": users,
            "user_ids": user_ids,
            "group_id": group.id,
            "assistants": {
                "public": {"slug": PUBLIC_SLUG, "uuid": str(public.uuid)},
                "private": {"slug": PRIVATE_SLUG, "uuid": str(private.uuid)},
            },
            "shares": {
                "public_token": PUBLIC_SHARE_TOKEN,
                "private_token": PRIVATE_SHARE_TOKEN,
            },
        }

    def _teardown(self):
        assistants = Assistant.objects.filter(
            slug__in=[PUBLIC_SLUG, PRIVATE_SLUG]
        )
        sessions = Session.objects.filter(assistant__in=assistants)
        Run.objects.filter(session__in=sessions).delete()
        sessions.delete()
        SharedQA.objects.filter(
            token__in=[PUBLIC_SHARE_TOKEN, PRIVATE_SHARE_TOKEN]
        ).delete()
        assistants.delete()
        # GROUP_NAME plus any ephemeral groups created by management specs.
        Group.objects.filter(name__startswith="e2e").delete()
        User.objects.filter(
            username__in=[spec["username"] for spec in USERS.values()]
        ).delete()

    def _pick_lensnode(self):
        for node in LensNode.objects.all():
            tasks = node.tasks if isinstance(node.tasks, list) else []
            dirs = (
                node.available_dirs
                if isinstance(node.available_dirs, list)
                else []
            )
            if tasks and dirs:
                return node
        raise CommandError("No LensNode with reported tasks and dirs found.")

    def _lensnode_task(self, lensnode):
        tasks = lensnode.tasks if isinstance(lensnode.tasks, list) else []
        if not tasks:
            raise CommandError("LensNode reports no tasks.")
        first = tasks[0]
        return first.get("name") if isinstance(first, dict) else str(first)

    def _lensnode_dir(self, lensnode):
        dirs = (
            lensnode.available_dirs
            if isinstance(lensnode.available_dirs, list)
            else []
        )
        if not dirs:
            raise CommandError("LensNode reports no available dirs.")
        first = dirs[0]
        return first.get("path") if isinstance(first, dict) else str(first)

    def _upsert_assistant(self, slug, name, lensnode, task, path, visibility):
        assistant, _ = Assistant.objects.update_or_create(
            slug=slug,
            defaults={
                "name": name,
                "lensnode": lensnode,
                "selected_task": task,
                "selected_dirs": [{"path": path}],
                "visibility": visibility,
                "status": Assistant.Status.ACTIVE,
            },
        )
        return assistant

    def _upsert_share(self, token, assistant):
        SharedQA.objects.update_or_create(
            token=token,
            defaults={
                "assistant": assistant,
                "assistant_name": assistant.name,
                "assistant_slug": assistant.slug,
                "question": "E2E question?",
                "answer": "E2E answer.",
                "title": f"{assistant.name} share",
                "is_listed": True,
                "status": SharedQA.Status.PUBLISHED,
                "published_at": timezone.now(),
            },
        )
