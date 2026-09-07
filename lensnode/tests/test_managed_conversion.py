import sys
import threading
import types

import pytest

from lensnode.datasource_sync import DataSourceSyncError
from lensnode.datasource_sync import convert_managed_workspace
from lensnode.gateway_model import RunCancelledError


def install_fake_markitdown(
    monkeypatch,
    failing_name="",
    text="Converted document.",
):
    """Install a deterministic MarkItDown test double."""

    class FakeMarkItDown:
        """Return text unless the configured filename must fail."""

        def convert(self, path):
            if failing_name and str(path).endswith(failing_name):
                raise RuntimeError("PASSWORD_PROTECTED")
            return types.SimpleNamespace(text_content=text)

    monkeypatch.setitem(
        sys.modules,
        "markitdown",
        types.SimpleNamespace(
            MarkItDown=FakeMarkItDown,
            __version__="test",
        ),
    )


def conversion_command(target, cancel_event=None):
    """Return a managed workspace conversion command."""

    return {
        "datasource_uuid": "managed-1",
        "name": "Managed documents",
        "source_type": "managed_workspace",
        "target_path": str(target),
        "conversion": {"document": True},
        "cancel_event": cancel_event,
    }


def test_managed_conversion_writes_sidecars_and_skips_unchanged(
    tmp_path,
    monkeypatch,
):
    """Managed conversion preserves originals and reuses fingerprints."""

    install_fake_markitdown(monkeypatch)
    document = tmp_path / "report.docx"
    original = b"external document bytes"
    document.write_bytes(original)
    (tmp_path / "notes.txt").write_text("Not a supported document.")

    first = convert_managed_workspace(
        conversion_command(tmp_path),
        workspace_path=tmp_path.parent,
    )
    second = convert_managed_workspace(
        conversion_command(tmp_path),
        workspace_path=tmp_path.parent,
    )
    forced_command = conversion_command(tmp_path)
    forced_command["force"] = True
    forced = convert_managed_workspace(
        forced_command,
        workspace_path=tmp_path.parent,
    )

    assert first["status"] == "success"
    assert first["conversion_summary"]["success"] == 1
    assert first["conversion_summary"]["unsupported"] == 1
    assert document.read_bytes() == original
    assert (tmp_path / "report.docx.sourcelens/content.md").is_file()
    assert (tmp_path / "report.docx.sourcelens/meta.json").is_file()
    assert second["conversion_summary"]["skipped"] == 1
    reasons = {
        item["reason"] for item in second["conversion_summary"]["items"]
    }
    assert reasons == {"UNCHANGED", "UNSUPPORTED_TYPE"}
    assert forced["conversion_summary"]["success"] == 1


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("max_file_size_mb", 1),
        ("max_pages", 1),
        ("max_images", 1),
    ],
)
def test_managed_conversion_policy_change_invalidates_fingerprint(
    tmp_path,
    monkeypatch,
    option,
    value,
):
    """A changed conversion limit must not reuse an older sidecar."""

    install_fake_markitdown(monkeypatch)
    (tmp_path / "report.docx").write_bytes(b"external document bytes")
    convert_managed_workspace(
        conversion_command(tmp_path),
        workspace_path=tmp_path.parent,
    )
    command = conversion_command(tmp_path)
    command["conversion"][option] = value

    result = convert_managed_workspace(
        command,
        workspace_path=tmp_path.parent,
    )

    assert result["conversion_summary"]["success"] == 1


def test_managed_conversion_continues_after_one_file_failure(
    tmp_path,
    monkeypatch,
):
    """A per-file failure does not fail the managed conversion batch."""

    install_fake_markitdown(monkeypatch, failing_name="protected.docx")
    (tmp_path / "ok.docx").write_bytes(b"ok")
    (tmp_path / "protected.docx").write_bytes(b"protected")

    result = convert_managed_workspace(
        conversion_command(tmp_path),
        workspace_path=tmp_path.parent,
    )

    summary = result["conversion_summary"]
    assert result["status"] == "success"
    assert summary["success"] == 1
    assert summary["failed"] == 1
    assert "CONVERSION_PARTIAL_FAILED" in summary["warnings"]
    failed = next(item for item in summary["items"] if item["status"] == "failed")
    assert failed["reason"] == "PASSWORD_PROTECTED"


def test_managed_conversion_stops_at_safe_boundary_when_cancelled(
    tmp_path,
    monkeypatch,
):
    """Cancellation prevents new sidecars from being written."""

    install_fake_markitdown(monkeypatch)
    document = tmp_path / "cancelled.docx"
    document.write_bytes(b"cancelled")
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(RunCancelledError):
        convert_managed_workspace(
            conversion_command(tmp_path, cancel_event=cancel_event),
            workspace_path=tmp_path.parent,
        )

    assert not (tmp_path / "cancelled.docx.sourcelens").exists()


def test_managed_conversion_reports_oversized_and_empty_documents(
    tmp_path,
    monkeypatch,
):
    """Policy limits and empty extraction have distinct safe reasons."""

    install_fake_markitdown(monkeypatch, text="")
    (tmp_path / "empty.docx").write_bytes(b"empty")
    (tmp_path / "large.docx").write_bytes(b"x" * (1024 * 1024 + 1))
    command = conversion_command(tmp_path)
    command["conversion"]["max_file_size_mb"] = 1

    result = convert_managed_workspace(
        command,
        workspace_path=tmp_path.parent,
    )

    summary = result["conversion_summary"]
    assert summary["failed"] == 0
    assert summary["skipped"] == 2
    assert {item["reason"] for item in summary["items"]} == {
        "FILE_TOO_LARGE",
        "NO_EXTRACTABLE_TEXT",
    }


def test_managed_conversion_enforces_pdf_page_limit(tmp_path, monkeypatch):
    """PDFs above max_pages are skipped before MarkItDown conversion."""

    fitz = pytest.importorskip("fitz")
    install_fake_markitdown(monkeypatch)
    document = fitz.open()
    document.new_page()
    document.new_page()
    pdf = tmp_path / "long.pdf"
    document.save(pdf)
    document.close()
    command = conversion_command(tmp_path)
    command["conversion"]["max_pages"] = 1

    result = convert_managed_workspace(
        command,
        workspace_path=tmp_path.parent,
    )

    summary = result["conversion_summary"]
    assert summary["skipped"] == 1
    assert summary["items"][0]["reason"] == "PAGE_LIMIT_EXCEEDED"


def test_managed_conversion_batches_files_and_bounds_manifest_details(
    tmp_path,
    monkeypatch,
):
    """Large manifests are processed in bounded batches."""

    install_fake_markitdown(monkeypatch)
    for index in range(5):
        (tmp_path / f"document-{index}.docx").write_bytes(b"document")
    command = conversion_command(tmp_path)
    command["conversion"]["batch_size"] = 2

    result = convert_managed_workspace(
        command,
        workspace_path=tmp_path.parent,
    )

    summary = result["conversion_summary"]
    assert summary["candidates"] == 5
    assert summary["success"] == 5
    assert len(summary["items"]) <= 200


def test_managed_conversion_reports_phase_aware_progress(tmp_path, monkeypatch):
    """Managed conversion progress distinguishes phase and overall state."""

    install_fake_markitdown(monkeypatch)
    (tmp_path / "report.docx").write_bytes(b"document")
    (tmp_path / "notes.bin").write_text("unsupported")
    events = []

    result = convert_managed_workspace(
        conversion_command(tmp_path),
        workspace_path=tmp_path.parent,
        emit=events.append,
    )

    parsing = [
        event
        for event in events
        if event.get("phase") == "PARSING_DOCUMENTS"
    ][-1]
    finalizing = events[-1]

    assert result["status"] == "success"
    assert parsing["phase_progress"] == {
        "current": 1,
        "total": 1,
        "unit": "files",
    }
    assert parsing["overall_progress_percent"] < 100
    assert parsing["progress_counts"] == {
        "total": 2,
        "candidates": 1,
        "processed": 2,
        "converted": 1,
        "failed": 0,
        "skipped": 0,
        "unsupported": 1,
    }
    assert finalizing["phase"] == "FINALIZING"
    assert finalizing["overall_progress_percent"] == 99


def test_managed_conversion_reports_embedded_image_progress(
    tmp_path,
    monkeypatch,
):
    """Managed conversion emits image progress before its file completes."""

    install_fake_markitdown(monkeypatch)
    (tmp_path / "report.docx").write_bytes(b"document")

    def convert_embedded_images(_path, context):
        callback = context["on_conversion_progress"]
        callback(
            {
                "stage": "recognizing_images",
                "image_completed": 0,
                "image_total": 2,
            }
        )
        callback(
            {
                "stage": "recognizing_images",
                "image_completed": 1,
                "image_total": 2,
            }
        )
        return {"markdown": "", "stats": {}, "cost": {}}

    monkeypatch.setattr(
        "lensnode.document_convert.convert_embedded_images",
        convert_embedded_images,
    )
    command = conversion_command(tmp_path)
    command["conversion"]["embedded_image"] = True
    events = []

    convert_managed_workspace(
        command,
        workspace_path=tmp_path.parent,
        emit=events.append,
    )

    visual = [
        event
        for event in events
        if event.get("phase") == "PROCESSING_EMBEDDED_IMAGES"
    ]

    assert len(visual) == 2
    assert visual[-1]["phase_progress"] == {
        "current": 1,
        "total": 2,
        "unit": "embedded_images",
        "scope": "current_file",
    }
    assert visual[-1]["overall_progress_percent"] == 50
    assert visual[-1]["progress_counts"]["processed"] == 0


def test_managed_conversion_reports_discovery_heartbeat(tmp_path, monkeypatch):
    """Managed conversion reports long file discovery before totals exist."""

    install_fake_markitdown(monkeypatch)
    for index in range(100):
        (tmp_path / f"document-{index}.docx").write_bytes(b"document")
    events = []

    convert_managed_workspace(
        conversion_command(tmp_path),
        workspace_path=tmp_path.parent,
        emit=events.append,
    )

    heartbeat = next(
        event
        for event in events
        if event.get("step") == "conversion_discovery_progress"
    )

    assert heartbeat["phase"] == "DISCOVERING_FILES"
    assert heartbeat["phase_progress"] == {
        "current": 100,
        "total": None,
        "unit": "files",
    }
    assert heartbeat["progress_counts"]["total"] is None


def test_managed_conversion_rejects_overlarge_workspace_manifest(tmp_path):
    """A configured workspace file budget fails before conversion starts."""

    (tmp_path / "document-a.docx").write_bytes(b"document")
    (tmp_path / "document-b.docx").write_bytes(b"document")
    command = conversion_command(tmp_path)
    command["conversion"]["max_files"] = 1

    with pytest.raises(DataSourceSyncError, match="RESOURCE_LIMIT_EXCEEDED"):
        convert_managed_workspace(
            command,
            workspace_path=tmp_path.parent,
        )
