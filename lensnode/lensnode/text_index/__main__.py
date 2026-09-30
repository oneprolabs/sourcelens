"""Explicit indexing CLI and private query subprocess protocol."""

import argparse
import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from .config import IndexUnavailable, TextIndexSettings


@contextlib.contextmanager
def library_output_to_stderr():
    """Redirect Python and native library output away from the JSON channel."""

    sys.stdout.flush()
    saved_stdout = os.dup(1)
    try:
        os.dup2(2, 1)
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        os.dup2(saved_stdout, 1)
        os.close(saved_stdout)


async def execute(args):
    """Run optional imports only after explicit indexing or query invocation."""

    settings = TextIndexSettings.from_env()
    if args.action == "index":
        from .pipeline import build_index

        return await build_index(
            settings,
            Path(args.root),
            str(UUID(args.datasource)),
            full_reprocess=args.full_reprocess,
        )
    from .query import search

    payload = sys.stdin.read(256 * 1024 + 1)
    if len(payload) > 256 * 1024:
        raise IndexUnavailable("TEXT_INDEX_SCOPE_LIMIT")
    request = json.loads(payload)
    return await search(
        settings,
        request["target_dirs"],
        request.get("policy") or {},
        request["query"],
        request.get("limit", 8),
    )


def main():
    """Keep credentials, exceptions, and library output out of tool results."""

    parser = argparse.ArgumentParser(description="SourceLens optional local text index worker")
    subparsers = parser.add_subparsers(dest="action", required=True)
    index = subparsers.add_parser("index", help="Build and atomically publish one manifest-backed datasource")
    index.add_argument("--root", required=True)
    index.add_argument("--datasource", required=True)
    index.add_argument("--full-reprocess", action="store_true")
    subparsers.add_parser("query", help="Read a trusted query request from stdin")
    args = parser.parse_args()
    try:
        with library_output_to_stderr():
            result = asyncio.run(execute(args))
    except IndexUnavailable as exc:
        result = {"error": str(exc)}
    except Exception as exc:
        result = {"error": "TEXT_INDEX_WORKER_FAILED", "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
