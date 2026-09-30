"""Authorized full-text retrieval with source verification before disclosure."""

from .config import MAX_RESULTS, IndexUnavailable, index_key
from .documents import authorized_scopes, read_document
from .store import search_index


async def search(settings, target_dirs, policy, query, limit=8):
    """Retrieve only allowed paths and verify the content behind each text hit."""

    query = str(query).strip()
    if not query or len(query) > 2000:
        raise IndexUnavailable("TEXT_INDEX_QUERY_INVALID")
    limit = min(MAX_RESULTS, max(1, int(limit)))
    scopes = authorized_scopes(settings, target_dirs, policy)
    if not scopes:
        raise IndexUnavailable("TEXT_INDEX_SCOPE_UNAVAILABLE")
    matches = []
    checked = {}
    for root, datasource_uuid, allowed, _entry in scopes:
        index_path = settings.state_path / index_key(root, datasource_uuid) / "index.sqlite3"
        rows = search_index(index_path, allowed, query, settings.profile, limit)
        for position, row in enumerate(rows, start=1):
            relative = row["path"]
            cache_key = (str(root), relative)
            if cache_key not in checked:
                checked[cache_key] = read_document(root, allowed[relative])
            document = checked[cache_key]
            if (
                document is None
                or document.source_hash != row["source_hash"]
                or document.text_hash != row["text_hash"]
                or document.source_id != row["source_id"]
                or row["text"] not in document.text
            ):
                raise IndexUnavailable("TEXT_INDEX_STALE")
            matches.append(
                {
                    "path": str(root / relative),
                    "relative_path": relative,
                    "datasource_uuid": datasource_uuid,
                    "line": row["start_line"],
                    "end_line": row["end_line"],
                    "text": row["text"],
                    "before": [],
                    "after": [],
                    "rank_score": 1.0 / (60 + position),
                    "generation": row["generation"],
                    "position_kind": "converted_text" if document.converted else "source_text",
                }
            )
    matches.sort(key=lambda row: (-row["rank_score"], row["path"], row["line"]))
    selected = []
    seen = set()
    size = 0
    for row in matches:
        identity = (row["path"], row["line"], row["text"])
        if identity in seen:
            continue
        cost = len(row["text"]) + 500
        if size + cost > 12000 or len(selected) >= limit:
            break
        seen.add(identity)
        selected.append(row)
        size += cost
    return {"mode": "indexed", "matches": selected, "profile": settings.profile}
