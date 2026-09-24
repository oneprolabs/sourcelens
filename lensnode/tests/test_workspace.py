import json
from pathlib import Path

from lensnode import workspace as workspace_module
from lensnode.agent_tools import build_agent_tools
from lensnode.workspace import (
    glob_files,
    read_workspace_window,
    search_workspace,
)

OLD_SIZE_CAP = 256 * 1024
TRUNCATED_LINE_CEILING = 2000 + len("…[truncated]")


def _make_big_file(path, keyword_line_no, keyword="Horcrux", total=10000):
    """Write a UTF-8 file larger than the old 256KB cap."""

    lines = []
    for index in range(1, total + 1):
        if index == keyword_line_no:
            lines.append(f"line {index}: the {keyword} secret was shared")
        else:
            lines.append(f"line {index}: ordinary narrative filler text here")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _make_converted_document(root, name="Contract-2026.pdf"):
    """Create a source document and its searchable conversion sidecar."""

    source = root / name
    source.write_bytes(b"document bytes")
    sidecar = Path(f"{source}.sourcelens")
    sidecar.mkdir(parents=True)
    content = sidecar / "content.md"
    content.write_text(
        "# Contract-2026\n\nWarranty period: five years.\n",
        encoding="utf-8",
    )
    (sidecar / "meta.json").write_text(
        '{"retrieval": {"source_path": "Contract-2026.pdf"}}',
        encoding="utf-8",
    )
    assets = sidecar / "assets"
    assets.mkdir()
    (assets / "description.txt").write_text(
        "internal image description",
        encoding="utf-8",
    )
    return source, content


def test_search_returns_line_matches_for_large_file(tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    big = _make_big_file(root / "book.txt", keyword_line_no=5000)
    assert big.stat().st_size > OLD_SIZE_CAP

    result = search_workspace([{"path": str(root)}], "Horcrux")

    matches = result["matches"]
    assert matches, "a >256KB file must still be searchable"
    hit = next(item for item in matches if item["line"] == 5000)
    assert "Horcrux" in hit["text"]
    assert hit["path"].endswith("book.txt")
    assert any(item["n"] == 4999 for item in hit["before"])
    assert any(item["n"] == 5001 for item in hit["after"])


def test_max_file_size_policy_is_ignored(tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    _make_big_file(root / "book.txt", keyword_line_no=20)

    result = search_workspace(
        [{"path": str(root)}],
        "Horcrux",
        policy={"max_file_size": 1024},
    )

    assert result["matches"], "deprecated max_file_size must no longer exclude"


def test_ranking_prioritizes_broad_term_coverage(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    # alphabetically first, but only one query term repeated -> noise
    (root / "aaa.txt").write_text(
        "\n".join(["alpha noise"] * 5) + "\n", encoding="utf-8"
    )
    # alphabetically last, but covers all three query terms -> relevant
    (root / "zzz.txt").write_text(
        "alpha line\nbeta line\ngamma line\n", encoding="utf-8"
    )

    result = search_workspace([{"path": str(root)}], "alpha beta gamma")

    assert result["matches"]
    assert result["matches"][0]["path"].endswith("zzz.txt")


def test_image_and_vector_assets_excluded_from_search(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "diagram.svg").write_text(
        "<text>deployment topology</text>\n", encoding="utf-8"
    )
    (root / "guide.md").write_text("deployment steps here\n", encoding="utf-8")

    result = search_workspace([{"path": str(root)}], "deployment")

    paths = [match["path"] for match in result["matches"]]
    assert any(path.endswith("guide.md") for path in paths)
    assert not any(path.endswith(".svg") for path in paths)


def test_converted_documents_use_source_paths_in_all_search_modes(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    source, content = _make_converted_document(root)
    target_dirs = [{"path": str(root)}]

    matches = search_workspace(target_dirs, "Warranty period")
    files = search_workspace(
        target_dirs,
        "Warranty period",
        output_mode="files",
    )
    counts = search_workspace(
        target_dirs,
        "Warranty period",
        output_mode="count",
    )
    found = glob_files(target_dirs, "**/*")

    assert matches["matches"][0]["path"] == str(source)
    assert files["files"] == [str(source)]
    assert counts["counts"] == [{"path": str(source), "count": 1}]
    assert found.count(str(source)) == 1
    assert str(content) not in found
    assert not any(".sourcelens" in path for path in found)


def test_original_document_path_reads_searchable_sidecar_text(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    source, _content = _make_converted_document(root)
    tools = {
        item.name: item
        for item in build_agent_tools(
            {"target_dirs": [{"path": str(root)}], "settings": {}}
        )
    }

    result = json.loads(
        tools["read_workspace_file"].invoke({"path": str(source)})
    )

    assert result["path"] == str(source)
    assert "Warranty period: five years." in result["content"]
    assert ".sourcelens" not in result["path"]


def test_orphaned_sidecar_artifacts_are_not_retrieval_evidence(tmp_path):
    root = tmp_path / "ws"
    sidecar = root / "missing.pdf.sourcelens"
    sidecar.mkdir(parents=True)
    content = sidecar / "content.md"
    content.write_text("orphaned secret marker\n", encoding="utf-8")
    target_dirs = [{"path": str(root)}]

    result = search_workspace(target_dirs, "orphaned secret marker")
    found = glob_files(target_dirs, "**/*")

    assert result["matches"] == []
    assert str(content) not in result["files"]
    assert str(content) not in found


def test_converted_document_alias_cannot_escape_workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"outside")
    source = root / "linked.pdf"
    source.symlink_to(outside)
    sidecar = root / "linked.pdf.sourcelens"
    sidecar.mkdir()
    content = sidecar / "content.md"
    content.write_text("outside alias marker\n", encoding="utf-8")
    target_dirs = [{"path": str(root)}]

    result = search_workspace(target_dirs, "outside alias marker")
    found = glob_files(target_dirs, "**/*")

    assert result["matches"] == []
    assert str(source) not in result["files"]
    assert str(source) not in found


def test_regex_search_matches_pattern(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "log.txt").write_text(
        "start\nERROR_CODE_42 happened\nok\nERROR_CODE_99 again\n",
        encoding="utf-8",
    )

    result = search_workspace(
        [{"path": str(root)}], r"ERROR_CODE_\d+", regex=True
    )

    assert sorted(match["line"] for match in result["matches"]) == [2, 4]


def test_output_mode_files_lists_matching_files(tmp_path):
    root = tmp_path / "ws"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("deploy here\n", encoding="utf-8")
    (root / "sub" / "b.txt").write_text("nothing\n", encoding="utf-8")

    result = search_workspace(
        [{"path": str(root)}], "deploy", output_mode="files"
    )

    assert result["mode"] == "files"
    assert any(path.endswith("a.txt") for path in result["files"])
    assert not any(path.endswith("b.txt") for path in result["files"])


def test_output_mode_count_returns_per_file_counts(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("deploy\ndeploy\nother\n", encoding="utf-8")

    result = search_workspace(
        [{"path": str(root)}], "deploy", output_mode="count"
    )

    assert result["mode"] == "count"
    assert result["counts"][0]["path"].endswith("a.txt")
    assert result["counts"][0]["count"] == 2


def test_glob_filter_restricts_search_by_type(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.md").write_text("deploy steps\n", encoding="utf-8")
    (root / "b.py").write_text("deploy = 1\n", encoding="utf-8")

    result = search_workspace([{"path": str(root)}], "deploy", glob="**/*.md")

    paths = [match["path"] for match in result["matches"]]
    assert any(path.endswith("a.md") for path in paths)
    assert not any(path.endswith("b.py") for path in paths)


def test_glob_files_finds_by_type(tmp_path):
    root = tmp_path / "ws"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "one.md").write_text("x\n", encoding="utf-8")
    (root / "docs" / "two.md").write_text("y\n", encoding="utf-8")
    (root / "code.py").write_text("z\n", encoding="utf-8")

    files = glob_files([{"path": str(root)}], "**/*.md")

    assert len(files) == 2
    assert all(path.endswith(".md") for path in files)


def test_workspace_tools_follow_datasource_symlinks(tmp_path):
    source = tmp_path / "datasources" / "source-version"
    source.mkdir(parents=True)
    document = source / "docs" / "guide.md"
    document.parent.mkdir()
    document.write_text("AGIOne retrieval marker\n", encoding="utf-8")
    session = tmp_path / "sessions" / "run-1" / "sources"
    session.mkdir(parents=True)
    link = session / "ds_source"
    link.symlink_to(source, target_is_directory=True)
    target_dirs = [{"path": str(tmp_path / "sessions" / "run-1")}]

    result = search_workspace(target_dirs, "AGIOne")
    files = glob_files(target_dirs, "**/*.md")

    assert any(match["path"].endswith("guide.md") for match in result["matches"])
    assert any(path.endswith("guide.md") for path in files)


def test_workspace_tools_reject_unselected_workspace_symlinks(tmp_path):
    runtime = tmp_path / ".sourcelens" / "runtime" / "runs" / "other"
    runtime.mkdir(parents=True)
    (runtime / "secret.md").write_text("must stay private", encoding="utf-8")
    session = tmp_path / "sessions" / "run-1" / "sources"
    session.mkdir(parents=True)
    (session / "runtime").symlink_to(runtime, target_is_directory=True)
    target_dirs = [{"path": str(tmp_path / "sessions" / "run-1")}]

    result = search_workspace(target_dirs, "private")
    files = glob_files(target_dirs, "**/*.md")

    assert result["matches"] == []
    assert not files


def test_workspace_tools_reject_unselected_file_symlinks(tmp_path):
    secret = tmp_path / ".sourcelens" / "runtime" / "secret.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("must stay private", encoding="utf-8")
    session = tmp_path / "sessions" / "run-1"
    session.mkdir(parents=True)
    (session / "secret.txt").symlink_to(secret)

    result = search_workspace([{"path": str(session)}], "private")

    assert result["matches"] == []


def test_glob_files_by_name_pattern(tmp_path):
    root = tmp_path / "ws"
    (root / "installation").mkdir(parents=True)
    (root / "installation" / "agione-quick-install.md").write_text(
        "x\n", encoding="utf-8"
    )
    (root / "readme.md").write_text("y\n", encoding="utf-8")

    files = glob_files([{"path": str(root)}], "**/*install*")

    assert any(path.endswith("agione-quick-install.md") for path in files)
    assert not any(path.endswith("readme.md") for path in files)


def test_glob_files_handles_invalid_pattern(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.md").write_text("x\n", encoding="utf-8")

    # an absolute pattern is invalid for pathlib glob and must not raise
    assert glob_files([{"path": str(root)}], "/etc/passwd") == []


def test_read_window_pages_with_offset_and_limit(tmp_path):
    target = tmp_path / "doc.txt"
    target.write_text(
        "\n".join(f"row {index}" for index in range(1, 1001)) + "\n",
        encoding="utf-8",
    )

    window = read_workspace_window(str(target), offset=1, limit=100)
    assert window["start_line"] == 1
    assert window["end_line"] == 100
    assert window["returned_lines"] == 100
    assert window["has_more"] is True
    assert window["content"].startswith("1\trow 1")

    deep = read_workspace_window(str(target), offset=950, limit=100)
    assert deep["start_line"] == 950
    assert deep["end_line"] == 1000
    assert deep["has_more"] is False
    assert "950\trow 950" in deep["content"]


def test_read_window_rejects_binary(tmp_path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"PK\x03\x04\x00\x00binary\x00content")

    window = read_workspace_window(str(target))

    assert window["error"] == "BINARY_FILE"


def test_long_lines_are_truncated(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    long_line = "x" * 5000 + " NEEDLE " + "y" * 5000
    (root / "wide.txt").write_text(long_line + "\n", encoding="utf-8")

    result = search_workspace([{"path": str(root)}], "NEEDLE")

    text = result["matches"][0]["text"]
    assert "truncated" in text
    assert len(text) <= TRUNCATED_LINE_CEILING


def test_search_without_match_lists_nested_files(tmp_path):
    root = tmp_path / "ws"
    nested = root / "inner"
    nested.mkdir(parents=True)
    (nested / "a.txt").write_text("alpha beta gamma\n", encoding="utf-8")

    result = search_workspace([{"path": str(root)}], "zzzznomatchquery")

    assert result["matches"] == []
    assert any(path.endswith("a.txt") for path in result["files"])
    assert "note" in result


def test_hidden_descendants_are_excluded_by_default_across_discovery_modes(
    tmp_path,
):
    root = tmp_path / "ws"
    hidden = root / ".claude"
    hidden.mkdir(parents=True)
    hidden_file = hidden / "instructions.md"
    hidden_file.write_text("hidden policy marker\n", encoding="utf-8")

    content = search_workspace([{"path": str(root)}], "policy marker")
    files = search_workspace(
        [{"path": str(root)}],
        "policy marker",
        output_mode="files",
    )
    counts = search_workspace(
        [{"path": str(root)}],
        "policy marker",
        output_mode="count",
    )
    fallback = search_workspace([{"path": str(root)}], "zzzznomatchquery")

    assert content["matches"] == []
    assert files["files"] == []
    assert counts["counts"] == []
    assert str(hidden_file) not in fallback["files"]
    assert str(hidden_file) not in glob_files([{"path": str(root)}], "**/*")


def test_scope_include_hidden_applies_to_all_discovery_modes(tmp_path):
    root = tmp_path / "ws"
    hidden = root / ".codex"
    hidden.mkdir(parents=True)
    hidden_file = hidden / "instructions.md"
    hidden_file.write_text("hidden policy marker\n", encoding="utf-8")
    target_dirs = [
        {
            "path": str(root),
            "retrieval_scope": {"include_hidden": True},
        }
    ]

    content = search_workspace(target_dirs, "policy marker")
    files = search_workspace(
        target_dirs,
        "policy marker",
        output_mode="files",
    )
    counts = search_workspace(
        target_dirs,
        "policy marker",
        output_mode="count",
    )
    fallback = search_workspace(target_dirs, "zzzznomatchquery")

    assert content["matches"][0]["path"] == str(hidden_file)
    assert files["files"] == [str(hidden_file)]
    assert counts["counts"] == [{"path": str(hidden_file), "count": 1}]
    assert str(hidden_file) in fallback["files"]
    assert str(hidden_file) in glob_files(target_dirs, "**/*")


def test_include_hidden_never_exposes_internal_run_data(tmp_path):
    root = tmp_path / "ws"
    hidden = root / ".codex"
    checkpoints = root / ".checkpoints"
    other_run = (
        root
        / ".sourcelens"
        / "runtime"
        / "runs"
        / "other-run"
        / "subject-documents"
    )
    hidden.mkdir(parents=True)
    checkpoints.mkdir()
    other_run.mkdir(parents=True)
    allowed_file = hidden / "instructions.md"
    checkpoint_file = checkpoints / "messages.txt"
    other_run_file = other_run / "private.txt"
    allowed_file.write_text("shared marker\n", encoding="utf-8")
    checkpoint_file.write_text("shared marker\n", encoding="utf-8")
    other_run_file.write_text("shared marker\n", encoding="utf-8")
    target_dirs = [
        {
            "path": str(root),
            "retrieval_scope": {"include_hidden": True},
        }
    ]

    result = search_workspace(target_dirs, "shared marker")
    found = glob_files(target_dirs, "**/*")
    tools = {
        tool.name: tool
        for tool in build_agent_tools(
            {"target_dirs": target_dirs, "settings": {}}
        )
    }
    checkpoint_read = json.loads(
        tools["read_workspace_file"].invoke({"path": str(checkpoint_file)})
    )
    other_run_read = json.loads(
        tools["read_workspace_file"].invoke({"path": str(other_run_file)})
    )

    assert [item["path"] for item in result["matches"]] == [
        str(allowed_file)
    ]
    assert str(checkpoint_file) not in found
    assert str(other_run_file) not in found
    assert checkpoint_read["error"] == "PATH_NOT_ALLOWED"
    assert other_run_read["error"] == "PATH_NOT_ALLOWED"


def test_directory_scope_overrides_assistant_hidden_policy(tmp_path):
    root = tmp_path / "ws"
    hidden = root / ".claude"
    hidden.mkdir(parents=True)
    hidden_file = hidden / "settings.json"
    hidden_file.write_text("policy marker\n", encoding="utf-8")

    disabled = search_workspace(
        [
            {
                "path": str(root),
                "retrieval_scope": {"include_hidden": False},
            }
        ],
        "policy marker",
        policy={"include_hidden": True},
    )
    enabled = search_workspace(
        [
            {
                "path": str(root),
                "retrieval_scope": {"include_hidden": True},
            }
        ],
        "policy marker",
        policy={"include_hidden": False},
    )

    assert disabled["matches"] == []
    assert enabled["matches"][0]["path"] == str(hidden_file)


def test_explicit_hidden_root_is_evaluated_relative_to_itself(tmp_path):
    root = tmp_path / ".managed" / ".claude"
    root.mkdir(parents=True)
    target = root / "instructions.md"
    target.write_text("policy marker\n", encoding="utf-8")

    result = search_workspace(
        [
            {
                "path": str(root),
                "retrieval_scope": {"include_hidden": True},
            }
        ],
        "policy marker",
    )

    assert result["matches"][0]["path"] == str(target)


def test_subject_runtime_root_remains_readable_through_agent_tools(tmp_path):
    root = (
        tmp_path
        / ".sourcelens"
        / "runtime"
        / "runs"
        / "run-123"
        / "subject-documents"
    )
    root.mkdir(parents=True)
    target = root / "content.md"
    target.write_text("subject marker\n", encoding="utf-8")
    tools = {
        tool.name: tool
        for tool in build_agent_tools(
            {
                "target_dirs": [
                    {
                        "path": str(root),
                        "material_role": "subject",
                    }
                ],
                "settings": {},
            }
        )
    }

    result = json.loads(
        tools["read_workspace_file"].invoke({"path": str(target)})
    )

    assert "subject marker" in result["content"]


def test_ripgrep_modes_add_hidden_flag_only_when_enabled(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "ws"
    root.mkdir()
    commands = []

    def record_command(command):
        commands.append(command)
        return ""

    monkeypatch.setattr(workspace_module, "_run_rg", record_command)
    enabled = [
        {
            "path": str(root),
            "retrieval_scope": {"include_hidden": True},
        }
    ]
    disabled = [{"path": str(root)}]

    for output_mode in ("content", "files", "count"):
        search_workspace(enabled, "marker", output_mode=output_mode)

    assert len(commands) == 3
    assert all("--hidden" in command for command in commands)

    commands.clear()
    for output_mode in ("content", "files", "count"):
        search_workspace(disabled, "marker", output_mode=output_mode)

    assert len(commands) == 3
    assert all("--hidden" not in command for command in commands)


def test_include_hidden_keeps_exclusions_and_symlink_containment(tmp_path):
    root = tmp_path / "ws"
    hidden = root / ".codex"
    excluded = root / ".git"
    hidden.mkdir(parents=True)
    excluded.mkdir()
    allowed_file = hidden / "instructions.md"
    excluded_file = excluded / "config"
    outside_file = tmp_path / "outside.txt"
    allowed_file.write_text("policy marker\n", encoding="utf-8")
    excluded_file.write_text("policy marker\n", encoding="utf-8")
    outside_file.write_text("policy marker\n", encoding="utf-8")
    (hidden / "outside-link.txt").symlink_to(outside_file)
    target_dirs = [
        {
            "path": str(root),
            "retrieval_scope": {
                "include_hidden": True,
                "exclude_dirs": [".git"],
            },
        }
    ]

    result = search_workspace(target_dirs, "policy marker")
    found = glob_files(target_dirs, "**/*")

    assert [item["path"] for item in result["matches"]] == [str(allowed_file)]
    assert str(allowed_file) in found
    assert str(excluded_file) not in found
    assert str(hidden / "outside-link.txt") not in found


def test_python_search_fallback_honors_include_hidden(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    hidden = root / ".claude"
    hidden.mkdir(parents=True)
    target = hidden / "instructions.md"
    target.write_text("policy marker\n", encoding="utf-8")
    monkeypatch.setattr(workspace_module, "_run_rg", lambda _cmd: None)

    result = search_workspace(
        [
            {
                "path": str(root),
                "retrieval_scope": {"include_hidden": True},
            }
        ],
        "policy marker",
    )

    assert result["matches"][0]["path"] == str(target)


def test_hidden_policy_controls_direct_reads_and_directory_fallback(tmp_path):
    root = tmp_path / "ws"
    hidden = root / ".claude"
    excluded = root / ".git"
    hidden.mkdir(parents=True)
    excluded.mkdir()
    hidden_file = hidden / "instructions.md"
    excluded_file = excluded / "config"
    outside_file = tmp_path / "outside.txt"
    hidden_file.write_text("policy marker\n", encoding="utf-8")
    excluded_file.write_text("excluded marker\n", encoding="utf-8")
    outside_file.write_text("outside marker\n", encoding="utf-8")
    link = hidden / "outside-link.txt"
    link.symlink_to(outside_file)

    default_tools = {
        tool.name: tool
        for tool in build_agent_tools(
            {"target_dirs": [{"path": str(root)}], "settings": {}}
        )
    }
    enabled_tools = {
        tool.name: tool
        for tool in build_agent_tools(
            {
                "target_dirs": [
                    {
                        "path": str(root),
                        "retrieval_scope": {
                            "include_hidden": True,
                            "exclude_dirs": [".git"],
                        },
                    }
                ],
                "settings": {},
            }
        )
    }

    default_read = json.loads(
        default_tools["read_workspace_file"].invoke({"path": str(hidden_file)})
    )
    enabled_read = json.loads(
        enabled_tools["read_workspace_file"].invoke({"path": str(hidden_file)})
    )
    directory = json.loads(
        enabled_tools["read_workspace_file"].invoke({"path": str(root)})
    )
    excluded_read = json.loads(
        enabled_tools["read_workspace_file"].invoke(
            {"path": str(excluded_file)}
        )
    )
    escaped_read = json.loads(
        enabled_tools["read_workspace_file"].invoke({"path": str(link)})
    )

    assert default_read["error"] == "PATH_NOT_ALLOWED"
    assert "policy marker" in enabled_read["content"]
    assert str(hidden_file) in directory["candidate_files"]
    assert str(excluded_file) not in directory["candidate_files"]
    assert excluded_read["error"] == "PATH_NOT_ALLOWED"
    assert escaped_read["error"] == "PATH_NOT_ALLOWED"


def test_tools_search_and_read_large_file_through_wrapper(tmp_path):
    root = tmp_path / "books"
    root.mkdir()
    big = _make_big_file(root / "book.txt", keyword_line_no=10)
    tools = {
        tool.name: tool
        for tool in build_agent_tools(
            {"target_dirs": [{"path": str(root)}], "settings": {}}
        )
    }

    search_out = json.loads(
        tools["search_workspace"].invoke({"query": "Horcrux"})
    )
    assert search_out["matches"]
    line = search_out["matches"][0]["line"]

    read_out = json.loads(
        tools["read_workspace_file"].invoke(
            {"path": str(big), "offset": line, "limit": 5}
        )
    )
    assert read_out["start_line"] == line
    assert "Horcrux" in read_out["content"]


def test_tool_lists_nested_files_when_path_is_directory(tmp_path):
    root = tmp_path / "harrypotter"
    inner = root / "HarryPotter"
    inner.mkdir(parents=True)
    (inner / "book01.txt").write_text("once upon a time\n", encoding="utf-8")
    tools = {
        tool.name: tool
        for tool in build_agent_tools(
            {"target_dirs": [{"path": str(root)}], "settings": {}}
        )
    }

    out = json.loads(tools["read_workspace_file"].invoke({"path": str(root)}))

    assert out["error"] == "PATH_IS_DIRECTORY"
    assert any(path.endswith("book01.txt") for path in out["candidate_files"])


def test_search_rerank_reorders_the_prefix_before_truncation(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("alpha one\n", encoding="utf-8")
    (root / "b.txt").write_text("alpha two\n", encoding="utf-8")
    (root / "c.txt").write_text("alpha three\n", encoding="utf-8")

    seen = {}

    def rerank(matches):
        seen["paths"] = [item["path"] for item in matches]
        return [2, 0]

    result = search_workspace(
        [{"path": str(root)}], "alpha", rerank=rerank
    )

    paths = [item["path"] for item in result["matches"]]
    assert paths == [
        seen["paths"][2],
        seen["paths"][0],
        seen["paths"][1],
    ]


def test_search_rerank_none_keeps_deterministic_order(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("alpha one\n", encoding="utf-8")
    (root / "b.txt").write_text("alpha two\n", encoding="utf-8")

    baseline = search_workspace([{"path": str(root)}], "alpha")
    same = search_workspace(
        [{"path": str(root)}], "alpha", rerank=lambda _matches: None
    )

    assert [item["path"] for item in same["matches"]] == [
        item["path"] for item in baseline["matches"]
    ]


def test_search_rerank_failure_keeps_deterministic_order(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("alpha one\n", encoding="utf-8")
    (root / "b.txt").write_text("alpha two\n", encoding="utf-8")

    def boom(_matches):
        raise RuntimeError("rerank failed")

    baseline = search_workspace([{"path": str(root)}], "alpha")
    result = search_workspace([{"path": str(root)}], "alpha", rerank=boom)

    assert [item["path"] for item in result["matches"]] == [
        item["path"] for item in baseline["matches"]
    ]


class _FakeEvidenceRanker:
    """Ranker stub returning a fixed evidence order and recording calls."""

    def __init__(self, order):
        self.order = order
        self.calls = []

    def rank_evidence(self, candidates, instructions=""):
        self.calls.append((candidates, instructions))
        return self.order


def test_agent_search_tool_reranks_with_the_bound_ranker(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("alpha one\n", encoding="utf-8")
    (root / "b.txt").write_text("alpha two\n", encoding="utf-8")

    ranker = _FakeEvidenceRanker(["1", "0"])
    tools = {
        tool.name: tool
        for tool in build_agent_tools(
            {
                "question": "where is alpha?",
                "target_dirs": [{"path": str(root)}],
                "settings": {},
            },
            evidence_reranker=ranker,
        )
    }

    payload = json.loads(
        tools["search_workspace"].invoke({"query": "alpha"})
    )

    assert [item["path"] for item in payload["matches"]] == [
        str(root / "b.txt"),
        str(root / "a.txt"),
    ]
    assert ranker.calls
    assert ranker.calls[0][1] == "where is alpha?"


def test_agent_search_tool_without_ranker_keeps_order(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("alpha one\n", encoding="utf-8")
    (root / "b.txt").write_text("alpha two\n", encoding="utf-8")

    baseline = search_workspace([{"path": str(root)}], "alpha")
    tools = {
        tool.name: tool
        for tool in build_agent_tools(
            {"target_dirs": [{"path": str(root)}], "settings": {}}
        )
    }
    payload = json.loads(
        tools["search_workspace"].invoke({"query": "alpha"})
    )

    assert [item["path"] for item in payload["matches"]] == [
        item["path"] for item in baseline["matches"]
    ]


def test_pipe_batches_or_keywords(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("only alpha here\n", encoding="utf-8")
    (root / "b.txt").write_text("only beta here\n", encoding="utf-8")
    (root / "c.txt").write_text("unrelated content\n", encoding="utf-8")

    result = search_workspace([{"path": str(root)}], "alpha|beta")

    paths = {item["path"] for item in result["matches"]}
    assert paths == {str(root / "a.txt"), str(root / "b.txt")}


def test_fullwidth_pipe_batches_or_keywords(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("only alpha here\n", encoding="utf-8")
    (root / "b.txt").write_text("only beta here\n", encoding="utf-8")

    result = search_workspace([{"path": str(root)}], "alpha｜beta")

    paths = {item["path"] for item in result["matches"]}
    assert paths == {str(root / "a.txt"), str(root / "b.txt")}


def test_search_payload_is_bounded_by_budget(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    for index in range(20):
        (root / f"f{index:02d}.txt").write_text(
            f"alpha payload line {index}\n", encoding="utf-8"
        )

    unbounded = search_workspace([{"path": str(root)}], "alpha")
    assert len(unbounded["matches"]) == 20
    assert "truncated" not in unbounded

    bounded = search_workspace(
        [{"path": str(root)}],
        "alpha",
        policy={"search_payload_chars": 300},
    )
    assert bounded["truncated"] is True
    assert 0 < len(bounded["matches"]) < 20
    assert bounded["note"]


def test_repeated_identical_search_short_circuits(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("alpha one\n", encoding="utf-8")

    tools = {
        tool.name: tool
        for tool in build_agent_tools(
            {"target_dirs": [{"path": str(root)}], "settings": {}}
        )
    }
    first = json.loads(
        tools["search_workspace"].invoke({"query": "alpha"})
    )
    assert first["matches"]

    second = json.loads(
        tools["search_workspace"].invoke({"query": "alpha"})
    )
    assert second.get("cached") is True
    assert "matches" not in second
    assert second["paths"] == [str(root / "a.txt")]
