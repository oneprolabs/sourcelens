"""Authorized navigation over file names, heading trees, and bounded summaries."""

import json
import re
from pathlib import Path

from ..workspace import is_path_allowed, target_scope
from .config import MAX_RESULTS, IndexUnavailable, index_directory
from .documents import authorized_scopes, safe_file
from .pipeline import current_revisions, load_catalog


async def search(settings, target_dirs, policy, query, limit=8, offset=0, path="", section_offset=0):
    """Return navigation hints; the Agent reasons over them and reads original files."""

    query = str(query).strip()
    if len(query) > 2000:
        raise IndexUnavailable("TEXT_INDEX_QUERY_INVALID")
    limit = min(MAX_RESULTS, max(1, int(limit)))
    offset = min(100000, max(0, int(offset)))
    section_offset = min(512, max(0, int(section_offset)))
    requested_path = str(path)[:2000]
    terms = re.findall(r"[\w.-]+", query.lower())
    matches = []
    permissions = {}
    seen = set()
    for root, identity, allowed, entry in authorized_scopes(settings, target_dirs, policy):
        catalog = load_catalog(index_directory(root, identity))
        revisions = current_revisions(root, allowed.values())
        stored = catalog["documents"]
        if catalog["profile"] != settings.profile or any(
            path not in stored or stored[path].get("revision") != value for path, value in revisions.items()
        ):
            raise IndexUnavailable("TEXT_INDEX_STALE")
        for relative in revisions:
            path = root / relative
            if str(path) in seen:
                continue
            seen.add(str(path))
            public_name = str(entry.get("name") or Path(entry["path"]).name)
            public_path = f"{public_name}/{path.relative_to(Path(entry['path']).resolve()).as_posix()}"
            if requested_path and requested_path not in {relative, public_path}:
                continue
            permissions[str(path)] = (root, relative, entry)
            document = stored[relative]
            sections = document["sections"]
            section_scores = [
                sum(term in (s["title"] + " " + s.get("summary", "")).lower() for term in terms) for s in sections
            ]
            file_score = sum(term in (relative + " " + document["title"]).lower() for term in terms)
            score = file_score + max(section_scores, default=0)
            if terms and not score:
                continue
            best = sections[section_scores.index(max(section_scores))] if sections else {}
            preview = (
                sorted(zip(section_scores, sections), key=lambda pair: -pair[0])
                if terms
                else list(zip(section_scores, sections))
            )
            matches.append(
                {
                    "path": str(path),
                    "relative_path": relative,
                    "datasource_uuid": identity,
                    "title": document["title"],
                    "text": best.get("title", document["title"]),
                    "line": best.get("line", 1),
                    "page": best.get("page"),
                    "end_page": best.get("end_page"),
                    "sections": [section for _, section in preview[section_offset : section_offset + 20]],
                    "section_count": len(sections),
                    "next_section_offset": section_offset + 20 if section_offset + 20 < len(sections) else None,
                    "sections_truncated": len(sections) > section_offset + 20,
                    "status": document["status"],
                    "rank_score": score,
                    "generation": catalog["generation"],
                    "evidence": False,
                    "position_kind": "navigation",
                }
            )
    matches.sort(key=lambda row: (-row["rank_score"], row["path"]))
    authorized = []
    for row in matches:
        root, relative, entry = permissions[row["path"]]
        source = safe_file(root, relative)
        if is_path_allowed(Path(entry["path"]).resolve(), source, target_scope(entry), policy):
            authorized.append(row)
    selected = []
    size = 0
    for row in authorized[offset : offset + limit]:
        cost = len(json.dumps(row, ensure_ascii=False))
        if size + cost > 24000:
            break
        selected.append(row)
        size += cost
    return {
        "mode": "navigation",
        "engine": "pageindex",
        "matches": selected,
        "total_matches": len(authorized),
        "next_offset": offset + len(selected) if offset + len(selected) < len(authorized) else None,
        "guidance": "Titles and summaries are navigation hints, not evidence. Reason over the tree, then read original files. Use search_workspace if titles miss the topic.",
    }
