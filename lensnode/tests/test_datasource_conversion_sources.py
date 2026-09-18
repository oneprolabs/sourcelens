import sys
import types

import pytest

from lensnode.datasource_sync import DataSourceSyncError
from lensnode.datasource_sync import convert_managed_workspace


def install_fake_markitdown(monkeypatch, text="Converted document."):
    """Install a deterministic MarkItDown test double."""

    class FakeMarkItDown:
        """Return fixed text for any convertible path."""

        def convert(self, path):
            del path
            return types.SimpleNamespace(text_content=text)

    monkeypatch.setitem(
        sys.modules,
        "markitdown",
        types.SimpleNamespace(
            MarkItDown=FakeMarkItDown,
            __version__="test",
        ),
    )


def conversion_command(target):
    """Return a conversion command for one target directory."""

    return {
        "datasource_uuid": "synced-1",
        "name": "Synced documents",
        "source_type": "git",
        "target_path": str(target),
        "conversion": {"document": True},
    }


def test_synced_source_type_reuses_local_content(tmp_path, monkeypatch):
    """Git content already on disk can be converted without a re-sync."""

    install_fake_markitdown(monkeypatch)
    target = tmp_path / "repo"
    target.mkdir()
    (target / "report.docx").write_bytes(b"external document bytes")

    result = convert_managed_workspace(
        conversion_command(target),
        workspace_path=tmp_path,
    )

    assert result["status"] == "success"
    assert result["conversion_summary"]["success"] == 1
    sidecar = target / "report.docx.sourcelens"
    assert sidecar.is_dir()
    content = (sidecar / "content.md").read_text(encoding="utf-8")
    assert "source_type: git" in content
    assert content.rstrip().endswith("Converted document.")


def test_conversion_rejects_missing_source_type(tmp_path):
    """A conversion command must identify its datasource source type."""

    command = conversion_command(tmp_path)
    command["source_type"] = ""

    with pytest.raises(DataSourceSyncError, match="NOT_SUPPORTED"):
        convert_managed_workspace(command, workspace_path=tmp_path)


def test_conversion_rejects_missing_directory(tmp_path):
    """Conversion needs a directory that already holds synced content."""

    command = conversion_command(tmp_path / "missing")

    with pytest.raises(DataSourceSyncError, match="DIRECTORY_REQUIRED"):
        convert_managed_workspace(command, workspace_path=tmp_path)
