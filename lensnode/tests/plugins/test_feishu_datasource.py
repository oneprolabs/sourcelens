import json
from pathlib import Path
from threading import Event, Lock

import httpx
import pytest

from lensnode.datasource_sync import DataSourceSyncError
from lensnode.datasource_sync import _list_feishu_folder_children
from lensnode.datasource_sync import _poll_feishu_export_task
from lensnode.datasource_sync import _sync_feishu_resources
from lensnode.plugin_package_loader import load_runtime_contract
from lensnode.plugin_runtime import PluginRuntimeError

FEISHU_RUNTIME = load_runtime_contract("feishu", "1.0.0")


def test_document_tool_exchanges_app_credentials_for_tenant_token():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "tenant_access_token": "tenant-token",
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"content": "document body"},
            },
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = FEISHU_RUNTIME.execute_tool(
            "feishu_get_document",
            client,
            {"token": "doc_one"},
            "app-secret",
            "https://open.feishu.cn",
            {"app_id": "cli_example123"},
        )

    assert result == {"token": "doc_one", "content": "document body"}
    assert requests[0].url.path.endswith("tenant_access_token/internal")
    assert requests[1].url.path.endswith(
        "/open-apis/docx/v1/documents/doc_one/raw_content"
    )


def test_runtime_builds_mixed_resource_sync_command():
    snapshot = {
        "plugin_key": "feishu",
        "datasource_uuid": "datasource-1",
        "resolved_config": {
            "endpoint": "https://open.feishu.cn",
            "connection_config": {"app_id": "cli_example123"},
            "datasource_config": {
                "resource_urls": [
                    "https://tenant.feishu.cn/drive/folder/fld_one",
                    "https://tenant.feishu.cn/docx/doc_one",
                ],
                "resources": [
                    {"kind": "folder", "token": "fld_one"},
                    {"kind": "docx", "token": "doc_one"},
                ],
                "recursive": True,
                "max_depth": 10,
                "incremental": True,
                "delete_missing": False,
            },
            "target_path": "/workspace/feishu",
            "sync_policy": {"interval_seconds": 3600},
        },
    }
    material = {
        "plugin_key": "feishu",
        "endpoint": "https://open.feishu.cn",
        "value": "app-secret",
    }

    command = FEISHU_RUNTIME.build_datasource_command(
        snapshot,
        material,
        "manual",
    )

    assert command["source_type"] == "feishu"
    assert command["config"]["sync_mode"] == "resource_list"
    assert command["config"]["resources"] == [
        {"kind": "folder", "token": "fld_one"},
        {"kind": "docx", "token": "doc_one"},
    ]
    assert command["config"]["app_id"] == "cli_example123"
    assert command["config"]["app_secret"] == "app-secret"


def test_multi_resource_sync_deduplicates_explicit_document_in_folder(
    tmp_path,
    monkeypatch,
):
    exported_tokens = []

    def list_children(folder_token, _headers):
        assert folder_token == "fld_one"
        return [
            {
                "token": "doc_one",
                "name": "Roadmap",
                "type": "docx",
                "modified_time": "1",
            }
        ]

    def export_document(token, item_type, _headers):
        exported_tokens.append((token, item_type))
        return {
            "content": b"content",
            "file_name": "Roadmap",
            "file_extension": "docx",
            "type": "docx",
        }

    monkeypatch.setattr(
        "lensnode.datasource_sync._list_feishu_folder_children",
        list_children,
    )
    monkeypatch.setattr(
        "lensnode.datasource_sync._export_feishu_document",
        export_document,
    )

    result = _sync_feishu_resources(
        {
            "resources": [
                {"kind": "docx", "token": "doc_one"},
                {"kind": "folder", "token": "fld_one"},
            ],
            "recursive": True,
            "max_depth": 10,
            "incremental": False,
            "delete_missing": False,
        },
        Path(tmp_path),
        {"Authorization": "Bearer tenant-token"},
        None,
        max_workers=2,
    )

    assert exported_tokens == [("doc_one", "docx")]
    assert result["files"] == 1
    assert result["scanned"] == 1
    assert (tmp_path / "folders" / "fld_one" / "Roadmap.docx").is_file()


def test_resource_roots_are_scanned_with_bounded_parallelism(
    tmp_path,
    monkeypatch,
):
    started = []
    lock = Lock()
    both_started = Event()

    def list_children(folder_token, _headers):
        with lock:
            started.append(folder_token)
            if len(started) == 2:
                both_started.set()
        assert both_started.wait(1)
        return []

    monkeypatch.setattr(
        "lensnode.datasource_sync._list_feishu_folder_children",
        list_children,
    )

    result = _sync_feishu_resources(
        {
            "resources": [
                {"kind": "folder", "token": "fld_one"},
                {"kind": "folder", "token": "fld_two"},
            ],
            "recursive": True,
            "max_depth": 10,
            "incremental": False,
            "delete_missing": False,
        },
        Path(tmp_path),
        {"Authorization": "Bearer tenant-token"},
        None,
        max_workers=2,
    )

    assert set(started) == {"fld_one", "fld_two"}
    assert result["folders"] == 2


def test_folder_pagination_rejects_a_repeated_page_token(monkeypatch):
    monkeypatch.setattr(
        "lensnode.datasource_sync._http_json",
        lambda *_args, **_kwargs: {
            "data": {
                "files": [],
                "has_more": True,
                "next_page_token": "same-page",
            }
        },
    )

    with pytest.raises(
        DataSourceSyncError,
        match="FEISHU_FOLDER_RESPONSE_INVALID",
    ):
        _list_feishu_folder_children("fld_one", {})


def test_wiki_resource_resolves_to_exportable_document(tmp_path, monkeypatch):
    requested_urls = []
    exported_tokens = []

    def http_json(url, **_kwargs):
        requested_urls.append(url)
        return {
            "data": {
                "node": {
                    "obj_token": "doc_wiki",
                    "obj_type": "docx",
                    "title": "Wiki page",
                }
            }
        }

    def export_document(token, item_type, _headers):
        exported_tokens.append((token, item_type))
        return {
            "content": b"wiki",
            "file_name": "Wiki page",
            "file_extension": "docx",
            "type": "docx",
        }

    monkeypatch.setattr("lensnode.datasource_sync._http_json", http_json)
    monkeypatch.setattr(
        "lensnode.datasource_sync._export_feishu_document",
        export_document,
    )

    result = _sync_feishu_resources(
        {
            "resources": [{"kind": "wiki", "token": "wik_one"}],
            "recursive": True,
            "max_depth": 10,
            "incremental": False,
            "delete_missing": False,
        },
        Path(tmp_path),
        {"Authorization": "Bearer tenant-token"},
        None,
        max_workers=2,
    )

    assert requested_urls[0].endswith("?token=wik_one")
    assert exported_tokens == [("doc_wiki", "docx")]
    assert result["files"] == 1


def test_explicit_document_without_metadata_is_not_skipped(
    tmp_path,
    monkeypatch,
):
    exported_tokens = []

    def export_document(token, item_type, _headers):
        exported_tokens.append((token, item_type))
        return {
            "content": b"content",
            "file_name": "Roadmap",
            "file_extension": "docx",
            "type": "docx",
        }

    monkeypatch.setattr(
        "lensnode.datasource_sync._export_feishu_document",
        export_document,
    )
    config = {
        "resources": [{"kind": "docx", "token": "doc_one"}],
        "recursive": True,
        "max_depth": 10,
        "incremental": True,
        "delete_missing": False,
    }

    for _ in range(2):
        _sync_feishu_resources(
            config,
            Path(tmp_path),
            {"Authorization": "Bearer tenant-token"},
            None,
            max_workers=2,
        )

    assert exported_tokens == [
        ("doc_one", "docx"),
        ("doc_one", "docx"),
    ]


def test_folder_scan_failure_never_deletes_previous_files(
    tmp_path,
    monkeypatch,
):
    old_path = tmp_path / "folders" / "old" / "Old.docx"
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(b"old")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "kind": "document",
                        "token": "doc_old",
                        "name": "Old",
                        "file": "folders/old/Old.docx",
                        "local_path": "folders/old/Old.docx",
                        "type": "docx",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def list_children(_folder_token, _headers):
        raise DataSourceSyncError("FEISHU_FOLDER_UNAVAILABLE")

    def export_document(_token, _item_type, _headers):
        return {
            "content": b"new",
            "file_name": "New",
            "file_extension": "docx",
            "type": "docx",
        }

    monkeypatch.setattr(
        "lensnode.datasource_sync._list_feishu_folder_children",
        list_children,
    )
    monkeypatch.setattr(
        "lensnode.datasource_sync._export_feishu_document",
        export_document,
    )

    _sync_feishu_resources(
        {
            "resources": [
                {"kind": "folder", "token": "fld_unavailable"},
                {"kind": "docx", "token": "doc_new"},
            ],
            "recursive": True,
            "max_depth": 10,
            "incremental": False,
            "delete_missing": True,
        },
        Path(tmp_path),
        {"Authorization": "Bearer tenant-token"},
        None,
        max_workers=2,
    )

    manifest = json.loads(
        (tmp_path / "manifest.json").read_text(encoding="utf-8")
    )
    old_item = next(
        item for item in manifest["items"] if item.get("token") == "doc_old"
    )
    assert old_path.is_file()
    assert manifest["scan_complete"] is False
    assert old_item["status"] == "skipped"


def test_delete_missing_false_keeps_local_file_and_sidecar(
    tmp_path,
    monkeypatch,
):
    old_path = tmp_path / "folders" / "old" / "Old.docx"
    sidecar = Path(f"{old_path}.sourcelens")
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(b"old")
    sidecar.mkdir()
    (sidecar / "content.md").write_text("old", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "kind": "document",
                        "token": "doc_old",
                        "name": "Old",
                        "file": "folders/old/Old.docx",
                        "local_path": "folders/old/Old.docx",
                        "type": "docx",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lensnode.datasource_sync._list_feishu_folder_children",
        lambda _folder_token, _headers: [],
    )

    result = _sync_feishu_resources(
        {
            "resources": [{"kind": "folder", "token": "fld_one"}],
            "recursive": True,
            "max_depth": 10,
            "incremental": True,
            "delete_missing": False,
        },
        Path(tmp_path),
        {"Authorization": "Bearer tenant-token"},
        None,
        max_workers=2,
    )

    assert old_path.is_file()
    assert sidecar.is_dir()
    assert result["_deleted_paths"] == []


def test_delete_missing_requires_two_complete_scans(tmp_path, monkeypatch):
    old_path = tmp_path / "folders" / "old" / "Old.docx"
    sidecar = Path(f"{old_path}.sourcelens")
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(b"old")
    sidecar.mkdir()
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "kind": "document",
                        "token": "doc_old",
                        "name": "Old",
                        "file": "folders/old/Old.docx",
                        "local_path": "folders/old/Old.docx",
                        "type": "docx",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "lensnode.datasource_sync._list_feishu_folder_children",
        lambda _folder_token, _headers: [],
    )
    config = {
        "resources": [{"kind": "folder", "token": "fld_one"}],
        "recursive": True,
        "max_depth": 10,
        "incremental": True,
        "delete_missing": True,
    }

    first = _sync_feishu_resources(
        config,
        Path(tmp_path),
        {"Authorization": "Bearer tenant-token"},
        None,
        max_workers=2,
    )
    first_manifest = json.loads(
        (tmp_path / "manifest.json").read_text(encoding="utf-8")
    )
    first_item = first_manifest["items"][0]

    assert first["_deleted_paths"] == []
    assert old_path.is_file()
    assert sidecar.is_dir()
    assert first_item["status"] == "missing"
    assert first_item["missing_scans"] == 1

    second = _sync_feishu_resources(
        config,
        Path(tmp_path),
        {"Authorization": "Bearer tenant-token"},
        None,
        max_workers=2,
    )
    second_manifest = json.loads(
        (tmp_path / "manifest.json").read_text(encoding="utf-8")
    )
    second_item = second_manifest["items"][0]

    assert second["_deleted_paths"] == ["folders/old/Old.docx"]
    assert not old_path.exists()
    assert second_item["status"] == "deleted"


def test_resource_sync_stops_before_scanning_when_cancelled(tmp_path):
    cancel_event = Event()
    cancel_event.set()

    with pytest.raises(
        DataSourceSyncError,
        match="LENS_SOURCE_SYNC_CANCELLED",
    ):
        _sync_feishu_resources(
            {
                "resources": [{"kind": "folder", "token": "fld_one"}],
                "recursive": True,
                "max_depth": 10,
                "incremental": False,
                "delete_missing": False,
            },
            Path(tmp_path),
            {"Authorization": "Bearer tenant-token"},
            None,
            max_workers=2,
            cancel_event=cancel_event,
        )


def test_export_poll_stops_when_resource_sync_is_cancelled(monkeypatch):
    cancel_event = Event()
    cancel_event.set()

    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("cancelled polling must not call Feishu")

    monkeypatch.setattr(
        "lensnode.datasource_sync._http_json",
        unexpected_request,
    )

    with pytest.raises(
        DataSourceSyncError,
        match="LENS_SOURCE_SYNC_CANCELLED",
    ):
        _poll_feishu_export_task(
            "ticket",
            "doc_one",
            "docx",
            {},
            cancel_event=cancel_event,
        )


def test_runtime_rejects_mismatched_material_and_unsafe_tokens():
    snapshot = {
        "plugin_key": "feishu",
        "resolved_config": {
            "endpoint": "https://open.feishu.cn",
            "connection_config": {"app_id": "cli_example123"},
            "datasource_config": {
                "resources": [{"kind": "docx", "token": "../secret"}],
                "recursive": True,
                "max_depth": 10,
                "incremental": True,
                "delete_missing": False,
            },
        },
    }
    material = {
        "plugin_key": "other",
        "endpoint": "https://open.feishu.cn",
        "value": "app-secret",
    }

    snapshot_without_identity = {**snapshot, "plugin_key": None}
    with pytest.raises(PluginRuntimeError, match="PLUGIN_SNAPSHOT_MISMATCH"):
        FEISHU_RUNTIME.build_datasource_command(
            snapshot_without_identity,
            material,
            "manual",
        )

    with pytest.raises(PluginRuntimeError, match="PLUGIN_MATERIAL_MISMATCH"):
        FEISHU_RUNTIME.build_datasource_command(snapshot, material, "manual")

    material["plugin_key"] = "feishu"
    with pytest.raises(PluginRuntimeError, match="PLUGIN_CONFIG_INVALID"):
        FEISHU_RUNTIME.build_datasource_command(snapshot, material, "manual")
