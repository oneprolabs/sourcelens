import zipfile

from lensnode.bounded_xlsx import inspect_xlsx


def test_inspect_xlsx_decodes_shared_and_inline_strings(tmp_path):
    path = tmp_path / "strings.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", "<sst><si><t>Shared text</t></si></sst>")
        archive.writestr("xl/worksheets/sheet1.xml", "<worksheet><sheetData>"
                          '<c r="A1" t="s"><v>0</v></c>'
                          '<c r="B1" t="inlineStr"><is><t>Inline text</t></is></c>'
                          "</sheetData></worksheet>")
    text, stats = inspect_xlsx(path)
    assert "Shared text" in text
    assert "Inline text" in text
    assert stats["effective_cells"] == 2
