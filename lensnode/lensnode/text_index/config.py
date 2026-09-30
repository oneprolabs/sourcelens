"""Operator-owned configuration for the optional local text index."""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

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

    state_path: Path
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
        """Keep generated state outside the Agent-readable workspace."""

        try:
            state = Path(os.environ["LENSNODE_TEXT_INDEX_STATE_PATH"]).resolve()
            workspace = Path(os.getenv("LENSNODE_WORKSPACE_PATH", "/workspace")).resolve(strict=True)
            if state.is_relative_to(workspace):
                raise ValueError
        except (KeyError, ValueError, OSError):
            raise IndexUnavailable("TEXT_INDEX_CONFIG_INVALID") from None
        return cls(state_path=state, workspace_path=workspace)


def index_key(root, datasource_uuid):
    """Separate local datasource deployments without sharing their indexes."""

    return digest([str(root.resolve()), datasource_uuid])
