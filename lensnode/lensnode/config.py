from dataclasses import dataclass
import os


@dataclass(frozen=True)
class LensNodeConfig:
    """Runtime configuration for a LensNode."""

    name: str
    token: str
    control_ws_url: str
    ai_gateway_url: str
    deliverable_upload_url: str
    tls_skip_verify: bool
    tls_ca_file: str | None
    deliverable_max_bytes: int
    workspace_path: str
    protocol_version: str
    agent_version: str
    heartbeat_interval_s: int
    request_timeout_s: int
    run_idle_timeout_s: int
    drain_timeout_s: int
    max_concurrent_runs: int
    summary_trigger_tokens: int
    summary_keep_tokens: int
    offload_tool_tokens: int
    offload_human_tokens: int | None
    tool_budget_max_calls: int = 64
    context_window_tokens: int = 128000
    summary_trigger_ratio: float = 0.75
    token_budget_max_tokens: int = 200000
    token_budget_hard_max_tokens: int = 500000
    token_budget_final_reserve_tokens: int = 40000
    token_budget_warn_ratio: float = 0.8
    mcp_discovery_timeout_s: int = 30
    mcp_tool_timeout_s: int = 60
    mcp_defer_threshold: int = 12
    mcp_stdio_allowlist: tuple[str, ...] = ("codegraph",)
    mcp_enable_codegraph: bool = True
    codegraph_command: str = "codegraph"
    codegraph_init_timeout_s: int = 300
    node_options: str = ""
    reasoning_effort: str | None = None
    planning_reasoning_effort: str | None = "medium"
    planner_repair_enabled: bool = False
    execution_backend: str = "trusted_container"
    delegation_base_url: str = ""
    stream_recovery_attempts: int = 3
    stream_recovery_backoff_s: float = 1.0
    stream_recovery_backoff_max_s: float = 8.0


def _optional_int(value):
    """Return int(value), or None when the env var is unset or empty."""

    return int(value) if value not in (None, "") else None


def _env_bool(name, default=False):
    """Return a case-insensitive boolean environment setting."""

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _node_options():
    """Return bounded V8 options for Node-based child processes."""

    options = os.getenv("LENSNODE_NODE_OPTIONS", "").strip()
    try:
        memory_limit_mb = int(os.getenv("LENSNODE_MEMORY_LIMIT_MB", "0"))
    except (TypeError, ValueError):
        memory_limit_mb = 0
    if memory_limit_mb <= 0:
        memory_limit_mb = _cgroup_memory_limit_mb()
    try:
        heap_mb = int(os.getenv("LENSNODE_NODE_MAX_OLD_SPACE_MB", "0"))
    except (TypeError, ValueError):
        heap_mb = 0
    if heap_mb <= 0 and memory_limit_mb > 0:
        heap_mb = max(64, memory_limit_mb // 2)
    if heap_mb > 0 and "--max-old-space-size=" not in options:
        options = f"{options} --max-old-space-size={heap_mb}".strip()
    return options


def _cgroup_memory_limit_mb():
    """Read a finite Linux cgroup memory limit when one is available."""

    for filename in [
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ]:
        try:
            with open(filename, encoding="ascii") as stream:
                raw = stream.read().strip()
            if raw == "max":
                continue
            limit = int(raw)
        except (OSError, ValueError):
            continue
        if limit > 0:
            return max(1, limit // (1024 * 1024))
    return 0


def _derive_ws_url(server_url):
    """Derive the control WebSocket URL from the base server URL."""

    base = server_url.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :] + "/ws/lens/lensnodes/"
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :] + "/ws/lens/lensnodes/"
    return base + "/ws/lens/lensnodes/"


def load_config():
    """Load LensNode configuration from environment variables.

    Only LENSNODE_SERVER_URL (the base address) is required for connectivity;
    the control WebSocket and AI gateway URLs are derived from it unless
    overridden explicitly.
    """

    server_url = os.getenv(
        "LENSNODE_SERVER_URL", "http://backend-api:8000"
    ).rstrip("/")
    return LensNodeConfig(
        name=os.getenv("LENSNODE_NAME", "local-dev-lensnode"),
        token=os.getenv("LENSNODE_TOKEN", "dev-lensnode-token"),
        control_ws_url=os.getenv("LENSNODE_CONTROL_WS_URL")
        or _derive_ws_url(server_url),
        ai_gateway_url=os.getenv("LENSNODE_AI_GATEWAY_URL")
        or f"{server_url}/api/lens/lensnode/ai-gateway/",
        deliverable_upload_url=os.getenv("LENSNODE_DELIVERABLE_UPLOAD_URL")
        or f"{server_url}/api/lens/lensnode/deliverables/",
        tls_skip_verify=_env_bool("LENSNODE_TLS_SKIP_VERIFY"),
        tls_ca_file=os.getenv("LENSNODE_TLS_CA_FILE") or None,
        deliverable_max_bytes=int(
            os.getenv("LENSNODE_DELIVERABLE_MAX_BYTES", str(50 * 1024 * 1024))
        ),
        workspace_path=os.getenv("LENSNODE_WORKSPACE_PATH", "/workspace"),
        protocol_version=os.getenv("LENSNODE_PROTOCOL_VERSION", "v1"),
        agent_version=os.getenv("LENSNODE_AGENT_VERSION", "dev"),
        heartbeat_interval_s=int(
            os.getenv("LENSNODE_HEARTBEAT_INTERVAL_S", "15")
        ),
        request_timeout_s=int(os.getenv("LENSNODE_REQUEST_TIMEOUT_S", "240")),
        run_idle_timeout_s=int(
            os.getenv("LENSNODE_RUN_IDLE_TIMEOUT_S", "180")
        ),
        drain_timeout_s=int(
            os.getenv("LENSNODE_DRAIN_TIMEOUT_S", "240")
        ),
        max_concurrent_runs=int(os.getenv("LENSNODE_MAX_CONCURRENT_RUNS", "1")),
        summary_trigger_tokens=int(
            os.getenv("LENSNODE_SUMMARY_TRIGGER_TOKENS", "48000")
        ),
        summary_keep_tokens=int(
            os.getenv("LENSNODE_SUMMARY_KEEP_TOKENS", "16000")
        ),
        context_window_tokens=int(
            os.getenv("LENSNODE_CONTEXT_WINDOW_TOKENS", "128000")
        ),
        summary_trigger_ratio=float(
            os.getenv("LENSNODE_SUMMARY_TRIGGER_RATIO", "0.75")
        ),
        token_budget_max_tokens=int(
            os.getenv("LENSNODE_TOKEN_BUDGET_MAX_TOKENS", "200000")
        ),
        token_budget_hard_max_tokens=int(
            os.getenv(
                "LENSNODE_TOKEN_BUDGET_HARD_MAX_TOKENS",
                "500000",
            )
        ),
        token_budget_final_reserve_tokens=int(
            os.getenv(
                "LENSNODE_TOKEN_BUDGET_FINAL_RESERVE_TOKENS",
                "40000",
            )
        ),
        token_budget_warn_ratio=float(
            os.getenv("LENSNODE_TOKEN_BUDGET_WARN_RATIO", "0.8")
        ),
        mcp_discovery_timeout_s=int(
            os.getenv("LENSNODE_MCP_DISCOVERY_TIMEOUT_S", "30")
        ),
        mcp_tool_timeout_s=int(
            os.getenv("LENSNODE_MCP_TOOL_TIMEOUT_S", "60")
        ),
        mcp_defer_threshold=int(
            os.getenv("LENSNODE_MCP_DEFER_THRESHOLD", "12")
        ),
        mcp_stdio_allowlist=tuple(
            item.strip()
            for item in os.getenv(
                "LENSNODE_MCP_STDIO_ALLOWLIST", "codegraph"
            ).split(",")
            if item.strip()
        ),
        mcp_enable_codegraph=_env_bool(
            "LENSNODE_MCP_ENABLE_CODEGRAPH", default=True
        ),
        codegraph_command=os.getenv(
            "LENSNODE_CODEGRAPH_COMMAND", "codegraph"
        ),
        codegraph_init_timeout_s=int(
            os.getenv("LENSNODE_CODEGRAPH_INIT_TIMEOUT_S", "300")
        ),
        node_options=_node_options(),
        reasoning_effort=os.getenv("LENSNODE_REASONING_EFFORT") or None,
        planning_reasoning_effort=(
            os.getenv(
                "LENSNODE_PLANNING_REASONING_EFFORT",
                "medium",
            )
            or None
        ),
        planner_repair_enabled=_env_bool(
            "LENSNODE_PLANNER_REPAIR_ENABLED", default=False
        ),
        execution_backend=os.getenv(
            "LENSNODE_EXECUTION_BACKEND", "trusted_container"
        ).strip().lower(),
        delegation_base_url=os.getenv("LENSNODE_DELEGATION_BASE_URL")
        or f"{server_url}/api/lens/lensnode/runs",
        offload_tool_tokens=int(
            os.getenv("LENSNODE_OFFLOAD_TOOL_TOKENS") or "5000"
        ),
        offload_human_tokens=_optional_int(
            os.getenv("LENSNODE_OFFLOAD_HUMAN_TOKENS")
        ),
        tool_budget_max_calls=int(
            os.getenv("LENSNODE_TOOL_BUDGET_MAX_CALLS", "64")
        ),
        stream_recovery_attempts=int(
            os.getenv("LENSNODE_STREAM_RECOVERY_ATTEMPTS", "3")
        ),
        stream_recovery_backoff_s=float(
            os.getenv("LENSNODE_STREAM_RECOVERY_BACKOFF_S", "1")
        ),
        stream_recovery_backoff_max_s=float(
            os.getenv("LENSNODE_STREAM_RECOVERY_BACKOFF_MAX_S", "8")
        ),
    )
