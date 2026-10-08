"""CocoIndex v1 incrementally prepares text; SourceLens publishes local FTS."""

import asyncio
import fcntl
import json
from pathlib import Path

import cocoindex as coco
from cocoindex.connectors import localfs
from cocoindex.ops.text import RecursiveSplitter, detect_code_language

from .config import IndexUnavailable, digest, index_directory
from .documents import Document, collect_documents, generation_for
from .store import publish


@coco.fn(memo=True)
def process_document(document: Document, target: localfs.DirTarget, chunk_size: int, chunk_overlap: int) -> None:
    """Keep one file's prepared text ownership stable across updates."""

    language = "markdown" if document.converted else detect_code_language(filename=document.path)
    chunks = RecursiveSplitter().split(
        document.text,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        language=language,
    )
    prepared = {
        "path": document.path,
        "source_id": document.source_id,
        "source_hash": document.source_hash,
        "text_hash": document.text_hash,
        "converted": document.converted,
        "chunks": [
            {"text": chunk.text, "start_line": chunk.start.line, "end_line": chunk.end.line} for chunk in chunks
        ],
    }
    target.declare_file(filename=digest(document.path) + ".json", content=json.dumps(prepared, ensure_ascii=False))


@coco.fn
async def index_documents(documents: list[Document], output_dir: Path, chunk_size: int, chunk_overlap: int) -> None:
    """Declare the complete retained corpus, including unchanged files."""

    target = await localfs.mount_dir_target(output_dir)
    await coco.mount_each(
        process_document,
        {doc.path: doc for doc in documents}.items(),
        target,
        chunk_size,
        chunk_overlap,
    )


async def build_index(settings, root, datasource_uuid, *, full_reprocess=False):
    """Build datasource-local state and publish only a complete, stable batch."""

    root = Path(root).resolve(strict=True)
    if root == settings.workspace_path or not root.is_relative_to(settings.workspace_path):
        raise IndexUnavailable("TEXT_INDEX_SOURCE_PATH_INVALID")
    index_dir = index_directory(root, datasource_uuid)
    state_dir = index_dir / settings.profile
    if any(
        path.is_symlink()
        for path in (
            state_dir,
            state_dir / "prepared",
            state_dir / "cocoindex.db",
            index_dir / "writer.lock",
            index_dir / "index.sqlite3",
        )
    ):
        raise IndexUnavailable("TEXT_INDEX_SOURCE_PATH_INVALID")
    state_dir.mkdir(parents=True, exist_ok=True)
    (index_dir.parent / ".gitignore").write_text("*\n!.gitignore\n", encoding="utf-8")
    with (index_dir / "writer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise IndexUnavailable("TEXT_INDEX_BUSY") from None
        documents = collect_documents(root, datasource_uuid)
        generation = generation_for(documents, settings.profile)
        errors = []

        def on_error(exc, _context):
            """Background failures veto publication without leaking source text."""

            errors.append(type(exc).__name__)

        environment = coco.Environment(
            coco.Settings(db_path=state_dir / "cocoindex.db"),
            event_loop=asyncio.get_running_loop(),
            exception_handler=on_error,
        )
        app = coco.App(
            coco.AppConfig(name="SourceLensTextV1", environment=environment, max_inflight_components=4),
            index_documents,
            documents=documents,
            output_dir=state_dir / "prepared",
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )
        handle = app.update(full_reprocess=full_reprocess)
        await handle
        stats = handle.stats()
        if errors or stats is None or stats.total.num_errors:
            raise IndexUnavailable("TEXT_INDEX_INCOMPLETE")
        if generation_for(collect_documents(root, datasource_uuid), settings.profile) != generation:
            raise IndexUnavailable("TEXT_INDEX_SOURCE_CHANGED")
        counts = publish(index_dir, state_dir / "prepared", documents, generation, settings.profile)
        return {
            "status": "ready",
            "generation": generation,
            "files": len(documents),
            "profile": settings.profile,
            **counts,
        }
