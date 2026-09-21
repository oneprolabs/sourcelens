"""Render one SourceLens Q&A turn as a styled, text-based PDF."""

import html
import re
import unicodedata

import bleach
import markdown
from django.utils import timezone
from weasyprint import HTML
from weasyprint.urls import URLFetchingError

DEFAULT_FILENAME = "SourceLens-conversation"
MAX_FILENAME_LENGTH = 80
UNSAFE_FILENAME_PATTERN = re.compile(r'[<>:"/\\|?*%]+')

# Inline evidence markers are an on-screen affordance; an exported document
# drops them, including the horizontal space before each marker, so the text
# reads cleanly.
SOURCE_MARKER_PATTERN = re.compile(
    r"[ \t]*\[\[\s*source:[^\]\n]*\]\]",
    re.IGNORECASE,
)

MARKDOWN_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "del",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}

LABELS = {
    "en": {
        "badge": "AI Agent Q&A",
        "answer_by": "Answer from AI Agent “{name}”",
        "question": "Question",
        "answer": "Answer",
        "input_files": "Question attachments",
        "output_files": "Generated files",
        "footer": (
            "Exported from SourceLens. AI-generated content should be "
            "verified before use."
        ),
    },
    "zh": {
        "badge": "AI Agent 问答",
        "answer_by": "来自 AI Agent「{name}」的回答",
        "question": "问题",
        "answer": "回答",
        "input_files": "问题附件",
        "output_files": "生成文件",
        "footer": ("由 SourceLens 导出。AI 生成内容在使用前" "应进行核实。"),
    },
}

PDF_CSS = """
@page {
  size: A4;
  margin: 18mm 28mm;
}
html {
  color: #1f2937;
  font-family: "Noto Sans CJK SC", "WenQuanYi Zen Hei", sans-serif;
  font-size: 11.25pt;
  letter-spacing: -.015em;
  line-height: 1.6;
}
body {
  margin: 0;
  orphans: 3;
  widows: 3;
}
.header {
  border-bottom: 1px solid #d1d5db;
  margin-bottom: 24px;
  padding-bottom: 18px;
}
.brand {
  color: #2563eb;
  font-size: 9pt;
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: uppercase;
}
h1.title {
  color: #111827;
  font-size: 22pt;
  line-height: 1.25;
  margin: 10px 0 8px;
}
.meta { color: #6b7280; margin: 2px 0; }
.question-card {
  background: #f3f4f6;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  break-inside: avoid;
  margin-bottom: 24px;
  padding: 16px;
}
.section-label {
  color: #374151;
  font-size: 9pt;
  letter-spacing: .08em;
  margin: 0 0 8px;
  text-transform: uppercase;
}
.question-text { margin: 0; white-space: pre-wrap; }
.answer h1, .answer h2, .answer h3,
.answer h4, .answer h5, .answer h6 {
  break-after: avoid;
  color: #111827;
  line-height: 1.35;
  margin: 1.15em 0 .45em;
}
.answer h1 { font-size: 18pt; }
.answer h2 { font-size: 15pt; }
.answer h3 { font-size: 13pt; }
.answer p { margin: .65em 0; }
.answer p, .answer li { orphans: 3; widows: 3; }
.answer ul, .answer ol { padding-left: 22px; }
.answer blockquote {
  border-left: 3px solid #d1d5db;
  color: #4b5563;
  margin: 1em 0;
  padding-left: 12px;
}
.answer code {
  background: #f3f4f6;
  border-radius: 3px;
  font-family: monospace;
  font-size: 9pt;
  letter-spacing: normal;
  overflow-wrap: anywhere;
  padding: 1px 4px;
}
.answer pre {
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  break-inside: auto;
  padding: 12px;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.answer pre code { background: transparent; padding: 0; }
.answer table {
  border-collapse: collapse;
  break-inside: auto;
  font-size: 8.5pt;
  table-layout: fixed;
  width: 100%;
  overflow-wrap: anywhere;
}
.answer thead { display: table-header-group; }
.answer tr { break-inside: avoid; }
.answer th, .answer td {
  border: 1px solid #d1d5db;
  padding: 6px 8px;
  text-align: left;
}
.answer th { background: #f3f4f6; font-weight: 700; }
.files { break-inside: avoid; margin-top: 18px; }
.files h3 { color: #4b5563; font-size: 10pt; margin: 0 0 4px; }
.files ul { margin: 0; padding-left: 20px; }
.footer {
  border-top: 1px solid #e5e7eb;
  color: #9ca3af;
  font-size: 8pt;
  margin-top: 32px;
  padding-top: 10px;
}
"""


def _clean_filename_part(value):
    """Return a filesystem-safe, normalized filename stem."""

    printable = "".join(
        " " if ord(character) < 32 or ord(character) == 127 else character
        for character in str(value or "")
    )
    normalized = unicodedata.normalize("NFKC", printable)
    normalized = UNSAFE_FILENAME_PATTERN.sub(" ", normalized)
    normalized = " ".join(normalized.split()).strip(". ")
    return "".join(list(normalized)[:MAX_FILENAME_LENGTH]).strip()


def build_qa_pdf_filename(summary="", question=""):
    """Build a PDF filename from the conversation summary or question."""

    stem = (
        _clean_filename_part(summary)
        or _clean_filename_part(question)
        or DEFAULT_FILENAME
    )
    return f"{stem}.pdf"


def _markdown_html(content):
    """Convert untrusted Markdown to a safe, resource-free HTML subset."""

    rendered = markdown.markdown(
        SOURCE_MARKER_PATTERN.sub("", content or ""),
        extensions=["fenced_code", "tables", "sane_lists"],
    )
    return bleach.clean(
        rendered,
        tags=MARKDOWN_TAGS,
        attributes={"a": ["href", "title"]},
        protocols={"http", "https", "mailto"},
        strip=True,
    )


def _language_labels(language_code):
    """Return PDF labels for the active request language."""

    language = "zh" if str(language_code).lower().startswith("zh") else "en"
    return LABELS[language]


def _format_date(value):
    """Format a datetime using the server's active local timezone."""

    if not value:
        return ""
    if hasattr(value, "strftime"):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def _file_name(file_value):
    """Read a display filename from a model, mapping, or string."""

    if isinstance(file_value, dict):
        return file_value.get("filename") or file_value.get("original_name")
    return (
        getattr(file_value, "filename", "")
        or getattr(file_value, "original_name", "")
        or str(file_value or "")
    )


def _file_list(title, files):
    """Render an escaped attachment filename list."""

    names = [_file_name(file_value) for file_value in files or []]
    names = [name for name in names if name]
    if not names:
        return ""
    items = "".join(f"<li>{html.escape(name)}</li>" for name in names)
    return (
        '<div class="files">'
        f"<h3>{html.escape(title)}</h3><ul>{items}</ul></div>"
    )


def build_qa_pdf_html(
    *,
    title,
    question,
    answer,
    assistant_name="",
    published_at=None,
    input_files=None,
    output_files=None,
    language_code="en",
):
    """Build the complete, styled HTML document used for PDF rendering."""

    labels = _language_labels(language_code)
    document_language = (
        "zh-CN" if str(language_code).lower().startswith("zh") else "en"
    )
    display_title = title or question
    assistant_line = ""
    if assistant_name:
        answer_by = labels["answer_by"].format(name=assistant_name)
        assistant_line = f'<p class="meta">{html.escape(answer_by)}</p>'
    date_line = ""
    if published_at:
        date_line = (
            f'<p class="meta">{html.escape(_format_date(published_at))}</p>'
        )
    input_list = _file_list(labels["input_files"], input_files)
    output_list = _file_list(labels["output_files"], output_files)
    return f"""<!doctype html>
<html lang="{document_language}">
<head>
  <meta charset="utf-8">
  <title>{html.escape(display_title)}</title>
  <style>{PDF_CSS}</style>
</head>
<body>
  <header class="header">
    <div class="brand">SourceLens · {html.escape(labels['badge'])}</div>
    <h1 class="title">{html.escape(display_title)}</h1>
    {assistant_line}
    {date_line}
  </header>
  <section class="question-card">
    <h2 class="section-label">{html.escape(labels['question'])}</h2>
    <p class="question-text">{html.escape(question)}</p>
    {input_list}
  </section>
  <section class="answer">
    <h2 class="section-label">{html.escape(labels['answer'])}</h2>
    {_markdown_html(answer)}
    {output_list}
  </section>
  <footer class="footer">{html.escape(labels['footer'])}</footer>
</body>
</html>"""


def _blocked_url_fetcher(url, *args, **kwargs):
    """Reject every external or local resource requested by WeasyPrint."""

    raise URLFetchingError(f"External PDF resource blocked: {url}")


def render_qa_pdf(**values):
    """Render a styled Q&A document as searchable PDF bytes."""

    document = build_qa_pdf_html(**values)
    return HTML(
        string=document,
        url_fetcher=_blocked_url_fetcher,
    ).write_pdf(pdf_variant="pdf/ua-1")
