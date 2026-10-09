"""Exercise public modules without the test suite's import order."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("module", ["mcp_tools", "gateway_model"])
def test_module_imports_without_preloading_agent_runtime(module):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import lensnode.{module}; "
            "from lensnode.token_budget import SharedTokenBudget; "
            "from lensnode.agent_runtime.limits import "
            "SharedTokenBudget as LegacyBudget; "
            "assert SharedTokenBudget is LegacyBudget",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
