import hashlib
import json
import tracemalloc
from pathlib import Path

from lensnode import agent_tools
from lensnode.agent_tools import _saved_output_synopsis
from lensnode.agent_tools import build_general_chat_tools
from lensnode.runtime_resources import RuntimeResources


def test_invalid_paths_return_schema_and_specific_recovery_without_guessing_fields(tmp_path):
    resources = _resources(tmp_path)
    ref, _ = _write_result(resources, {"repositories": [{"repository": "team/repo", "pull_requests": []}]})
    tool = _tool(resources, "analyze_structured_output")
    bad_path = json.loads(tool.invoke({"ref": ref, "operation": "count", "path": "repositories.pull_requests"}))
    assert bad_path["error"] == "PATH_NOT_FOUND"
    assert "numeric" in bad_path["recovery_hint"]
    assert "synopsis" in bad_path
    bad_field = json.loads(tool.invoke({
        "ref": ref,
        "operation": "project",
        "path": "repositories",
        "fields": ["name"],
    }))
    assert bad_field["error"] == "FIELD_NOT_FOUND"
    assert "repository" in json.dumps(bad_field["synopsis"])


def test_optional_projection_preserves_successful_rows_and_strict_required_fields(tmp_path):
    resources = _resources(tmp_path)
    ref, _ = _write_result(resources, {"repositories": [
        {"repository": "team/healthy"},
        {"repository": "team/partial", "errors": {"pull_requests": "RESPONSE_TOO_LARGE"}},
    ]})
    tool = _tool(resources, "analyze_structured_output")
    result = json.loads(tool.invoke({
        "ref": ref, "operation": "project", "path": "repositories", "fields": ["repository", "errors?"],
    }))
    assert result["result"] == [
        {"repository": "team/healthy", "errors": None},
        {"repository": "team/partial", "errors": {"pull_requests": "RESPONSE_TOO_LARGE"}},
    ]
    strict = json.loads(tool.invoke({
        "ref": ref, "operation": "project", "path": "repositories", "fields": ["name"],
    }))
    assert strict["error"] == "FIELD_NOT_FOUND"


def _resources(root, transforms=None):
    """Build minimal runtime resources for General Chat tool tests."""

    root = Path(root)
    skill_dir = root / "skills" / "orders"
    skill_dir.mkdir(parents=True)
    kwargs = {
        "root": root,
        "skill_paths": ["skills/orders"],
        "context_skill_contents": [],
        "skill_environments": {"orders": {}},
        "mcp_config_path": root / "mcp.json",
    }
    if transforms is not None:
        kwargs["skill_transforms"] = {"orders": transforms}
    return RuntimeResources(
        **kwargs,
    )


def _write_result(resources, payload, filename="orders.json"):
    """Write one JSON result and return its virtual runtime reference."""

    output_dir = resources.root / "large_tool_results"
    output_dir.mkdir(parents=True)
    path = output_dir / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    return f"/large_tool_results/{filename}", path


def _write_text_result(resources, content, filename="result.txt"):
    """Write one text result and return its virtual runtime reference."""

    output_dir = resources.root / "large_tool_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    path.write_text(content, encoding="utf-8")
    return f"/large_tool_results/{filename}", path


def _tool(resources, name, command=None, events=None):
    """Return one General Chat tool by name."""

    tools = build_general_chat_tools(
        command or {},
        resources,
        emit_event=(
            (lambda event, detail: events.append((event, detail)))
            if events is not None
            else None
        ),
    )
    return next(item for item in tools if item.name == name)


def test_analyze_structured_output_groups_json_and_records_trace(tmp_path):
    resources = _resources(tmp_path)
    ref, path = _write_result(
        resources,
        {
            "results": [
                {"status": "paid", "amount": 10},
                {"status": "pending", "amount": 20},
                {"status": "paid", "amount": 30},
            ]
        },
    )
    events = []

    payload = json.loads(
        _tool(resources, "analyze_structured_output", events=events).invoke(
            {
                "ref": ref,
                "operation": "group_count",
                "path": "results",
                "group_by": ["status"],
            }
        )
    )

    assert payload["ok"] is True
    assert payload["result"] == [
        {"group": {"status": "paid"}, "count": 2},
        {"group": {"status": "pending"}, "count": 1},
    ]
    assert payload["input_ref"] == ref
    assert payload["input_bytes"] == path.stat().st_size
    assert payload["input_sha256"] == hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    assert payload["output_bytes"] > 0
    assert payload["duration_ms"] >= 0
    assert len(payload["invocation_id"]) == 32
    assert events[0][0] == "tool.analyze_structured_output.start"
    assert events[-1][0] == "tool.analyze_structured_output.done"
    assert events[0][1]["invocation_id"] == payload["invocation_id"]
    assert events[-1][1]["input_ref"] == ref
    assert events[-1][1]["input_bytes"] == path.stat().st_size
    assert events[-1][1]["output_bytes"] == payload["output_bytes"]
    assert events[-1][1]["result_count"] == 2


def test_project_supports_root_and_nested_object_metadata(tmp_path):
    """Object metadata can be projected without treating it as a record collection."""

    resources = _resources(tmp_path)
    ref, _path = _write_result(resources, {"ok": True, "coverage": {"complete": True}, "items": [1, 2]})
    tool = _tool(resources, "analyze_structured_output")

    root = json.loads(tool.invoke({"ref": ref, "operation": "project", "fields": ["ok", "coverage.complete"]}))
    nested = json.loads(tool.invoke({"ref": ref, "operation": "project", "path": "coverage", "fields": ["complete"]}))

    assert root["ok"] is True
    assert root["result"] == {"ok": True, "coverage.complete": True}
    assert nested["result"] == {"complete": True}


def test_analyze_structured_output_supports_bounded_operations(tmp_path):
    resources = _resources(tmp_path)
    ref, _path = _write_result(
        resources,
        {
            "results": [
                {"id": "a", "amount": 10},
                {"id": "b", "amount": 30},
                {"id": "c", "amount": 20},
            ]
        },
    )
    tool = _tool(resources, "analyze_structured_output")

    count = json.loads(
        tool.invoke({"ref": ref, "operation": "count", "path": "results"})
    )
    projected = json.loads(
        tool.invoke(
            {
                "ref": ref,
                "operation": "project",
                "path": "results",
                "fields": ["id"],
                "limit": 2,
            }
        )
    )
    summed = json.loads(
        tool.invoke(
            {
                "ref": ref,
                "operation": "sum",
                "path": "results",
                "field": "amount",
            }
        )
    )
    minimum = json.loads(
        tool.invoke(
            {
                "ref": ref,
                "operation": "min",
                "path": "results",
                "field": "amount",
            }
        )
    )
    maximum = json.loads(
        tool.invoke(
            {
                "ref": ref,
                "operation": "max",
                "path": "results",
                "field": "amount",
            }
        )
    )
    sorted_result = json.loads(
        tool.invoke(
            {
                "ref": ref,
                "operation": "sort",
                "path": "results",
                "field": "amount",
                "fields": ["id"],
                "descending": True,
                "limit": 2,
            }
        )
    )
    page = json.loads(
        _tool(resources, "analyze_structured_output").invoke(
            {
                "ref": ref,
                "operation": "paginate",
                "path": "results",
                "fields": ["id"],
                "offset": 1,
                "limit": 1,
            }
        )
    )
    sample = json.loads(
        _tool(resources, "analyze_structured_output").invoke(
            {
                "ref": ref,
                "operation": "sample",
                "path": "results",
                "fields": ["id"],
                "limit": 2,
            }
        )
    )

    assert count["result"] == 3
    assert projected["result"] == [{"id": "a"}, {"id": "b"}]
    assert summed["result"] == 60
    assert minimum["result"] == 10
    assert maximum["result"] == 30
    assert [item["id"] for item in sorted_result["result"]] == ["b", "c"]
    assert sorted_result["result"] == [{"id": "b"}, {"id": "c"}]
    assert page["result"] == [{"id": "b"}]
    assert sample["result"] == [{"id": "a"}, {"id": "b"}]


def test_analyze_structured_output_validates_record_completeness(tmp_path):
    resources = _resources(tmp_path)
    ref, _path = _write_result(
        resources,
        {
            "results": [
                {"id": "a", "enterprise": "Acme", "quantity": 10},
                {"id": "b", "enterprise": "", "quantity": 20},
                {"id": "b", "enterprise": "Beta", "quantity": None},
                {"enterprise": "Gamma", "quantity": 30},
            ]
        },
    )

    payload = json.loads(
        _tool(resources, "analyze_structured_output").invoke(
            {
                "ref": ref,
                "operation": "validate_records",
                "path": "results",
                "expected_count": 4,
                "unique_by": ["id"],
                "fields": ["enterprise", "quantity"],
            }
        )
    )

    assert payload["ok"] is True
    assert payload["result"] == {
        "valid": False,
        "total_count": 4,
        "expected_count": 4,
        "count_matches": True,
        "unique_by": ["id"],
        "duplicate_count": 1,
        "missing_unique_key_count": 1,
        "missing_required": {"enterprise": 1, "quantity": 1},
    }


def test_validate_records_is_a_dedicated_tool_and_records_evidence(tmp_path):
    resources = _resources(tmp_path)
    ref, _path = _write_result(
        resources,
        {
            "total": 2,
            "items": [
                {"code": "ORDER-1"},
                {"code": "ORDER-2"},
            ],
        },
    )
    runtime_evidence = {}
    tools = build_general_chat_tools(
        {},
        resources,
        runtime_evidence=runtime_evidence,
    )
    validate_records = next(
        item for item in tools if item.name == "validate_records"
    )

    payload = json.loads(
        validate_records.invoke(
            {
                "ref": ref,
                "unique_by": ["code"],
                "fields": ["code"],
            }
        )
    )

    assert payload["result"]["valid"] is True
    assert runtime_evidence["record_validation"] == payload["result"]


def test_analyze_structured_output_verifies_all_276_records(tmp_path):
    resources = _resources(tmp_path)
    ref, _path = _write_result(
        resources,
        {
            "results": [
                {
                    "id": f"order-{index}",
                    "enterprise": f"enterprise-{index}",
                    "quantity": index + 1,
                }
                for index in range(276)
            ]
        },
    )

    payload = json.loads(
        _tool(resources, "analyze_structured_output").invoke(
            {
                "ref": ref,
                "operation": "validate_records",
                "path": "results",
                "expected_count": 276,
                "unique_by": ["id"],
                "fields": ["enterprise", "quantity"],
            }
        )
    )

    assert payload["result"]["valid"] is True
    assert payload["result"]["total_count"] == 276
    assert payload["result"]["duplicate_count"] == 0
    assert payload["result"]["missing_required"] == {
        "enterprise": 0,
        "quantity": 0,
    }


def test_validate_records_rejects_an_unbounded_collection(
    tmp_path,
    monkeypatch,
):
    resources = _resources(tmp_path)
    ref, _path = _write_result(
        resources,
        [{"id": 1}, {"id": 2}, {"id": 3}],
    )
    monkeypatch.setattr(
        agent_tools,
        "_STRUCTURED_VALIDATION_MAX_ITEMS",
        2,
    )

    payload = json.loads(
        _tool(resources, "analyze_structured_output").invoke(
            {
                "ref": ref,
                "operation": "validate_records",
                "unique_by": ["id"],
            }
        )
    )

    assert payload["ok"] is False
    assert payload["error"] == "VALIDATION_ITEM_LIMIT_EXCEEDED"


def test_analyze_structured_output_rejects_unsafe_or_invalid_input(tmp_path):
    resources = _resources(tmp_path)
    outside = resources.root / "outside.json"
    outside.write_text("[]", encoding="utf-8")
    output_dir = resources.root / "large_tool_results"
    output_dir.mkdir()
    (output_dir / "link.json").symlink_to(outside)
    invalid = output_dir / "invalid.json"
    invalid.write_text("not json", encoding="utf-8")
    tool = _tool(resources, "analyze_structured_output")

    escaped = json.loads(
        tool.invoke(
            {
                "ref": "/large_tool_results/../outside.json",
                "operation": "count",
            }
        )
    )
    linked = json.loads(
        tool.invoke(
            {
                "ref": "/large_tool_results/link.json",
                "operation": "count",
            }
        )
    )
    malformed = json.loads(
        tool.invoke(
            {
                "ref": "/large_tool_results/invalid.json",
                "operation": "count",
            }
        )
    )

    assert escaped["error"] == "INPUT_REF_NOT_ALLOWED"
    assert linked["error"] == "INPUT_REF_NOT_ALLOWED"
    assert malformed["error"] == "INVALID_JSON"


def test_structured_analysis_ignores_legacy_fixed_call_budget(tmp_path):
    resources = _resources(tmp_path)
    ref, _path = _write_result(resources, [1, 2, 3])
    command = {
        "settings": {"tool_policy": {"structured_analysis_max_calls": 1}}
    }
    tool = _tool(resources, "analyze_structured_output", command=command)

    results = [
        json.loads(tool.invoke({"ref": ref, "operation": "count"}))
        for _index in range(3)
    ]

    assert [result["result"] for result in results] == [3, 3, 3]
    assert all(result["ok"] is True for result in results)
    assert all("call_budget_exhausted" not in result for result in results)


def test_record_validation_does_not_treat_wrapper_total_as_page_count(
    tmp_path,
):
    resources = _resources(tmp_path)
    ref, _path = _write_result(
        resources,
        {
            "total": 44,
            "items": [{"code": f"ORDER-{index}"} for index in range(20)],
        },
    )
    tool = _tool(resources, "validate_records")

    result = json.loads(
        tool.invoke({"ref": ref, "unique_by": ["code"]})
    )["result"]

    assert result["valid"] is True
    assert result["total_count"] == 20
    assert result["expected_count"] is None
    assert result["count_matches"] is None


def test_record_validation_can_repeat_with_an_explicit_complete_count(
    tmp_path,
):
    resources = _resources(tmp_path)
    ref, _path = _write_result(
        resources,
        {
            "total": 2,
            "items": [
                {"code": "ORDER-1", "status": "approved"},
                {"code": "ORDER-2", "status": "pending"},
            ],
        },
    )
    command = {
        "settings": {
            "tool_policy": {
                "structured_analysis_max_calls": 1,
                "structured_validation_max_calls": 1,
            }
        }
    }
    tool = _tool(
        resources,
        "analyze_structured_output",
        command=command,
    )

    first = json.loads(tool.invoke({"ref": ref, "operation": "count"}))
    second = json.loads(
        tool.invoke({"ref": ref, "operation": "count"})
    )
    validated = json.loads(
        tool.invoke(
            {
                "ref": ref,
                "operation": "validate_records",
                "expected_count": 2,
                "unique_by": ["code"],
                "fields": ["code", "status"],
            }
        )
    )
    repeated = json.loads(
        tool.invoke(
            {
                "ref": ref,
                "operation": "validate_records",
                "expected_count": 2,
                "unique_by": ["code"],
            }
        )
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert validated["result"] == {
        "valid": True,
        "total_count": 2,
        "expected_count": 2,
        "count_matches": True,
        "unique_by": ["code"],
        "duplicate_count": 0,
        "missing_unique_key_count": 0,
        "missing_required": {"code": 0, "status": 0},
    }
    assert repeated["ok"] is True


def test_inspect_saved_output_summarizes_csv_and_reads_bounded_window(
    tmp_path,
):
    resources = _resources(tmp_path)
    ref, _path = _write_text_result(
        resources,
        "id,status,amount\n1,paid,10\n2,pending,20\n3,paid,30\n",
    )

    payload = json.loads(
        _tool(resources, "inspect_saved_output").invoke(
            {"ref": ref, "offset": 1, "limit": 2}
        )
    )

    assert payload["ok"] is True
    assert payload["format"] == "csv"
    assert payload["synopsis"]["columns"] == [
        "id",
        "status",
        "amount",
    ]
    assert payload["synopsis"]["row_count"] == 3
    assert payload["lines"] == [
        {"number": 2, "text": "1,paid,10"},
        {"number": 3, "text": "2,pending,20"},
    ]
    assert payload["has_more"] is True


def test_inspect_saved_output_summarizes_text_and_caps_long_lines(tmp_path):
    resources = _resources(tmp_path)
    ref, _path = _write_text_result(
        resources,
        "first\n" + ("x" * 800) + "\nthird\n",
    )

    payload = json.loads(
        _tool(resources, "inspect_saved_output").invoke(
            {"ref": ref, "offset": 0, "limit": 2}
        )
    )

    assert payload["format"] == "text"
    assert payload["synopsis"]["line_count"] == 3
    assert payload["lines"][0] == {"number": 1, "text": "first"}
    assert len(payload["lines"][1]["text"]) == 501
    assert payload["lines"][1]["text"].endswith("…")


def test_inspect_saved_output_rejects_escape_and_symlink(tmp_path):
    resources = _resources(tmp_path)
    outside = resources.root / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    output_dir = resources.root / "large_tool_results"
    output_dir.mkdir()
    (output_dir / "link.txt").symlink_to(outside)
    tool = _tool(resources, "inspect_saved_output")

    escaped = json.loads(
        tool.invoke(
            {
                "ref": "/large_tool_results/../outside.txt",
                "offset": 0,
                "limit": 1,
            }
        )
    )
    linked = json.loads(
        tool.invoke(
            {
                "ref": "/large_tool_results/link.txt",
                "offset": 0,
                "limit": 1,
            }
        )
    )

    assert escaped == {"ok": False, "error": "INPUT_REF_NOT_ALLOWED"}
    assert linked == {"ok": False, "error": "INPUT_REF_NOT_ALLOWED"}


def test_saved_output_synopsis_bounds_memory_for_large_text():
    text = "value\n" * (2 * 1024 * 1024 // 6)
    raw = text.encode("utf-8")

    tracemalloc.start()
    output_format, synopsis = _saved_output_synopsis(raw, text)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert output_format == "text"
    assert synopsis["line_count"] > 300000
    assert peak < len(raw) * 2


def test_run_skill_transform_uses_declared_entrypoint_and_input_ref(tmp_path):
    transforms = {
        "summarize-orders": {
            "entrypoint": "scripts/summarize_orders.py",
            "input_format": "json",
            "environment": [],
        }
    }
    resources = _resources(tmp_path, transforms)
    script = resources.root / "skills/orders/scripts/summarize_orders.py"
    script.parent.mkdir()
    script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "print(json.dumps({'count': len(payload['results'])}))\n",
        encoding="utf-8",
    )
    transforms["summarize-orders"]["sha256"] = hashlib.sha256(
        script.read_bytes()
    ).hexdigest()
    ref, input_path = _write_result(resources, {"results": [1, 2, 3]})
    events = []

    payload = json.loads(
        _tool(resources, "run_skill_transform", events=events).invoke(
            {
                "skill": "orders",
                "transform": "summarize-orders",
                "stdin_ref": ref,
            }
        )
    )

    assert payload["ok"] is True
    assert json.loads(payload["stdout"])["count"] == 3
    assert payload["input_ref"] == ref
    assert payload["input_bytes"] == input_path.stat().st_size
    assert payload["stdout_bytes"] > 0
    assert payload["duration_ms"] >= 0
    assert len(payload["invocation_id"]) == 32
    assert events[0][0] == "tool.run_skill_transform.start"
    assert events[-1][0] == "tool.run_skill_transform.done"
    assert events[-1][1]["entrypoint"] == "scripts/summarize_orders.py"
    assert events[-1][1]["input_sha256"] == payload["input_sha256"]
    assert events[-1][1]["stdout_sha256"] == payload["stdout_sha256"]


def test_run_skill_transform_rejects_undeclared_and_invalid_json(tmp_path):
    transforms = {
        "summarize-orders": {
            "entrypoint": "scripts/summarize_orders.py",
            "input_format": "json",
            "environment": [],
        }
    }
    resources = _resources(tmp_path, transforms)
    script = resources.root / "skills/orders/scripts/summarize_orders.py"
    script.parent.mkdir()
    script.write_text("print('ok')\n", encoding="utf-8")
    transforms["summarize-orders"]["sha256"] = hashlib.sha256(
        script.read_bytes()
    ).hexdigest()
    output_dir = resources.root / "large_tool_results"
    output_dir.mkdir()
    invalid = output_dir / "invalid.json"
    invalid.write_text("not json", encoding="utf-8")
    tool = _tool(resources, "run_skill_transform")

    undeclared = json.loads(
        tool.invoke(
            {
                "skill": "orders",
                "transform": "other",
                "stdin_ref": "/large_tool_results/invalid.json",
            }
        )
    )
    malformed = json.loads(
        tool.invoke(
            {
                "skill": "orders",
                "transform": "summarize-orders",
                "stdin_ref": "/large_tool_results/invalid.json",
            }
        )
    )

    assert undeclared["error"] == "TRANSFORM_NOT_DECLARED"
    assert malformed["error"] == "INVALID_JSON"


def test_skill_transform_ignores_legacy_fixed_call_budget(tmp_path):
    transforms = {
        "summarize-orders": {
            "entrypoint": "scripts/summarize_orders.py",
            "input_format": "json",
            "environment": [],
        }
    }
    resources = _resources(tmp_path, transforms)
    script = resources.root / "skills/orders/scripts/summarize_orders.py"
    script.parent.mkdir()
    script.write_text("print('ok')\n", encoding="utf-8")
    transforms["summarize-orders"]["sha256"] = hashlib.sha256(
        script.read_bytes()
    ).hexdigest()
    ref, _path = _write_result(resources, {})
    command = {
        "settings": {"tool_policy": {"skill_transform_max_calls": 1}}
    }
    tool = _tool(resources, "run_skill_transform", command=command)

    results = [
        json.loads(
            tool.invoke(
                {
                    "skill": "orders",
                    "transform": "summarize-orders",
                    "stdin_ref": ref,
                }
            )
        )
        for _index in range(2)
    ]

    assert [result["ok"] for result in results] == [True, True]


def test_run_skill_transform_rejects_entrypoint_hash_mismatch(tmp_path):
    expected = b"print('expected')\n"
    transforms = {
        "summarize-orders": {
            "entrypoint": "scripts/summarize_orders.py",
            "input_format": "json",
            "environment": [],
            "sha256": hashlib.sha256(expected).hexdigest(),
        }
    }
    resources = _resources(tmp_path, transforms)
    script = resources.root / "skills/orders/scripts/summarize_orders.py"
    script.parent.mkdir()
    script.write_text("print('tampered')\n", encoding="utf-8")
    ref, _path = _write_result(resources, {})

    payload = json.loads(
        _tool(resources, "run_skill_transform").invoke(
            {
                "skill": "orders",
                "transform": "summarize-orders",
                "stdin_ref": ref,
            }
        )
    )

    assert payload["error"] == "TRANSFORM_HASH_MISMATCH"
