import pytest

from lensnode.agent_runtime import _resolve_token_budget
from lensnode.config import load_config


def test_heavy_work_concurrency_setting_is_not_part_of_lensnode_config(
    monkeypatch,
):
    monkeypatch.setenv("LENSNODE_HEAVY_WORK_CONCURRENCY", "7")

    config = load_config()

    assert not hasattr(config, "heavy_work_concurrency")


def test_load_config_reads_runtime_version(monkeypatch):
    monkeypatch.setenv("LENSNODE_AGENT_VERSION", "2026.08.27")

    config = load_config()

    assert config.agent_version == "2026.08.27"


def test_load_config_separates_runtime_path_from_workspace(monkeypatch):
    monkeypatch.setenv("LENSNODE_WORKSPACE_PATH", "/data/workspace")
    monkeypatch.setenv("LENSNODE_RUNTIME_PATH", "/data/runtime")

    config = load_config()

    assert config.workspace_path == "/data/workspace"
    assert config.runtime_path == "/data/runtime"


def test_load_config_keeps_runtime_path_compatible_by_default(monkeypatch):
    monkeypatch.setenv("LENSNODE_WORKSPACE_PATH", "/data/workspace")
    monkeypatch.delenv("LENSNODE_RUNTIME_PATH", raising=False)

    config = load_config()

    assert config.runtime_path == "/data/workspace"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("0", False),
        ("invalid", False),
    ],
)
def test_load_config_parses_tls_skip_verify(monkeypatch, value, expected):
    monkeypatch.setenv("LENSNODE_TLS_SKIP_VERIFY", value)

    config = load_config()

    assert config.tls_skip_verify is expected


def test_load_config_uses_secure_tls_defaults(monkeypatch):
    monkeypatch.delenv("LENSNODE_TLS_SKIP_VERIFY", raising=False)
    monkeypatch.delenv("LENSNODE_TLS_CA_FILE", raising=False)

    config = load_config()

    assert config.tls_skip_verify is False
    assert config.tls_ca_file is None


def test_load_config_reads_tls_ca_file(monkeypatch):
    monkeypatch.setenv("LENSNODE_TLS_CA_FILE", "/etc/sourcelens/ca.crt")

    config = load_config()

    assert config.tls_ca_file == "/etc/sourcelens/ca.crt"


def test_load_config_reads_run_token_budget(monkeypatch):
    monkeypatch.setenv("LENSNODE_TOKEN_BUDGET_MAX_TOKENS", "120000")
    monkeypatch.setenv("LENSNODE_TOKEN_BUDGET_HARD_MAX_TOKENS", "600000")
    monkeypatch.setenv(
        "LENSNODE_TOKEN_BUDGET_FINAL_RESERVE_TOKENS",
        "50000",
    )
    monkeypatch.setenv("LENSNODE_TOKEN_BUDGET_WARN_RATIO", "0.75")

    config = load_config()

    assert config.token_budget_max_tokens == 120000
    assert config.token_budget_hard_max_tokens == 600000
    assert config.token_budget_final_reserve_tokens == 50000
    assert config.token_budget_warn_ratio == 0.75


def test_load_config_reads_stream_recovery_policy(monkeypatch):
    monkeypatch.setenv("LENSNODE_STREAM_RECOVERY_ATTEMPTS", "5")
    monkeypatch.setenv("LENSNODE_STREAM_RECOVERY_BACKOFF_S", "2.5")
    monkeypatch.setenv("LENSNODE_STREAM_RECOVERY_BACKOFF_MAX_S", "12")

    config = load_config()

    assert config.stream_recovery_attempts == 5
    assert config.stream_recovery_backoff_s == 2.5
    assert config.stream_recovery_backoff_max_s == 12.0


def test_resolve_token_budget_prefers_run_sent_budget():
    config = type(
        "Config",
        (),
        {
            "token_budget_max_tokens": 200000,
            "token_budget_hard_max_tokens": 500000,
            "token_budget_final_reserve_tokens": 40000,
        },
    )()

    budget = _resolve_token_budget(
        config,
        {
            "token_budget": {
                "profile": "deep",
                "max_tokens": 900000,
                "final_reserve_tokens": 75000,
            }
        },
    )

    assert budget == {
        "profile": "deep",
        "max_tokens": 900000,
        "final_reserve_tokens": 75000,
    }


def test_unlimited_profile_applies_no_ceiling():
    config = type(
        "Config",
        (),
        {
            "token_budget_max_tokens": 200000,
            "token_budget_hard_max_tokens": 500000,
            "token_budget_final_reserve_tokens": 40000,
        },
    )()

    budget = _resolve_token_budget(
        config,
        {
            "token_budget": {
                "profile": "unlimited",
                "max_tokens": 0,
                "final_reserve_tokens": 0,
            }
        },
    )

    assert budget == {
        "profile": "unlimited",
        "max_tokens": 0,
        "final_reserve_tokens": 0,
    }


def test_resolve_token_budget_falls_back_to_config_without_run_budget():
    config = type(
        "Config",
        (),
        {
            "token_budget_max_tokens": 200000,
            "token_budget_hard_max_tokens": 500000,
            "token_budget_final_reserve_tokens": 40000,
        },
    )()

    budget = _resolve_token_budget(config, {})

    assert budget == {
        "profile": "system",
        "max_tokens": 200000,
        "final_reserve_tokens": 40000,
    }


def test_load_config_reads_mcp_runtime_limits(monkeypatch):
    monkeypatch.setenv("LENSNODE_MCP_DISCOVERY_TIMEOUT_S", "12")
    monkeypatch.setenv("LENSNODE_MCP_TOOL_TIMEOUT_S", "45")
    monkeypatch.setenv("LENSNODE_MCP_DEFER_THRESHOLD", "8")

    config = load_config()

    assert config.mcp_discovery_timeout_s == 12
    assert config.mcp_tool_timeout_s == 45
    assert config.mcp_defer_threshold == 8


def test_load_config_uses_medium_reasoning_for_initial_planning(
    monkeypatch,
):
    monkeypatch.delenv(
        "LENSNODE_PLANNING_REASONING_EFFORT",
        raising=False,
    )

    config = load_config()

    assert config.planning_reasoning_effort == "medium"


def test_load_config_disables_planner_repair_by_default(monkeypatch):
    monkeypatch.delenv(
        "LENSNODE_PLANNER_REPAIR_ENABLED",
        raising=False,
    )

    config = load_config()

    assert config.planner_repair_enabled is False


def test_load_config_can_enable_planner_repair(monkeypatch):
    monkeypatch.setenv("LENSNODE_PLANNER_REPAIR_ENABLED", "true")

    config = load_config()

    assert config.planner_repair_enabled is True


def test_load_config_uses_trusted_container_execution_by_default(monkeypatch):
    monkeypatch.delenv("LENSNODE_EXECUTION_BACKEND", raising=False)

    config = load_config()

    assert config.execution_backend == "trusted_container"


def test_load_config_reads_execution_backend(monkeypatch):
    monkeypatch.setenv("LENSNODE_EXECUTION_BACKEND", "filesystem")

    config = load_config()

    assert config.execution_backend == "filesystem"


def test_load_config_allows_mcp_discovery_and_cleanup_headroom(monkeypatch):
    monkeypatch.delenv(
        "LENSNODE_MCP_DISCOVERY_TIMEOUT_S",
        raising=False,
    )

    config = load_config()

    assert config.mcp_discovery_timeout_s == 30
