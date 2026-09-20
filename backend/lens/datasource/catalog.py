"""Read file catalogs from independent datasource child storage."""

import json
from pathlib import Path, PurePosixPath

from django.conf import settings


def _read_json(path, root):
    """Read optional metadata without following paths outside its root."""

    if not path.resolve().is_relative_to(root):
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        from .services import DataSourcePathError

        raise DataSourcePathError("DATASOURCE_CATALOG_UNREADABLE") from exc
    return value if isinstance(value, dict) else {}


def _is_hidden_catalog_path(relative_path):
    """Return whether a catalog path is hidden or a macOS archive artifact."""

    return any(
        part.startswith(".") or part == "__MACOSX"
        for part in PurePosixPath(relative_path).parts
    )


def list_stored_datasource_files(datasource, page=1, page_size=20, **filters):
    """Return manifest entries without requiring a node or target path."""

    from .services import DataSourcePathError

    storage = Path(settings.MEDIA_ROOT).resolve()
    entries = []
    items = list(datasource.items.filter(status="active").order_by("uuid"))
    for item in items:
        key = PurePosixPath(item.storage_key)
        if not item.storage_key or key.is_absolute() or ".." in key.parts:
            raise DataSourcePathError("DATASOURCE_STORAGE_PATH_INVALID")
        root = (storage / key).resolve()
        if root == storage or not root.is_relative_to(storage):
            raise DataSourcePathError("DATASOURCE_STORAGE_PATH_INVALID")
        manifest = _read_json(root / "manifest.json", root)
        for record in manifest.get("items") or manifest.get("documents") or []:
            relative = record.get("local_path") or record.get("file")
            if not relative:
                continue
            if _is_hidden_catalog_path(relative):
                continue
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts:
                continue
            source = root / path
            if not source.resolve().is_relative_to(root):
                continue
            metadata = _read_json(
                Path(f"{source}.sourcelens") / "meta.json", root
            )
            conversion = metadata.get("conversion") or {}
            sync_status = str(record.get("status") or "synced").lower()
            if sync_status in {"cataloged", "skipped"}:
                sync_status = "synced"
            display_path = path.as_posix()
            if len(items) > 1:
                display_path = f"{item.uuid}/{display_path}"
            entries.append({
                "type": "file",
                "path": display_path,
                "name": str(record.get("name") or path.name),
                "extension": str(
                    record.get("extension") or record.get("file_extension")
                    or path.suffix.lstrip(".")
                ).lower(),
                "sync_status": sync_status,
                "conversion_status": conversion.get("status", "not_converted"),
                "source_updated_at": (record.get("metadata") or {}).get(
                    "modified_time", ""
                ),
                "converted_at": conversion.get("generated_at", ""),
                "conversion_error": conversion.get("error", ""),
            })
    query = str(filters.get("query") or "").strip().lower()
    sync_status = str(filters.get("sync_status") or "").strip().lower()
    conversion_status = str(
        filters.get("conversion_status") or ""
    ).strip().lower()
    if query or sync_status or conversion_status:
        entries = [
            entry for entry in entries
            if query in entry["path"].lower()
            and all(
                not value or entry[key].lower() == value
                for key, value in (
                    ("sync_status", sync_status),
                    ("conversion_status", conversion_status),
                )
            )
        ]
        entries.sort(key=lambda entry: entry["path"].lower())
        start = (page - 1) * page_size
        return {
            "count": len(entries), "page": page, "page_size": page_size,
            "results": entries[start:start + page_size],
        }

    directory = _normalize_directory(filters.get("directory"))
    prefix = f"{directory}/" if directory else ""
    directories = {}
    files = []
    for entry in entries:
        path = entry["path"]
        if prefix and not path.startswith(prefix):
            continue
        remainder = path[len(prefix):]
        if not remainder:
            continue
        head, separator, _rest = remainder.partition("/")
        if separator:
            directories.setdefault(head, _directory_entry(prefix + head, head))
            continue
        files.append(entry)
    candidates = sorted(
        directories.values(), key=lambda entry: entry["name"].lower()
    )
    candidates.extend(sorted(files, key=lambda entry: entry["path"].lower()))
    start = (page - 1) * page_size
    return {
        "count": len(candidates), "page": page, "page_size": page_size,
        "results": candidates[start:start + page_size],
    }


def _normalize_directory(value):
    """Return a safe datasource-relative directory path."""

    parts = [
        part for part in str(value or "").strip().replace("\\", "/").split("/")
        if part not in {"", "."}
    ]
    if any(part == ".." for part in parts):
        from .services import DataSourcePathError

        raise DataSourcePathError("DATASOURCE_FILE_QUERY_INVALID")
    directory = "/".join(parts)
    if directory and _is_hidden_catalog_path(directory):
        return ""
    return directory


def _directory_entry(relative_path, name):
    """Return a catalog entry describing one subdirectory."""

    return {
        "type": "directory",
        "path": relative_path,
        "name": name,
        "extension": "",
        "sync_status": "",
        "conversion_status": "",
        "source_updated_at": "",
        "converted_at": "",
        "conversion_error": "",
    }
