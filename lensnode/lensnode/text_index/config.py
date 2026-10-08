"""Operator-owned configuration for the optional local text index."""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

INDEX_DIR_NAME = ".cocoindex"
PIPELINE_VERSION = 1
MAX_FILES = 10000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_CORPUS_BYTES = 64 * 1024 * 1024
MAX_RESULTS = 20


class IndexUnavailable(RuntimeError):
    """A stable, non-sensitive reason to fall back to workspace search."""


def digest(value):
    """Hash a JSON-compatible value with a deterministic representation."""

    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class TextIndexSettings:
    """Local paths and chunking parameters; there is no model or database URL."""

    workspace_path: Path
    chunk_size: int = 1600
    chunk_overlap: int = 200

    @property
    def profile(self):
        """Identify all processing parameters that affect prepared text."""

        return digest(
            {
                "chunk_size": self.chunk_size,
                "chunk_overlap": self.chunk_overlap,
                "pipeline": PIPELINE_VERSION,
            }
        )

    @classmethod
    def from_env(cls):
        """Load the workspace containing datasource-local indexes."""

        try:
            workspace = Path(os.getenv("LENSNODE_WORKSPACE_PATH", "/workspace")).resolve(strict=True)
            if not workspace.is_dir():
                raise ValueError
        except (KeyError, ValueError, OSError):
            raise IndexUnavailable("TEXT_INDEX_CONFIG_INVALID") from None
        return cls(workspace_path=workspace)


def index_directory(root, datasource_uuid):
    """Keep identity-isolated state with its datasource, rejecting linked directories."""

    state = Path(root) / INDEX_DIR_NAME
    directory = state / digest(datasource_uuid)
    if state.is_symlink() or directory.is_symlink() or (state / ".gitignore").is_symlink():
        raise IndexUnavailable("TEXT_INDEX_SOURCE_PATH_INVALID")
    return directory
