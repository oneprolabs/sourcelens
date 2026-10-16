"""Regression checks for shared Redis deployment settings."""

import os
import runpy
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase


class RedisDeploymentSettingsTests(SimpleTestCase):
    """Keep dedicated overrides compatible while allowing one shared address."""

    def load_settings(self, module, environment):
        """Evaluate one settings fragment without retaining process overrides."""

        path = Path(__file__).resolve().parents[1] / "settings" / f"{module}.py"
        with patch.dict(os.environ, environment, clear=True):
            return runpy.run_path(str(path))

    def test_celery_uses_shared_redis_url(self):
        settings = self.load_settings("celery", {"REDIS_URL": "rediss://user:password@redis.test:6380/0"})

        self.assertEqual(settings["CELERY_BROKER_URL"], "rediss://user:password@redis.test:6380/0")

    def test_dedicated_celery_broker_takes_precedence(self):
        settings = self.load_settings(
            "celery",
            {"REDIS_URL": "redis://shared:6379/0", "CELERY_BROKER_URL": "amqp://broker.test"},
        )

        self.assertEqual(settings["CELERY_BROKER_URL"], "amqp://broker.test")

    def test_cache_preserves_shared_redis_connection_options(self):
        settings = self.load_settings(
            "cache", {"REDIS_URL": "rediss://user:p%40ss@redis.test:6380/7?ssl_cert_reqs=required"}
        )

        self.assertEqual(
            settings["CACHES"]["default"]["LOCATION"],
            "rediss://user:p%40ss@redis.test:6380/1?ssl_cert_reqs=required",
        )

    def test_cache_keeps_legacy_broker_host(self):
        settings = self.load_settings(
            "cache", {"REDIS_URL": "redis://shared:6379/0", "CELERY_BROKER_URL": "redis://legacy:6379/9"}
        )

        self.assertEqual(settings["CACHES"]["default"]["LOCATION"], "redis://legacy:6379/1")

    def test_dedicated_cache_url_takes_precedence(self):
        settings = self.load_settings(
            "cache", {"REDIS_URL": "redis://shared:6379/0", "CACHE_REDIS_URL": "redis://cache.test:6379/5"}
        )

        self.assertEqual(settings["CACHES"]["default"]["LOCATION"], "redis://cache.test:6379/5")
