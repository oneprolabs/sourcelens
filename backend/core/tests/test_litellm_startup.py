"""Cold-process coverage for LiteLLM's cyclic import and logging modules."""

import os
import subprocess
import sys
from unittest import TestCase


class LiteLLMStartupTests(TestCase):
    """Initialize the provider runtime before request threads can import it."""

    def test_ready_preloads_provider_and_logging_before_concurrent_access(self):
        """Use real imports in a fresh process, without a provider request."""

        script = """
import concurrent.futures
import importlib
import logging
import sys
from core.apps import CoreConfig
CoreConfig("core", importlib.import_module("core")).ready()
names = ["litellm", "litellm.types.secret_managers.main", "litellm.rust_bridge.catalog"]
assert all(name in sys.modules for name in names), "provider initialization is still deferred"
with concurrent.futures.ThreadPoolExecutor(max_workers=18) as pool:
    list(pool.map(importlib.import_module, names * 6))
logging.getLogger("httpx").warning("synthetic initialization check")
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""},
            capture_output=True,
            text=True,
            timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
