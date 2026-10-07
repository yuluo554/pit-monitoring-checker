"""测试用的最小 xlsx 构造器（标准库 zip + 手写 OOXML 分片）。

只用它来证明读侧通路，不进 `src/`：写侧的正式实现属 M5 报告导出。
数值单元格保留原文本，日期单元格写 1900 纪元序列号 + 日期样式，其余走 sharedStrings ——
这三种形态正好覆盖 `pmc.ingest.xlsx` 的三条取值分支。
日历上不存在的日期（如 `2026-13-18`）写成一格文本，让行级校验按 `bad_date` 拒收，
与 CSV 通路保持同一口径。
"""

from __future__ import annotations

import zipfile
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from pmc.ingest.csvio import DATE_RE, NUMBER_RE

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
EXCEL_EPOCH = date(1899, 12, 30)


def esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def column_name(index: int) -> str:
    name = ""
    cursor = index + 1
    while cursor:
        cursor, remainder = divmod(cursor - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def date_serial(text: str) -> Optional[int]:
    """日期文本 → Excel 序列号；不是真实日历日则返回 None（当文本处理）。"""
    if not DATE_RE.match(text):
        return None
    year, month, day = (int(part) for part in text.split("-"))
    try:
        stamp = date(year, month, day)
    except ValueError:
        return None
    serial = (stamp - EXCEL_EPOCH).days
    #: 1900-03-01 之前 Excel 的序列号少一位（它把 1900 年当闰年），与读侧的分支对称
    return serial - 1 if serial <= 60 else serial


def _is_serial_cell(index: int, value: str, date_columns: Tuple[int, ...]) -> bool:
    return index in date_columns and date_serial(value) is not None


def _content_types() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
        'package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )


def _root_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="{0}">'
        '<Relationship Id="rId1" Type="{1}/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>".format(REL_NS, DOC_REL_NS)
    )


def _workbook() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="{0}" xmlns:r="{1}"><sheets>'
        '<sheet name="观测" sheetId="1" r:id="rId1"/>'
        "</sheets></workbook>".format(MAIN_NS, DOC_REL_NS)
    )


def _workbook_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="{0}">'
        '<Relationship Id="rId1" Type="{1}/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="{1}/styles" Target="styles.xml"/>'
        '<Relationship Id="rId3" Type="{1}/sharedStrings" Target="sharedStrings.xml"/>'
        "</Relationships>".format(REL_NS, DOC_REL_NS)
    )


def _styles() -> str:
    """下标 1 的样式是内建日期格式 14（yyyy-mm-dd），读侧靠它认出哪一列是序列号。"""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="{0}"><cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
        '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" applyNumberFormat="1"/>'
        "</cellXfs></styleSheet>".format(MAIN_NS)
    )


def _shared_strings(values: Sequence[str]) -> str:
    body = "".join(
        '<si><t xml:space="preserve">{0}</t></si>'.format(esc(value)) for value in values
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="{0}" count="{1}" uniqueCount="{1}">{2}</sst>'.format(
            MAIN_NS, len(values), body
        )
    )


def _worksheet(
    rows: Sequence[Sequence[str]],
    string_ids: Dict[str, int],
    date_columns: Tuple[int, ...],
    inline_row: Optional[int],
    skip_empty_rows: bool,
) -> str:
    parts: List[str] = []
    for row_number, cells in enumerate(rows, start=1):
        cell_xml: List[str] = []
        for index, value in enumerate(cells):
            if value == "":
                continue
            ref = "{0}{1}".format(column_name(index), row_number)
            serial = date_serial(value) if _is_serial_cell(index, value, date_columns) else None
            if serial is not None:
                cell_xml.append('<c r="{0}" s="1"><v>{1}</v></c>'.format(ref, serial))
            elif NUMBER_RE.match(value):
                cell_xml.append('<c r="{0}"><v>{1}</v></c>'.format(ref, esc(value)))
            elif (
                inline_row is not None
                and row_number == inline_row
                and index == len(cells) - 1
            ):
                cell_xml.append(
                    '<c r="{0}" t="inlineStr"><is><t>{1}</t></is></c>'.format(ref, esc(value))
                )
            else:
                cell_xml.append('<c r="{0}" t="s"><v>{1}</v></c>'.format(ref, string_ids[value]))
        if not cell_xml and skip_empty_rows:
            continue
        parts.append('<row r="{0}">{1}</row>'.format(row_number, "".join(cell_xml)))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="{0}"><sheetData>{1}</sheetData></worksheet>'.format(
            MAIN_NS, "".join(parts)
        )
    )


def collect_strings(
    body: Sequence[Sequence[str]], date_columns: Tuple[int, ...]
) -> Tuple[List[str], Dict[str, int]]:
    strings: List[str] = []
    ids: Dict[str, int] = {}
    for cells in body:
        for index, value in enumerate(cells):
            if not value or value in ids:
                continue
            if NUMBER_RE.match(value):
                continue
            if _is_serial_cell(index, value, date_columns):
                continue
            ids[value] = len(strings)
            strings.append(value)
    return strings, ids


def write_xlsx(
    path: str,
    rows: Sequence[Sequence[str]],
    *,
    date_columns: Tuple[int, ...] = (1,),
    inline_row: Optional[int] = None,
    skip_empty_rows: bool = True,
    header: Optional[Sequence[str]] = None,
) -> str:
    """把单元格矩阵写成一份最小可用 xlsx，返回落盘路径。`header` 非空时前置为第 1 行。"""
    body = list(rows) if header is None else [tuple(header)] + list(rows)
    strings, string_ids = collect_strings(body, date_columns)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types())
        archive.writestr("_rels/.rels", _root_rels())
        archive.writestr("xl/workbook.xml", _workbook())
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels())
        archive.writestr("xl/styles.xml", _styles())
        archive.writestr("xl/sharedStrings.xml", _shared_strings(strings))
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            _worksheet(body, string_ids, date_columns, inline_row, skip_empty_rows),
        )
    return path
