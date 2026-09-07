import json
import sys
import threading
import types
import zipfile
from io import BytesIO

import pytest

from lensnode.datasource_manifest import SyncResult
from lensnode.document_convert import describe_image_bytes
from lensnode.document_convert import document_image_context
from lensnode.document_convert import image_prompt
from lensnode.document_convert import post_process_documents
from lensnode.document_convert import prepare_image_for_model
from lensnode.document_convert import standalone_image_context
from lensnode.document_convert import xlsx_stats
from lensnode.gateway_model import RunCancelledError

Image = pytest.importorskip("PIL.Image")
fitz = pytest.importorskip("fitz")


def write_image(path, size=(96, 96), pattern=True):
    """Write a deterministic test image."""

    image = Image.new("RGB", size, "white")
    if pattern:
        pixels = image.load()
        for x in range(size[0]):
            for y in range(size[1]):
                pixels[x, y] = (
                    (x * 3) % 255,
                    (y * 5) % 255,
                    ((x + y) * 7) % 255,
                )
    image.save(path, format="PNG")


def sync_item(path):
    """Return a manifest-compatible sync item."""

    extension = path.suffix.lower().lstrip(".")
    return {
        "source_id": f"git:test:{path.name}",
        "source_type": "git",
        "source_path": path.name,
        "local_path": path.name,
        "file": path.name,
        "name": path.name,
        "kind": "file",
        "type": "file",
        "extension": extension,
        "file_extension": extension,
        "status": "synced",
        "metadata": {},
        "remote": {},
    }


def image_bytes(size=(96, 96)):
    """Return deterministic PNG bytes."""

    buffer = BytesIO()
    image = Image.new("RGB", size, "white")
    pixels = image.load()
    for x in range(size[0]):
        for y in range(size[1]):
            pixels[x, y] = (
                (x * 3) % 255,
                (y * 5) % 255,
                ((x + y) * 7) % 255,
            )
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def write_xlsx(path, dimension, cells):
    """Write a minimal XLSX archive with the supplied worksheet cells."""

    rows = "".join(
        f'<c r="{reference}" t="inlineStr"><is><t>{value}</t></is></c>'
        for reference, value in cells
    )
    worksheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/><sheetData>{rows}</sheetData>'
        '</worksheet>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><sheets><sheet name="Data" '
            'sheetId="1" r:id="rId1" xmlns:r="http://schemas.'
            'openxmlformats.org/officeDocument/2006/relationships"/>'
            "</sheets></workbook>",
        )
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


def test_xlsx_stats_ignores_formatting_only_detected_range(tmp_path):
    """A stale worksheet dimension does not become effective data range."""

    path = tmp_path / "stale-range.xlsx"
    write_xlsx(path, "A1:XFD1048576", [("A1", "Name"), ("B2", "Value")])

    stats = xlsx_stats(path, max_cells=10)

    assert stats["detected_range"] == "A1:XFD1048576"
    assert stats["effective_range"] == "A1:B2"
    assert stats["rows"] == 2
    assert stats["columns"] == 2
    assert stats["truncated"] is False


def test_xlsx_stats_stops_after_cell_scan_budget(tmp_path):
    """Sparse XLSX parsing stops once its cell-scan budget is hit."""

    path = tmp_path / "over-budget.xlsx"
    write_xlsx(
        path,
        "A1:XFD1048576",
        [("A1", "one"), ("B2", "two"), ("C3", "three")],
    )

    stats = xlsx_stats(path, max_cells=2)

    assert stats["truncated"] is True
    assert stats["truncation_reason"] == "SPREADSHEET_CELL_BUDGET_EXCEEDED"
    assert stats["effective_cells"] == 2
    assert stats["scanned_cells"] == 3


def test_xlsx_stats_stops_after_scanning_style_only_cells(tmp_path):
    """Style-only cells cannot exhaust conversion work without truncation."""

    path = tmp_path / "style-only.xlsx"
    cells = "".join(f'<c r="A{row}" s="1"/>' for row in range(1, 5))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><sheetData>'
            f"{cells}</sheetData></worksheet>",
        )

    stats = xlsx_stats(path, max_cells=2)

    assert stats["truncated"] is True
    assert stats["truncation_reason"] == "SPREADSHEET_CELL_BUDGET_EXCEEDED"
    assert stats["scanned_cells"] == 3


def test_xlsx_stats_applies_cell_scan_budget_across_worksheets(tmp_path):
    """Worksheet boundaries cannot reset the spreadsheet scan budget."""

    path = tmp_path / "multiple-sheets.xlsx"
    worksheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData><c r="A1" s="1"/>'
        '<c r="A2" s="1"/></sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        archive.writestr("xl/worksheets/sheet2.xml", worksheet)

    stats = xlsx_stats(path, max_cells=3)

    assert stats["truncated"] is True
    assert stats["scanned_cells"] == 4
    assert stats["sheets"] == 2


def test_xlsx_stats_rejects_oversized_worksheet_xml_before_expansion(tmp_path):
    """A compressed XLSX cannot expand worksheet XML beyond its byte budget."""

    path = tmp_path / "compressed.xlsx"
    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", "x" * 1024)

    stats = xlsx_stats(path, max_xml_bytes=128)

    assert stats["truncated"] is True
    assert stats["truncation_reason"] == "SPREADSHEET_XML_BUDGET_EXCEEDED"
    assert stats["sheet_stats"][0]["xml_bytes"] == 1024


def test_prepare_image_skips_tiny_image(tmp_path):
    """Very small images are skipped before model upload."""

    path = tmp_path / "tiny.png"
    write_image(path, size=(32, 32))

    result = prepare_image_for_model(path, {"conversion": {"image": True}})

    assert result["skipped"] is True
    assert result["reason"] == "IMAGE_TOO_SMALL"
    assert result["stats"]["images_skipped"] == 1


def test_prepare_image_skips_blank_image(tmp_path):
    """Blank images are skipped before model upload."""

    path = tmp_path / "blank.png"
    Image.new("RGB", (96, 96), "white").save(path, format="PNG")

    result = prepare_image_for_model(
        path,
        {
            "conversion": {
                "image": True,
                "min_image_bytes": 1,
                "min_image_dimension": 64,
            }
        },
    )

    assert result["skipped"] is True
    assert result["reason"] == "IMAGE_BLANK"
    assert result["stats"]["images_blank"] == 1


def test_prepare_image_downscales_and_reencodes(tmp_path):
    """Large images are downscaled and re-encoded before model upload."""

    path = tmp_path / "large.png"
    write_image(path, size=(260, 220))

    result = prepare_image_for_model(
        path,
        {
            "conversion": {
                "image": True,
                "image_max_dimension": 80,
                "min_image_bytes": 1,
            }
        },
    )

    assert result["mime_type"] == "image/jpeg"
    assert (
        max(result["stats"]["image_width"], result["stats"]["image_height"])
        == 80
    )
    assert result["stats"]["images_compressed"] == 1
    assert result["stats"]["image_upload_bytes"] == len(result["bytes"])


def test_image_prompt_uses_document_language(tmp_path):
    """Image prompts follow the source document language."""

    chinese = document_image_context(
        {},
        tmp_path / "行程单.pdf",
        "申请时间 行程时间 共计一单行程 合计金额",
    )
    english = document_image_context(
        {},
        tmp_path / "runbook.pdf",
        "This architecture diagram shows the recovery workflow.",
    )

    assert chinese["document_language"] == "zh"
    assert "请使用简体中文" in image_prompt(chinese)
    assert english["document_language"] == "en"
    assert "Describe this image in English" in image_prompt(english)


def test_standalone_image_context_uses_filename_language(tmp_path):
    """Standalone images infer language from the file path."""

    context = standalone_image_context({}, tmp_path / "网络拓扑图.png")

    assert context["document_language"] == "zh"
    assert "网络拓扑图.png" in image_prompt(context)


def test_post_process_images_skips_duplicate_before_model_call(
    tmp_path,
    monkeypatch,
):
    """Duplicate image bytes are skipped before calling the vision model."""

    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    write_image(first, size=(96, 96))
    second.write_bytes(first.read_bytes())
    calls = []

    def describe_image_bytes(image_bytes, mime_type, context):
        del image_bytes, mime_type, context
        calls.append(True)
        return "A useful image description.", {"total_tokens": 12}

    monkeypatch.setattr(
        "lensnode.document_convert.describe_image_bytes",
        describe_image_bytes,
    )

    summary = post_process_documents(
        {
            "datasource_uuid": "ds1",
            "name": "repo",
            "source_type": "git",
            "target_path": str(tmp_path),
            "conversion": {
                "image": True,
                "vision_model_ref": "vision-model",
                "min_image_bytes": 1,
            },
            "ai_gateway_url": "http://gateway",
            "lensnode_token": "token",
        },
        SyncResult(items=[sync_item(first), sync_item(second)]),
    )

    assert len(calls) == 1
    assert summary["converted"] == 1
    assert summary["skipped"] == 1
    assert summary["images_duplicate"] == 1
    assert summary["images_skipped"] == 1
    assert summary["details"]["images_duplicate"][0]["path"] == "second.png"
    meta_path = tmp_path / "first.png.sourcelens/meta.json"
    meta = json.loads(meta_path.read_text())
    assert meta["conversion"]["stats"]["images_recognized"] == 1


def test_post_process_document_recognizes_embedded_office_image(
    tmp_path,
    monkeypatch,
):
    """Office embedded images are described and appended to Markdown."""

    doc = tmp_path / "report.docx"
    with zipfile.ZipFile(doc, "w") as archive:
        archive.writestr("word/media/image1.png", image_bytes())

    class FakeMarkItDown:
        """Minimal MarkItDown test double."""

        def convert(self, path):
            del path
            return types.SimpleNamespace(text_content="Document text.")

    fake_module = types.SimpleNamespace(
        MarkItDown=FakeMarkItDown,
        __version__="test",
    )
    monkeypatch.setitem(sys.modules, "markitdown", fake_module)

    def describe_image_bytes(image_bytes_value, mime_type, context):
        del image_bytes_value, mime_type, context
        return "Embedded chart description.", {"total_tokens": 18}

    monkeypatch.setattr(
        "lensnode.document_convert.describe_image_bytes",
        describe_image_bytes,
    )

    progress = []
    summary = post_process_documents(
        {
            "datasource_uuid": "ds1",
            "name": "repo",
            "source_type": "git",
            "target_path": str(tmp_path),
            "conversion": {
                "document": True,
                "embedded_image": True,
                "vision_model_ref": "vision-model",
                "min_image_bytes": 1,
            },
            "ai_gateway_url": "http://gateway",
            "lensnode_token": "token",
            "progress_queue": types.SimpleNamespace(put=progress.append),
        },
        SyncResult(items=[sync_item(doc)]),
    )

    content = (tmp_path / "report.docx.sourcelens/content.md").read_text()
    meta_path = tmp_path / "report.docx.sourcelens/meta.json"
    meta = json.loads(meta_path.read_text())
    assert "## Embedded Images" in content
    assert "Embedded chart description." in content
    assert summary["embedded_images_total"] == 1
    assert summary["embedded_images_recognized"] == 1
    assert meta["conversion"]["stats"]["embedded_images_recognized"] == 1
    assert progress == [
        {
            "stage": "recognizing_images",
            "image_completed": 0,
            "image_total": 1,
        },
        {
            "stage": "recognizing_images",
            "image_completed": 1,
            "image_total": 1,
        },
    ]


def test_document_text_survives_embedded_image_gateway_timeout(
    tmp_path,
    monkeypatch,
):
    """One failed embedded image leaves converted document text available."""

    doc = tmp_path / "report.docx"
    with zipfile.ZipFile(doc, "w") as archive:
        archive.writestr("word/media/image1.png", image_bytes())
    install_fake_markitdown(monkeypatch)

    def fail_image_description(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("504 Gateway Time-out")

    monkeypatch.setattr(
        "lensnode.document_convert.describe_image_bytes",
        fail_image_description,
    )

    summary = post_process_documents(
        conversion_context(
            tmp_path,
            {
                "document": True,
                "embedded_image": True,
                "vision_model_ref": "vision-model",
                "min_image_bytes": 1,
            },
        ),
        SyncResult(items=[sync_item(doc)]),
    )

    content = (tmp_path / "report.docx.sourcelens/content.md").read_text()
    item = summary["items"][0]
    assert "Document text." in content
    assert summary["success"] == 1
    assert summary["warnings"] == [
        "DOCUMENT_TEXT_EXTRACTED_WITH_VISUAL_FAILURE"
    ]
    assert item["stats"]["visual_failure_codes"] == [
        "VISUAL_UPSTREAM_TIMEOUT"
    ]


def test_post_process_pdf_recognizes_embedded_image(tmp_path, monkeypatch):
    """PDF embedded image objects are described through the shared pipeline."""

    pdf = tmp_path / "report.pdf"
    document = fitz.open()
    page = document.new_page(width=200, height=200)
    page.insert_image(fitz.Rect(20, 20, 160, 160), stream=image_bytes())
    document.save(pdf)
    document.close()
    install_fake_markitdown(monkeypatch)
    calls = []

    def describe_image_bytes(image_bytes_value, mime_type, context):
        del image_bytes_value, mime_type, context
        calls.append(True)
        return "PDF diagram description.", {"total_tokens": 21}

    monkeypatch.setattr(
        "lensnode.document_convert.describe_image_bytes",
        describe_image_bytes,
    )

    summary = post_process_documents(
        conversion_context(
            tmp_path,
            {
                "document": True,
                "embedded_image": True,
                "vision_model_ref": "vision-model",
                "min_image_bytes": 1,
                "pdf_extract_images": True,
                "pdf_extract_images_on_text_pages": True,
                "pdf_render_scanned_pages": False,
            },
        ),
        SyncResult(items=[sync_item(pdf)]),
    )

    content = (tmp_path / "report.pdf.sourcelens/content.md").read_text()
    assert len(calls) == 1
    assert "## PDF Images" in content
    assert "PDF diagram description." in content
    assert summary["pdf_pages"] == 1
    assert summary["pdf_images_recognized"] == 1


def test_pdf_text_survives_embedded_image_gateway_timeout(
    tmp_path,
    monkeypatch,
):
    """A PDF image timeout leaves document text and exposes a warning."""

    pdf = tmp_path / "report.pdf"
    document = fitz.open()
    page = document.new_page(width=200, height=200)
    page.insert_text((20, 30), "This page has extractable document text.")
    page.insert_image(fitz.Rect(20, 60, 160, 180), stream=image_bytes())
    document.save(pdf)
    document.close()
    install_fake_markitdown(monkeypatch)

    def fail_image_description(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("504 Gateway Time-out")

    monkeypatch.setattr(
        "lensnode.document_convert.describe_image_bytes",
        fail_image_description,
    )

    summary = post_process_documents(
        conversion_context(
            tmp_path,
            {
                "document": True,
                "embedded_image": True,
                "vision_model_ref": "vision-model",
                "min_image_bytes": 1,
                "pdf_extract_images": True,
                "pdf_extract_images_on_text_pages": True,
            },
        ),
        SyncResult(items=[sync_item(pdf)]),
    )

    content = (tmp_path / "report.pdf.sourcelens/content.md").read_text()
    item = summary["items"][0]
    assert "Document text." in content
    assert summary["success"] == 1
    assert summary["warnings"] == [
        "DOCUMENT_TEXT_EXTRACTED_WITH_VISUAL_FAILURE"
    ]
    assert item["stats"]["visual_failure_codes"] == [
        "VISUAL_UPSTREAM_TIMEOUT"
    ]


def test_post_process_pdf_skips_embedded_image_on_text_page_by_default(
    tmp_path,
    monkeypatch,
):
    """PDF text pages do not describe embedded images by default."""

    pdf = tmp_path / "itinerary.pdf"
    document = fitz.open()
    page = document.new_page(width=200, height=200)
    page.insert_text((20, 30), "This page already has enough text content.")
    page.insert_image(fitz.Rect(20, 60, 160, 180), stream=image_bytes())
    document.save(pdf)
    document.close()
    install_fake_markitdown(monkeypatch)
    calls = []

    def describe_image_bytes(image_bytes_value, mime_type, context):
        del image_bytes_value, mime_type, context
        calls.append(True)
        return "Unwanted logo description.", {"total_tokens": 21}

    monkeypatch.setattr(
        "lensnode.document_convert.describe_image_bytes",
        describe_image_bytes,
    )

    summary = post_process_documents(
        conversion_context(
            tmp_path,
            {
                "document": True,
                "embedded_image": True,
                "vision_model_ref": "vision-model",
                "min_image_bytes": 1,
                "pdf_extract_images": True,
                "pdf_render_scanned_pages": False,
            },
        ),
        SyncResult(items=[sync_item(pdf)]),
    )

    content = (tmp_path / "itinerary.pdf.sourcelens/content.md").read_text()
    assert calls == []
    assert "## PDF Images" not in content
    assert summary["pdf_pages_with_text"] == 1
    assert summary["pdf_images_recognized"] == 0


def test_post_process_pdf_renders_scanned_page_when_enabled(
    tmp_path,
    monkeypatch,
):
    """Scanned PDF pages are rendered only when the option is enabled."""

    pdf = tmp_path / "scan.pdf"
    document = fitz.open()
    page = document.new_page(width=200, height=200)
    page.draw_rect(
        fitz.Rect(30, 30, 170, 170),
        color=(0.1, 0.2, 0.8),
        fill=(0.1, 0.2, 0.8),
    )
    document.save(pdf)
    document.close()
    install_fake_markitdown(monkeypatch)
    calls = []

    def describe_image_bytes(image_bytes_value, mime_type, context):
        del image_bytes_value, mime_type, context
        calls.append(True)
        return "Rendered scanned page description.", {"total_tokens": 34}

    monkeypatch.setattr(
        "lensnode.document_convert.describe_image_bytes",
        describe_image_bytes,
    )

    summary = post_process_documents(
        conversion_context(
            tmp_path,
            {
                "document": True,
                "embedded_image": True,
                "vision_model_ref": "vision-model",
                "min_image_bytes": 1,
                "pdf_extract_images": False,
                "pdf_render_scanned_pages": True,
                "pdf_render_dpi": 96,
            },
        ),
        SyncResult(items=[sync_item(pdf)]),
    )

    content = (tmp_path / "scan.pdf.sourcelens/content.md").read_text()
    assert len(calls) == 1
    assert "Rendered scanned page description." in content
    assert summary["pdf_scanned_pages"] == 1
    assert summary["pdf_rendered_pages"] == 1


def install_fake_markitdown(monkeypatch):
    """Install a minimal MarkItDown test double."""

    class FakeMarkItDown:
        """Minimal MarkItDown test double."""

        def convert(self, path):
            del path
            return types.SimpleNamespace(text_content="Document text.")

    fake_module = types.SimpleNamespace(
        MarkItDown=FakeMarkItDown,
        __version__="test",
    )
    monkeypatch.setitem(sys.modules, "markitdown", fake_module)


def conversion_context(tmp_path, conversion):
    """Return a datasource conversion context."""

    return {
        "datasource_uuid": "ds1",
        "name": "repo",
        "source_type": "git",
        "target_path": str(tmp_path),
        "conversion": conversion,
        "ai_gateway_url": "http://gateway",
        "lensnode_token": "token",
    }


def test_describe_image_stops_before_gateway_after_run_cancellation():
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(RunCancelledError, match="cancelled"):
        describe_image_bytes(
            b"image",
            "image/png",
            {
                "cancel_event": cancel_event,
                "conversion": {"vision_model_ref": "vision-model"},
                "ai_gateway_url": "https://control.example/ai-gateway/",
                "lensnode_token": "node-token",
            },
        )
