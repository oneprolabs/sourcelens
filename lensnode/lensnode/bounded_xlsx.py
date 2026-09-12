"""Bounded, sparse XLSX inspection used by workspace conversion."""

import zipfile
from pathlib import Path
from xml.etree import ElementTree


def _column_name(number):
    name = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _coordinate(reference):
    letters = "".join(c for c in reference if c.isalpha())
    digits = "".join(c for c in reference if c.isdigit())
    if not letters or not digits:
        return 0, 0
    column = 0
    for letter in letters.upper():
        column = column * 26 + ord(letter) - 64
    return int(digits), column


def inspect_xlsx(path, max_cells=100000, max_xml_bytes=50 * 1024 * 1024,
                 max_chars=1_000_000):
    """Inspect non-empty cells with hard cell, XML, and output budgets."""
    total_xml = 0
    scanned = 0
    effective = 0
    rows = 0
    columns = 0
    sheets = []
    output = []
    truncated = False
    try:
        with zipfile.ZipFile(path) as archive:
            shared_strings = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                shared_strings = ["".join(node.itertext()) for node in root
                                  if node.tag.rsplit("}", 1)[-1] == "si"]
            names = sorted(n for n in archive.namelist()
                           if n.startswith("xl/worksheets/") and n.endswith(".xml"))
            for name in names:
                info = archive.getinfo(name)
                if total_xml + info.file_size > max_xml_bytes:
                    truncated = True
                    break
                total_xml += info.file_size
                sheet_rows = set()
                sheet_cols = set()
                sheet_cells = []
                with archive.open(name) as source:
                    for event, element in ElementTree.iterparse(source,
                                                                 events=("end",)):
                        if element.tag.rsplit("}", 1)[-1] != "c":
                            continue
                        scanned += 1
                        if scanned > max_cells:
                            truncated = True
                            break
                        kind = element.attrib.get("t")
                        value_node = next((child for child in element
                                           if child.tag.rsplit("}", 1)[-1] == "v"), None)
                        if kind == "s" and value_node is not None:
                            index = int(value_node.text or -1)
                            value = (shared_strings[index]
                                     if 0 <= index < len(shared_strings) else "")
                        elif kind == "inlineStr":
                            value = "".join(element.itertext())
                        else:
                            value = value_node.text if value_node is not None else ""
                        if value:
                            row, column = _coordinate(element.attrib.get("r", ""))
                            if row and column:
                                sheet_rows.add(row)
                                sheet_cols.add(column)
                                sheet_cells.append((row, column, value))
                        element.clear()
                    if truncated and scanned > max_cells:
                        pass
                if sheet_cells:
                    rows = max(rows, max(r for r, _, _ in sheet_cells))
                    columns = max(columns, max(c for _, c, _ in sheet_cells))
                    effective += len(sheet_cells)
                    sheet_name = Path(name).stem
                    output.append(f"## {sheet_name}")
                    for row, column, value in sheet_cells:
                        output.append(f"{_column_name(column)}{row}: {value}")
                sheets.append({"name": Path(name).stem, "effective_cells": len(sheet_cells),
                               "rows": len(sheet_rows), "columns": len(sheet_cols)})
                if truncated:
                    break
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise RuntimeError("SPREADSHEET_PARSE_FAILED") from exc
    text = "\n".join(output)
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return text, {"xlsx_files": 1, "sheets": len(sheets), "rows": rows,
                  "columns": columns, "effective_cells": effective,
                  "scanned_cells": scanned, "xml_bytes": total_xml,
                  "sheet_stats": sheets, "truncated": truncated,
                  **({"truncation_reason": "SPREADSHEET_CELL_BUDGET_EXCEEDED"}
                     if truncated else {})}
