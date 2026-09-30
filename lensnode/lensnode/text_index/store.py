"""Incremental local SQLite FTS5 publication; no vector representations."""

import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path

from .config import IndexUnavailable, digest

SCHEMA = """
CREATE TABLE metadata (generation TEXT NOT NULL, profile TEXT NOT NULL);
CREATE TABLE documents (path TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    text TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    converted INTEGER NOT NULL
);
CREATE INDEX chunks_path ON chunks(path);
CREATE VIRTUAL TABLE chunks_fts USING fts5(text, tokenize='unicode61');
"""
CJK = re.compile(r"[\u3400-\u9fff]+")
WORDS = re.compile(r"[^\W_]+", re.UNICODE)


def terms(text):
    """Tokenize words and CJK bigrams without an embedding model."""

    result = []
    for word in WORDS.findall(text.casefold()):
        if CJK.fullmatch(word) and len(word) > 2:
            result.extend(word[index : index + 2] for index in range(len(word) - 1))
        else:
            result.append(word)
    return list(dict.fromkeys(result))


def connect_readonly(path):
    """Open a published immutable inode without creating a missing index."""

    if not path.is_file() or path.is_symlink():
        raise IndexUnavailable("TEXT_INDEX_UNAVAILABLE")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def publish(index_dir, staging_dir, documents, generation, profile):
    """Update a private copy and atomically expose the complete FTS generation."""

    published = index_dir / "index.sqlite3"
    descriptor, filename = tempfile.mkstemp(prefix="publish-", suffix=".sqlite3", dir=index_dir)
    os.close(descriptor)
    temporary = Path(filename)
    changed = 0
    removed = 0
    connection = sqlite3.connect(temporary)
    try:
        if published.exists():
            source = connect_readonly(published)
            try:
                source.backup(connection)
            finally:
                source.close()
        else:
            connection.executescript(SCHEMA)
        previous = dict(connection.execute("SELECT path, fingerprint FROM documents"))
        with connection:
            retained = {doc.path for doc in documents}
            for path in previous.keys() - retained:
                _delete_document(connection, path)
                removed += 1
            for doc in documents:
                prepared = json.loads((staging_dir / (digest(doc.path) + ".json")).read_text())
                fingerprint = digest(prepared)
                if prepared["source_hash"] != doc.source_hash or prepared["text_hash"] != doc.text_hash:
                    raise IndexUnavailable("TEXT_INDEX_STAGING_STALE")
                if previous.get(doc.path) == fingerprint:
                    continue
                _delete_document(connection, doc.path)
                connection.execute("INSERT INTO documents VALUES (?, ?)", (doc.path, fingerprint))
                for chunk in prepared["chunks"]:
                    cursor = connection.execute(
                        "INSERT INTO chunks (path, source_id, source_hash, text_hash, text, start_line, end_line, converted) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            doc.path,
                            doc.source_id,
                            doc.source_hash,
                            doc.text_hash,
                            chunk["text"],
                            chunk["start_line"],
                            chunk["end_line"],
                            int(doc.converted),
                        ),
                    )
                    connection.execute(
                        "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)",
                        (cursor.lastrowid, " ".join(terms(chunk["text"]))),
                    )
                changed += 1
            connection.execute("DELETE FROM metadata")
            connection.execute("INSERT INTO metadata VALUES (?, ?)", (generation, profile))
        connection.close()
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, published)
        return {"changed_files": changed, "deleted_files": removed}
    finally:
        connection.close()
        temporary.unlink(missing_ok=True)


def _delete_document(connection, path):
    """Keep FTS row IDs and source records consistent in the same transaction."""

    connection.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE path = ?)", (path,))
    connection.execute("DELETE FROM chunks WHERE path = ?", (path,))
    connection.execute("DELETE FROM documents WHERE path = ?", (path,))


def search_index(path, allowed_paths, query, profile, limit, *, path_allowed=None):
    """Apply the trusted file allowlist before exposing matching chunk text."""

    tokens = [token[:128] for token in terms(query)[:20]]
    if not tokens or not allowed_paths:
        return []
    expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
    connection = connect_readonly(path)
    try:
        meta = connection.execute("SELECT generation, profile FROM metadata").fetchone()
        if meta is None or meta["profile"] != profile:
            raise IndexUnavailable("TEXT_INDEX_PROFILE_STALE")
        placeholders = ",".join("?" for _ in allowed_paths)
        candidates = connection.execute(
            "SELECT chunks.id, chunks.path, bm25(chunks_fts) AS rank FROM chunks_fts "
            "JOIN chunks ON chunks.id = chunks_fts.rowid "
            f"WHERE chunks_fts MATCH ? AND chunks.path IN ({placeholders}) "
            "ORDER BY rank, chunks.path, chunks.start_line",
            (expression, *allowed_paths),
        )
        rows = []
        for candidate in candidates:
            if path_allowed is not None and not path_allowed(candidate["path"]):
                continue
            row = connection.execute("SELECT * FROM chunks WHERE id = ?", (candidate["id"],)).fetchone()
            rows.append({**dict(row), "rank": candidate["rank"], "generation": meta["generation"]})
            if len(rows) >= limit:
                break
        return rows
    except sqlite3.Error:
        raise IndexUnavailable("TEXT_INDEX_UNAVAILABLE") from None
    finally:
        connection.close()
