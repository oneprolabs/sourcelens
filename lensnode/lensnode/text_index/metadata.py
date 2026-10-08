"""Describe processed datasource retrieval capabilities without model inference."""

import sqlite3
from pathlib import Path

from .config import IndexUnavailable, TextIndexSettings, index_directory
from .documents import collect_documents, generation_for, read_manifest, source_revisions
from .store import connect_readonly, validate_source_revisions


def retrieval_metadata(root, datasource_uuid):
    """Report verified local index state and conservative tool recommendations."""

    root = Path(root)
    settings = TextIndexSettings(root.parent)
    result = {
        "schema_version": 1,
        "analysis_status": "complete",
        "text_documents": 0,
        "text_bytes": 0,
        "index": {"status": "unavailable"},
        "recommendation": {
            "default_tool": "search_workspace",
            "exact_tool": "search_workspace",
            "structural_tool": "codegraph",
            "basis": "verified_index_state",
            "performance": "not_measured",
        },
    }
    try:
        documents = collect_documents(root, datasource_uuid)
        result["text_documents"] = len(documents)
        result["text_bytes"] = sum(len(document.text.encode("utf-8")) for document in documents)
        generation = generation_for(documents, settings.profile)
        result["source_generation"] = generation
        path = index_directory(root, datasource_uuid) / "index.sqlite3"
        if not path.exists():
            return result
        connection = connect_readonly(path)
        try:
            metadata = connection.execute("SELECT generation, profile FROM metadata").fetchone()
            try:
                validate_source_revisions(connection, source_revisions(root, read_manifest(root, datasource_uuid)[1]))
            except IndexUnavailable:
                result["index"]["status"] = "stale"
                return result
        finally:
            connection.close()
        if metadata is None or metadata["generation"] != generation or metadata["profile"] != settings.profile:
            result["index"]["status"] = "stale"
            return result
        result["index"] = {"status": "ready", "generation": generation}
        result["recommendation"]["ranked_tool"] = "search_indexed_workspace"
    except (IndexUnavailable, OSError, ValueError) as exc:
        result["analysis_status"] = "incomplete"
        result["index"]["status"] = "unverified"
        result["analysis_reason"] = str(exc) if isinstance(exc, IndexUnavailable) else "TEXT_INDEX_ANALYSIS_FAILED"
    except sqlite3.Error:
        # Corrupt index schemas must not turn a completed datasource sync into a failure.
        result["analysis_status"] = "incomplete"
        result["index"]["status"] = "unverified"
        result["analysis_reason"] = "TEXT_INDEX_ANALYSIS_FAILED"
    return result
