"""
Consolidated startup initialization command.

Runs migrations, default superuser creation, periodic-task registration, and
collectstatic in a single Python process so Django and heavy dependencies are
loaded once during container startup.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Run container startup initialization steps in one process."""

    help = "Run all container startup initialization steps in a single process"
    requires_system_checks = []

    def add_arguments(self, parser):
        """Add command-line options."""

        parser.add_argument(
            "--skip-collectstatic",
            action="store_true",
            default=False,
            help="Skip collectstatic.",
        )
        parser.add_argument(
            "--skip-superuser",
            action="store_true",
            default=False,
            help="Skip superuser creation.",
        )
        parser.add_argument(
            "--skip-periodic-tasks",
            action="store_true",
            default=False,
            help="Skip periodic task registration.",
        )

    def handle(self, *args, **options):
        """Run initialization steps in dependency order."""

        self.stdout.write(
            self.style.MIGRATE_HEADING("=== sourcelens init ===")
        )

        call_command("migrate", "--noinput", verbosity=1)

        if not options["skip_superuser"]:
            self._ensure_superuser()

        self._ensure_default_lensnode()

        if not options["skip_periodic_tasks"]:
            self._soft("register_periodic_tasks")

        if not options["skip_collectstatic"]:
            call_command("collectstatic", "--noinput", verbosity=0)

        self.stdout.write(
            self.style.SUCCESS("=== sourcelens init complete ===")
        )

    def _soft(self, command_name, **kwargs):
        """Run a management command without aborting startup on failure."""

        try:
            call_command(command_name, verbosity=0, **kwargs)
        except Exception as exc:
            self.stderr.write(
                self.style.WARNING(
                    f"{command_name} completed with warnings: {exc}"
                )
            )

    def _ensure_superuser(self):
        """Create the default superuser if it does not already exist."""

        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.com")
        password = os.environ.get(
            "DJANGO_SUPERUSER_PASSWORD",
            "adminpassword",
        )

        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(
                f'Superuser "{username}" already exists, skipping.'
            )
            return

        User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
        )
        self.stdout.write(
            self.style.SUCCESS(f'Superuser "{username}" created.')
        )

    def _ensure_default_lensnode(self):
        """Create the default local LensNode used by Docker development."""

        token = os.environ.get("LENSNODE_TOKEN", "dev-lensnode-token")
        name = os.environ.get("LENSNODE_NAME", "local-dev-lensnode")
        workspace_path = os.environ.get("LENSNODE_WORKSPACE_PATH", "/workspace")

        try:
            from lens.models import LensNode
            from lens.lensnode_auth import hash_lensnode_token
        except Exception as exc:
            self.stderr.write(
                self.style.WARNING(f"LensNode bootstrap skipped: {exc}")
            )
            return

        lensnode, created = LensNode.objects.get_or_create(
            name=name,
            defaults={
                "workspace_path": workspace_path,
                "enrollment_status": LensNode.EnrollmentStatus.APPROVED,
                "auth_token_hash": hash_lensnode_token(token),
                "token_revoked": False,
                "tasks": [
                    {
                        "name": "deepagent_code_qa",
                        "description": (
                            "Answer questions over selected workspace directories."
                        ),
                    }
                ],
                "available_dirs": [],
            },
        )
        if not created:
            lensnode.workspace_path = workspace_path
            lensnode.enrollment_status = LensNode.EnrollmentStatus.APPROVED
            lensnode.auth_token_hash = hash_lensnode_token(token)
            lensnode.token_revoked = False
            lensnode.save(
                update_fields=[
                    "workspace_path",
                    "enrollment_status",
                    "auth_token_hash",
                    "token_revoked",
                    "updated_at",
                ]
            )

        action = "created" if created else "updated"
        self.stdout.write(f'Default LensNode "{name}" {action}.')
