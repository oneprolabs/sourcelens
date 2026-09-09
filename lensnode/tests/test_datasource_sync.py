import base64
import io
import json
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
from threading import Event

import httpx
import pytest

from lensnode.datasource_manifest import MARKER_FILE
from lensnode.datasource_sync import (
    DataSourceSyncError,
    _cleanup_removed_git_repositories,
    datasource_sync_workers,
    inspect_datasource_path,
    _default_git_branch,
    _export_feishu_document,
    _export_filename,
    _feishu_folder_token,
    _feishu_export_extension,
    _feishu_export_type,
    _discover_git_organization,
    _is_feishu_exportable_type,
    _feishu_item_unchanged,
    _feishu_target_file_path,
    _git_remote_branches,
    _git_auth_environment,
    _git_error_detail,
    _run_git,
    _validate_git_tree_size,
    _http_json,
    _manifest_item_to_sync_item,
    list_datasource_files,
    _poll_feishu_export_task,
    _raise_feishu_business_error,
    _resolve_git_repository_target,
    repo_target_subdir,
    _sync_git,
    _sync_git_submodules,
    _sync_feishu_folder,
    sync_datasource,
    upload_managed_workspace,
)
from lensnode.path_rules import source_sha256
from lensnode.path_rules import stable_suffix


def test_datasource_sync_workers_defaults_to_four():
    """Datasource sync workers default to four and accept command override."""

    assert datasource_sync_workers({}) == 4
    assert datasource_sync_workers({"max_workers": "8"}) == 8
    assert datasource_sync_workers({"max_workers": "invalid"}) == 4


def test_list_datasource_files_returns_safe_paginated_manifest_items(tmp_path):
    """Datasource file catalogs expose relative paths, never workspace paths."""

    target = tmp_path / "catalog"
    target.mkdir()
    (target / ".sourcelens-datasource.json").write_text(
        json.dumps({"datasource_uuid": "datasource-1"}),
        encoding="utf-8",
    )
    (target / "report.pdf").write_bytes(b"pdf")
    sidecar = target / "report.pdf.sourcelens"
    sidecar.mkdir()
    (sidecar / "meta.json").write_text(
        json.dumps(
            {
                "conversion": {
                    "status": "success",
                    "generated_at": "2026-09-09T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "source_id": "feishu:token:doc_one",
                        "local_path": "report.pdf",
                        "name": "Report",
                        "file_extension": "pdf",
                        "status": "synced",
                        "metadata": {"modified_time": "2026-09-08T00:00:00Z"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = list_datasource_files(
        {
            "datasource_uuid": "datasource-1",
            "target_path": str(target),
            "page": 1,
            "page_size": 20,
        },
        workspace_path=tmp_path,
    )

    assert result["count"] == 1
    assert result["results"] == [
        {
            "path": "report.pdf",
            "name": "Report",
            "extension": "pdf",
            "sync_status": "synced",
            "conversion_status": "success",
            "source_updated_at": "2026-09-08T00:00:00Z",
            "converted_at": "2026-09-09T00:00:00Z",
            "conversion_error": "",
        }
    ]


def test_list_datasource_files_lists_managed_workspace_files(tmp_path):
    """Managed workspaces list source files without exposing sidecar content."""

    target = tmp_path / "managed"
    target.mkdir()
    (target / "notes.txt").write_text("notes", encoding="utf-8")
    sidecar = target / "notes.txt.sourcelens"
    sidecar.mkdir()
    (sidecar / "content.md").write_text("derived", encoding="utf-8")

    result = list_datasource_files(
        {
            "datasource_uuid": "datasource-1",
            "source_type": "managed_workspace",
            "target_path": str(target),
        },
        workspace_path=tmp_path,
    )

    assert result["count"] == 1
    assert result["results"][0]["path"] == "notes.txt"
    assert result["results"][0]["conversion_status"] == "not_converted"


def test_managed_workspace_path_must_exist(tmp_path):
    """Managed workspace inspection never offers to create missing paths."""

    result = inspect_datasource_path(
        {
            "source_type": "managed_workspace",
            "target_path": str(tmp_path / "missing"),
        },
        workspace_path=tmp_path,
    )

    assert result["status"] == "blocked"
    assert result["message_code"] == "managed_workspace_missing"
    assert result["will_create"] is False


def test_managed_workspace_accepts_non_git_directory(tmp_path):
    """Managed workspace inspection accepts externally populated content."""

    target = tmp_path / "restored"
    target.mkdir()
    (target / "snapshot.txt").write_text("external data")

    result = inspect_datasource_path(
        {
            "source_type": "managed_workspace",
            "target_path": str(target),
        },
        workspace_path=tmp_path,
    )

    assert result["status"] == "available"
    assert result["message_code"] == "managed_workspace_available"
    assert result["exists"] is True
    assert result["is_directory"] is True


def test_managed_workspace_rejects_symlink_outside_workspace(tmp_path):
    """Managed workspace inspection resolves symlinks before validation."""

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    target = tmp_path / "outside-link"
    target.symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        DataSourceSyncError,
        match="LENS_SOURCE_TARGET_PATH_INVALID",
    ):
        inspect_datasource_path(
            {
                "source_type": "managed_workspace",
                "target_path": str(target),
            },
            workspace_path=tmp_path,
        )


def test_managed_workspace_upload_extracts_archive_and_converts(
    tmp_path,
    monkeypatch,
):
    """Managed uploads safely extract archives before conversion."""

    target = tmp_path / "documents"
    target.mkdir()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("nested/guide.pdf", b"pdf")
    monkeypatch.setattr(
        "lensnode.datasource_sync.convert_managed_workspace",
        lambda command, workspace_path: {
            "status": "success",
            "conversion_summary": {"converted": 1},
        },
    )

    result = upload_managed_workspace(
        {
            "target_path": str(target),
            "filename": "package.zip",
            "content_base64": base64.b64encode(archive.getvalue()).decode(),
        },
        workspace_path=tmp_path,
    )

    assert result["uploaded"] == "package.zip"
    assert (target / "package.zip").exists()
    assert (target / "nested" / "guide.pdf").read_bytes() == b"pdf"


def test_managed_workspace_upload_rejects_archive_path_traversal(tmp_path):
    """Managed uploads reject archive members escaping the workspace."""

    target = tmp_path / "documents"
    target.mkdir()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../outside.txt", b"unsafe")

    with pytest.raises(
        DataSourceSyncError,
        match="DATASOURCE_UPLOAD_ARCHIVE_PATH_INVALID",
    ):
        upload_managed_workspace(
            {
                "target_path": str(target),
                "filename": "package.zip",
                "content_base64": base64.b64encode(
                    archive.getvalue()
                ).decode(),
            },
            workspace_path=tmp_path,
        )
    assert not (tmp_path / "outside.txt").exists()


def test_git_auth_environment_keeps_token_out_of_repository_url():
    """Git credentials are supplied through ephemeral process config."""

    environment = _git_auth_environment(
        {
            "auth_scheme": "token",
            "access_token": "ghp_example",
        }
    )

    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert "ghp_example" not in environment["GIT_CONFIG_VALUE_0"]
    assert "Basic" in environment["GIT_CONFIG_VALUE_0"]


def test_git_error_detail_redacts_url_credentials():
    """Git diagnostics must not return credentials to task history."""

    class Failure:
        stderr = (
            "fatal: unable to access "
            "'https://oauth2:secret@github.com/repo'"
        )
        stdout = ""

    assert "secret" not in _git_error_detail(Failure())
    assert "https://***@github.com/repo" in _git_error_detail(Failure())


def test_run_git_honors_cancellation_before_start():
    """Cancelled datasource work must not start a Git subprocess."""

    cancel_event = Event()
    cancel_event.set()

    with pytest.raises(DataSourceSyncError, match="LENS_SOURCE_SYNC_CANCELLED"):
        _run_git(["--version"], cancel_event=cancel_event)


def test_validate_git_tree_size_enforces_file_ceiling(tmp_path, monkeypatch):
    """Git repositories must stay within the LensNode resource budget."""

    target = tmp_path / "repo"
    target.mkdir()
    (target / "one.txt").write_text("content", encoding="utf-8")
    monkeypatch.setattr("lensnode.datasource_sync.GIT_MAX_FILES", 0)

    with pytest.raises(
        DataSourceSyncError,
        match="LENS_SOURCE_RESOURCE_LIMIT_EXCEEDED",
    ):
        _validate_git_tree_size(target)


def test_git_remote_branches_parses_heads():
    """Remote branch discovery returns branch names."""

    output = (
        "abc\trefs/heads/main\n"
        "def\trefs/heads/feature/demo\n"
        "ghi\trefs/tags/v1.0.0\n"
    )

    assert _git_remote_branches(output) == ["main", "feature/demo"]


def test_default_git_branch_prefers_main():
    """Git connection tests can choose a branch when input is empty."""

    assert _default_git_branch(["dev", "main"]) == "main"
    assert _default_git_branch(["dev", "master"]) == "master"
    assert _default_git_branch(["release"]) == "release"


def test_cleanup_removed_git_repositories_keeps_current_and_foreign(tmp_path):
    """Only stale child repositories owned by this datasource are removed."""

    stale = tmp_path / "team" / "stale"
    current = tmp_path / "team" / "current"
    foreign = tmp_path / "other" / "foreign"
    for child, datasource_uuid in [
        (stale, "datasource-1"),
        (current, "datasource-1"),
        (foreign, "datasource-2"),
    ]:
        child.mkdir(parents=True)
        (child / MARKER_FILE).write_text(
            json.dumps({"datasource_uuid": datasource_uuid}),
            encoding="utf-8",
        )

    removed = _cleanup_removed_git_repositories(
        tmp_path,
        {"team/current"},
        "datasource-1",
    )

    assert removed == ["team/stale"]
    assert not stale.exists()
    assert current.exists()
    assert foreign.exists()


def test_repo_target_subdir_preserves_safe_resource_path():
    assert repo_target_subdir({"target_subdir": "group/team/repo"}) == (
        "group/team/repo"
    )


@pytest.mark.parametrize(
    "target_subdir",
    ["/absolute/repo", "../escape", "group//repo", "group/./repo"],
)
def test_repo_target_subdir_rejects_unsafe_resource_path(target_subdir):
    with pytest.raises(
        DataSourceSyncError,
        match="LENS_SOURCE_CONFIG_INVALID",
    ):
        repo_target_subdir({"target_subdir": target_subdir})


def test_repository_target_reuses_owned_legacy_encoded_path(tmp_path):
    legacy = tmp_path / "group%2Frepo"
    (legacy / ".git").mkdir(parents=True)
    (legacy / MARKER_FILE).write_text(
        json.dumps({"datasource_uuid": "datasource-1"}),
        encoding="utf-8",
    )

    identity, target = _resolve_git_repository_target(
        tmp_path,
        {"target_subdir": "group/repo"},
        "datasource-1",
        single_repository=False,
    )

    assert identity == "group/repo"
    assert target == legacy.resolve()


def test_repository_target_reuses_unmarked_matching_legacy_repo(
    tmp_path,
    monkeypatch,
):
    legacy = tmp_path / "group%2Frepo"
    (legacy / ".git").mkdir(parents=True)
    monkeypatch.setattr(
        "lensnode.datasource_sync._git_output",
        lambda *args, **kwargs: "https://git.example.com/group/repo.git",
    )

    identity, target = _resolve_git_repository_target(
        tmp_path,
        {
            "repo_url": "https://git.example.com/group/repo.git",
            "target_subdir": "group/repo",
        },
        "datasource-1",
        single_repository=False,
    )

    assert identity == "group/repo"
    assert target == legacy.resolve()


def test_repository_target_does_not_reuse_foreign_marked_repo(
    tmp_path,
    monkeypatch,
):
    legacy = tmp_path / "group%2Frepo"
    (legacy / ".git").mkdir(parents=True)
    (legacy / MARKER_FILE).write_text(
        json.dumps({"datasource_uuid": "datasource-2"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lensnode.datasource_sync._git_output",
        lambda *args, **kwargs: "https://git.example.com/group/repo.git",
    )

    identity, target = _resolve_git_repository_target(
        tmp_path,
        {
            "repo_url": "https://git.example.com/group/repo.git",
            "target_subdir": "group/repo",
        },
        "datasource-1",
        single_repository=False,
    )

    assert identity == "group/repo"
    assert target == (tmp_path / "group" / "repo").resolve()


def test_repository_target_reuses_owned_single_repository_root(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / MARKER_FILE).write_text(
        json.dumps({"datasource_uuid": "datasource-1"}),
        encoding="utf-8",
    )

    identity, target = _resolve_git_repository_target(
        tmp_path,
        {"target_subdir": "group/repo"},
        "datasource-1",
        single_repository=True,
    )

    assert identity == "group/repo"
    assert target == tmp_path.resolve()


def test_repository_target_reuses_owned_legacy_leaf_path(tmp_path):
    legacy = tmp_path / "repo"
    (legacy / ".git").mkdir(parents=True)
    (legacy / MARKER_FILE).write_text(
        json.dumps({"datasource_uuid": "datasource-1"}),
        encoding="utf-8",
    )

    identity, target = _resolve_git_repository_target(
        tmp_path,
        {"target_subdir": "group/repo"},
        "datasource-1",
        single_repository=True,
    )

    assert identity == "group/repo"
    assert target == legacy.resolve()


def test_repository_target_uses_canonical_path_for_new_repository(tmp_path):
    identity, target = _resolve_git_repository_target(
        tmp_path,
        {"target_subdir": "group/team/repo"},
        "datasource-1",
        single_repository=True,
    )

    assert identity == "group/team/repo"
    assert target == (tmp_path / "group" / "team" / "repo").resolve()


def test_repository_target_blocks_unrecognized_existing_single_layout(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"datasource_uuid": "datasource-1", "items": []}),
        encoding="utf-8",
    )

    with pytest.raises(
        DataSourceSyncError,
        match="LENS_SOURCE_GIT_LAYOUT_MIGRATION_REQUIRED",
    ):
        _resolve_git_repository_target(
            tmp_path,
            {"target_subdir": "group/repo"},
            "datasource-1",
            single_repository=True,
        )


def test_new_git_datasource_syncs_to_canonical_resource_path(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=source,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=source,
        check=True,
    )
    subprocess.run(
        ["git", "branch", "-M", "main"],
        cwd=source,
        check=True,
    )
    (source / "README.md").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=source, check=True)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "datasource"
    result = sync_datasource(
        {
            "source_type": "git",
            "datasource_uuid": "datasource-1",
            "target_path": str(target),
            "config": {
                "repositories": [
                    {
                        "repo_url": str(source),
                        "branch": "main",
                        "target_subdir": "group/repo",
                    }
                ]
            },
        },
        workspace_path=workspace,
    )

    repository = target / "group" / "repo"
    manifest = json.loads((target / "manifest.json").read_text())
    assert result["status"] == "success"
    assert (repository / ".git").is_dir()
    assert manifest["items"][0]["local_path"] == "group/repo/README.md"
    assert manifest["items"][0]["source_id"] == (
        f"git:{source}:main:README.md"
    )


def test_git_manifest_items_honors_repository_directory(tmp_path, monkeypatch):
    """Git datasource manifests include only the selected subdirectory."""

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("guide")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("app")
    monkeypatch.setattr(
        "lensnode.datasource_sync._git_output",
        lambda *args, **kwargs: "commit",
    )

    from lensnode.datasource_sync import _git_manifest_items

    items = _git_manifest_items(
        tmp_path,
        "https://github.com/owner/repo.git",
        "main",
        directory="docs",
    )

    assert [item.local_path for item in items] == ["docs/guide.md"]


def test_discover_github_org_repositories(monkeypatch):
    """GitHub organization URLs use the GitHub organization API."""

    calls = []

    def fake_api(url, config, auth_style, timeout=30):
        del config, timeout
        calls.append((url, auth_style))
        return [
            {
                "name": "repo",
                "full_name": "Acme/repo",
                "clone_url": "https://github.com/Acme/repo.git",
                "default_branch": "main",
            }
        ], ""

    monkeypatch.setattr("lensnode.datasource_sync._git_api_json", fake_api)
    monkeypatch.setattr(
        "lensnode.datasource_sync._git_repo_branches",
        lambda repo_url, config: ["main"],
    )

    result = _discover_git_organization(
        {
            "repo_url": "https://github.com/Acme",
            "provider": "github",
            "auth_scheme": "token",
            "access_token": "ghp_example",
        }
    )

    assert result["status"] == "success"
    assert result["details"]["owner_type"] == "org"
    assert result["details"]["repositories"][0]["repo_url"].endswith(
        "/repo.git"
    )
    assert calls[0] == (
        "https://api.github.com/orgs/Acme/repos?per_page=100&page=1",
        "github",
    )


def test_discover_github_user_repositories_after_org_404(monkeypatch):
    """GitHub user namespace URLs fall back from orgs to users."""

    calls = []

    def fake_api(url, config, auth_style, timeout=30):
        del config, timeout
        calls.append((url, auth_style))
        if "/orgs/" in url:
            return None, 'HTTP 404: {"message":"Not Found"}'
        return [
            {
                "name": "dotfiles",
                "full_name": "CarltonXu/dotfiles",
                "clone_url": "https://github.com/CarltonXu/dotfiles.git",
                "default_branch": "main",
            }
        ], ""

    monkeypatch.setattr("lensnode.datasource_sync._git_api_json", fake_api)
    monkeypatch.setattr(
        "lensnode.datasource_sync._git_repo_branches",
        lambda repo_url, config: ["main"],
    )

    result = _discover_git_organization(
        {
            "repo_url": "https://github.com/CarltonXu/",
            "provider": "github",
            "auth_scheme": "token",
            "access_token": "ghp_example",
        }
    )

    assert result["status"] == "success"
    assert result["details"]["owner_type"] == "user"
    assert result["details"]["repositories"][0]["path"] == (
        "CarltonXu/dotfiles"
    )
    assert calls[0][0] == (
        "https://api.github.com/orgs/CarltonXu/repos?per_page=100&page=1"
    )
    assert calls[1][0] == (
        "https://api.github.com/users/CarltonXu/repos?per_page=100&page=1"
    )


def test_discover_github_repository_url(monkeypatch):
    """GitHub owner/repo URLs use the repository API."""

    calls = []

    def fake_api(url, config, auth_style, timeout=30):
        del config, timeout
        calls.append((url, auth_style))
        return {
            "name": "repo",
            "full_name": "Acme/repo",
            "clone_url": "https://github.com/Acme/repo.git",
            "default_branch": "main",
        }, ""

    monkeypatch.setattr("lensnode.datasource_sync._git_api_json", fake_api)
    monkeypatch.setattr(
        "lensnode.datasource_sync._git_repo_branches",
        lambda repo_url, config: ["main"],
    )

    result = _discover_git_organization(
        {
            "repo_url": "https://github.com/Acme/repo",
            "provider": "github",
            "auth_scheme": "token",
            "access_token": "ghp_example",
        }
    )

    assert result["status"] == "success"
    assert result["details"]["owner_type"] == "repo"
    assert result["details"]["repositories"][0]["name"] == "repo"
    assert calls == [
        ("https://api.github.com/repos/Acme/repo", "github"),
    ]


def test_sync_git_uses_shallow_clone(tmp_path, monkeypatch):
    """Git clone uses depth=1 for datasource cache sync."""

    calls = []

    def run_git(args, cwd=None, timeout=600, detail_prefix=""):
        del timeout
        calls.append((args, cwd, detail_prefix))
        target = tmp_path / "repo"
        target.mkdir(exist_ok=True)
        return None

    monkeypatch.setattr("lensnode.datasource_sync._run_git", run_git)
    monkeypatch.setattr(
        "lensnode.datasource_sync._count_files",
        lambda path: 1,
    )

    result = _sync_git(
        {
            "config": {
                "repo_url": "https://github.com/example/repo.git",
                "branch": "main",
            },
            "target_path": str(tmp_path / "repo"),
        },
        str(tmp_path),
        None,
    )

    assert result["synced"] == 1
    assert calls[0][0][:3] == ["clone", "--depth", "1"]
    assert calls[0][2] == "LENS_SOURCE_GIT_CLONE_FAILED"


def test_sync_git_update_uses_shallow_fetch(tmp_path, monkeypatch):
    """Existing Git datasource updates use shallow fetch and hard reset."""

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    calls = []

    def run_git(args, cwd=None, timeout=600, detail_prefix=""):
        del timeout
        calls.append((args, cwd, detail_prefix))
        return None

    monkeypatch.setattr("lensnode.datasource_sync._run_git", run_git)
    monkeypatch.setattr(
        "lensnode.datasource_sync._git_output",
        lambda args, cwd=None: (
            "https://github.com/example/repo.git"
            if args[:3] == ["remote", "get-url", "origin"]
            else "commit1"
        ),
    )
    monkeypatch.setattr(
        "lensnode.datasource_sync._count_files",
        lambda path: 1,
    )

    _sync_git(
        {
            "config": {
                "repo_url": "https://github.com/example/repo.git",
                "branch": "main",
            },
            "target_path": str(repo),
        },
        str(tmp_path),
        None,
    )

    assert calls[0][0] == [
        "fetch",
        "--depth",
        "1",
        "origin",
        "main",
        "--prune",
    ]
    assert calls[1][0] == ["checkout", "main"]
    assert calls[2][0] == ["reset", "--hard", "origin/main"]


def test_sync_git_reports_manifest_delta(tmp_path, monkeypatch):
    """Git sync reports changed, skipped, and deleted paths from manifest."""

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    unchanged = repo / "unchanged.md"
    changed = repo / "changed.md"
    unchanged.write_text("same", encoding="utf-8")
    changed.write_text("new", encoding="utf-8")
    (repo / "manifest.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "source_id": (
                            "git:https://github.com/example/repo.git:"
                            "main:unchanged.md"
                        ),
                        "source_type": "git",
                        "local_path": "unchanged.md",
                        "extension": "md",
                        "metadata": {
                            "size": str(unchanged.stat().st_size),
                            "sha256": source_sha256(unchanged),
                        },
                    },
                    {
                        "source_id": (
                            "git:https://github.com/example/repo.git:"
                            "main:deleted.md"
                        ),
                        "source_type": "git",
                        "local_path": "deleted.md",
                        "extension": "md",
                        "metadata": {"size": "7", "sha256": "deleted"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    def git_output(args, cwd=None):
        del cwd
        if args[:3] == ["remote", "get-url", "origin"]:
            return "https://github.com/example/repo.git"
        if args == ["rev-parse", "HEAD"]:
            return "commit1"
        return ""

    monkeypatch.setattr("lensnode.datasource_sync._git_output", git_output)
    monkeypatch.setattr(
        "lensnode.datasource_sync._run_git",
        lambda *args, **kwargs: None,
    )

    result = _sync_git(
        {
            "config": {
                "repo_url": "https://github.com/example/repo.git",
                "branch": "main",
            },
            "target_path": str(repo),
        },
        str(tmp_path),
        None,
    )

    assert result["changed"] == 1
    assert result["skipped"] == 1
    assert result["deleted"] == 1
    assert result["_changed_paths"] == ["changed.md"]
    assert result["_deleted_paths"] == ["deleted.md"]


def test_sync_git_submodules_runs_when_declared(tmp_path, monkeypatch):
    """Repositories declaring submodules synchronize them recursively."""

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitmodules").write_text("", encoding="utf-8")
    calls = []

    def run_git(args, cwd=None, timeout=600, detail_prefix=""):
        del timeout
        calls.append((args, cwd, detail_prefix))
        return None

    monkeypatch.setattr("lensnode.datasource_sync._run_git", run_git)

    _sync_git_submodules(repo)

    assert calls[0][0] == ["submodule", "sync", "--recursive"]
    assert calls[1][0] == [
        "submodule",
        "update",
        "--init",
        "--recursive",
        "--depth",
        "1",
    ]
    assert calls[1][2] == "LENS_SOURCE_GIT_SUBMODULE_UPDATE_FAILED"


def test_sync_git_submodules_uses_plugin_url_rewrites(tmp_path, monkeypatch):
    """The generic executor applies a Plugin-provided URL rewrite plan."""

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitmodules").write_text("", encoding="utf-8")
    calls = []

    def run_git(args, cwd=None, timeout=600, detail_prefix=""):
        del timeout
        calls.append((args, cwd, detail_prefix))
        return None

    monkeypatch.setattr("lensnode.datasource_sync._run_git", run_git)

    _sync_git_submodules(
        repo,
        config={
            "submodule_url_resolver": lambda _target, _config: [
                {"from": "git@example:", "to": "https://example/"},
            ],
        },
    )

    assert calls[0][0][:2] == [
        "-c",
        'url."https://example/".insteadOf=git@example:',
    ]


def test_feishu_folder_token_from_drive_url():
    """Feishu folder URLs can be normalized to folder tokens."""

    token = _feishu_folder_token(
        {"folder_url": "https://example.feishu.cn/drive/folder/fldabc123"}
    )

    assert token == "fldabc123"


def test_sync_feishu_folder_preserves_tree(tmp_path, monkeypatch):
    """Drive folder sync writes nested documents to local directories."""

    def list_children(folder_token, headers):
        del headers
        if folder_token == "root":
            return [
                {
                    "token": "child",
                    "name": "Child Folder",
                    "type": "folder",
                },
                {
                    "token": "doc1",
                    "name": "Root Doc",
                    "type": "docx",
                },
            ]
        if folder_token == "child":
            return [
                {
                    "token": "doc2",
                    "name": "Nested Doc",
                    "type": "docx",
                }
            ]
        return []

    def export_document(doc_id, item_type, headers):
        del item_type
        del headers
        return {
            "file_name": doc_id,
            "file_extension": "docx",
            "type": "docx",
            "content": f"content {doc_id}".encode("utf-8"),
        }

    monkeypatch.setattr(
        "lensnode.datasource_sync._list_feishu_folder_children",
        list_children,
    )
    monkeypatch.setattr(
        "lensnode.datasource_sync._export_feishu_document",
        export_document,
    )

    result = _sync_feishu_folder(
        {
            "folder_token": "root",
            "recursive": True,
            "max_depth": 5,
        },
        tmp_path,
        {},
        None,
    )

    assert result["synced"] == 2
    assert (tmp_path / "doc1.docx").exists()
    assert (tmp_path / "Child Folder" / "doc2.docx").exists()


def test_sync_feishu_folder_uses_configured_workers(tmp_path, monkeypatch):
    """Drive folder sync creates a worker pool with configured size."""

    max_workers = []

    def list_children(folder_token, headers):
        del folder_token, headers
        return []

    def thread_pool_executor(*args, **kwargs):
        max_workers.append(kwargs.get("max_workers"))
        return RealThreadPoolExecutor(*args, **kwargs)

    monkeypatch.setattr(
        "lensnode.datasource_sync._list_feishu_folder_children",
        list_children,
    )
    monkeypatch.setattr(
        "lensnode.datasource_sync.ThreadPoolExecutor",
        thread_pool_executor,
    )

    _sync_feishu_folder(
        {
            "folder_token": "root",
            "recursive": True,
            "max_depth": 5,
        },
        tmp_path,
        {},
        None,
        max_workers=4,
    )

    assert max_workers == [4]


def test_sync_feishu_folder_skips_unchanged_item(tmp_path, monkeypatch):
    """Drive folder sync skips unchanged items from previous manifest."""

    exports = []

    def list_children(folder_token, headers):
        del folder_token, headers
        return [
            {
                "token": "doc1",
                "name": "Root Doc",
                "type": "docx",
                "modified_time": "100",
            }
        ]

    def export_document(doc_id, item_type, headers):
        del item_type, headers
        exports.append(doc_id)
        return {
            "file_name": "Root Doc",
            "file_extension": "docx",
            "type": "docx",
            "content": b"content",
        }

    monkeypatch.setattr(
        "lensnode.datasource_sync._list_feishu_folder_children",
        list_children,
    )
    monkeypatch.setattr(
        "lensnode.datasource_sync._export_feishu_document",
        export_document,
    )

    first = _sync_feishu_folder(
        {"folder_token": "root", "recursive": True, "max_depth": 5},
        tmp_path,
        {},
        None,
        max_workers=1,
    )
    second = _sync_feishu_folder(
        {"folder_token": "root", "recursive": True, "max_depth": 5},
        tmp_path,
        {},
        None,
        max_workers=1,
    )
    manifest = json.loads((tmp_path / "manifest.json").read_text())

    assert exports == ["doc1"]
    assert first["synced"] == 1
    assert second["synced"] == 0
    assert second["skipped"] == 1
    assert manifest["items"][0]["status"] == "skipped"


def test_sync_feishu_folder_skips_unchanged_nested_item(tmp_path, monkeypatch):
    """Nested Feishu items use root-relative manifest paths when skipped."""

    exports = []

    def list_children(folder_token, headers):
        del headers
        if folder_token == "root":
            return [
                {
                    "token": "child",
                    "name": "Child Folder",
                    "type": "folder",
                }
            ]
        if folder_token == "child":
            return [
                {
                    "token": "doc1",
                    "name": "Nested Doc",
                    "type": "docx",
                    "modified_time": "100",
                }
            ]
        return []

    def export_document(doc_id, item_type, headers):
        del item_type, headers
        exports.append(doc_id)
        return {
            "file_name": "Nested Doc",
            "file_extension": "docx",
            "type": "docx",
            "content": b"content",
        }

    monkeypatch.setattr(
        "lensnode.datasource_sync._list_feishu_folder_children",
        list_children,
    )
    monkeypatch.setattr(
        "lensnode.datasource_sync._export_feishu_document",
        export_document,
    )

    first = _sync_feishu_folder(
        {"folder_token": "root", "recursive": True, "max_depth": 5},
        tmp_path,
        {},
        None,
        max_workers=1,
    )
    second = _sync_feishu_folder(
        {"folder_token": "root", "recursive": True, "max_depth": 5},
        tmp_path,
        {},
        None,
        max_workers=1,
    )

    assert exports == ["doc1"]
    assert first["synced"] == 1
    assert second["synced"] == 0
    assert second["skipped"] == 1


def test_sync_feishu_folder_skips_raw_file_without_metadata(
    tmp_path,
    monkeypatch,
):
    """Raw Feishu files without mtime/size still use stable identity."""

    downloads = []

    def list_children(folder_token, headers):
        del folder_token, headers
        return [
            {
                "token": "file1",
                "name": "Report.pdf",
                "type": "pdf",
            }
        ]

    def download_file(file_token, headers):
        del headers
        downloads.append(file_token)
        return b"pdf content"

    monkeypatch.setattr(
        "lensnode.datasource_sync._list_feishu_folder_children",
        list_children,
    )
    monkeypatch.setattr(
        "lensnode.datasource_sync._download_feishu_file",
        download_file,
    )

    first = _sync_feishu_folder(
        {"folder_token": "root", "recursive": True, "max_depth": 5},
        tmp_path,
        {},
        None,
        max_workers=1,
    )
    second = _sync_feishu_folder(
        {"folder_token": "root", "recursive": True, "max_depth": 5},
        tmp_path,
        {},
        None,
        max_workers=1,
    )

    assert downloads == ["file1"]
    assert first["synced"] == 1
    assert second["synced"] == 0
    assert second["skipped"] == 1


def test_feishu_unchanged_uses_remote_type_from_unified_manifest(tmp_path):
    """Feishu comparison survives the unified manifest type field."""

    source = tmp_path / "Root Doc.docx"
    source.write_bytes(b"content")
    previous = _manifest_item_to_sync_item(
        {
            "kind": "document",
            "token": "doc1",
            "source_id": "feishu:token:doc1",
            "source_path": "Root Doc",
            "name": "Root Doc",
            "type": "docx",
            "file": "Root Doc.docx",
            "local_path": "Root Doc.docx",
            "file_extension": "docx",
            "metadata": {"modified_time": "100"},
            "remote": {"token": "doc1", "type": "docx"},
        },
        tmp_path,
    ).to_manifest()

    assert previous["type"] == "document"
    assert _feishu_item_unchanged(
        {
            "token": "doc1",
            "name": "Root Doc",
            "type": "docx",
            "modified_time": "100",
        },
        previous,
        tmp_path,
        tmp_path,
    )


def test_feishu_unchanged_matches_raw_file_extension_from_name(tmp_path):
    """Raw Feishu files compare manifest extension with filename suffix."""

    source = (
        tmp_path
        / "个人内容"
        / "2026年出差"
        / "4月份-4.13-15-武汉"
        / "26379166812001512498"
        / "26379166812001512498.pdf"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(b"pdf content")
    local_path = source.relative_to(tmp_path).as_posix()
    previous = {
        "source_id": "feishu:token:TZGzbnwqPobSdhxv4aIcjnnsnvd",
        "source_type": "feishu",
        "source_path": "26379166812001512498.pdf",
        "local_path": local_path,
        "file": local_path,
        "name": "26379166812001512498.pdf",
        "kind": "file",
        "type": "file",
        "extension": "pdf",
        "file_extension": "pdf",
        "status": "synced",
        "metadata": {"modified_time": "1782438856"},
        "remote": {
            "token": "TZGzbnwqPobSdhxv4aIcjnnsnvd",
            "type": "file",
        },
        "token": "TZGzbnwqPobSdhxv4aIcjnnsnvd",
    }

    assert _feishu_item_unchanged(
        {
            "token": "TZGzbnwqPobSdhxv4aIcjnnsnvd",
            "name": "26379166812001512498.pdf",
            "type": "file",
            "modified_time": "1782438856",
        },
        previous,
        source.parent,
        tmp_path,
    )


def test_feishu_target_path_reuses_existing_stable_hash_file(tmp_path):
    """Repeated raw file sync reuses an existing token-suffixed file."""

    filename = "Report.pdf"
    hashed = tmp_path / f"Report__{stable_suffix('file1')}.pdf"
    hashed.write_bytes(b"old")
    path = _feishu_target_file_path(
        tmp_path,
        tmp_path,
        filename,
        "file1",
        None,
    )

    assert path == hashed


def test_sync_feishu_folder_overwrites_changed_previous_file(
    tmp_path,
    monkeypatch,
):
    """Changed Feishu items keep the previous local path."""

    modified_times = ["100", "200"]

    def list_children(folder_token, headers):
        del folder_token, headers
        return [
            {
                "token": "doc1",
                "name": "Root Doc",
                "type": "docx",
                "modified_time": modified_times[0],
            }
        ]

    def export_document(doc_id, item_type, headers):
        del item_type, headers
        return {
            "file_name": "Root Doc",
            "file_extension": "docx",
            "type": "docx",
            "content": f"content {modified_times[0]}".encode("utf-8"),
        }

    monkeypatch.setattr(
        "lensnode.datasource_sync._list_feishu_folder_children",
        list_children,
    )
    monkeypatch.setattr(
        "lensnode.datasource_sync._export_feishu_document",
        export_document,
    )

    _sync_feishu_folder(
        {"folder_token": "root", "recursive": True, "max_depth": 5},
        tmp_path,
        {},
        None,
        max_workers=1,
    )
    modified_times[0] = "200"
    second = _sync_feishu_folder(
        {"folder_token": "root", "recursive": True, "max_depth": 5},
        tmp_path,
        {},
        None,
        max_workers=1,
    )

    files = list(tmp_path.glob("Root Doc*.docx"))
    assert second["changed"] == 1
    assert len(files) == 1
    assert files[0].read_bytes() == b"content 200"


def test_feishu_export_filename_uses_original_extension():
    exported = {
        "file_name": "Example Doc",
        "file_extension": "docx",
    }

    assert _export_filename(exported, "fallback", "docx") == "Example Doc.docx"
    assert _feishu_export_extension("bitable") == "xlsx"
    assert _is_feishu_exportable_type("slides")
    assert _is_feishu_exportable_type("slide")
    assert _feishu_export_type("slides") == "slides"
    assert _feishu_export_type("slide") == "slides"
    assert _feishu_export_extension("slides") == "pptx"
    assert _feishu_export_extension("slide") == "pptx"


def test_poll_feishu_export_task_waits_while_processing(monkeypatch):
    """Feishu job_status=2 means processing, not failed."""

    responses = [
        {"data": {"result": {"job_status": 2}}},
        {
            "data": {
                "result": {
                    "job_status": 0,
                    "file_token": "exported",
                }
            }
        },
    ]

    def http_json(*args, **kwargs):
        del args, kwargs
        return responses.pop(0)

    monkeypatch.setattr("lensnode.datasource_sync._http_json", http_json)
    monkeypatch.setattr("time.sleep", lambda *args, **kwargs: None)

    result = _poll_feishu_export_task("ticket", "doc", "docx", {})

    assert result["job_status"] == 0
    assert result["file_token"] == "exported"
    assert responses == []


def test_poll_feishu_export_task_accepts_string_processing_status(monkeypatch):
    """Feishu job_status may be returned as a string by API clients."""

    responses = [
        {"data": {"result": {"job_status": "2"}}},
        {
            "data": {
                "result": {
                    "job_status": "0",
                    "file_token": "exported",
                }
            }
        },
    ]

    def http_json(*args, **kwargs):
        del args, kwargs
        return responses.pop(0)

    monkeypatch.setattr("lensnode.datasource_sync._http_json", http_json)
    monkeypatch.setattr("time.sleep", lambda *args, **kwargs: None)

    result = _poll_feishu_export_task("ticket", "doc", "docx", {})

    assert result["job_status"] == "0"
    assert result["file_token"] == "exported"
    assert responses == []


def test_feishu_business_error_is_preserved():
    with pytest.raises(DataSourceSyncError) as exc:
        _raise_feishu_business_error(
            {"code": 99991663, "msg": "permission denied"}
        )

    assert "99991663" in str(exc.value)
    assert "permission denied" in str(exc.value)


def test_export_feishu_document_preserves_failed_result(monkeypatch):
    """Export failures keep Feishu result details for diagnosis."""

    monkeypatch.setattr(
        "lensnode.datasource_sync._create_feishu_export_task",
        lambda *args, **kwargs: "ticket1",
    )
    monkeypatch.setattr(
        "lensnode.datasource_sync._poll_feishu_export_task",
        lambda *args, **kwargs: {
            "job_status": 2,
            "job_error_msg": "permission denied",
            "file_extension": "docx",
        },
    )

    with pytest.raises(DataSourceSyncError) as exc:
        _export_feishu_document("doc1", "docx", {})

    message = str(exc.value)
    assert "LENS_SOURCE_EXPORT_FAILED" in message
    assert "doc1" in message
    assert "ticket1" in message
    assert "job_status=2" in message
    assert "processing" in message
    assert "permission denied" in message


def test_export_feishu_document_shows_official_status_reason(monkeypatch):
    """Export failures include Feishu job status and reason."""

    monkeypatch.setattr(
        "lensnode.datasource_sync._create_feishu_export_task",
        lambda *args, **kwargs: "ticket1",
    )
    monkeypatch.setattr(
        "lensnode.datasource_sync._poll_feishu_export_task",
        lambda *args, **kwargs: {
            "job_status": 107,
            "job_error_msg": "file exceeds limit",
        },
    )

    with pytest.raises(DataSourceSyncError) as exc:
        _export_feishu_document("doc1", "docx", {})

    message = str(exc.value)
    assert "job_status=107" in message
    assert "document too large" in message
    assert "file exceeds limit" in message


def test_sync_feishu_folder_fails_when_every_item_fails(tmp_path, monkeypatch):
    """A folder sync with only failed items should not be marked successful."""

    monkeypatch.setattr(
        "lensnode.datasource_sync._list_feishu_folder_children",
        lambda *args, **kwargs: [
            {
                "token": "doc1",
                "name": "Broken Doc",
                "type": "docx",
            }
        ],
    )

    def export_document(*args, **kwargs):
        del args, kwargs
        raise DataSourceSyncError("export failed")

    monkeypatch.setattr(
        "lensnode.datasource_sync._export_feishu_document",
        export_document,
    )

    with pytest.raises(DataSourceSyncError) as exc:
        _sync_feishu_folder(
            {
                "folder_token": "root",
                "recursive": True,
                "max_depth": 5,
            },
            tmp_path,
            {},
            None,
        )

    assert "all Feishu Drive items failed" in str(exc.value)
    assert (tmp_path / "manifest.json").exists()


def test_http_json_preserves_network_error(monkeypatch):
    def raise_timeout(*args, **kwargs):
        del args, kwargs
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", raise_timeout)

    with pytest.raises(DataSourceSyncError) as exc:
        _http_json("https://example.invalid")

    assert "TimeoutError" in str(exc.value)
