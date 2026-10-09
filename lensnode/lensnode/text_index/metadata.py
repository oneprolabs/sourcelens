"""Describe file-directory and document-tree readiness after datasource processing."""

import os
from pathlib import Path

from .config import IndexUnavailable, TextIndexSettings, digest, index_directory
from .documents import read_manifest
from .pipeline import current_revisions, load_catalog


def retrieval_metadata(root, datasource_uuid, *, settings=None):
    """Report bounded directory facts without reading or persisting document bodies."""

    root = Path(root)
    settings = settings or TextIndexSettings(root.parent, index_model=os.getenv("LENSNODE_PAGEINDEX_MODEL", "").strip())
    result = {
        "schema_version": 2,
        "analysis_status": "complete",
        "text_documents": 0,
        "text_bytes": 0,
        "index": {"status": "unavailable", "engine": "pageindex"},
        "recommendation": {
            "default_tool": "search_workspace",
            "exact_tool": "search_workspace",
            "structural_tool": "codegraph",
            "basis": "verified_navigation_state",
            "performance": "not_measured",
        },
    }
    try:
        _, items = read_manifest(root, datasource_uuid)
        revisions = current_revisions(root, items)
        result["source_generation"] = digest([settings.profile, revisions])
        try:
            catalog = load_catalog(index_directory(root, datasource_uuid))
        except IndexUnavailable as exc:
            if str(exc) == "TEXT_INDEX_UNAVAILABLE":
                return result
            raise
        documents = catalog["documents"]
        status = (
            "ready"
            if catalog["profile"] == settings.profile
            and revisions == {path: row["revision"] for path, row in documents.items()}
            else "stale"
        )
        result["text_documents"] = sum(row["status"] == "ready" for row in documents.values())
        result["text_bytes"] = sum(row["bytes"] for row in documents.values() if row["status"] == "ready")
        result["index"] = {
            "status": status,
            "engine": "pageindex",
            "generation": catalog["generation"],
            "catalog_files": len(documents),
            "document_trees": sum(bool(row["sections"]) for row in documents.values()),
            "pdf_trees": sum(row["kind"] == "pdf" and row["status"] == "ready" for row in documents.values()),
            "failed_files": sum(row["status"] == "failed" for row in documents.values()),
        }
        if status == "ready":
            result["recommendation"]["navigation_tool"] = "search_indexed_workspace"
    except (IndexUnavailable, OSError, ValueError, KeyError, TypeError) as exc:
        result["analysis_status"] = "incomplete"
        result["index"]["status"] = "unverified"
        result["analysis_reason"] = str(exc) if isinstance(exc, IndexUnavailable) else "TEXT_INDEX_ANALYSIS_FAILED"
    return result
