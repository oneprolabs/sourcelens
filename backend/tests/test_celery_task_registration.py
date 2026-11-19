"""Worker startup must register account tasks before accepting broker messages."""

import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


class CeleryTaskRegistrationTests(SimpleTestCase):
    """Exercise the worker loader in a fresh process without test-suite imports."""

    def test_account_notices_are_registered_before_pool_process_init(self):
        """The parent worker must know both delivery and recovery task names."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from core.celery import app; "
                "app.loader.import_default_modules(); "
                "expected = {'accounts.tasks.deliver_api_key_notice_task', "
                "'accounts.tasks.dispatch_pending_api_key_notices_task'}; "
                "missing = expected.difference(app.tasks); "
                "assert not missing, f'Unregistered account tasks: {sorted(missing)}'; "
                "queues = {app.amqp.router.route(app.tasks[name]._get_exec_options(), name)['queue'].name "
                "for name in expected}; "
                "assert queues == {'backend'}, f'Unexpected account queues: {queues}'",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
