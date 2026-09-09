import base64
import json
import os
import re
import shutil
import signal
import subprocess
import tarfile
import time
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request

from . import datasource_manifest as manifest_store
from .datasource_adapters import DataSourceAdapterRegistry
from .datasource_adapters import FunctionDataSourceAdapter
from .document_convert import empty_cost_stats
from .document_convert import is_convertible, post_process_documents
from .document_convert import merge_cost_stats
from .path_rules import is_excluded_path
from .path_rules import normalize_excluded_roots
from .path_rules import relative_path
from .path_rules import safe_filename
from .path_rules import sidecar_path
from .path_rules import source_sha256
from .path_rules import stable_suffix
from .path_rules import unique_child_path

WORKSPACE_ROOT = "/workspace"
GIT_SHALLOW_DEPTH = "1"
DETAIL_ITEMS_LIMIT = 200
DISCOVERY_PROGRESS_INTERVAL = 100
DEFAULT_CONVERSION_BATCH_SIZE = 16
DEFAULT_CONVERSION_MAX_FILES = 100000
GIT_MAX_FILES = 100000
GIT_MAX_BYTES = 1024 * 1024 * 1024
UPLOAD_MAX_EXTRACTED_BYTES = 250 * 1024 * 1024
UPLOAD_MAX_EXTRACTED_FILES = 10000
DEFAULT_DATASOURCE_SYNC_WORKERS = 4
FEISHU_EXPORT_PENDING_STATUSES = {1, 2}
FEISHU_EXPORT_SUCCESS_STATUS = 0
FEISHU_EXPORT_POLL_INTERVAL_S = 2
FEISHU_EXPORT_TIMEOUT_S = 600
FEISHU_MAX_SYNC_ITEMS = 100000
FEISHU_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{4,255}$")
FEISHU_RESOURCE_KINDS = frozenset(
    {"bitable", "docx", "folder", "sheet", "slides", "wiki"}
)
FEISHU_EXPORT_STATUS_MESSAGES = {
    0: "success",
    1: "initializing",
    2: "processing",
    3: "internal error",
    107: "document too large",
    108: "processing timeout",
    109: "content block permission denied",
    110: "permission denied",
    111: "document deleted",
    122: "export blocked while creating copy",
    123: "document not found",
    6000: "too many images",
}


class DataSourceSyncError(RuntimeError):
    """Raised when LensNode cannot synchronize a datasource."""


def utc_timestamp():
    """Return the current UTC timestamp."""

    return datetime.now(timezone.utc).isoformat()


def normalize_target_path(target_path, workspace_path=WORKSPACE_ROOT):
    """Return a safe target path inside the LensNode workspace."""

    workspace = Path(workspace_path or WORKSPACE_ROOT).resolve()
    raw = str(target_path or "").strip()
    if not raw:
        raise DataSourceSyncError("LENS_SOURCE_TARGET_PATH_REQUIRED")

    target = Path(raw)
    if not target.is_absolute():
        target = workspace / raw
    target = target.resolve()
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise DataSourceSyncError("LENS_SOURCE_TARGET_PATH_INVALID") from exc
    if target == workspace:
        raise DataSourceSyncError("LENS_SOURCE_TARGET_PATH_REQUIRED")
    return target


def inspect_datasource_path(command, workspace_path=WORKSPACE_ROOT):
    """Inspect a datasource target path without mutating local files."""

    target = normalize_target_path(command.get("target_path"), workspace_path)
    config = command.get("config") or {}
    source_type = command.get("source_type") or "git"
    result = {
        "path": str(target),
        "exists": target.exists(),
        "is_directory": target.is_dir(),
        "is_empty": False,
        "is_git_repo": False,
        "source_compatible": True,
        "will_create": False,
        "status": "available",
        "message_code": "available",
        "message": "Target path is available.",
    }

    if not target.exists():
        if source_type == "managed_workspace":
            result.update(
                {
                    "source_compatible": False,
                    "status": "blocked",
                    "message_code": "managed_workspace_missing",
                    "message": (
                        "Managed workspace directory does not exist."
                    ),
                }
            )
            return result
        result["will_create"] = True
        result["message_code"] = "will_create"
        result["message"] = "Directory will be created during first sync."
        return result

    if not target.is_dir():
        result.update(
            {
                "source_compatible": False,
                "status": "blocked",
                "message_code": "not_directory",
                "message": "Target path exists but is not a directory.",
            }
        )
        return result

    children = list(target.iterdir())
    result["is_empty"] = len(children) == 0
    result["is_git_repo"] = (target / ".git").is_dir()
    if source_type == "managed_workspace":
        result["message_code"] = "managed_workspace_available"
        result["message"] = "Managed workspace directory is available."
        return result
    if result["is_empty"]:
        result["message_code"] = "empty"
        result["message"] = "Directory is empty and can be used."
        return result

    if source_type == "git" and config.get("git_organization_parent"):
        result["message_code"] = "merge"
        result["message"] = "Directory exists; repositories will be stored in child directories."
        return result

    if source_type == "git":
        return _inspect_git_path(target, config, result)

    result["message_code"] = "merge"
    result["message"] = "Directory exists; files may be merged by sync."
    return result


def _inspect_git_path(target, config, result):
    """Inspect Git repository compatibility for a target path."""

    if not result["is_git_repo"]:
        result.update(
            {
                "source_compatible": False,
                "status": "blocked",
                "message_code": "not_git_repo",
                "message": "Directory is not empty and is not a Git repo.",
            }
        )
        return result

    repo_url = config.get("repo_url") or ""
    remote_url = _git_output(["remote", "get-url", "origin"], cwd=target)
    result["remote_url"] = remote_url
    if repo_url and _normalize_repo_url(repo_url) != _normalize_repo_url(remote_url):
        result.update(
            {
                "source_compatible": False,
                "status": "blocked",
                "message_code": "remote_mismatch",
                "message": "Existing Git remote does not match repo URL.",
            }
        )
        return result

    result["message_code"] = "git_update"
    result["message"] = "Existing Git repository can be updated."
    return result


def sync_datasource(command, workspace_path=WORKSPACE_ROOT, emit=None):
    """Synchronize one datasource command and return metrics."""

    source_type = command.get("source_type")
    adapter = datasource_adapter_registry().get(source_type)
    if adapter is None:
        raise DataSourceSyncError("LENS_SOURCE_TYPE_UNSUPPORTED")
    result = adapter.sync(command, workspace_path, emit)

    target = Path(result.get("target_path") or "").resolve()
    if not target:
        return result
    context = _sync_context(command, target)
    manifest_store.write_datasource_marker(target, context)
    sync_items = result.pop("_sync_items", [])
    changed_paths = result.pop("_changed_paths", [])
    deleted_paths = result.pop("_deleted_paths", [])
    sync_result = manifest_store.SyncResult(
        items=sync_items,
        changed_paths=changed_paths,
        deleted_paths=deleted_paths,
        stats=_sync_summary_from_result(result),
        changed_only=True,
    )
    sync_details = _sync_details_by_metric(
        sync_items,
        changed_paths,
        deleted_paths,
    )
    if sync_details["details"]:
        result["details"] = sync_details["details"]
        result["details_truncated"] = sync_details["details_truncated"]
    changed_details = sync_details["details"].get("changed") or []
    if changed_details:
        result["changed_items"] = changed_details
        result["changed_items_truncated"] = sync_details[
            "details_truncated"
        ].get("changed", 0)
    if sync_items:
        manifest_payload = manifest_store.build_manifest(context, sync_result)
        manifest_payload["synced_at"] = utc_timestamp()
        manifest_store.write_manifest(target, manifest_payload)

    deleted_sidecars = manifest_store.cleanup_deleted_sidecars(
        target,
        deleted_paths,
        context["excluded_datasource_roots"],
    )
    conversion_summary = post_process_documents(context, sync_result, emit)
    conversion_summary["deleted_sidecars"] = deleted_sidecars
    result["conversion_summary"] = conversion_summary
    return result


def list_datasource_files(command, workspace_path=WORKSPACE_ROOT):
    """Return one safe, paginated view of a datasource manifest."""

    target = normalize_target_path(command.get("target_path"), workspace_path)
    datasource_uuid = str(command.get("datasource_uuid") or "")
    source_type = str(command.get("source_type") or "")
    marker = manifest_store.read_manifest_marker(target)
    if (
        source_type != "managed_workspace"
        and (
            not datasource_uuid
            or marker.get("datasource_uuid") != datasource_uuid
        )
    ):
        raise DataSourceSyncError("DATASOURCE_MANIFEST_NOT_FOUND")

    query = str(command.get("query") or "").strip().lower()
    sync_status = str(command.get("sync_status") or "").strip().lower()
    conversion_status = str(
        command.get("conversion_status") or ""
    ).strip().lower()
    try:
        page = max(1, int(command.get("page") or 1))
        page_size = min(100, max(1, int(command.get("page_size") or 20)))
    except (TypeError, ValueError) as exc:
        raise DataSourceSyncError("DATASOURCE_FILE_QUERY_INVALID") from exc

    candidates = []
    if source_type == "managed_workspace":
        manifest_items = _managed_workspace_catalog_items(target)
    else:
        manifest_items = manifest_store.manifest_items(
            manifest_store.read_manifest(target)
        )
    for item in manifest_items:
        local_path = manifest_store.manifest_local_path(item)
        if not local_path:
            continue
        display_path = Path(local_path).as_posix()
        public_status = _public_sync_status(item.get("status"))
        if query and query not in display_path.lower():
            continue
        if sync_status and public_status != sync_status:
            continue
        entry = _datasource_file_entry(target, item, public_status)
        if entry is None:
            continue
        candidates.append((display_path, entry))
    candidates.sort(key=lambda item: item[0].lower())
    if conversion_status:
        candidates = [
            candidate
            for candidate in candidates
            if candidate[1]["conversion_status"].lower() == conversion_status
        ]
    start = (page - 1) * page_size
    page_items = candidates[start : start + page_size]
    return {
        "count": len(candidates),
        "page": page,
        "page_size": page_size,
        "results": [
            entry for _path, entry in page_items
        ],
    }


def _managed_workspace_catalog_items(target):
    """Return manifest-compatible items from a managed workspace directory."""

    target = Path(target)
    items = []
    for path in target.rglob("*"):
        if (
            not path.is_file()
            or _is_datasource_catalog_internal_path(target, path)
        ):
            continue
        try:
            local_path = relative_path(target, path)
        except ValueError:
            continue
        items.append(
            {
                "local_path": local_path,
                "name": path.name,
                "file_extension": path.suffix.lstrip(".").lower(),
                "status": "synced",
                "metadata": {
                    "modified_time": datetime.fromtimestamp(
                        path.stat().st_mtime,
                        timezone.utc,
                    ).isoformat(),
                },
            }
        )
    return items


def _is_datasource_catalog_internal_path(target, path):
    """Return whether a path is internal datasource bookkeeping content."""

    relative = path.resolve().relative_to(Path(target).resolve())
    if relative.name in {
        manifest_store.MANIFEST_FILE,
        manifest_store.MARKER_FILE,
    }:
        return True
    return any(part.endswith(".sourcelens") for part in relative.parts)


def _datasource_file_entry(target, item, sync_status=None):
    """Return safe catalog data for one manifest item."""

    local_path = manifest_store.manifest_local_path(item)
    if not local_path:
        return None
    path = (Path(target) / local_path).resolve()
    try:
        path.relative_to(Path(target).resolve())
    except ValueError:
        return None
    conversion = _read_datasource_conversion(path)
    metadata = item.get("metadata") or {}
    return {
        "path": Path(local_path).as_posix(),
        "name": str(item.get("name") or Path(local_path).name),
        "extension": str(
            item.get("extension")
            or item.get("file_extension")
            or Path(local_path).suffix.lstrip(".")
        ).lower(),
        "sync_status": sync_status or _public_sync_status(item.get("status")),
        "conversion_status": conversion["status"],
        "source_updated_at": str(metadata.get("modified_time") or ""),
        "converted_at": conversion["generated_at"],
        "conversion_error": conversion["error"],
    }


def _public_sync_status(value):
    """Return the catalog's stable public sync state."""

    status = str(value or "synced").lower()
    return "synced" if status in {"cataloged", "skipped"} else status


def _read_datasource_conversion(path):
    """Return the public conversion state for one source file."""

    meta_path = sidecar_path(path) / "meta.json"
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    conversion = payload.get("conversion") or {}
    return {
        "status": str(conversion.get("status") or "not_converted"),
        "generated_at": str(conversion.get("generated_at") or ""),
        "error": str(conversion.get("error") or ""),
    }


def convert_managed_workspace(
    command,
    workspace_path=WORKSPACE_ROOT,
    emit=None,
):
    """Convert files in a managed workspace without synchronizing it."""

    if command.get("source_type") != "managed_workspace":
        raise DataSourceSyncError("DATASOURCE_CONVERSION_NOT_SUPPORTED")
    target = normalize_target_path(
        command.get("target_path"),
        workspace_path,
    )
    if not target.is_dir():
        raise DataSourceSyncError("MANAGED_WORKSPACE_DIRECTORY_REQUIRED")

    context = _sync_context(command, target)
    context["managed_conversion_progress"] = True
    conversion = context["conversion"]
    _emit_managed_conversion_event(
        emit,
        phase="DISCOVERING_FILES",
        message="Discovering files in the managed workspace.",
        phase_current=0,
        phase_total=0,
        phase_unit="files",
        overall_progress_percent=0,
        summary=_empty_managed_conversion_summary(),
        total=0,
        candidates=0,
        processed=0,
    )
    total, supported_total, unsupported_total, unsupported = (
        _scan_managed_workspace_conversion(
            target,
            context["excluded_datasource_roots"],
            conversion,
            emit=emit,
        )
    )
    _emit_managed_conversion_event(
        emit,
        phase="DISCOVERING_FILES",
        message="Finished discovering managed workspace files.",
        phase_current=total,
        phase_total=total,
        phase_unit="files",
        overall_progress_percent=5,
        summary=_empty_managed_conversion_summary(),
        total=total,
        candidates=supported_total,
        processed=0,
    )
    max_files = _conversion_resource_limit(
        conversion,
        "max_files",
        DEFAULT_CONVERSION_MAX_FILES,
    )
    if total > max_files:
        raise DataSourceSyncError("RESOURCE_LIMIT_EXCEEDED")
    max_bytes = _conversion_resource_limit(conversion, "max_bytes", 0)
    if max_bytes and _managed_workspace_size(
        target,
        context["excluded_datasource_roots"],
    ) > max_bytes:
        raise DataSourceSyncError("RESOURCE_LIMIT_EXCEEDED")

    summary = None
    processed = 0
    batch = []
    batch_size = _conversion_resource_limit(
        conversion,
        "batch_size",
        DEFAULT_CONVERSION_BATCH_SIZE,
    )
    _emit_managed_conversion_event(
        emit,
        phase="PARSING_DOCUMENTS",
        message="Processing convertible workspace files.",
        phase_current=0,
        phase_total=supported_total,
        phase_unit="files",
        overall_progress_percent=10,
        summary=_managed_conversion_summary(
            _empty_managed_conversion_summary(),
            unsupported,
            supported_total,
            0,
            unsupported_total=unsupported_total,
        ),
        total=total,
        candidates=supported_total,
        processed=unsupported_total,
    )
    for item in _managed_workspace_conversion_items(
        target,
        context["excluded_datasource_roots"],
    ):
        if not is_convertible(target / item.local_path, conversion):
            continue
        batch.append(item)
        if len(batch) < batch_size:
            continue
        batch_summary = post_process_documents(
            context,
            manifest_store.SyncResult(items=batch),
            emit=_managed_conversion_batch_emitter(
                emit,
                summary,
                total=total,
                supported_total=supported_total,
                unsupported=unsupported,
                unsupported_total=unsupported_total,
                processed_before=processed,
            ),
        )
        summary = _merge_managed_conversion_summaries(summary, batch_summary)
        processed += len(batch)
        _emit_managed_conversion_progress(
            emit,
            summary,
            total,
            supported_total,
            unsupported,
            unsupported_total,
            processed,
        )
        batch = []
    if batch:
        batch_summary = post_process_documents(
            context,
            manifest_store.SyncResult(items=batch),
            emit=_managed_conversion_batch_emitter(
                emit,
                summary,
                total=total,
                supported_total=supported_total,
                unsupported=unsupported,
                unsupported_total=unsupported_total,
                processed_before=processed,
            ),
        )
        summary = _merge_managed_conversion_summaries(summary, batch_summary)
        processed += len(batch)
        _emit_managed_conversion_progress(
            emit,
            summary,
            total,
            supported_total,
            unsupported,
            unsupported_total,
            processed,
        )
    if summary is None:
        summary = _empty_managed_conversion_summary()
    summary = _managed_conversion_summary(
        summary,
        unsupported,
        supported_total,
        supported_total,
        unsupported_total=unsupported_total,
    )
    if summary["failed"]:
        summary["warnings"] = list(
            dict.fromkeys(
                [
                    *(summary.get("warnings") or []),
                    "CONVERSION_PARTIAL_FAILED",
                ]
            )
        )
    _emit_managed_conversion_event(
        emit,
        phase="FINALIZING",
        message="Finalizing managed workspace conversion.",
        phase_current=1,
        phase_total=1,
        phase_unit="steps",
        overall_progress_percent=99,
        summary=summary,
        total=total,
        candidates=supported_total,
        processed=total,
    )
    return {
        "status": "success",
        "target_path": str(target),
        "conversion_summary": summary,
        "warnings": summary.get("warnings") or [],
    }


def upload_managed_workspace(command, workspace_path=WORKSPACE_ROOT):
    """Write one upload into a managed workspace and convert its contents."""

    if command.get("source_type", "managed_workspace") != "managed_workspace":
        raise DataSourceSyncError("DATASOURCE_UPLOAD_NOT_SUPPORTED")
    target = normalize_target_path(command.get("target_path"), workspace_path)
    if not target.is_dir():
        raise DataSourceSyncError("MANAGED_WORKSPACE_DIRECTORY_REQUIRED")
    filename = safe_filename(command.get("filename"))
    if not filename:
        raise DataSourceSyncError("DATASOURCE_UPLOAD_FILENAME_INVALID")
    try:
        content = base64.b64decode(
            str(command.get("content_base64") or ""),
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise DataSourceSyncError("DATASOURCE_UPLOAD_CONTENT_INVALID") from exc
    archive_path = target / filename
    if archive_path.exists():
        raise DataSourceSyncError("DATASOURCE_UPLOAD_FILE_EXISTS")
    archive_path.write_bytes(content)
    extracted = []
    try:
        if filename.lower().endswith(".zip"):
            extracted = _extract_zip_archive(archive_path, target)
        elif filename.lower().endswith((".tar", ".tar.gz", ".tgz")):
            extracted = _extract_tar_archive(archive_path, target)
        result = convert_managed_workspace(
            {
                **command,
                "target_path": str(target),
                "source_type": "managed_workspace",
                "conversion": command.get("conversion")
                or {"document": True, "image": True},
            },
            workspace_path=workspace_path,
        )
    except Exception:
        archive_path.unlink(missing_ok=True)
        for path in reversed(extracted):
            path.unlink(missing_ok=True)
        raise
    result["uploaded"] = filename
    result["extracted_files"] = [str(path) for path in extracted]
    return result


def _archive_member_path(root, name):
    """Return a safe archive member path under root."""

    normalized = str(name or "").replace("\\", "/")
    member = Path(normalized)
    if member.is_absolute() or any(
        part in {"", ".", ".."} for part in member.parts
    ):
        raise DataSourceSyncError("DATASOURCE_UPLOAD_ARCHIVE_PATH_INVALID")
    path = (root / member).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise DataSourceSyncError(
            "DATASOURCE_UPLOAD_ARCHIVE_PATH_INVALID"
        ) from exc
    return path


def _extract_zip_archive(archive_path, root):
    """Extract a ZIP archive without permitting unsafe members."""

    extracted = []
    extracted_bytes = 0
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            if len(extracted) >= UPLOAD_MAX_EXTRACTED_FILES:
                raise DataSourceSyncError("DATASOURCE_UPLOAD_FILE_LIMIT")
            extracted_bytes += info.file_size
            if extracted_bytes > UPLOAD_MAX_EXTRACTED_BYTES:
                raise DataSourceSyncError("DATASOURCE_UPLOAD_SIZE_LIMIT")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise DataSourceSyncError("DATASOURCE_UPLOAD_LINK_INVALID")
            path = _archive_member_path(root, info.filename)
            if path.exists():
                raise DataSourceSyncError("DATASOURCE_UPLOAD_FILE_EXISTS")
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, path.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            extracted.append(path)
    return extracted


def _extract_tar_archive(archive_path, root):
    """Extract a tar archive without permitting unsafe members."""

    extracted = []
    extracted_bytes = 0
    with tarfile.open(archive_path, "r:*") as archive:
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                raise DataSourceSyncError("DATASOURCE_UPLOAD_LINK_INVALID")
            if not member.isfile():
                continue
            if len(extracted) >= UPLOAD_MAX_EXTRACTED_FILES:
                raise DataSourceSyncError("DATASOURCE_UPLOAD_FILE_LIMIT")
            extracted_bytes += member.size
            if extracted_bytes > UPLOAD_MAX_EXTRACTED_BYTES:
                raise DataSourceSyncError("DATASOURCE_UPLOAD_SIZE_LIMIT")
            path = _archive_member_path(root, member.name)
            if path.exists():
                raise DataSourceSyncError("DATASOURCE_UPLOAD_FILE_EXISTS")
            path.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise DataSourceSyncError("DATASOURCE_UPLOAD_ARCHIVE_INVALID")
            with source, path.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            extracted.append(path)
    return extracted


def _managed_workspace_conversion_items(target, excluded_roots):
    """Yield files found under a managed workspace conversion root."""

    for path in target.rglob("*"):
        if not path.is_file() or _is_generated_datasource_path(target, path):
            continue
        if is_excluded_path(path, excluded_roots):
            continue
        try:
            local_path = relative_path(target, path)
        except ValueError:
            continue
        yield manifest_store.SyncItem(
            source_id=f"managed_workspace:{local_path}",
            source_type="managed_workspace",
            source_path=local_path,
            local_path=local_path,
            name=path.name,
            kind="file",
            extension=path.suffix.lower().lstrip("."),
            status="cataloged",
            metadata={"size": str(path.stat().st_size)},
        )


def _scan_managed_workspace_conversion(
    target,
    excluded_roots,
    conversion,
    emit=None,
):
    """Scan workspace metadata without retaining the complete file list."""

    total = 0
    supported_total = 0
    unsupported_total = 0
    unsupported = []
    for item in _managed_workspace_conversion_items(target, excluded_roots):
        total += 1
        if is_convertible(target / item.local_path, conversion):
            supported_total += 1
        else:
            unsupported_total += 1
            if len(unsupported) < DETAIL_ITEMS_LIMIT:
                unsupported.append(item)
        if total % DISCOVERY_PROGRESS_INTERVAL == 0:
            _emit(
                emit,
                "conversion_discovery_progress",
                "running",
                f"Discovered {total} workspace files so far.",
                category="conversion",
                phase="DISCOVERING_FILES",
                overall_progress_percent=0,
                phase_progress={
                    "current": total,
                    "total": None,
                    "unit": "files",
                },
                progress_counts={
                    "total": None,
                    "candidates": None,
                    "processed": 0,
                    "converted": 0,
                    "failed": 0,
                    "skipped": 0,
                    "unsupported": None,
                },
                substantive_progress=True,
            )
    return total, supported_total, unsupported_total, unsupported


def _managed_workspace_size(target, excluded_roots):
    """Return workspace bytes without retaining directory entries."""

    total = 0
    for item in _managed_workspace_conversion_items(target, excluded_roots):
        total += int(item.metadata.get("size") or 0)
    return total


def _conversion_resource_limit(conversion, key, default):
    """Return a bounded conversion resource setting."""

    try:
        value = int((conversion or {}).get(key) or default)
    except (TypeError, ValueError):
        value = default
    if key == "batch_size":
        return max(1, min(value, 128))
    return max(1, value) if value else 0


def _empty_managed_conversion_summary():
    """Return the fixed-shape summary used by conversion batches."""

    return {
        "candidates": 0,
        "converted": 0,
        "success": 0,
        "skipped": 0,
        "failed": 0,
        "markdown": 0,
        "deleted_sidecars": 0,
        "chars": 0,
        "estimated_tokens": 0,
        "images_recognized": 0,
        "images_skipped": 0,
        "images_blank": 0,
        "images_duplicate": 0,
        "images_compressed": 0,
        "embedded_images_total": 0,
        "embedded_images_recognized": 0,
        "embedded_images_skipped": 0,
        "embedded_images_duplicate": 0,
        "embedded_images_blank": 0,
        "pdf_pages": 0,
        "pdf_pages_processed": 0,
        "pdf_pages_with_text": 0,
        "pdf_scanned_pages": 0,
        "pdf_images_total": 0,
        "pdf_images_recognized": 0,
        "pdf_images_skipped": 0,
        "pdf_rendered_pages": 0,
        "xlsx_files": 0,
        "sheets": 0,
        "rows": 0,
        "truncated_files": 0,
        "cost": empty_cost_stats(),
        "warnings": [],
        "items": [],
        "items_truncated": 0,
        "details": {},
        "details_truncated": {},
    }


def _merge_managed_conversion_summaries(current, incoming):
    """Merge one bounded conversion batch into the aggregate summary."""

    if current is None:
        current = _empty_managed_conversion_summary()
    for key, value in incoming.items():
        if key in {
            "cost",
            "items",
            "details",
            "details_truncated",
            "warnings",
        }:
            continue
        if isinstance(value, (int, float)):
            current[key] = int(current.get(key) or 0) + int(value or 0)
    merge_cost_stats(current["cost"], incoming.get("cost") or {})
    current["warnings"] = list(
        dict.fromkeys(
            [
                *(current.get("warnings") or []),
                *(incoming.get("warnings") or []),
            ]
        )
    )
    for item in incoming.get("items") or []:
        if len(current["items"]) < DETAIL_ITEMS_LIMIT:
            current["items"].append(item)
        else:
            current["items_truncated"] += 1
    for group, details in (incoming.get("details") or {}).items():
        bucket = current["details"].setdefault(group, [])
        available = max(DETAIL_ITEMS_LIMIT - len(bucket), 0)
        bucket.extend(details[:available])
        current["details_truncated"][group] = (
            int(current["details_truncated"].get(group) or 0)
            + int((incoming.get("details_truncated") or {}).get(group) or 0)
            + max(len(details) - available, 0)
        )
    return current


def _emit_managed_conversion_progress(
    emit,
    summary,
    total,
    supported_total,
    unsupported,
    unsupported_total,
    processed,
):
    """Emit one bounded aggregate progress update after each batch."""

    if emit is None:
        return
    summary = _managed_conversion_summary(
        summary,
        unsupported,
        processed,
        processed,
        unsupported_total=unsupported_total,
    )
    _emit_managed_conversion_event(
        emit,
        phase="PARSING_DOCUMENTS",
        message=(
            f"Processed {processed}/{supported_total} convertible files."
        ),
        phase_current=processed,
        phase_total=supported_total,
        phase_unit="files",
        overall_progress_percent=_managed_conversion_overall_percent(
            processed,
            supported_total,
        ),
        summary=summary,
        total=total,
        candidates=supported_total,
        processed=unsupported_total + processed,
    )


def _managed_conversion_overall_percent(processed, candidates):
    """Return overall progress while reserving time for finalization."""

    if candidates <= 0:
        return 90
    return min(90, 10 + int(80 * processed / candidates))


def _managed_conversion_batch_emitter(
    emit,
    previous_summary,
    *,
    total,
    supported_total,
    unsupported,
    unsupported_total,
    processed_before,
):
    """Translate one batch's local events to workspace-level progress."""

    def emit_event(event):
        """Emit an event with global counters and phase semantics."""

        if emit is None:
            return
        local_current = int(event.get("progress_current") or 0)
        phase = event.get("phase") or "PARSING_DOCUMENTS"
        phase_progress = event.get("phase_progress") or {}
        if phase == "PROCESSING_EMBEDDED_IMAGES":
            document_current = int(event.get("document_current") or 1)
            image_current = int(phase_progress.get("current") or 0)
            image_total = int(phase_progress.get("total") or 0)
            completed = processed_before + max(document_current - 1, 0)
            visual_fraction = (
                image_current / image_total if image_total else 0
            )
            overall_current = completed + visual_fraction
            phase_current = image_current
            phase_total = image_total
            phase_unit = phase_progress.get("unit") or "embedded_images"
        else:
            completed = processed_before + local_current
            overall_current = completed
            phase_current = completed
            phase_total = supported_total
            phase_unit = "files"
        summary = _managed_conversion_progress_summary(
            previous_summary,
            event.get("summary") or {},
        )
        summary = _managed_conversion_summary(
            summary,
            unsupported,
            supported_total,
            completed,
            unsupported_total=unsupported_total,
        )
        event.update(
            {
                "message": (
                    event.get("message")
                    if phase == "PROCESSING_EMBEDDED_IMAGES"
                    else (
                        f"Processed {completed}/{supported_total} "
                        "convertible files."
                    )
                ),
                "phase": phase,
                "overall_progress_percent": (
                    _managed_conversion_overall_percent(
                        overall_current,
                        supported_total,
                    )
                ),
                "phase_progress": {
                    "current": phase_current,
                    "total": phase_total,
                    "unit": phase_unit,
                    **(
                        {"scope": "current_file"}
                        if phase == "PROCESSING_EMBEDDED_IMAGES"
                        else {}
                    ),
                },
                "progress_counts": _managed_conversion_progress_counts(
                    summary,
                    total=total,
                    candidates=supported_total,
                    processed=unsupported_total + completed,
                ),
                "progress_total": phase_total,
                "progress_current": phase_current,
                "progress_percent": _managed_conversion_overall_percent(
                    overall_current,
                    supported_total,
                ),
                "summary": summary,
                "substantive_progress": True,
            }
        )
        emit(event)

    return emit_event


def _managed_conversion_progress_summary(previous, incoming):
    """Combine completed batches with a local in-progress batch summary."""

    result = _empty_managed_conversion_summary()
    for key in ["converted", "success", "skipped", "failed"]:
        result[key] = int((previous or {}).get(key) or 0) + int(
            (incoming or {}).get(key) or 0
        )
    return result


def _managed_conversion_progress_counts(
    summary,
    *,
    total,
    candidates,
    processed,
):
    """Return stable managed-conversion lifecycle counters."""

    return {
        "total": int(total),
        "candidates": int(candidates),
        "processed": int(processed),
        "converted": int(summary.get("converted") or 0),
        "failed": int(summary.get("failed") or 0),
        "skipped": int(summary.get("skipped") or 0),
        "unsupported": int(summary.get("unsupported") or 0),
    }


def _emit_managed_conversion_event(
    emit,
    *,
    phase,
    message,
    phase_current,
    phase_total,
    phase_unit,
    overall_progress_percent,
    summary,
    total,
    candidates,
    processed,
):
    """Emit one machine-readable managed-conversion progress event."""

    if emit is None:
        return
    progress_counts = _managed_conversion_progress_counts(
        summary,
        total=total,
        candidates=candidates,
        processed=processed,
    )
    _emit(
        emit,
        "conversion_progress",
        "running",
        message,
        category="conversion",
        summary=summary,
        progress_total=phase_total,
        progress_current=phase_current,
        progress_percent=overall_progress_percent,
        phase=phase,
        overall_progress_percent=overall_progress_percent,
        phase_progress={
            "current": int(phase_current),
            "total": int(phase_total),
            "unit": phase_unit,
        },
        progress_counts=progress_counts,
        substantive_progress=True,
    )


def _managed_conversion_summary(
    summary,
    unsupported,
    supported_total,
    supported_current,
    unsupported_total=None,
):
    """Add standalone conversion lifecycle counters and outcomes."""

    result = dict(summary or {})
    unsupported_items = [
        {
            "status": "unsupported",
            "path": item.local_path,
            "name": item.name,
            "extension": item.extension,
            "reason": "UNSUPPORTED_TYPE",
            "stats": {},
        }
        for item in unsupported
    ]
    items = list(result.get("items") or [])
    existing_paths = {item.get("path") for item in items}
    available = max(DETAIL_ITEMS_LIMIT - len(items), 0)
    new_items = [
        item
        for item in unsupported_items
        if item["path"] not in existing_paths
    ]
    items.extend(new_items[:available])
    completed = min(max(supported_current, 0), supported_total)
    remaining = max(supported_total - completed, 0)
    active = 1 if remaining else 0
    unsupported_count = (
        len(unsupported) if unsupported_total is None else unsupported_total
    )
    result.update(
        {
            "total": supported_total + unsupported_count,
            "waiting": max(remaining - active, 0),
            "active": active,
            "succeeded": int(result.get("success") or 0),
            "unsupported": unsupported_count,
            "items": items,
            "items_truncated": int(result.get("items_truncated") or 0)
            + max(len(new_items) - available, 0),
        }
    )
    details = dict(result.get("details") or {})
    if unsupported_items:
        details["unsupported"] = unsupported_items[:DETAIL_ITEMS_LIMIT]
    result["details"] = details
    return result


def _conversion_percent(current, total):
    """Return a bounded integer conversion progress percentage."""

    if total <= 0:
        return 100
    return min(100, int((current / total) * 100))


def datasource_adapter_registry():
    """Return the datasource adapter registry."""

    registry = DataSourceAdapterRegistry()
    registry.register(FunctionDataSourceAdapter("git", _sync_git))
    registry.register(FunctionDataSourceAdapter("feishu", _sync_feishu))
    return registry


def _sync_context(command, target):
    """Return the common datasource sync context."""

    conversion = (command.get("sync_policy") or {}).get("conversion")
    conversion = command.get("conversion") or conversion or {}
    return {
        "datasource_uuid": str(command.get("datasource_uuid") or ""),
        "name": command.get("name") or "",
        "source_type": command.get("source_type") or "",
        "target_path": str(target),
        "config": command.get("config") or {},
        "conversion": conversion,
        "trigger": command.get("trigger") or "",
        "max_workers": command.get("max_workers") or 0,
        "excluded_datasource_roots": normalize_excluded_roots(
            command.get("excluded_datasource_roots") or [],
            target,
        ),
        "ai_gateway_url": command.get("ai_gateway_url") or "",
        "lensnode_token": command.get("lensnode_token") or "",
        "gateway_http_client": command.get("gateway_http_client"),
        "tls_skip_verify": bool(command.get("tls_skip_verify", False)),
        "tls_ca_file": command.get("tls_ca_file") or None,
        "vision_model_ref": conversion.get("vision_model_ref") or "",
        "force": bool(command.get("force", False)),
        "cancel_event": command.get("cancel_event"),
        "on_activity": command.get("on_activity"),
    }


def _sync_summary_from_result(result):
    """Return sync summary fields from a datasource result."""

    keys = [
        "synced",
        "files",
        "folders",
        "failed",
        "scanned",
        "changed",
        "skipped",
        "deleted",
        "documents",
        "by_extension",
        "by_type",
    ]
    return {key: result.get(key) for key in keys if key in result}


def _sync_details_by_metric(items, changed_paths, deleted_paths):
    """Return compact sync details grouped by summary metric."""

    changed = set(changed_paths or [])
    deleted = set(deleted_paths or [])
    details = {
        "scanned": [],
        "changed": [],
        "skipped": [],
        "success": [],
        "failed": [],
        "deleted": [],
        "documents": [],
        "files": [],
    }
    truncated = {key: 0 for key in details}
    for item in items or []:
        local_path = manifest_store.manifest_local_path(item)
        status = item.get("status") or "synced"
        detail = _sync_detail_item(item, local_path)
        if status != "deleted":
            _append_limited_detail(details, truncated, "scanned", detail)
        if local_path not in changed:
            if status == "skipped":
                _append_limited_detail(details, truncated, "skipped", detail)
            elif status == "deleted":
                _append_limited_detail(details, truncated, "deleted", detail)
            elif status == "failed" or item.get("error"):
                _append_limited_detail(details, truncated, "failed", detail)
        else:
            _append_limited_detail(details, truncated, "changed", detail)
            if status == "failed" or item.get("error"):
                _append_limited_detail(details, truncated, "failed", detail)
            else:
                _append_limited_detail(details, truncated, "success", detail)
        kind = item.get("kind") or item.get("type") or ""
        if kind == "document":
            _append_limited_detail(details, truncated, "documents", detail)
        elif kind == "file":
            _append_limited_detail(details, truncated, "files", detail)

    for path in deleted:
        if any(item.get("path") == path for item in details["deleted"]):
            continue
        detail = {
            "status": "deleted",
            "path": path,
            "name": Path(path).name,
            "extension": Path(path).suffix.lower().lstrip("."),
            "source_type": "",
        }
        _append_limited_detail(details, truncated, "deleted", detail)

    return {
        "details": {key: value for key, value in details.items() if value},
        "details_truncated": {
            key: value for key, value in truncated.items() if value
        },
    }


def _sync_detail_item(item, local_path):
    """Return one compact sync detail item."""

    return {
        "status": item.get("status") or "synced",
        "path": local_path,
        "name": item.get("name") or Path(local_path).name,
        "extension": (
            item.get("extension")
            or item.get("file_extension")
            or Path(local_path).suffix.lower().lstrip(".")
        ),
        "source_type": item.get("source_type") or "",
        "reason": item.get("error") or "",
    }


def _append_limited_detail(details, truncated, key, item):
    """Append a detail item with a per-metric limit."""

    if len(details[key]) >= DETAIL_ITEMS_LIMIT:
        truncated[key] += 1
        return
    details[key].append(item)


def datasource_sync_workers(command):
    """Return configured datasource sync worker count."""

    try:
        value = int(command.get("max_workers") or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else DEFAULT_DATASOURCE_SYNC_WORKERS


def test_datasource_connection(command):
    """Test datasource connectivity without mutating local files."""

    source_type = command.get("source_type")
    config = command.get("config") or {}
    if source_type == "git":
        return _test_git_connection(config)
    if source_type == "feishu":
        return _test_feishu_connection(config)
    raise DataSourceSyncError("LENS_SOURCE_TYPE_UNSUPPORTED")


def _test_git_connection(config):
    """Test that a Git repository and branch are reachable."""

    repo_url = config.get("repo_url")
    if not repo_url:
        raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")

    branch = str(config.get("branch") or "").strip()
    try:
        result = _run_git(
            ["ls-remote", "--heads", repo_url],
            timeout=60,
            detail_prefix="LENS_SOURCE_GIT_LS_REMOTE_FAILED",
            **_git_run_options(config),
        )
    except DataSourceSyncError:
        organization_result = _discover_git_organization(config)
        if organization_result is not None:
            return organization_result
        return {
            "status": "failed",
            "message_code": "git_unreachable",
            "message": "Git repository is not reachable.",
        }

    branches = _git_remote_branches(result.stdout)
    selected_branch = branch or _default_git_branch(branches)
    if branch and branch not in branches:
        return {
            "status": "failed",
            "message_code": "git_branch_missing",
            "message": "Git branch does not exist or is not accessible.",
            "details": {"branch": branch, "branches": branches},
        }
    if not selected_branch:
        return {
            "status": "failed",
            "message_code": "git_branch_missing",
            "message": "Git branch does not exist or is not accessible.",
            "details": {"branch": branch, "branches": branches},
        }
    return {
        "status": "success",
        "message_code": "git_branch_available",
        "message": "Git repository is reachable and branch exists.",
        "details": {"branch": selected_branch, "branches": branches},
    }


def _discover_git_organization(config):
    """Return repositories under a Git organization URL when supported."""

    repo_url = config.get("repo_url")
    parsed = parse.urlsplit(str(repo_url or "").rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    group_path = parsed.path.strip("/")
    if not group_path:
        return None

    projects, attempts = _discover_git_projects(parsed, group_path, config)
    if not projects:
        if not str(parsed.path or "").rstrip("/").endswith(".git"):
            return {
                "status": "failed",
                "message_code": "git_organization_unreachable",
                "message": "Git organization is not reachable.",
                "details": {
                    "scope": "organization",
                    "organization_url": repo_url,
                    "attempts": attempts,
                },
            }
        return None

    repositories = _git_project_repositories(projects, config)
    if not repositories:
        return None
    owner_types = sorted(
        {
            project.get("owner_type")
            for project in projects
            if project.get("owner_type")
        }
    )
    return {
        "status": "success",
        "message_code": "git_organization_available",
        "message": "Git organization is reachable.",
        "details": {
            "scope": "organization",
            "owner_type": owner_types[0] if len(owner_types) == 1 else "",
            "organization_url": repo_url,
            "repositories": repositories,
        },
    }


def _git_project_repositories(projects, config):
    """Return repository entries with branch details."""

    repositories = []
    max_workers = min(12, max(1, len(projects)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_git_project_repository, project, config)
            for project in projects
        ]
        for future in as_completed(futures):
            repository = future.result()
            if repository is not None:
                repositories.append(repository)
    repositories.sort(key=lambda item: item.get("name") or "")
    return repositories


def _git_project_repository(project, config):
    """Return one repository entry with branches."""

    clone_url = project.get("repo_url") or ""
    if not clone_url:
        return None
    branches = _git_project_branches(project, config)
    selected_branch = (
        project.get("default_branch")
        if project.get("default_branch") in branches
        else _default_git_branch(branches)
    )
    return {
        "name": project.get("name") or _repo_name_from_url(clone_url),
        "path": project.get("path") or project.get("name") or "",
        "repo_url": clone_url,
        "default_branch": selected_branch,
        "branches": branches,
    }


def _git_project_branches(project, config):
    """Return branches for a discovered Git project."""

    if project.get("service") == "gitlab" and project.get("api_id") is not None:
        branches = _gitlab_project_branches(project, config)
        if branches:
            return branches
    clone_url = project.get("repo_url") or ""
    return _git_repo_branches(clone_url, config)


def _gitlab_project_branches(project, config):
    """Return GitLab project branches through the GitLab API."""

    api_base_url = project.get("api_base_url") or ""
    api_id = project.get("api_id")
    if not api_base_url or api_id is None:
        return []
    branches = []
    for page in range(1, 11):
        url = (
            f"{api_base_url}/api/v4/projects/{parse.quote(str(api_id), safe='')}"
            f"/repository/branches?per_page=100&page={page}"
        )
        payload, _error_detail = _git_api_json(
            url,
            config,
            auth_style="gitlab",
            timeout=10,
        )
        if not isinstance(payload, list):
            return branches
        if not payload:
            break
        branches.extend(
            item.get("name")
            for item in payload
            if item.get("name")
        )
        if len(payload) < 100:
            break
    return branches


def _discover_git_projects(parsed, group_path, config):
    """Discover projects from supported Git organization APIs."""

    attempts = []
    provider = str(config.get("provider") or "").lower()
    hostname = str(parsed.hostname or "").lower()
    if provider == "github" or hostname == "github.com":
        projects, github_attempts = _discover_github_projects(
            parsed,
            group_path,
            config,
        )
        attempts.extend(github_attempts)
        return projects, attempts
    if provider == "gitlab":
        projects, attempt = _discover_gitlab_projects(
            parsed,
            group_path,
            config,
        )
        attempts.append(attempt)
        return projects, attempts
    projects, attempt = _discover_gitlab_projects(parsed, group_path, config)
    attempts.append(attempt)
    if projects:
        return projects, attempts
    projects, attempt = _discover_gitea_projects(parsed, group_path, config)
    attempts.append(attempt)
    return projects, attempts


def _discover_github_projects(parsed, group_path, config):
    """Discover repositories from a GitHub owner or repository URL."""

    api_base = _github_api_base(parsed, config)
    parts = [part for part in group_path.split("/") if part]
    if not parts:
        return None, []
    if len(parts) >= 2:
        owner, repo = parts[0], parts[1]
        url = (
            f"{api_base}/repos/{parse.quote(owner, safe='')}"
            f"/{parse.quote(repo, safe='')}"
        )
        payload, error_detail = _git_api_json(
            url,
            config,
            auth_style="github",
        )
        if not isinstance(payload, dict):
            return None, [_git_api_attempt("github", url, error_detail)]
        return (
            [_github_project(payload, api_base, "repo")],
            [_git_api_attempt("github", url, error_detail)],
        )

    owner = parts[0]
    projects, attempts = _discover_github_owner_projects(
        api_base,
        owner,
        "org",
        config,
    )
    if projects:
        return projects, attempts
    user_projects, user_attempts = _discover_github_owner_projects(
        api_base,
        owner,
        "user",
        config,
    )
    attempts.extend(user_attempts)
    return user_projects, attempts


def _github_api_base(parsed, config):
    """Return the GitHub API base URL for public or enterprise GitHub."""

    endpoint = str(config.get("endpoint_url") or "").rstrip("/")
    if endpoint:
        endpoint_parsed = parse.urlsplit(endpoint)
        if endpoint_parsed.netloc == "github.com":
            return "https://api.github.com"
        return f"{endpoint}/api/v3"
    if parsed.netloc == "github.com":
        return "https://api.github.com"
    base_url = parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return f"{base_url}/api/v3"


def _discover_github_owner_projects(api_base, owner, owner_type, config):
    """Discover GitHub repositories for an organization or user owner."""

    projects = []
    attempts = []
    last_error = ""
    owner_path = "orgs" if owner_type == "org" else "users"
    for page in range(1, 11):
        url = (
            f"{api_base}/{owner_path}/{parse.quote(owner, safe='')}/repos"
            f"?per_page=100&page={page}"
        )
        payload, error_detail = _git_api_json(
            url,
            config,
            auth_style="github",
        )
        attempts.append(_git_api_attempt("github", url, error_detail))
        if not isinstance(payload, list):
            last_error = error_detail
            return None if page == 1 else projects, attempts
        if not payload:
            break
        projects.extend(
            _github_project(item, api_base, owner_type)
            for item in payload
        )
        if len(payload) < 100:
            break
    if last_error and not projects:
        return None, attempts
    return projects, attempts


def _github_project(item, api_base, owner_type):
    """Return a normalized GitHub repository project entry."""

    return {
        "service": "github",
        "api_base_url": api_base,
        "owner_type": owner_type,
        "name": item.get("name") or item.get("full_name"),
        "path": item.get("full_name") or item.get("name"),
        "repo_url": item.get("clone_url") or item.get("html_url") or "",
        "default_branch": item.get("default_branch") or "",
    }


def _discover_gitlab_projects(parsed, group_path, config):
    """Discover projects from a GitLab group URL."""

    encoded_group = parse.quote(group_path, safe="")
    base_url = parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    projects = []
    last_error = ""
    for page in range(1, 11):
        url = (
            f"{base_url}/api/v4/groups/{encoded_group}/projects"
            f"?include_subgroups=true&simple=true&per_page=100&page={page}"
        )
        payload, error_detail = _git_api_json(
            url,
            config,
            auth_style="gitlab",
        )
        if not isinstance(payload, list):
            last_error = error_detail
            return (
                None if page == 1 else projects,
                _git_api_attempt("gitlab", url, last_error),
            )
        if not payload:
            break
        projects.extend(
            {
                "service": "gitlab",
                "api_base_url": base_url,
                "api_id": item.get("id"),
                "name": item.get("name") or item.get("path"),
                "path": item.get("path") or item.get("name"),
                "repo_url": (
                    item.get("http_url_to_repo")
                    or item.get("web_url")
                    or ""
                ),
                "default_branch": item.get("default_branch") or "",
            }
            for item in payload
        )
        if len(payload) < 100:
            break
    return projects, _git_api_attempt("gitlab", url, last_error)


def _discover_gitea_projects(parsed, group_path, config):
    """Discover repositories from a Gitea organization URL."""

    org = group_path.split("/", 1)[0]
    base_url = parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    projects = []
    last_error = ""
    for page in range(1, 11):
        url = (
            f"{base_url}/api/v1/orgs/{parse.quote(org)}/repos"
            f"?limit=100&page={page}"
        )
        payload, error_detail = _git_api_json(
            url,
            config,
            auth_style="gitea",
        )
        if not isinstance(payload, list):
            last_error = error_detail
            return (
                None if page == 1 else projects,
                _git_api_attempt("gitea", url, last_error),
            )
        if not payload:
            break
        projects.extend(
            {
                "service": "gitea",
                "name": item.get("name") or item.get("full_name"),
                "path": item.get("name") or item.get("full_name"),
                "repo_url": (
                    item.get("clone_url")
                    or item.get("html_url")
                    or ""
                ),
                "default_branch": item.get("default_branch") or "",
            }
            for item in payload
        )
        if len(payload) < 100:
            break
    return projects, _git_api_attempt("gitea", url, last_error)


def _git_api_attempt(service, url, error_detail):
    """Return a compact Git API discovery attempt detail."""

    return {
        "service": service,
        "url": url,
        "error": error_detail or "",
    }


def _git_api_json(url, config, auth_style, timeout=30):
    """Fetch JSON from a Git service API."""

    headers = {"Accept": "application/json"}
    credentials = _load_credentials(config)
    token = credentials.get("token") or credentials.get("password")
    if token and auth_style == "gitlab":
        headers["PRIVATE-TOKEN"] = token
    elif token and auth_style == "github":
        headers["Authorization"] = f"Bearer {token}"
    elif token and auth_style == "gitea":
        headers["Authorization"] = f"token {token}"
    req = request.Request(url, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")), ""
    except error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")[:500]
        except Exception:
            body = ""
        return None, f"HTTP {exc.code}: {body}"
    except error.URLError as exc:
        return None, str(exc.reason)
    except TimeoutError:
        return None, "request timed out"
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def _git_repo_branches(repo_url, config):
    """Return remote branches for one repository URL."""

    try:
        result = _run_git(
            ["ls-remote", "--heads", repo_url],
            timeout=60,
            detail_prefix="LENS_SOURCE_GIT_LS_REMOTE_FAILED",
            **_git_run_options(config),
        )
    except DataSourceSyncError:
        return []
    return _git_remote_branches(result.stdout)


def _repo_name_from_url(repo_url):
    """Return a displayable repository name from a clone URL."""

    path = parse.urlsplit(repo_url or "").path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if path else ""
    return name[:-4] if name.endswith(".git") else name


def _test_feishu_connection(config):
    """Test that Feishu document identifiers are available."""

    sync_mode = config.get("sync_mode") or "document_list"
    credentials = _load_credentials(config)
    token = _feishu_tenant_token(credentials)
    if sync_mode == "drive_folder":
        folder_token = _feishu_folder_token(config)
        if not folder_token:
            return {
                "status": "failed",
                "message_code": "feishu_folder_missing",
                "message": "Feishu Drive folder URL or token is required.",
            }
        if not token:
            return {
                "status": "failed",
                "message_code": "LENS_SOURCE_CREDENTIAL_INVALID",
                "message": "Feishu app credential is required.",
            }
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        try:
            children = _list_feishu_folder_children(folder_token, headers)
        except DataSourceSyncError as exc:
            return {
                "status": "failed",
                "message_code": "feishu_folder_unreachable",
                "message": "Feishu Drive folder is not reachable.",
                "details": {
                    "folder_token": folder_token,
                    "error": str(exc),
                },
            }
        return {
            "status": "success",
            "message_code": "feishu_folder_available",
            "message": "Feishu Drive folder is reachable.",
            "details": {
                "folder_token": folder_token,
                "children": len(children),
            },
        }

    doc_ids = _feishu_doc_ids(config)
    if not doc_ids:
        return {
            "status": "failed",
            "message_code": "feishu_document_missing",
            "message": "Feishu document ID or URL is required.",
        }

    if not token:
        return {
            "status": "failed",
            "message_code": "LENS_SOURCE_CREDENTIAL_INVALID",
            "message": "Feishu app credential is required.",
        }

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    try:
        document = _export_feishu_document(doc_ids[0], "docx", headers)
    except DataSourceSyncError as exc:
        return {
            "status": "failed",
            "message_code": "feishu_unreachable",
            "message": "Feishu document is not reachable.",
            "details": {
                "doc_id": doc_ids[0],
                "error": str(exc),
            },
        }
    return {
        "status": "success",
        "message_code": "feishu_document_available",
        "message": "Feishu document is reachable.",
        "details": {
            "doc_id": doc_ids[0],
            "title": document.get("file_name") or "",
        },
    }


def _sync_git(command, workspace_path, emit):
    """Clone or update a Git repository into the target path."""

    config = command.get("config") or {}
    if config.get("scope_type") == "organization" or config.get("repositories"):
        return _sync_git_repositories(command, workspace_path, emit)

    repo_url = config.get("repo_url")
    if not repo_url:
        raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")

    target = normalize_target_path(command.get("target_path"), workspace_path)
    git_options = _git_run_options(config)
    cancel_event = command.get("cancel_event")
    if cancel_event is not None:
        git_options["cancel_event"] = cancel_event
    branch = config.get("branch") or _git_default_branch_from_remote(
        repo_url,
        git_options,
    )
    if not branch:
        raise DataSourceSyncError(
            "LENS_SOURCE_GIT_DEFAULT_BRANCH_UNAVAILABLE"
        )
    previous_manifest = _read_manifest(target)
    previous_items = manifest_store.manifest_items_by_source_id(
        previous_manifest
    )
    _emit(emit, "check_path", "running", "Checking target directory.")
    inspection = inspect_datasource_path(command, workspace_path)
    if not inspection.get("source_compatible"):
        raise DataSourceSyncError(inspection.get("message") or "PATH_BLOCKED")

    target.parent.mkdir(parents=True, exist_ok=True)
    _emit(emit, "sync_content", "running", "Synchronizing Git repository.")
    if not (target / ".git").exists():
        _run_git(
            [
                "clone",
                "--depth",
                GIT_SHALLOW_DEPTH,
                "--branch",
                branch,
                "--single-branch",
                repo_url,
                str(target),
            ],
            detail_prefix="LENS_SOURCE_GIT_CLONE_FAILED",
            **git_options,
        )
    else:
        remote_url = _git_output(
            ["remote", "get-url", "origin"],
            cwd=target,
        )
        if _has_embedded_credentials(remote_url):
            _run_git(
                [
                    "remote",
                    "set-url",
                    "origin",
                    _strip_url_credentials(repo_url),
                ],
                cwd=target,
                detail_prefix="LENS_SOURCE_GIT_REMOTE_UPDATE_FAILED",
                **git_options,
            )
        _run_git(
            [
                "fetch",
                "--depth",
                GIT_SHALLOW_DEPTH,
                "origin",
                branch,
                "--prune",
            ],
            cwd=target,
            detail_prefix="LENS_SOURCE_GIT_FETCH_FAILED",
            **git_options,
        )
        _run_git(
            ["checkout", branch],
            cwd=target,
            detail_prefix="LENS_SOURCE_GIT_CHECKOUT_FAILED",
            **git_options,
        )
        _run_git(
            ["reset", "--hard", f"origin/{branch}"],
            cwd=target,
            detail_prefix="LENS_SOURCE_GIT_RESET_FAILED",
            **git_options,
        )

    if config.get("allow_submodules", True):
        _sync_git_submodules(
            target,
            config=config,
            git_options=git_options,
        )

    _validate_git_tree_size(target)

    items = _git_manifest_items(
        target,
        repo_url,
        branch,
        directory=config.get("directory") or "",
    )
    changed_paths, deleted_paths, skipped = _git_manifest_delta(
        items,
        previous_items,
    )
    files = len(items)
    summary = {
        "scanned": files,
        "changed": len(changed_paths),
        "skipped": skipped,
        "deleted": len(deleted_paths),
        "failed": 0,
        "files": files,
        "folders": 0,
        "documents": 0,
        "by_extension": _count_file_extensions(target),
        "by_type": {"git": 1},
    }
    _emit(
        emit,
        "manifest",
        "done",
        (
            f"Git sync completed with {files} files, "
            f"{summary['changed']} changed, {skipped} skipped."
        ),
        category="summary",
        progress_total=1,
        progress_current=1,
        progress_percent=100,
        summary=summary,
    )
    return {
        "synced": 1,
        "files": files,
        "target_path": str(target),
        "_sync_items": items,
        "_changed_paths": changed_paths,
        "_deleted_paths": deleted_paths,
        **summary,
    }


def _sync_git_repositories(command, workspace_path, emit):
    """Synchronize Git repositories under one datasource root."""

    config = command.get("config") or {}
    repositories = [
        item for item in config.get("repositories") or []
        if item.get("enabled", True)
    ]
    if not repositories:
        raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")

    root = normalize_target_path(command.get("target_path"), workspace_path)
    root.mkdir(parents=True, exist_ok=True)
    repository_targets = [
        (
            repository,
            *_resolve_git_repository_target(
                root,
                repository,
                command.get("datasource_uuid"),
                single_repository=len(repositories) == 1,
            ),
        )
        for repository in repositories
    ]
    target_paths = [
        target for _repository, _identity, target in repository_targets
    ]
    if len(set(target_paths)) != len(target_paths):
        raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")
    if root.resolve() not in target_paths:
        repository_names = {
            target.relative_to(root.resolve()).as_posix()
            for target in target_paths
        }
        _cleanup_removed_git_repositories(
            root,
            repository_names,
            command.get("datasource_uuid"),
        )
    totals = {
        "synced": 0,
        "files": 0,
        "folders": 0,
        "documents": 0,
        "scanned": 0,
        "changed": 0,
        "skipped": 0,
        "deleted": 0,
        "failed": 0,
        "by_extension": {},
        "by_type": {"git": len(repositories)},
    }
    sync_items = []
    changed_paths = []
    deleted_paths = []
    repository_summaries = []
    failed_repositories = []
    total = len(repositories)
    _emit(
        emit,
        "sync_plan",
        "running",
        f"Prepared {total} Git repositories for synchronization.",
        category="summary",
        progress_total=total,
        progress_current=0,
        progress_percent=0,
        summary=totals,
    )
    for index, (repository, identity, repo_target) in enumerate(
        repository_targets,
        start=1,
    ):
        relative_target = repo_target.relative_to(root.resolve()).as_posix()
        path_prefix = "" if relative_target == "." else relative_target
        repo_command = {
            **command,
            "target_path": str(repo_target),
            "config": {
                **config,
                "repo_url": repository.get("repo_url") or "",
                "branch": (
                    repository.get("branch") or config.get("branch") or ""
                ),
            },
        }
        repo_command["config"].pop("repositories", None)
        repo_command["config"]["scope_type"] = "repository"
        _emit(
            emit,
            "repository_started",
            "running",
            f"Synchronizing Git repository {identity}.",
            category="repository",
            progress_total=total,
            progress_current=index - 1,
            progress_percent=int(((index - 1) / total) * 100),
            current_file=identity,
        )
        repository_event_status = "done"
        repository_event_message = f"Finished Git repository {identity}."
        repository_event_error = ""
        try:
            result = _sync_git(repo_command, workspace_path, emit)
            repo_items = result.pop("_sync_items", [])
            repo_changed = result.pop("_changed_paths", [])
            repo_deleted = result.pop("_deleted_paths", [])
            _write_git_repository_manifest(
                repo_command,
                repo_target,
                result,
                repo_items,
                repo_changed,
                repo_deleted,
            )
            prefixed_items = _prefix_sync_items(repo_items, path_prefix)
            sync_items.extend(prefixed_items)
            changed_paths.extend(_prefix_paths(repo_changed, path_prefix))
            deleted_paths.extend(_prefix_paths(repo_deleted, path_prefix))
            _merge_git_summary(totals, result)
            totals["synced"] += 1
            repository_summaries.append(
                _repository_summary(identity, repository, "success", result)
            )
        except Exception as exc:
            if str(exc) == "LENS_SOURCE_SYNC_CANCELLED":
                raise
            totals["failed"] += 1
            repository_event_status = "failed"
            repository_event_error = str(exc)
            repository_event_message = (
                f"Failed Git repository {identity}: {repository_event_error}"
            )
            failure = _repository_summary(
                identity,
                repository,
                "failed",
                {"error": str(exc)},
            )
            failed_repositories.append(failure)
            repository_summaries.append(failure)
        _emit(
            emit,
            "repository_done",
            repository_event_status,
            repository_event_message,
            category="repository",
            progress_total=total,
            progress_current=index,
            progress_percent=int((index / total) * 100),
            summary=totals,
            current_file=identity,
            error=repository_event_error,
        )

    return {
        **totals,
        "status": (
            "failed" if failed_repositories and not totals["synced"] else "success"
        ),
        "target_path": str(root),
        "repository_summaries": repository_summaries,
        "failed_repositories": failed_repositories,
        "partial_success": bool(failed_repositories and totals["synced"]),
        "_sync_items": sync_items,
        "_changed_paths": changed_paths,
        "_deleted_paths": deleted_paths,
    }


def repo_target_subdir(repository):
    """Return a safe repository identity path below a datasource root."""

    raw = (
        repository.get("target_subdir")
        or repository.get("name")
        or repository.get("path")
        or _repo_name_from_url(repository.get("repo_url") or "")
        or "repository"
    )
    value = str(raw).strip()
    if not value or value.startswith("/") or "\\" in value:
        raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")
    parts = value.split("/")
    if any(
        not part
        or part in {".", ".."}
        or safe_filename(part) != part
        for part in parts
    ):
        raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")
    return "/".join(parts)


def _resolve_git_repository_target(
    root,
    repository,
    datasource_uuid,
    *,
    single_repository,
):
    """Return the canonical identity and compatible on-disk target."""

    root = Path(root).resolve()
    identity = repo_target_subdir(repository)
    canonical = (root / identity).resolve()
    try:
        canonical.relative_to(root)
    except ValueError as exc:
        raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID") from exc
    if canonical.exists():
        return identity, canonical

    legacy_candidates = []
    if single_repository:
        legacy_candidates.append(root)
    legacy_candidates.extend(
        [
            root / parse.quote(identity, safe=""),
            root / safe_filename(identity),
            root / identity.rsplit("/", 1)[-1],
        ]
    )
    seen = set()
    for candidate in legacy_candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if _is_compatible_git_repository(
            candidate,
            datasource_uuid,
            repository.get("repo_url") or "",
        ):
            return identity, candidate
    if single_repository and (root / manifest_store.MANIFEST_FILE).is_file():
        raise DataSourceSyncError(
            "LENS_SOURCE_GIT_LAYOUT_MIGRATION_REQUIRED"
        )
    return identity, canonical


def _is_owned_git_repository(path, datasource_uuid):
    """Return whether a Git directory marker belongs to the datasource."""

    path = Path(path)
    marker = path / manifest_store.MARKER_FILE
    if not (path / ".git").exists() or not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return str(payload.get("datasource_uuid") or "") == str(
        datasource_uuid or ""
    )


def _is_compatible_git_repository(path, datasource_uuid, repo_url):
    """Return whether an existing Git directory is safe to reuse."""

    path = Path(path)
    if not (path / ".git").exists():
        return False
    marker = path / manifest_store.MARKER_FILE
    if marker.exists():
        return _is_owned_git_repository(path, datasource_uuid)
    if not repo_url:
        return False
    remote_url = _git_output(["remote", "get-url", "origin"], cwd=path)
    return bool(remote_url) and _normalize_repo_url(
        remote_url
    ) == _normalize_repo_url(repo_url)


def _cleanup_removed_git_repositories(root, repository_names, datasource_uuid):
    """Remove owned repositories no longer configured for a datasource."""

    datasource_uuid = str(datasource_uuid or "")
    if not datasource_uuid:
        return []
    root = Path(root).resolve()
    if (root / ".git").exists():
        return []
    stale = []
    for current, dirnames, filenames in os.walk(root):
        current = Path(current)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name != ".git" and not (current / name).is_symlink()
        )
        if current == root:
            continue
        if manifest_store.MARKER_FILE not in filenames:
            if (current / ".git").exists():
                dirnames[:] = []
            continue
        dirnames[:] = []
        marker = current / manifest_store.MARKER_FILE
        try:
            marker_payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(marker_payload.get("datasource_uuid") or "") != datasource_uuid:
            continue
        name = current.relative_to(root).as_posix()
        if name not in repository_names:
            stale.append((name, current))

    removed = []
    for name, path in sorted(stale, key=lambda item: item[0]):
        shutil.rmtree(path)
        _remove_empty_git_namespace_dirs(path.parent, root)
        removed.append(name)
    return removed


def _remove_empty_git_namespace_dirs(path, root):
    """Remove empty repository namespace directories below the root."""

    path = Path(path)
    root = Path(root)
    while path != root:
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent


def _write_git_repository_manifest(
    command,
    target,
    result,
    items,
    changed_paths,
    deleted_paths,
):
    context = _sync_context(command, target)
    sync_result = manifest_store.SyncResult(
        items=items,
        changed_paths=changed_paths,
        deleted_paths=deleted_paths,
        stats=_sync_summary_from_result(result),
    )
    payload = manifest_store.build_manifest(context, sync_result)
    payload["synced_at"] = utc_timestamp()
    manifest_store.write_datasource_marker(target, context)
    manifest_store.write_manifest(target, payload)


def _prefix_sync_items(items, prefix):
    if not prefix:
        return list(items or [])
    result = []
    for item in items or []:
        local_path = f"{prefix}/{item.local_path}"
        source_path = f"{prefix}/{item.source_path}"
        result.append(
            manifest_store.SyncItem(
                source_id=item.source_id,
                source_type=item.source_type,
                source_path=source_path,
                local_path=local_path,
                name=item.name,
                kind=item.kind,
                extension=item.extension,
                status=item.status,
                metadata=item.metadata,
                remote=item.remote,
            )
        )
    return result


def _prefix_paths(paths, prefix):
    if not prefix:
        return list(paths or [])
    return [f"{prefix}/{path}" for path in paths or []]


def _merge_git_summary(target, source):
    for key in [
        "files",
        "folders",
        "documents",
        "scanned",
        "changed",
        "skipped",
        "deleted",
    ]:
        target[key] += int(source.get(key) or 0)
    for extension, count in (source.get("by_extension") or {}).items():
        for _ in range(int(count or 0)):
            _increment_counter(target["by_extension"], extension)


def _repository_summary(name, repository, status, result):
    return {
        "name": name,
        "repo_url": repository.get("repo_url") or "",
        "branch": repository.get("branch") or "",
        "status": status,
        "files": result.get("files") or 0,
        "changed": result.get("changed") or 0,
        "skipped": result.get("skipped") or 0,
        "deleted": result.get("deleted") or 0,
        "error": result.get("error") or "",
    }


def _sync_git_submodules(target, config=None, git_options=None):
    """Synchronize Git submodules using a Plugin-provided URL rewrite plan."""

    if not (target / ".gitmodules").exists():
        return
    rewrites = _resolve_submodule_url_rewrites(target, config)
    git_args = _submodule_git_config_args(rewrites)
    _run_git(
        [*git_args, "submodule", "sync", "--recursive"],
        cwd=target,
        detail_prefix="LENS_SOURCE_GIT_SUBMODULE_SYNC_FAILED",
        **(git_options or {}),
    )
    _run_git(
        [
            *git_args,
            "submodule",
            "update",
            "--init",
            "--recursive",
            "--depth",
            GIT_SHALLOW_DEPTH,
        ],
        cwd=target,
        detail_prefix="LENS_SOURCE_GIT_SUBMODULE_UPDATE_FAILED",
        **(git_options or {}),
    )


def _resolve_submodule_url_rewrites(target, config):
    """Resolve a bounded, Plugin-owned list of Git URL rewrites."""

    resolver = (config or {}).get("submodule_url_resolver")
    if resolver is None:
        return []
    if not callable(resolver):
        raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")
    try:
        rewrites = resolver(target, config)
    except Exception as exc:
        raise DataSourceSyncError(
            "LENS_SOURCE_GIT_SUBMODULE_PLAN_FAILED"
        ) from exc
    if not isinstance(rewrites, list) or len(rewrites) > 20:
        raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")
    return rewrites


def _submodule_git_config_args(rewrites):
    """Serialize validated Plugin URL rewrites as process-local Git config."""

    args = []
    seen = set()
    for rewrite in rewrites:
        if not isinstance(rewrite, dict):
            raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")
        source = rewrite.get("from")
        destination = rewrite.get("to")
        parsed = parse.urlsplit(str(destination or ""))
        if (
            not isinstance(source, str)
            or not source
            or len(source) > 512
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")
        identity = (source, destination)
        if identity in seen:
            continue
        seen.add(identity)
        args.extend(
            [
                "-c",
                f'url.{json.dumps(destination)}.insteadOf={source}',
            ]
        )
    return args


def _git_manifest_items(target, repo_url, branch, directory=""):
    """Return unified manifest items for a Git datasource."""

    items = []
    commit = _git_output(["rev-parse", "HEAD"], cwd=target)
    directory_path = Path(str(directory or "").strip())
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        if _is_generated_datasource_path(target, path):
            continue
        local_path = relative_path(target, path)
        if directory_path != Path("."):
            try:
                Path(local_path).relative_to(directory_path)
            except ValueError:
                continue
        extension = path.suffix.lower().lstrip(".")
        source_id = f"git:{repo_url}:{branch}:{local_path}"
        items.append(
            manifest_store.SyncItem(
                source_id=source_id,
                source_type="git",
                source_path=local_path,
                local_path=local_path,
                name=path.name,
                kind="file",
                extension=extension,
                status="synced",
                metadata={
                    "commit": commit,
                    "size": str(path.stat().st_size),
                    "sha256": source_sha256(path),
                },
                remote={
                    "repo_url": repo_url,
                    "branch": branch,
                    "path": local_path,
                },
            )
        )
    return items


def _validate_git_tree_size(target):
    """Reject repositories that exceed the LensNode resource ceiling."""

    files = 0
    total_bytes = 0
    for path in target.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        files += 1
        try:
            total_bytes += path.stat().st_size
        except OSError as exc:
            raise DataSourceSyncError(
                "LENS_SOURCE_RESOURCE_STAT_FAILED"
            ) from exc
        if files > GIT_MAX_FILES or total_bytes > GIT_MAX_BYTES:
            raise DataSourceSyncError("LENS_SOURCE_RESOURCE_LIMIT_EXCEEDED")


def _git_manifest_delta(items, previous_items):
    """Return changed and deleted Git paths compared with previous manifest."""

    current_ids = set()
    changed_paths = []
    skipped = 0
    for item in items:
        source_id = manifest_store.manifest_source_id(item)
        current_ids.add(source_id)
        previous_item = previous_items.get(source_id)
        if previous_item and _git_item_signature(
            item.to_manifest()
        ) == _git_item_signature(previous_item):
            skipped += 1
            item.status = "skipped"
            continue
        changed_paths.append(item.local_path)

    deleted_paths = []
    for source_id, previous_item in previous_items.items():
        if source_id in current_ids:
            continue
        local_path = _manifest_local_path(previous_item)
        if local_path:
            deleted_paths.append(local_path)
    return changed_paths, deleted_paths, skipped


def _git_item_signature(item):
    """Return comparable Git file metadata."""

    metadata = item.get("metadata") or {}
    return {
        "source_type": item.get("source_type") or "git",
        "local_path": _manifest_local_path(item),
        "extension": item.get("extension") or item.get("file_extension") or "",
        "size": str(metadata.get("size") or ""),
        "sha256": str(metadata.get("sha256") or ""),
    }












def _sync_feishu(command, workspace_path, emit):
    """Export Feishu content into local files."""

    config = command.get("config") or {}
    credentials = _load_credentials(config)
    target = normalize_target_path(command.get("target_path"), workspace_path)
    target.mkdir(parents=True, exist_ok=True)
    max_workers = datasource_sync_workers(command)
    _emit(
        emit,
        "sync_config",
        "running",
        f"Feishu sync uses {max_workers} workers.",
        category="config",
        max_workers=max_workers,
    )
    token = _feishu_tenant_token(credentials)
    if not token:
        raise DataSourceSyncError("LENS_SOURCE_CREDENTIAL_INVALID")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    sync_mode = config.get("sync_mode") or "document_list"
    if sync_mode == "resource_list":
        return _sync_feishu_resources(
            config,
            target,
            headers,
            emit,
            max_workers,
            cancel_event=command.get("cancel_event"),
        )
    if sync_mode == "drive_folder":
        return _sync_feishu_folder(config, target, headers, emit, max_workers)
    return _sync_feishu_documents(config, target, headers, emit, max_workers)


def _sync_feishu_documents(config, target, headers, emit, max_workers=1):
    """Export configured Feishu documents into their original file format."""

    docs_dir = target / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    doc_ids = _feishu_doc_ids(config)
    if not doc_ids:
        raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")

    synced = 0
    documents = []
    sync_items = []
    max_workers = max(1, int(max_workers or 1))
    stats = {
        "scanned": len(doc_ids),
        "changed": len(doc_ids),
        "skipped": 0,
        "deleted": 0,
        "failed": 0,
        "folders": 0,
        "documents": 0,
        "files": 0,
        "by_extension": {},
        "by_type": {"docx": len(doc_ids)},
    }
    _emit(
        emit,
        "sync_plan",
        "running",
        f"Prepared {len(doc_ids)} Feishu documents for export.",
        category="summary",
        progress_total=len(doc_ids),
        progress_current=0,
        progress_percent=100 if not doc_ids else 0,
        summary=stats,
    )
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _sync_feishu_document_item,
                doc_id,
                docs_dir,
                headers,
                emit,
            ): doc_id
            for doc_id in doc_ids
        }
        for future in as_completed(futures):
            try:
                item = future.result()
                documents.append(item)
                sync_items.append(_manifest_item_to_sync_item(item, target))
                synced += 1
                stats["documents"] += 1
                stats["files"] += 1
                _increment_counter(
                    stats["by_extension"],
                    _manifest_item_extension(item),
                )
            except DataSourceSyncError:
                stats["failed"] += 1
                raise
            _emit(
                emit,
                "sync_progress",
                "running",
                f"Exported {synced}/{len(doc_ids)} Feishu documents.",
                category="summary",
                progress_total=len(doc_ids),
                progress_current=synced,
                progress_percent=_progress_percent(synced, len(doc_ids)),
                summary=stats,
            )

    _write_manifest(
        target,
        {
            "source_type": "feishu",
            "synced_at": utc_timestamp(),
            "stats": stats,
            "documents": documents,
        },
    )
    _emit(
        emit,
        "manifest",
        "done",
        f"Feishu sync completed with {synced} documents.",
        category="summary",
        progress_total=len(doc_ids),
        progress_current=synced,
        progress_percent=100,
        summary=stats,
    )
    return {
        "synced": synced,
        "files": synced,
        "target_path": str(target),
        "_sync_items": sync_items,
        "_changed_paths": [item.local_path for item in sync_items],
        "_deleted_paths": [],
        **stats,
    }


def _sync_feishu_resources(
    config,
    target,
    headers,
    emit,
    max_workers=1,
    cancel_event=None,
):
    """Synchronize mixed Feishu folders and explicit documents."""

    resources = config.get("resources")
    if (
        not isinstance(resources, list)
        or not 1 <= len(resources) <= 100
    ):
        raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")
    max_workers = max(1, int(max_workers or 1))
    recursive = config.get("recursive", True) is not False
    max_depth = int(config.get("max_depth") or 10)
    incremental = config.get(
        "feishu_incremental",
        config.get("incremental", True),
    ) is not False
    delete_missing = config.get(
        "feishu_delete_missing",
        config.get("delete_missing", False),
    ) is True
    previous_manifest = _read_manifest(target)
    previous_items = _manifest_items_by_token(previous_manifest)
    pending_by_token = {}
    manifest_items = []
    seen_tokens = set()
    scanned_folders = set()
    scan_failures = []
    deleted_paths = []
    stats = {
        "folders": 0,
        "documents": 0,
        "files": 0,
        "scanned": 0,
        "changed": 0,
        "skipped": 0,
        "deleted": 0,
        "failed": 0,
        "by_extension": {},
        "by_type": {},
    }
    scan_queue = deque()
    for resource in resources:
        if not isinstance(resource, dict):
            raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")
        token = resource.get("token")
        kind = resource.get("kind")
        if (
            not isinstance(token, str)
            or not isinstance(kind, str)
            or kind not in FEISHU_RESOURCE_KINDS
            or not FEISHU_TOKEN_PATTERN.fullmatch(token)
        ):
            raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")
        if kind == "folder":
            root_dir = target / "folders" / _safe_filename(token)
            root_dir.mkdir(parents=True, exist_ok=True)
            scan_queue.append(
                ("folder", token, root_dir, 1, token)
            )
        else:
            scan_queue.append(
                ("explicit", {"kind": kind, "token": token})
            )

    documents_dir = target / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while scan_queue:
            _check_feishu_cancel(cancel_event)
            futures = {}
            while scan_queue and len(futures) < max_workers:
                task = scan_queue.popleft()
                if task[0] == "folder":
                    _, folder_token, _folder_dir, _depth, _root = task
                    if folder_token in scanned_folders:
                        continue
                    scanned_folders.add(folder_token)
                    future = executor.submit(
                        _list_feishu_folder_children,
                        folder_token,
                        headers,
                    )
                else:
                    future = executor.submit(
                        _feishu_explicit_item,
                        task[1],
                        headers,
                    )
                futures[future] = task
            for future in as_completed(futures):
                _check_feishu_cancel(cancel_event)
                task = futures[future]
                try:
                    result = future.result()
                except DataSourceSyncError as exc:
                    failed_token = (
                        task[1] if task[0] == "folder"
                        else task[1]["token"]
                    )
                    scan_failures.append(failed_token)
                    stats["failed"] += 1
                    _emit(
                        emit,
                        "scan_resource",
                        "failed",
                        f"Failed to scan Feishu resource {failed_token}.",
                        category="summary",
                        token=failed_token,
                        error=str(exc),
                    )
                    continue
                if task[0] == "folder":
                    _, folder_token, folder_dir, depth, root_token = task
                    stats["folders"] += 1
                    _collect_feishu_folder_children(
                        result,
                        folder_dir,
                        depth,
                        root_token,
                        scan_queue,
                        pending_by_token,
                        seen_tokens,
                        stats,
                        recursive,
                        max_depth,
                    )
                    continue
                resource = task[1]
                item = result
                token = _feishu_item_token(item)
                root_id = f"{resource['kind']}:{resource['token']}"
                existing = pending_by_token.get(token)
                if existing is not None:
                    existing["roots"].add(root_id)
                    continue
                pending_by_token[token] = {
                    "item": item,
                    "target_dir": documents_dir,
                    "roots": {root_id},
                    "from_folder": False,
                }
                seen_tokens.add(token)
                stats["scanned"] += 1
                _increment_counter(
                    stats["by_type"],
                    _feishu_item_type(item),
                )
                if stats["scanned"] > FEISHU_MAX_SYNC_ITEMS:
                    raise DataSourceSyncError(
                        "LENS_SOURCE_ITEM_LIMIT_EXCEEDED"
                    )

    pending_items = []
    for token, pending in pending_by_token.items():
        item = pending["item"]
        previous_item = previous_items.get(token)
        target_dir = pending["target_dir"]
        roots = sorted(pending["roots"])
        has_change_metadata = bool(_feishu_item_sync_metadata(item))
        can_skip = pending.get("from_folder") or has_change_metadata
        if incremental and can_skip and _feishu_item_unchanged(
            item,
            previous_item,
            target_dir,
            target,
        ):
            manifest_item = _feishu_manifest_item_from_previous(
                item,
                previous_item,
                target_dir,
                target,
            )
            manifest_item["remote"] = {
                **(manifest_item.get("remote") or {}),
                "roots": roots,
            }
            manifest_items.append(manifest_item)
            stats["skipped"] += 1
            _increment_counter(
                stats["by_extension"],
                _manifest_item_extension(manifest_item),
            )
            continue
        pending_items.append(
            (item, target_dir, previous_item, roots)
        )

    stats["changed"] = len(pending_items)
    _emit(
        emit,
        "sync_plan",
        "running",
        (
            f"Prepared {stats['scanned']} unique Feishu items; "
            f"{stats['changed']} need sync, {stats['skipped']} skipped."
        ),
        category="summary",
        progress_total=stats["changed"],
        progress_current=0,
        progress_percent=100 if not pending_items else 0,
        summary=stats,
    )
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _sync_feishu_drive_item,
                item,
                target_dir,
                target,
                previous_item,
                headers,
                emit,
                cancel_event,
            ): (item, roots, previous_item)
            for item, target_dir, previous_item, roots in pending_items
        }
        for future in as_completed(futures):
            _check_feishu_cancel(cancel_event)
            item, roots, previous_item = futures[future]
            completed += 1
            try:
                manifest_item = future.result()
                manifest_item["remote"] = {
                    **(manifest_item.get("remote") or {}),
                    "roots": roots,
                }
                manifest_items.append(manifest_item)
                if manifest_item.get("kind") == "document":
                    stats["documents"] += 1
                else:
                    stats["files"] += 1
                _increment_counter(
                    stats["by_extension"],
                    _manifest_item_extension(manifest_item),
                )
            except DataSourceSyncError as exc:
                if str(exc) == "LENS_SOURCE_SYNC_CANCELLED":
                    raise
                stats["failed"] += 1
                manifest_items.append(
                    {
                        **(previous_item or {}),
                        "token": _feishu_item_token(item),
                        "name": _feishu_item_name(item),
                        "type": _feishu_item_type(item),
                        "status": "failed",
                        "error": str(exc),
                        "remote": {"roots": roots},
                    }
                )
            _emit(
                emit,
                "sync_progress",
                "running",
                f"Synced {completed}/{stats['changed']} changed items.",
                category="summary",
                progress_total=stats["changed"],
                progress_current=completed,
                progress_percent=_progress_percent(
                    completed,
                    stats["changed"],
                ),
                summary=stats,
            )

    _finalize_feishu_missing_items(
        target,
        previous_items,
        seen_tokens,
        manifest_items,
        deleted_paths,
        stats,
        delete_missing=delete_missing,
        scan_complete=not scan_failures,
    )
    _write_manifest(
        target,
        {
            "source_type": "feishu",
            "sync_mode": "resource_list",
            "incremental": incremental,
            "delete_missing": delete_missing,
            "scan_complete": not scan_failures,
            "synced_at": utc_timestamp(),
            "stats": stats,
            "items": manifest_items,
        },
    )
    total = stats["documents"] + stats["files"]
    if total + stats["skipped"] == 0 and stats["failed"]:
        raise DataSourceSyncError(
            "LENS_SOURCE_SYNC_FAILED: all Feishu resources failed"
        )
    return {
        **stats,
        "synced": total,
        "files": total,
        "target_path": str(target),
        "_sync_items": [
            _manifest_item_to_sync_item(item, target)
            for item in manifest_items
            if _manifest_local_path(item)
        ],
        "_changed_paths": [
            _manifest_local_path(item)
            for item in manifest_items
            if item.get("status") not in {"deleted", "failed", "skipped"}
            and _manifest_local_path(item)
        ],
        "_deleted_paths": deleted_paths,
    }


def _collect_feishu_folder_children(
    children,
    folder_dir,
    depth,
    root_token,
    folder_queue,
    pending_by_token,
    seen_tokens,
    stats,
    recursive,
    max_depth,
):
    """Collect one scanned folder page into the global sync plan."""

    for child in children:
        if not isinstance(child, dict):
            raise DataSourceSyncError("FEISHU_FOLDER_RESPONSE_INVALID")
        token = _feishu_item_token(child)
        if (
            not isinstance(token, str)
            or not FEISHU_TOKEN_PATTERN.fullmatch(token)
        ):
            raise DataSourceSyncError("FEISHU_FOLDER_RESPONSE_INVALID")
        item_type = _feishu_item_type(child)
        if item_type == "folder":
            stats["scanned"] += 1
            _increment_counter(stats["by_type"], item_type)
            if stats["scanned"] > FEISHU_MAX_SYNC_ITEMS:
                raise DataSourceSyncError(
                    "LENS_SOURCE_ITEM_LIMIT_EXCEEDED"
                )
            if recursive and depth < max_depth:
                next_dir = folder_dir / _safe_filename(
                    _feishu_item_name(child)
                )
                next_dir.mkdir(parents=True, exist_ok=True)
                folder_queue.append(
                    ("folder", token, next_dir, depth + 1, root_token)
                )
            continue
        if token not in seen_tokens:
            stats["scanned"] += 1
            _increment_counter(stats["by_type"], item_type)
            if stats["scanned"] > FEISHU_MAX_SYNC_ITEMS:
                raise DataSourceSyncError(
                    "LENS_SOURCE_ITEM_LIMIT_EXCEEDED"
                )
        seen_tokens.add(token)
        existing = pending_by_token.get(token)
        if existing is None:
            pending_by_token[token] = {
                "item": child,
                "target_dir": folder_dir,
                "roots": {f"folder:{root_token}"},
                "from_folder": True,
            }
        else:
            existing["roots"].add(f"folder:{root_token}")
            if not existing.get("from_folder"):
                existing["item"] = child
                existing["target_dir"] = folder_dir
                existing["from_folder"] = True


def _feishu_explicit_item(resource, headers):
    """Return one Drive-like item for an explicit resource."""

    if resource["kind"] == "wiki":
        return _resolve_feishu_wiki_node(resource["token"], headers)
    return {
        "token": resource["token"],
        "name": resource["token"],
        "type": resource["kind"],
    }


def _resolve_feishu_wiki_node(token, headers):
    """Resolve a Wiki node to its exportable object identity."""

    query = parse.urlencode({"token": token})
    payload = _http_json(
        "https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node"
        f"?{query}",
        headers=headers,
    )
    node = (payload.get("data") or {}).get("node")
    if not isinstance(node, dict):
        raise DataSourceSyncError("FEISHU_WIKI_RESPONSE_INVALID")
    object_token = node.get("obj_token")
    object_type = str(node.get("obj_type") or "").lower()
    if (
        not isinstance(object_token, str)
        or not FEISHU_TOKEN_PATTERN.fullmatch(object_token)
        or not _is_feishu_exportable_type(object_type)
    ):
        raise DataSourceSyncError("FEISHU_WIKI_RESPONSE_INVALID")
    return {
        "token": object_token,
        "name": node.get("title") or object_token,
        "type": object_type,
    }


def _finalize_feishu_missing_items(
    target,
    previous_items,
    seen_tokens,
    manifest_items,
    deleted_paths,
    stats,
    *,
    delete_missing,
    scan_complete,
):
    """Preserve or delete previous items according to scan completeness."""

    for token, item in previous_items.items():
        if token in seen_tokens:
            continue
        if not scan_complete:
            manifest_items.append({**item, "status": "skipped"})
            continue
        if not delete_missing:
            manifest_items.append({**item, "status": "skipped"})
            stats["skipped"] += 1
            continue
        missing_scans = int(item.get("missing_scans") or 0) + 1
        if missing_scans < 2:
            manifest_items.append(
                {
                    **item,
                    "status": "missing",
                    "missing_scans": missing_scans,
                }
            )
            stats["skipped"] += 1
            continue
        deleted_item = {**item, "status": "deleted"}
        manifest_items.append(deleted_item)
        stats["deleted"] += 1
        local_path = _manifest_local_path(item)
        if local_path:
            deleted_paths.append(local_path)
        if local_path:
            _delete_manifest_file(target, local_path)


def _check_feishu_cancel(cancel_event):
    """Stop a mixed Feishu sync after cancellation is requested."""

    if cancel_event is not None and cancel_event.is_set():
        raise DataSourceSyncError("LENS_SOURCE_SYNC_CANCELLED")


def _sync_feishu_document_item(doc_id, docs_dir, headers, emit):
    """Export one Feishu document and return manifest metadata."""

    _emit(
        emit,
        "item_started",
        "running",
        f"Exporting Feishu {doc_id}.",
        category="document",
        kind="document",
        token=doc_id,
        item_type="docx",
        item_name=doc_id,
    )
    try:
        exported = _export_feishu_document(doc_id, "docx", headers)
    except DataSourceSyncError as exc:
        _emit(
            emit,
            "item_failed",
            "failed",
            f"Failed to export Feishu {doc_id}.",
            category="document",
            kind="document",
            token=doc_id,
            item_type="docx",
            item_name=doc_id,
            error=str(exc),
        )
        raise
    filename = _export_filename(exported, doc_id, "docx")
    (docs_dir / filename).write_bytes(exported["content"])
    _emit(
        emit,
        "item_done",
        "done",
        f"Exported Feishu {doc_id} to docs/{filename}.",
        category="document",
        kind="document",
        token=doc_id,
        item_type=exported.get("type") or "docx",
        item_name=exported.get("file_name") or doc_id,
        file=f"docs/{filename}",
        file_extension=exported.get("file_extension") or "docx",
    )
    return {
        "doc_id": doc_id,
        "title": exported.get("file_name") or doc_id,
        "file": f"docs/{filename}",
        "type": exported.get("type") or "docx",
        "file_extension": exported.get("file_extension") or "docx",
    }


def _sync_feishu_folder(config, target, headers, emit, max_workers=1):
    """Recursively synchronize a Feishu Drive folder."""

    folder_token = _feishu_folder_token(config)
    if not folder_token:
        raise DataSourceSyncError("LENS_SOURCE_CONFIG_INVALID")
    recursive = config.get("recursive", True) is not False
    max_depth = int(config.get("max_depth") or 10)
    root_dir = target

    previous_manifest = _read_manifest(target)
    previous_items = _manifest_items_by_token(previous_manifest)
    incremental = config.get(
        "feishu_incremental",
        config.get("incremental", True),
    ) is not False
    delete_missing = config.get(
        "feishu_delete_missing",
        config.get("delete_missing", False),
    ) is True
    seen_tokens = set()
    manifest_items = []
    deleted_paths = []
    pending_items = []
    stats = {
        "folders": 0,
        "documents": 0,
        "files": 0,
        "scanned": 0,
        "changed": 0,
        "skipped": 0,
        "deleted": 0,
        "failed": 0,
        "by_extension": {},
        "by_type": {},
    }

    def emit_scan_progress(force=False):
        if not force and stats["scanned"] % 25 != 0:
            return
        _emit(
            emit,
            "scan_progress",
            "running",
            (
                f"Scanned {stats['scanned']} Feishu Drive items in "
                f"{stats['folders']} folders."
            ),
            category="summary",
            progress_current=stats["scanned"],
            summary=stats,
        )

    def scan(current_token, current_dir, depth):
        stats["folders"] += 1
        _emit(
            emit,
            "scan_folder",
            "running",
            f"Scanning Feishu folder {current_token}.",
            category="summary",
            progress_current=stats["scanned"],
            summary=stats,
        )
        children = _list_feishu_folder_children(current_token, headers)
        item_futures = {}
        for child in children:
            name = _feishu_item_name(child)
            item_type = _feishu_item_type(child)
            token = _feishu_item_token(child)
            if not token:
                continue
            seen_tokens.add(token)
            stats["scanned"] += 1
            _increment_counter(stats["by_type"], item_type or "unknown")
            emit_scan_progress()
            if item_type == "folder":
                if not recursive or depth >= max_depth:
                    continue
                next_dir = current_dir / _safe_filename(name)
                next_dir.mkdir(parents=True, exist_ok=True)
                scan(token, next_dir, depth + 1)
                continue
            previous_item = previous_items.get(token)
            if incremental and _feishu_item_unchanged(
                child,
                previous_item,
                current_dir,
                root_dir,
            ):
                item = _feishu_manifest_item_from_previous(
                    child,
                    previous_item,
                    current_dir,
                    root_dir,
                )
                manifest_items.append(item)
                stats["skipped"] += 1
                _increment_counter(
                    stats["by_extension"],
                    _manifest_item_extension(item),
                )
                _emit(
                    emit,
                    "item_skipped",
                    "done",
                    f"Skipped unchanged Feishu {name}.",
                    category="document",
                    token=token,
                    item_type=item_type,
                    item_name=name,
                    file=item.get("file"),
                )
                continue
            pending_items.append((child, current_dir, previous_item))

    scan(folder_token, root_dir, 1)
    emit_scan_progress(force=True)
    stats["changed"] = len(pending_items)
    _emit(
        emit,
        "sync_plan",
        "running",
        (
            f"Scanned {stats['scanned']} items; "
            f"{stats['changed']} need sync, {stats['skipped']} skipped."
        ),
        category="summary",
        progress_total=stats["changed"],
        progress_current=0,
        progress_percent=100 if stats["changed"] == 0 else 0,
        summary=stats,
    )

    completed = 0
    max_workers = max(1, int(max_workers or 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        item_futures = {
            executor.submit(
                _sync_feishu_drive_item,
                child,
                current_dir,
                root_dir,
                previous_item,
                headers,
                emit,
            ): child
            for child, current_dir, previous_item in pending_items
        }
        for future in as_completed(item_futures):
            child = item_futures[future]
            name = _feishu_item_name(child)
            item_type = _feishu_item_type(child)
            token = _feishu_item_token(child)
            completed += 1
            try:
                item = future.result()
                manifest_items.append(item)
                if item.get("kind") == "document":
                    stats["documents"] += 1
                else:
                    stats["files"] += 1
                _increment_counter(
                    stats["by_extension"],
                    _manifest_item_extension(item),
                )
            except DataSourceSyncError as exc:
                stats["failed"] += 1
                _emit(
                    emit,
                    "item_failed",
                    "failed",
                    f"Failed to sync Feishu {name}.",
                    category="document",
                    token=token,
                    item_type=item_type,
                    item_name=name,
                    error=str(exc),
                )
                manifest_items.append(
                    {
                        "token": token,
                        "name": name,
                        "type": item_type,
                        "error": str(exc),
                    }
                )
            _emit(
                emit,
                "sync_progress",
                "running",
                (
                    f"Synced {completed}/{stats['changed']} changed items."
                ),
                category="summary",
                progress_total=stats["changed"],
                progress_current=completed,
                progress_percent=_progress_percent(
                    completed,
                    stats["changed"],
                ),
                summary=stats,
            )

    _finalize_feishu_missing_items(
        target,
        previous_items,
        seen_tokens,
        manifest_items,
        deleted_paths,
        stats,
        delete_missing=delete_missing,
        scan_complete=True,
    )
    _write_manifest(
        target,
        {
            "source_type": "feishu",
            "sync_mode": "drive_folder",
            "folder_token": folder_token,
            "incremental": incremental,
            "delete_missing": delete_missing,
            "synced_at": utc_timestamp(),
            "stats": stats,
            "items": manifest_items,
        },
    )
    total = stats["documents"] + stats["files"]
    total_success = total + stats["skipped"]
    if total_success == 0 and stats["failed"] > 0:
        message = (
            "LENS_SOURCE_SYNC_FAILED: all Feishu Drive items failed; "
            "see manifest.json for item errors"
        )
        _emit(emit, "manifest", "failed", message)
        raise DataSourceSyncError(message)
    _emit(
        emit,
        "manifest",
        "done",
        (
            f"Feishu folder sync completed with {total} files, "
            f"{stats['skipped']} skipped."
        ),
        category="summary",
        summary=stats,
    )
    return {
        "synced": total,
        "files": total,
        "target_path": str(target),
        "folders": stats["folders"],
        "scanned": stats["scanned"],
        "changed": stats["changed"],
        "skipped": stats["skipped"],
        "deleted": stats["deleted"],
        "failed": stats["failed"],
        "documents": stats["documents"],
        "by_extension": stats["by_extension"],
        "by_type": stats["by_type"],
        "_sync_items": [
            _manifest_item_to_sync_item(item, target)
            for item in manifest_items
        ],
        "_changed_paths": [
            _manifest_local_path(item)
            for item in manifest_items
            if item.get("status") not in {"deleted", "skipped"}
        ],
        "_deleted_paths": deleted_paths,
    }


def _sync_feishu_drive_item(
    item,
    target_dir,
    root_dir,
    previous_item,
    headers,
    emit,
    cancel_event=None,
):
    """Synchronize one Feishu Drive file item."""

    _check_feishu_cancel(cancel_event)
    token = _feishu_item_token(item)
    name = _feishu_item_name(item)
    item_type = _feishu_item_type(item)
    if _is_feishu_exportable_type(item_type):
        _emit(
            emit,
            "item_started",
            "running",
            f"Exporting Feishu {name}.",
            category="document",
            kind="document",
            token=token,
            item_type=item_type,
            item_name=name,
        )
        if cancel_event is None:
            exported = _export_feishu_document(token, item_type, headers)
        else:
            exported = _export_feishu_document(
                token,
                item_type,
                headers,
                cancel_event=cancel_event,
            )
        filename = _export_filename(exported, name or token, item_type)
        path = _feishu_target_file_path(
            target_dir,
            root_dir,
            filename,
            token,
            previous_item,
        )
        path.write_bytes(exported["content"])
        local_path = relative_path(root_dir, path)
        _emit(
            emit,
            "item_done",
            "done",
            f"Exported Feishu {name} to {local_path}.",
            category="document",
            kind="document",
            token=token,
            item_type=item_type,
            item_name=exported.get("file_name") or name or token,
            file=local_path,
            file_extension=exported.get("file_extension") or "",
        )
        return {
            "kind": "document",
            "token": token,
            "source_id": f"feishu:token:{token}",
            "source_path": name or token,
            "name": exported.get("file_name") or name or token,
            "type": item_type,
            "file": local_path,
            "local_path": local_path,
            "file_extension": exported.get("file_extension") or "",
            "metadata": _feishu_item_sync_metadata(item),
            "remote": {"token": token, "type": item_type},
        }

    _emit(
        emit,
        "item_started",
        "running",
        f"Downloading Feishu {name}.",
        category="file",
        kind="file",
        token=token,
        item_type=item_type,
        item_name=name,
    )
    filename = _safe_filename(name or token)
    _check_feishu_cancel(cancel_event)
    raw = _download_feishu_file(token, headers)
    _check_feishu_cancel(cancel_event)
    path = _feishu_target_file_path(
        target_dir,
        root_dir,
        filename,
        token,
        previous_item,
    )
    path.write_bytes(raw)
    local_path = relative_path(root_dir, path)
    _emit(
        emit,
        "item_done",
        "done",
        f"Downloaded Feishu {name} to {local_path}.",
        category="file",
        kind="file",
        token=token,
        item_type=item_type,
        item_name=name,
        file=local_path,
    )
    return {
        "kind": "file",
        "token": token,
        "source_id": f"feishu:token:{token}",
        "source_path": name or token,
        "name": name,
        "type": item_type,
        "file": local_path,
        "local_path": local_path,
        "file_extension": _feishu_item_file_extension(item),
        "metadata": _feishu_item_sync_metadata(item),
        "remote": {"token": token, "type": item_type},
    }


def _run_git(
    args,
    cwd=None,
    timeout=600,
    detail_prefix="LENS_SOURCE_SYNC_FAILED",
    env=None,
    cancel_event=None,
):
    """Run a Git command and raise a datasource error on failure."""

    if cancel_event is None:
        return _run_git_without_cancellation(
            args,
            cwd=cwd,
            timeout=timeout,
            detail_prefix=detail_prefix,
            env=env,
        )
    if cancel_event.is_set():
        raise DataSourceSyncError("LENS_SOURCE_SYNC_CANCELLED")
    try:
        process = subprocess.Popen(
            ["git", *args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=env,
        )
    except OSError as exc:
        raise DataSourceSyncError(
            f"{detail_prefix}: git command failed"
        ) from exc
    try:
        started = time.monotonic()
        while True:
            if cancel_event.is_set():
                _terminate_git_process(process)
                raise DataSourceSyncError("LENS_SOURCE_SYNC_CANCELLED")
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() - started >= timeout:
                    _terminate_git_process(process)
                    raise DataSourceSyncError(
                        f"{detail_prefix}: git command timed out"
                    )
        if process.returncode:
            error = subprocess.CalledProcessError(
                process.returncode,
                ["git", *args],
                output=stdout,
                stderr=stderr,
            )
            detail = _git_error_detail(error)
            raise DataSourceSyncError(f"{detail_prefix}: {detail}") from error
        return subprocess.CompletedProcess(
            ["git", *args],
            process.returncode,
            stdout,
            stderr,
        )
    except DataSourceSyncError:
        raise
    except OSError as exc:
        raise DataSourceSyncError(
            f"{detail_prefix}: git command failed"
        ) from exc


def _run_git_without_cancellation(args, cwd, timeout, detail_prefix, env):
    """Run Git with the legacy path when cancellation is unused."""

    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        detail = _git_error_detail(exc)
        raise DataSourceSyncError(f"{detail_prefix}: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DataSourceSyncError(
            f"{detail_prefix}: git command timed out"
        ) from exc
    except OSError as exc:
        raise DataSourceSyncError(
            f"{detail_prefix}: git command failed"
        ) from exc


def _terminate_git_process(process):
    """Terminate a cancellable Git process and its children."""

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        process.terminate()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()
        process.communicate()


def _git_error_detail(exc):
    """Return a compact Git error detail for task diagnostics."""

    stderr = (exc.stderr or "").strip()
    stdout = (exc.stdout or "").strip()
    detail = stderr or stdout or str(exc)
    detail = _redact_git_detail(detail)
    if len(detail) > 1000:
        return f"{detail[:1000]}..."
    return detail


def _git_run_options(config):
    """Return subprocess options that keep Git credentials ephemeral."""

    environment = _git_auth_environment(config)
    return {"env": environment} if environment is not None else {}


def _git_auth_environment(config):
    """Provide Git authentication through process-only configuration."""

    if not isinstance(config, dict) or config.get("auth_scheme") != "token":
        return None
    credentials = _load_credentials(config)
    token = credentials.get("token") or credentials.get("password")
    if not isinstance(token, str) or not token:
        return None
    encoded = base64.b64encode(
        f"x-access-token:{token}".encode("utf-8")
    ).decode("ascii")
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {encoded}",
        }
    )
    return environment


def _has_embedded_credentials(value):
    """Return whether a URL contains userinfo credentials."""

    parsed = parse.urlsplit(str(value or ""))
    return bool(parsed.username or parsed.password)


def _strip_url_credentials(value):
    """Remove userinfo credentials from one URL before persisting it."""

    parsed = parse.urlsplit(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return str(value or "")
    hostname = parsed.hostname or ""
    netloc = hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return parse.urlunsplit(
        (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
    )


def _redact_git_detail(value):
    """Remove URL credentials from Git diagnostics."""

    return re.sub(
        r"(https?://)[^/\s@]+@",
        r"\1***@",
        str(value or ""),
        flags=re.IGNORECASE,
    )


def _git_output(args, cwd=None):
    """Run a Git command and return stdout, or an empty string."""

    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def _git_remote_branches(output):
    """Return branch names from git ls-remote --heads output."""

    branches = []
    for line in str(output or "").splitlines():
        parts = line.strip().split()
        if len(parts) < 2 or not parts[1].startswith("refs/heads/"):
            continue
        branch = parts[1][len("refs/heads/") :]
        if branch:
            branches.append(branch)
    return branches


def _git_default_branch_from_remote(repo_url, git_options=None):
    """Return the remote HEAD branch without embedding credentials in a URL."""

    try:
        result = _run_git(
            ["ls-remote", "--symref", repo_url, "HEAD"],
            timeout=60,
            detail_prefix="LENS_SOURCE_GIT_LS_REMOTE_FAILED",
            **(git_options or {}),
        )
    except DataSourceSyncError:
        return ""
    for line in str(getattr(result, "stdout", "") or "").splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "ref:" and parts[1].startswith(
            "refs/heads/"
        ) and parts[2] == "HEAD":
            return parts[1][len("refs/heads/") :]
    return ""


def _default_git_branch(branches):
    """Return the preferred branch from a remote branch list."""

    for branch in ["main", "master"]:
        if branch in branches:
            return branch
    return branches[0] if branches else ""


def _normalize_repo_url(value):
    """Return a comparable Git remote URL without credentials."""

    parsed = parse.urlsplit(value or "")
    if parsed.scheme in {"http", "https"}:
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return parse.urlunsplit(
            (parsed.scheme, netloc, parsed.path.rstrip("/"), "", "")
        )
    return str(value or "").rstrip("/")


def _load_credentials(config):
    """Load transient datasource credentials from command config."""

    if config.get("access_token"):
        return {
            "token": config.get("access_token"),
            "username": config.get("username") or "oauth2",
        }
    if config.get("app_id") or config.get("app_secret"):
        return {
            "app_id": config.get("app_id") or "",
            "app_secret": config.get("app_secret") or "",
        }
    return {}


def _feishu_tenant_token(credentials):
    """Return an existing or newly requested Feishu tenant token."""

    if credentials.get("tenant_access_token"):
        return credentials["tenant_access_token"]
    app_id = credentials.get("app_id")
    app_secret = credentials.get("app_secret")
    if not app_id or not app_secret:
        return ""
    payload = json.dumps(
        {"app_id": app_id, "app_secret": app_secret},
        ensure_ascii=False,
    ).encode("utf-8")
    data = _http_json(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        method="POST",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    token = data.get("tenant_access_token")
    if not token:
        raise DataSourceSyncError("LENS_SOURCE_CREDENTIAL_INVALID")
    return token


def _feishu_doc_ids(config):
    """Return Feishu document IDs from explicit IDs or document URLs."""

    doc_ids = list(config.get("doc_ids") or [])
    document_url = config.get("document_url") or ""
    if document_url:
        doc_ids.extend(re.findall(r"/(?:docx|docs)/([A-Za-z0-9]+)", document_url))
    return [item for item in dict.fromkeys(doc_ids) if item]


def _feishu_folder_token(config):
    """Return a Feishu Drive folder token from explicit token or URL."""

    if config.get("folder_token"):
        return str(config["folder_token"]).strip()
    folder_url = config.get("folder_url") or ""
    patterns = [
        r"/drive/folder/([A-Za-z0-9_-]+)",
        r"/folder/([A-Za-z0-9_-]+)",
        r"[?&]folder_token=([A-Za-z0-9_-]+)",
        r"[?&]token=([A-Za-z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, folder_url)
        if match:
            return match.group(1)
    return ""


def _list_feishu_folder_children(folder_token, headers):
    """List immediate children under a Feishu Drive folder."""

    items = []
    page_token = ""
    seen_page_tokens = set()
    while True:
        query = {
            "folder_token": folder_token,
            "page_size": "100",
        }
        if page_token:
            query["page_token"] = page_token
        payload = _http_json(
            "https://open.feishu.cn/open-apis/drive/v1/files"
            f"?{parse.urlencode(query)}",
            headers=headers,
        )
        data = payload.get("data") or payload
        batch = (
            data.get("files")
            or data.get("items")
            or data.get("children")
            or []
        )
        if not isinstance(batch, list):
            raise DataSourceSyncError("FEISHU_FOLDER_RESPONSE_INVALID")
        items.extend(batch)
        if len(items) > FEISHU_MAX_SYNC_ITEMS:
            raise DataSourceSyncError("LENS_SOURCE_ITEM_LIMIT_EXCEEDED")
        next_page_token = (
            data.get("next_page_token")
            or data.get("page_token")
            or ""
        )
        if not data.get("has_more") or not next_page_token:
            break
        if next_page_token in seen_page_tokens:
            raise DataSourceSyncError("FEISHU_FOLDER_RESPONSE_INVALID")
        seen_page_tokens.add(next_page_token)
        page_token = next_page_token
    return items


def _feishu_item_token(item):
    """Return a Drive item token from common Feishu response fields."""

    return (
        item.get("token")
        or item.get("file_token")
        or item.get("doc_token")
        or item.get("obj_token")
        or item.get("id")
        or ""
    )


def _feishu_item_name(item):
    """Return a Drive item display name."""

    return item.get("name") or item.get("title") or _feishu_item_token(item)


def _feishu_item_type(item):
    """Return a normalized Drive item type."""

    value = str(
        item.get("type")
        or item.get("file_type")
        or item.get("obj_type")
        or ""
    ).lower()
    if value in {"folder", "dir", "directory"}:
        return "folder"
    return value or "file"


def _is_feishu_exportable_type(item_type):
    """Return whether a Drive item should use Feishu export tasks."""

    return item_type in {
        "doc",
        "docx",
        "docs",
        "sheet",
        "slide",
        "slides",
        "bitable",
        "base",
    }


def _feishu_export_type(item_type):
    """Return the Feishu export API type for a Drive item type."""

    if item_type in {"doc", "docx", "docs"}:
        return "docx"
    if item_type == "sheet":
        return "sheet"
    if item_type in {"slide", "slides"}:
        return "slides"
    if item_type in {"bitable", "base"}:
        return "bitable"
    return item_type or "docx"


def _feishu_export_extension(item_type):
    """Return the preferred original-format export extension."""

    export_type = _feishu_export_type(item_type)
    mapping = {
        "docx": "docx",
        "sheet": "xlsx",
        "slides": "pptx",
        "bitable": "xlsx",
    }
    return mapping.get(export_type, "docx")


def _export_feishu_document(
    file_token,
    item_type,
    headers,
    cancel_event=None,
):
    """Export one Feishu document-like item with the official Drive API."""

    _check_feishu_cancel(cancel_event)
    export_type = _feishu_export_type(item_type)
    file_extension = _feishu_export_extension(item_type)
    ticket = _create_feishu_export_task(
        file_token,
        export_type,
        file_extension,
        headers,
    )
    if cancel_event is None:
        result = _poll_feishu_export_task(
            ticket,
            file_token,
            export_type,
            headers,
        )
    else:
        result = _poll_feishu_export_task(
            ticket,
            file_token,
            export_type,
            headers,
            cancel_event=cancel_event,
        )
    status = _feishu_job_status(result)
    if status != FEISHU_EXPORT_SUCCESS_STATUS:
        detail = _feishu_export_status_detail(result)
        raise DataSourceSyncError(
            "LENS_SOURCE_EXPORT_FAILED: "
            f"token={file_token} type={export_type} "
            f"extension={file_extension} ticket={ticket} {detail}"
        )
    export_file_token = result.get("file_token")
    if not export_file_token:
        detail = _compact_json(result)
        raise DataSourceSyncError(
            "LENS_SOURCE_EXPORT_FILE_MISSING: "
            f"token={file_token} type={export_type} "
            f"extension={file_extension} ticket={ticket} result={detail}"
        )
    try:
        _check_feishu_cancel(cancel_event)
        content = _download_feishu_export_file(export_file_token, headers)
        _check_feishu_cancel(cancel_event)
    except DataSourceSyncError as exc:
        if str(exc) == "LENS_SOURCE_SYNC_CANCELLED":
            raise
        raise DataSourceSyncError(
            "LENS_SOURCE_EXPORT_DOWNLOAD_FAILED: "
            f"token={file_token} export_file_token={export_file_token} "
            f"type={export_type} extension={file_extension} error={exc}"
        ) from exc
    return {
        "content": content,
        "file_name": result.get("file_name") or file_token,
        "file_extension": result.get("file_extension") or file_extension,
        "type": result.get("type") or export_type,
        "file_token": export_file_token,
    }


def _create_feishu_export_task(file_token, file_type, file_extension, headers):
    """Create a Feishu Drive export task and return its ticket."""

    payload = {
        "token": file_token,
        "type": file_type,
        "file_extension": file_extension,
    }
    data = _http_json(
        "https://open.feishu.cn/open-apis/drive/v1/export_tasks",
        method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
    )
    ticket = (data.get("data") or {}).get("ticket") or data.get("ticket")
    if not ticket:
        detail = _compact_json(data)
        raise DataSourceSyncError(
            "LENS_SOURCE_EXPORT_TICKET_MISSING: "
            f"token={file_token} type={file_type} "
            f"extension={file_extension} response={detail}"
        )
    return ticket


def _poll_feishu_export_task(
    ticket,
    file_token,
    file_type,
    headers,
    cancel_event=None,
):
    """Poll a Feishu Drive export task until it finishes."""

    query = parse.urlencode({"token": file_token, "type": file_type})
    url = (
        "https://open.feishu.cn/open-apis/drive/v1/export_tasks/"
        f"{ticket}?{query}"
    )
    result = {}
    deadline = time.monotonic() + FEISHU_EXPORT_TIMEOUT_S
    while time.monotonic() < deadline:
        _check_feishu_cancel(cancel_event)
        data = _http_json(url, headers=headers)
        data = data.get("data") or data
        result = data.get("result") or data
        status = _feishu_job_status(result)
        if status == FEISHU_EXPORT_SUCCESS_STATUS:
            return result
        if status not in FEISHU_EXPORT_PENDING_STATUSES:
            return result
        _check_feishu_cancel(cancel_event)
        time.sleep(FEISHU_EXPORT_POLL_INTERVAL_S)
    detail = _feishu_export_status_detail(result)
    raise DataSourceSyncError(
        "LENS_SOURCE_EXPORT_TIMEOUT: "
        f"token={file_token} type={file_type} ticket={ticket} "
        f"last_result={detail}"
    )


def _feishu_job_status(result):
    """Return Feishu export job status as an integer when possible."""

    value = result.get("job_status")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _feishu_export_status_detail(result):
    """Return a readable Feishu export status detail."""

    status = _feishu_job_status(result)
    message = FEISHU_EXPORT_STATUS_MESSAGES.get(status, "unknown status")
    error_msg = str(result.get("job_error_msg") or "").strip()
    detail = (
        f"job_status={result.get('job_status')} status={message} "
        f"job_error_msg={error_msg or '-'}"
    )
    return f"{detail} result={_compact_json(result)}"


def _download_feishu_export_file(file_token, headers):
    """Download an exported Feishu Drive file."""

    url = (
        "https://open.feishu.cn/open-apis/drive/v1/export_tasks/file/"
        f"{file_token}/download"
    )
    return _http_bytes(url, headers=headers)


def _export_filename(exported, fallback_name, fallback_type):
    """Return a safe filename for an exported Feishu document."""

    file_name = exported.get("file_name") or fallback_name
    extension = exported.get("file_extension")
    extension = extension or _feishu_export_extension(fallback_type)
    stem = _safe_filename(file_name)
    if stem.lower().endswith(f".{extension.lower()}"):
        return stem
    return f"{stem}.{extension}"


def _download_feishu_file(file_token, headers):
    """Download a Feishu Drive file as bytes."""

    urls = [
        (
            "https://open.feishu.cn/open-apis/drive/v1/files/"
            f"{file_token}/download"
        ),
        (
            "https://open.feishu.cn/open-apis/drive/v1/medias/"
            f"{file_token}/download"
        ),
    ]
    last_error = None
    for url in urls:
        try:
            return _http_bytes(url, headers=headers)
        except DataSourceSyncError as exc:
            last_error = exc
            continue
    raise last_error or DataSourceSyncError("LENS_SOURCE_SYNC_FAILED")


def _feishu_item_sync_metadata(item):
    """Return metadata used to decide whether a Feishu item changed."""

    metadata = {}
    for source, target in [
        ("modified_time", "modified_time"),
        ("updated_time", "modified_time"),
        ("edit_time", "modified_time"),
        ("size", "size"),
        ("checksum", "checksum"),
        ("md5", "checksum"),
    ]:
        value = item.get(source)
        if value not in (None, ""):
            metadata[target] = str(value)
    return metadata


def _feishu_item_signature(item):
    """Return comparable Feishu metadata, or empty when unavailable."""

    metadata = _feishu_item_sync_metadata(item)
    signature = {
        key: metadata[key]
        for key in ["modified_time", "size", "checksum"]
        if metadata.get(key)
    }
    item_type = _feishu_item_type(item)
    if item_type:
        signature["type"] = item_type
    if _is_feishu_exportable_type(item_type):
        signature["file_extension"] = _feishu_export_extension(item_type)
    else:
        file_extension = _feishu_item_file_extension(item)
        if file_extension:
            signature["file_extension"] = file_extension
    if any(key != "type" for key in signature):
        return signature
    token = _feishu_item_token(item)
    name = _feishu_item_name(item)
    if token:
        signature["token"] = token
    if name:
        signature["name"] = name
    return signature if token or name else {}


def _feishu_item_file_extension(item):
    """Return a comparable file extension for a Feishu Drive item."""

    name = _feishu_item_name(item)
    extension = Path(str(name or "")).suffix.lower().lstrip(".")
    return extension or ""


def _feishu_manifest_signature(item):
    """Return comparable metadata from a manifest item."""

    metadata = item.get("metadata") or {}
    remote = item.get("remote") or {}
    signature = {
        key: str(metadata[key])
        for key in ["modified_time", "size", "checksum"]
        if metadata.get(key) not in (None, "")
    }
    item_type = remote.get("type") or item.get("type")
    if item_type:
        signature["type"] = item_type
    if item.get("file_extension"):
        signature["file_extension"] = item.get("file_extension")
    if any(key != "type" for key in signature):
        return signature
    token = item.get("token") or remote.get("token")
    name = item.get("name") or item.get("source_path")
    if token:
        signature["token"] = token
    if name:
        signature["name"] = name
    return signature if token or name else {}


def _feishu_item_unchanged(item, previous_item, target_dir, root_dir=None):
    """Return whether a remote Feishu item can be skipped."""

    if not previous_item or previous_item.get("status") == "deleted":
        return False
    signature = _feishu_item_signature(item)
    previous_signature = _feishu_manifest_signature(previous_item)
    if not signature or signature != previous_signature:
        return False
    previous_file = _manifest_local_path(previous_item)
    if not previous_file:
        return False
    paths = _feishu_previous_file_paths(previous_file, target_dir, root_dir)
    return any(path.exists() for path in paths)


def _feishu_previous_file_paths(previous_file, target_dir, root_dir=None):
    """Return candidate local paths for a previous Feishu manifest item."""

    previous_path = Path(str(previous_file))
    if previous_path.is_absolute():
        return [previous_path]
    paths = []
    if root_dir is not None:
        paths.append(Path(root_dir) / previous_path)
    paths.append(Path(target_dir) / previous_path.name)
    paths.append(Path(target_dir) / previous_path)
    return list(dict.fromkeys(paths))


def _feishu_target_file_path(
    target_dir,
    root_dir,
    filename,
    token,
    previous_item=None,
):
    """Return a stable local path for a Feishu file download."""

    previous_file = _manifest_local_path(previous_item or {})
    if previous_file:
        for path in _feishu_previous_file_paths(
            previous_file,
            target_dir,
            root_dir,
        ):
            try:
                path.resolve().relative_to(Path(root_dir).resolve())
            except ValueError:
                continue
            if path.exists():
                return path
    path = unique_child_path(target_dir, filename, token)
    stable_path = target_dir / _feishu_stable_filename(filename, token)
    if stable_path.exists():
        return stable_path
    return path


def _feishu_stable_filename(filename, token):
    """Return the stable conflict filename for one Feishu item."""

    path = Path(filename)
    suffix = stable_suffix(token or filename)
    stem = path.stem or "document"
    return f"{stem}__{suffix}{path.suffix}"


def _feishu_manifest_item_from_previous(
    item,
    previous_item,
    target_dir,
    root_dir=None,
):
    """Return manifest metadata for an unchanged Feishu item."""

    token = _feishu_item_token(item)
    name = _feishu_item_name(item)
    item_type = _feishu_item_type(item)
    previous_file = _manifest_local_path(previous_item)
    return {
        **previous_item,
        "kind": previous_item.get("kind") or "document",
        "token": token,
        "source_id": f"feishu:token:{token}",
        "source_path": previous_item.get("source_path") or name,
        "name": name,
        "type": item_type,
        "file": previous_file,
        "local_path": previous_file,
        "metadata": _feishu_item_sync_metadata(item),
        "remote": {"token": token, "type": item_type},
        "status": "skipped",
        "missing_scans": 0,
    }


def _manifest_items_by_token(manifest):
    """Return manifest items keyed by token."""

    return manifest_store.manifest_items_by_token(manifest)


def _manifest_local_path(item):
    """Return current or legacy local path for one manifest item."""

    return item.get("local_path") or item.get("file") or ""


def _manifest_item_to_sync_item(item, target):
    """Return a unified sync item from current or legacy manifest data."""

    local_path = _manifest_local_path(item)
    token = item.get("token") or (item.get("remote") or {}).get("token") or ""
    source_id = item.get("source_id") or (
        f"feishu:token:{token}" if token else local_path
    )
    path = Path(local_path)
    extension = item.get("file_extension") or item.get("extension")
    extension = extension or path.suffix.lower().lstrip(".")
    return manifest_store.SyncItem(
        source_id=source_id,
        source_type=item.get("source_type") or "feishu",
        source_path=item.get("source_path") or item.get("name") or local_path,
        local_path=local_path,
        name=item.get("name") or path.name,
        kind=item.get("kind") or item.get("type") or "file",
        extension=extension,
        status=item.get("status") or "synced",
        metadata=item.get("metadata") or {},
        remote=item.get("remote") or {"token": token, "type": item.get("type")},
        missing_scans=int(item.get("missing_scans") or 0),
    )


def _increment_counter(counter, key):
    """Increment a normalized counter key."""

    key = str(key or "unknown").strip().lower() or "unknown"
    counter[key] = int(counter.get(key) or 0) + 1


def _manifest_item_extension(item):
    """Return a normalized file extension for a manifest item."""

    extension = str(
        item.get("file_extension") or item.get("extension") or ""
    ).strip().lower()
    if extension:
        return extension.lstrip(".") or "unknown"
    file_name = str(item.get("file") or item.get("name") or "")
    suffix = Path(file_name).suffix.lower().lstrip(".")
    return suffix or str(item.get("type") or "unknown").lower()


def _progress_percent(current, total):
    """Return bounded integer progress percentage."""

    try:
        current_value = int(current or 0)
        total_value = int(total or 0)
    except (TypeError, ValueError):
        return 0
    if total_value <= 0:
        return 100
    return max(0, min(100, int(current_value * 100 / total_value)))


def _delete_manifest_file(target, file_name):
    """Delete a file listed in manifest if it is under target."""

    try:
        path = (target / str(file_name)).resolve()
        path.relative_to(target.resolve())
    except ValueError:
        return
    if path.is_file():
        path.unlink()


def _http_json(url, method="GET", data=None, headers=None):
    """Request a JSON endpoint and return the decoded object."""

    req = request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with _urlopen_with_retries(req, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = _http_error_detail(exc)
        raise DataSourceSyncError(detail) from exc
    except Exception as exc:
        raise DataSourceSyncError(
            f"LENS_SOURCE_SYNC_FAILED: {type(exc).__name__}: {exc}"
        ) from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DataSourceSyncError("LENS_SOURCE_RESPONSE_INVALID") from exc
    _raise_feishu_business_error(payload)
    return payload


def _http_bytes(url, headers=None):
    """Request a binary endpoint and return bytes."""

    req = request.Request(url, headers=headers or {}, method="GET")
    try:
        with _urlopen_with_retries(req, timeout=120) as response:
            return response.read()
    except error.HTTPError as exc:
        detail = _http_error_detail(exc)
        raise DataSourceSyncError(detail) from exc
    except Exception as exc:
        raise DataSourceSyncError(
            f"LENS_SOURCE_SYNC_FAILED: {type(exc).__name__}: {exc}"
        ) from exc


def _urlopen_with_retries(req, timeout, attempts=3):
    """Open a URL with short retries for transient network failures."""

    last_error = None
    for attempt in range(attempts):
        try:
            return request.urlopen(req, timeout=timeout)
        except error.HTTPError:
            raise
        except error.URLError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                raise
            time.sleep(1)
    raise last_error


def _http_error_detail(exc):
    """Return a compact HTTP error detail, including JSON body if present."""

    try:
        raw = exc.read().decode("utf-8")
    except Exception:
        raw = ""
    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        code = payload.get("code") or payload.get("error")
        msg = payload.get("msg") or payload.get("message")
        if code or msg:
            return f"HTTP_{exc.code}: {code or ''} {msg or ''}".strip()
        return f"HTTP_{exc.code}: {raw[:300]}"
    return f"HTTP_{exc.code}: {exc.reason}"


def _raise_feishu_business_error(payload):
    """Raise when Feishu returns a JSON business error with HTTP 200."""

    code = payload.get("code")
    if code in (None, 0):
        return
    msg = payload.get("msg") or payload.get("message") or "Feishu API error"
    raise DataSourceSyncError(f"FEISHU_API_ERROR: {code} {msg}")


def _safe_filename(value):
    """Return a filesystem-safe filename stem."""

    return safe_filename(value)


def _compact_json(value):
    """Return compact JSON for diagnostics without oversized messages."""

    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        raw = str(value)
    if len(raw) > 1000:
        return f"{raw[:1000]}..."
    return raw


def _read_manifest(target):
    """Read datasource sync manifest if it exists."""

    return manifest_store.read_manifest(target)


def _write_manifest(target, payload):
    """Write datasource sync manifest."""

    manifest_store.write_manifest(target, payload)


def _count_files(path):
    """Return the number of non-git files under a path."""

    count = 0
    for item in path.rglob("*"):
        if _is_generated_datasource_path(path, item):
            continue
        if item.is_file():
            count += 1
    return count


def _count_file_extensions(path):
    """Return non-git file counts grouped by extension."""

    counts = {}
    for item in path.rglob("*"):
        if _is_generated_datasource_path(path, item) or not item.is_file():
            continue
        _increment_counter(counts, item.suffix.lower().lstrip("."))
    return counts


def _is_generated_datasource_path(root, path):
    """Return whether a path is generated datasource metadata."""

    try:
        parts = Path(path).relative_to(root).parts
    except ValueError:
        parts = Path(path).parts
    if ".git" in parts:
        return True
    if any(part.endswith(".sourcelens") for part in parts):
        return True
    return Path(path).name in {
        "manifest.json",
        "manifest.json.tmp",
        ".sourcelens-datasource.json",
    }


def _emit(emit, step, status, message, **extra):
    """Emit a datasource sync progress event."""

    if emit is not None:
        payload = {
            "step": step,
            "status": status,
            "message": message,
            "timestamp": utc_timestamp(),
        }
        payload.update(extra)
        emit(payload)
