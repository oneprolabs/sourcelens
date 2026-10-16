"""Render deployment configurations using sample data, without starting services."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lensnode"))

from lensnode.config import load_config


class DeploymentConfigTests(unittest.TestCase):
    """Exercise Compose interpolation, env_file precedence, and runtime derivation."""

    def render(self, filename, extra=""):
        """Use a temporary config home so real credentials never enter the test."""

        with tempfile.TemporaryDirectory() as directory:
            config_home = Path(directory)
            sample = (ROOT / "env.sample").read_text() + "\n" + extra
            for name in (".env", ".env.dev"):
                (config_home / name).write_text(sample)
            result = subprocess.run(
                [
                    "docker", "compose", "--project-directory", directory,
                    "--env-file", str(config_home / ".env"),
                    "-f", str(ROOT / filename), "--profile", "*", "config", "--format", "json",
                ],
                cwd=directory,
                env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]},
                capture_output=True,
                text=True,
                check=True,
            )
            return json.loads(result.stdout)

    def test_root_stacks_derive_addresses_and_keep_deployment_defaults(self):
        cases = (
            ("docker-compose.yml", "http://nginx:80", "sourcelens-api-blue", 240, "sourcelens"),
            ("docker-compose.standalone.yml", "http://backend-api:8000", "backend-api", 240, "sourcelens"),
            ("docker-compose.dev.yml", "http://backend-api:8000", "backend-api", 600, "sourcelens-dev"),
        )
        legacy = "\n".join(
            f"LENSNODE_{name}=https://retired.example.test/obsolete"
            for name in ("CONTROL_WS_URL", "AI_GATEWAY_URL", "DELIVERABLE_UPLOAD_URL", "DELEGATION_BASE_URL")
        )
        for filename, base, api, timeout, project in cases:
            for extra in ("", legacy):
                with self.subTest(filename=filename, legacy=bool(extra)):
                    rendered = self.render(filename, extra)
                    services = rendered["services"]
                    environment = services["lensnode"]["environment"]
                    with patch.dict(os.environ, environment, clear=True):
                        config = load_config()
                    self.assertEqual(rendered["name"], project)
                    self.assertEqual(config.control_ws_url, base.replace("http://", "ws://") + "/ws/lens/lensnodes/")
                    self.assertEqual(config.ai_gateway_url, base + "/api/lens/lensnode/ai-gateway/")
                    self.assertEqual(config.deliverable_upload_url, base + "/api/lens/lensnode/deliverables/")
                    self.assertEqual(config.delegation_base_url, base + "/api/lens/lensnode/runs")
                    self.assertEqual(config.max_concurrent_runs, 8)
                    self.assertEqual(config.request_timeout_s, timeout)
                    self.assertEqual(config.planning_reasoning_effort, "medium")
                    self.assertEqual(
                        services["backend-worker"]["environment"]["LENSNODE_AI_GATEWAY_URL"], config.ai_gateway_url
                    )
                    self.assertEqual(services[api]["environment"]["LENSNODE_TOKEN"], config.token)
                    self.assertEqual(services["backend-worker"]["environment"]["LENSNODE_TOKEN"], config.token)
                    self.assertEqual(services[api]["environment"]["LENSNODE_AI_GATEWAY_URL"], config.ai_gateway_url)
                    if filename != "docker-compose.dev.yml":
                        self.assertEqual(services["lensnode"]["stop_grace_period"], "4m30s")
                        self.assertLess(config.drain_timeout_s, 270)

    def test_existing_tuning_is_read_from_env_file(self):
        for filename in ("docker-compose.yml", "docker-compose.standalone.yml", "docker-compose.dev.yml"):
            with self.subTest(filename=filename):
                rendered = self.render(
                    filename,
                    "LENSNODE_MCP_TOOL_TIMEOUT_S=91\nLENSNODE_PLANNING_REASONING_EFFORT=none\n"
                    "LENSNODE_REQUEST_TIMEOUT_S=333\nLENSNODE_MAX_CONCURRENT_RUNS=5\n",
                )
                with patch.dict(os.environ, rendered["services"]["lensnode"]["environment"], clear=True):
                    config = load_config()
                self.assertEqual(config.mcp_tool_timeout_s, 91)
                self.assertEqual(config.planning_reasoning_effort, "none")
                self.assertEqual(config.request_timeout_s, 333)
                self.assertEqual(config.max_concurrent_runs, 5)

    def test_remote_worker_uses_base_url_for_every_endpoint(self):
        rendered = self.render("lensnode/docker-compose.yml", "LENSNODE_SERVER_URL=https://remote.example.test\n")
        with patch.dict(os.environ, rendered["services"]["lensnode"]["environment"], clear=True):
            config = load_config()
        self.assertEqual(config.control_ws_url, "wss://remote.example.test/ws/lens/lensnodes/")
        self.assertEqual(config.deliverable_upload_url, "https://remote.example.test/api/lens/lensnode/deliverables/")
        self.assertEqual(config.delegation_base_url, "https://remote.example.test/api/lens/lensnode/runs")

    def test_remote_worker_keeps_legacy_endpoint_overrides(self):
        rendered = self.render(
            "lensnode/docker-compose.yml",
            "LENSNODE_CONTROL_WS_URL=wss://legacy.example.test/ws\n"
            "LENSNODE_AI_GATEWAY_URL=https://legacy.example.test/ai\n",
        )
        with patch.dict(os.environ, rendered["services"]["lensnode"]["environment"], clear=True):
            config = load_config()
        self.assertEqual(config.control_ws_url, "wss://legacy.example.test/ws")
        self.assertEqual(config.ai_gateway_url, "https://legacy.example.test/ai")


if __name__ == "__main__":
    unittest.main()
