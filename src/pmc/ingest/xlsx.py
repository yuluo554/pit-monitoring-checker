"""XLSX 观测表 → 8 列单元格矩阵（plan/09 §十，P07 拍板落在 M2 初）。

只用标准库 `zipfile` + `ElementTree`：`06 D03` 的内核零依赖对读侧同样成立，
所以 openpyxl / xlrd / pandas 一律不装。

**判读口径只有一条**：本模块产出的就是 `csvio.parse_rows` 的输入，
行级校验与 11 个 reason_code 完全复用，不长第二套。本模块只回答两个问题：
单元格里到底是文本还是数值，以及这个数值是不是一个日期序列号。
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from datetime import date, timedelta
from typing import Dict, List, Optional, Set
from xml.etree import ElementTree

from pmc.errors import InputError

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_R_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
PACKAGE_R_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

#: 内建数字格式里属于日期/时间的那些（ECMA-376 的固定表号）
BUILTIN_DATE_FORMATS = frozenset({14, 15, 16, 17, 22, 45, 46, 47})
DATE_TOKENS = "ymd"
#: Excel 的 1900 纪元里 60 号是不存在的 1900-02-29，如实交出去让行级校验拒收
PHANTOM_SERIAL = 60

_COLUMN_RE = re.compile(r"^([A-Za-z]+)")
_LITERAL_RE = re.compile(r"(\[[^\]]*\]|\"[^\"]*\")")


def _tag(name: str) -> str:
    return "{{{0}}}{1}".format(MAIN_NS, name)


def _column_index(ref: str) -> int:
    """`B7` → 1；`AA1` → 26。列字母是唯一可信的位置来源（稀疏存储会跳过空单元格）。"""
    match = _COLUMN_RE.match(ref or "")
    if not match:
        return -1
    index = 0
    for char in match.group(1).upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def serial_to_date_text(raw: str) -> str:
    """Excel 日期序列号 → `YYYY-MM-DD`（1900 纪元，含 1900-02-29 兼容偏移）。"""
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return raw
    days = int(number)
    if days <= 0:
        return raw
    if days == PHANTOM_SERIAL:
        return "1900-02-29"
    if days < PHANTOM_SERIAL:
        base = date(1899, 12, 31)
    else:
        base = date(1899, 12, 30)
    stamp = base + timedelta(days=days)
    return "{0:04d}-{1:02d}-{2:02d}".format(stamp.year, stamp.month, stamp.day)


def _format_is_date(code: Optional[str]) -> bool:
    if not code:
        return False
    stripped = _LITERAL_RE.sub("", code).lower()
    return any(token in stripped for token in DATE_TOKENS)


def _date_style_indexes(styles_root: Optional[ElementTree.Element]) -> Set[int]:
    """cellXfs 里下标为 s 的样式是否日期格式：只有被认成日期的样式才做序列号换算。"""
    if styles_root is None:
        return set()
    custom: Dict[int, str] = {}
    for node in styles_root.iter(_tag("numFmt")):
        try:
            custom[int(node.get("numFmtId"))] = node.get("formatCode", "")
        except (TypeError, ValueError):
            continue
    indexes = set()
    cellxfs = styles_root.find(_tag("cellXfs"))
    if cellxfs is None:
        return indexes
    for index, xf in enumerate(cellxfs.findall(_tag("xf"))):
        try:
            num_fmt_id = int(xf.get("numFmtId", "0"))
        except (TypeError, ValueError):
            continue
        if num_fmt_id in BUILTIN_DATE_FORMATS or _format_is_date(custom.get(num_fmt_id)):
            indexes.add(index)
    return indexes


def _shared_strings(root: ElementTree.Element) -> List[str]:
    out = []
    for item in root.findall(_tag("si")):
        parts = [node.text or "" for node in item.iter(_tag("t"))]
        out.append("".join(parts))
    return out


def _sheet_path(archive: zipfile.ZipFile) -> str:
    """工作簿里的第一张表：优先按 workbook.xml + rels 解析，退化到 sheet1.xml。"""
    names = set(archive.namelist())
    if "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names:
        try:
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        except ElementTree.ParseError as exc:
            raise InputError("xl/workbook.xml 解析失败：{0}".format(exc))
        targets = {}
        for rel in rels:
            if rel.get("Id"):
                targets[rel.get("Id")] = rel.get("Target") or ""
        for sheet in workbook.iter(_tag("sheet")):
            rid = sheet.get("{{0}}id".format(OFFICE_R_NS))
            target = targets.get(rid)
            if not target:
                continue
            cleaned = target.replace("\\", "/")
            while cleaned.startswith("/xl/"):
                cleaned = cleaned[4:]
            path = cleaned if cleaned.startswith("xl/") else "xl/" + cleaned.lstrip("./")
            if path in names:
                return path
            raise InputError(
                "工作簿指向不存在的工作表 {0}：请另存为标准 xlsx".format(path)
            )
    if "xl/worksheets/sheet1.xml" in names:
        return "xl/worksheets/sheet1.xml"
    candidates = sorted(n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml"))
    if not candidates:
        raise InputError("这个 xlsx 里没有 xl/worksheets/ 工作表，无法逐行判读")
    return candidates[0]


def _cell_text(cell: ElementTree.Element, shared: List[str], date_styles: Set[int]) -> str:
    kind = cell.get("t", "n")
    if kind == "s":
        node = cell.find(_tag("v"))
        raw = "" if node is None else (node.text or "")
        try:
            return shared[int(raw)]
        except (TypeError, ValueError, IndexError):
            #: 越界或坏索引当文本交给行级校验：拒收的是那一行，不是一个单元格
            return raw
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(_tag("t")))
    node = cell.find(_tag("v"))
    raw = "" if node is None else (node.text or "")
    if kind == "b":
        return {"1": "1", "0": "0", "true": "1", "false": "0"}.get(raw.strip().lower(), raw)
    if kind == "e":
        return raw
    if kind in ("str",):
        return raw
    if not raw:
        return ""
    try:
        style = int(cell.get("s", "0"))
    except (TypeError, ValueError):
        style = 0
    if style in date_styles:
        return serial_to_date_text(raw)
    return raw


def load_rows(path: str, width: int) -> Tuple[List[List[str]], str]:
    """读第一张工作表，返回 (8 列单元格矩阵, 文件 sha256)。

    矩阵的行序 = 工作表里出现的行序（全空行不占号），第 1 行是表头 —— 与 CSV 的物理行号同口径。
    尾随空单元格在 XLSX 里不落盘，按空字符串补齐；出现超出表头列数的单元格则文件级硬失败。
    """
    with open(path, "rb") as handle:
        blob = handle.read()
    sha256 = hashlib.sha256(blob).hexdigest()
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise InputError(
            "{0} 不是合法 xlsx（zip 打不开：{1}）：请另存为 .xlsx 而非改扩展名".format(
                path.replace("\\", "/"), exc
            )
        )
    with archive:
        names = set(archive.namelist())
        sheet_path = _sheet_path(archive)
        try:
            sheet_root = ElementTree.fromstring(archive.read(sheet_path))
        except ElementTree.ParseError as exc:
            raise InputError("{0} 工作表 XML 解析失败：{1}".format(sheet_path, exc))
        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            try:
                shared = _shared_strings(ElementTree.fromstring(archive.read("xl/sharedStrings.xml")))
            except ElementTree.ParseError as exc:
                raise InputError("xl/sharedStrings.xml 解析失败：{0}".format(exc))
        date_styles: Set[int] = set()
        if "xl/styles.xml" in names:
            try:
                date_styles = _date_style_indexes(
                    ElementTree.fromstring(archive.read("xl/styles.xml"))
                )
            except ElementTree.ParseError as exc:
                raise InputError("xl/styles.xml 解析失败：{0}".format(exc))

        rows: List[List[str]] = []
        for row_node in sheet_root.iter(_tag("row")):
            cells = [""] * width
            populated = False
            for cell in row_node.findall(_tag("c")):
                index = _column_index(cell.get("r", ""))
                if index < 0:
                    raise InputError(
                        "{0} 的单元格缺列标（r 属性）：结构不可信，请另存为标准 xlsx".format(sheet_path)
                    )
                if index >= width:
                    raise InputError(
                        "{0} 出现第 {1} 列，超出表头 {2} 列：多余列要么删掉，要么走契约变更".format(
                            sheet_path, index + 1, width
                        )
                    )
                cells[index] = _cell_text(cell, shared, date_styles)
                populated = True
            if not populated:
                continue
            rows.append(cells)
    if not rows:
        raise InputError("{0} 是空表，没有任何观测行".format(path.replace("\\", "/")))
    return rows, sha256
