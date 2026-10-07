"""xlsx 写出侧：标准库 `zipfile` + 手写 OOXML（plan/06 D03 / D34，禁第三方 xlsx 库）。

与读侧 `pmc/ingest/xlsx.py` 对称的四条纪律：

* **零依赖**：只用 `zipfile` + 字符串拼接，openpyxl / xlsxwriter 一律不装；
* **无身份与时间**：`ZipInfo` 日期固定 1980-01-01，`docProps/core.xml` 只写占位符，
  生成时间只出现在正文由用户手填的那一格 —— 否则"两次导出逐字节一致"无从断言；
* **压缩级别显式写死**：`ZIP_DEFLATED` + `compresslevel=6`，同一条命令两次导出逐字节一致；
  级别不显式则跟随解释器默认，属"动了会打挂字节对账"的隐式输入；
* **不建 sharedStrings**：文本走 `inlineStr`，少一处顺序不确定源；数值一律 ASCII 字面量（C17）。
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from typing import Dict, List, Optional, Sequence, Tuple

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OFFICE_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_R_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DRAW_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
XDR_NS = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"

REL_DOC = OFFICE_R_NS + "/officeDocument"
REL_WORKSHEET = OFFICE_R_NS + "/worksheet"
REL_STYLES = OFFICE_R_NS + "/styles"
REL_DRAWING = OFFICE_R_NS + "/drawing"
REL_CHART = OFFICE_R_NS + "/chart"
REL_CORE = PACKAGE_R_NS + "/metadata/core-properties"
REL_APP = OFFICE_R_NS + "/extended-properties"

#: 冻结产物的 ZIP 时间戳：1980-01-01 00:00:00 是 ZIP 纪元起点，不是"真实时间"
ZIP_STAMP: Tuple[int, int, int, int, int, int] = (1980, 1, 1, 0, 0, 0)

#: 显式压缩级别：写死后两次导出才逐字节一致（默认值随解释器/平台构建而变）
COMPRESS_LEVEL = 6

XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

_MAX_SHEET_NAME = 31
_FORBIDDEN_IN_SHEET_NAME = "[]:*?/\\"

#: 内建样式名 → cellXfs 下标（顺序即下标，改顺序等于改所有 `s=` 引用）
STYLE_ORDER = (
    "default",
    "header",
    "title",
    "subtitle",
    "note",
    "wrap",
    "center",
    "num0",
    "num3",
    "num4",
    "flag",
)
STYLE_IDS: Dict[str, int] = {name: index for index, name in enumerate(STYLE_ORDER)}

#: 自定义数字格式号（ECMA-376 把 164 起留给自定义）
NUMFMT_DECIMAL3 = 164
NUMFMT_DECIMAL4 = 165
NUMFMT_INT = 166


class OoxmlError(Exception):
    """产物结构不合法：宁可不出报告，也不写出打不开的 xlsx。"""


def escape(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def column_name(index: int) -> str:
    """0 → A，25 → Z，26 → AA（与读侧 `_column_index` 互逆）。"""
    if index < 0:
        raise OoxmlError("列下标不得为负：{0}".format(index))
    name = ""
    probe = index
    while True:
        name = chr(ord("A") + probe % 26) + name
        probe = probe // 26 - 1
        if probe < 0:
            return name


def cell_ref(col: int, row: int) -> str:
    """`row` 从 1 起（OOXML 口径），`col` 从 0 起（本模块口径）。"""
    return "{0}{1}".format(column_name(col), row)


def absolute_range(sheet: str, col_from: int, row_from: int, col_to: int, row_to: int) -> str:
    """`'过程线数据'!$E$2:$E$21`：图表引用必须带表名与绝对符号。"""
    return "{0}!${1}${2}:${3}${4}".format(
        quote_sheet(sheet), column_name(col_from), row_from, column_name(col_to), row_to
    )


def quote_sheet(name: str) -> str:
    return "'{0}'".format(name.replace("'", "''"))


def check_sheet_name(name: str) -> None:
    if not name:
        raise OoxmlError("工作表名不得为空")
    if len(name) > _MAX_SHEET_NAME:
        raise OoxmlError("工作表名超过 {0} 字符：{1}".format(_MAX_SHEET_NAME, name))
    bad = [ch for ch in name if ch in _FORBIDDEN_IN_SHEET_NAME]
    if bad:
        raise OoxmlError("工作表名含非法字符 {0}：{1}".format("".join(bad), name))
    if name.startswith("'") or name.endswith("'"):
        raise OoxmlError("工作表名首尾不得是引号：{0}".format(name))


# --------------------------------------------------------------------------- 单元格


class Cell(object):
    """一个单元格：值 + 样式名。值类型决定 OOXML 写法，不做隐式转换。"""

    __slots__ = ("value", "style")

    def __init__(self, value, style: str = "default") -> None:
        if style not in STYLE_IDS:
            raise OoxmlError("未登记样式：{0}".format(style))
        self.value = value
        self.style = style

    @property
    def numeric(self) -> bool:
        return isinstance(self.value, (int, float)) and not isinstance(self.value, bool)

    def is_empty(self) -> bool:
        return self.value is None or self.value == ""


def C(value, style: str = "default") -> Cell:
    return Cell(value, style)


def num(value, style: str = "default") -> Cell:
    """数值格：`None` 写占位文本 `-`，绝不落成 0（待定值纪律延伸到展示层）。"""
    if value is None:
        return C("-", style)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OoxmlError("num() 只收 int/float，收到 {0}".format(type(value).__name__))
    if value != value or value in (float("inf"), float("-inf")):
        raise OoxmlError("NaN 与无穷不进报告：{0}".format(value))
    return C(value, style)


class Sheet(object):
    def __init__(self, name: str) -> None:
        check_sheet_name(name)
        self.name = name
        self.rows: List[List[Cell]] = []
        self.widths: Dict[int, float] = {}
        #: (冻结列数, 冻结行数)：None = 不冻结
        self.freeze: Optional[Tuple[int, int]] = None

    def add_row(self, cells: Sequence[Cell]) -> int:
        self.rows.append(list(cells))
        return len(self.rows)

    def add_texts(self, values: Sequence[object], style: str = "default") -> int:
        return self.add_row([C(value, style) for value in values])

    def set_widths(self, widths: Sequence[float]) -> None:
        for index, width in enumerate(widths):
            self.widths[index] = float(width)

    def last_row(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return max((len(row) for row in self.rows), default=1)

    def dimension(self) -> str:
        return "A1:{0}{1}".format(column_name(max(0, self.n_cols - 1)), max(1, len(self.rows)))


# --------------------------------------------------------------------------- 图表


class ChartSeries(object):
    def __init__(
        self,
        name: str,
        values_ref: str,
        cache: Sequence[Optional[float]],
        color: str = "1F4E79",
        dashed: bool = False,
        width_eighths: int = 22225,
    ) -> None:
        self.name = name
        self.values_ref = values_ref
        self.cache = list(cache)
        self.color = color
        self.dashed = dashed
        self.width = width_eighths


class Chart(object):
    """折线图：测点 × 轮次 × 累计值 + 阈值线（题面 01 §模块 4，plan/03 §6）。"""

    def __init__(
        self,
        title: str,
        axis_title: str,
        categories_ref: str,
        categories_cache: Sequence[str],
        series: Sequence[ChartSeries],
        anchor_from: Tuple[int, int],
        anchor_to: Tuple[int, int],
    ) -> None:
        self.title = title
        self.axis_title = axis_title
        self.categories_ref = categories_ref
        self.categories_cache = list(categories_cache)
        self.series = list(series)
        self.anchor_from = anchor_from
        self.anchor_to = anchor_to
        if not self.series:
            raise OoxmlError("图表至少一条系列")
        if not self.categories_cache:
            raise OoxmlError("图表类别轴为空")
        for item in self.series:
            if len(item.cache) != len(self.categories_cache):
                raise OoxmlError(
                    "系列 {0} 的点数({1})与类别数({2})不一致".format(
                        item.name, len(item.cache), len(self.categories_cache)
                    )
                )


# --------------------------------------------------------------------------- 文档


class XlsxDoc(object):
    def __init__(self, title: str) -> None:
        self.title = title
        self.sheets: List[Sheet] = []
        #: 工作表名 → 挂在该表上的图表列表
        self.charts: Dict[str, List[Chart]] = {}

    def sheet(self, name: str) -> Sheet:
        for item in self.sheets:
            if item.name == name:
                return item
        raise OoxmlError("工作表未登记：{0}".format(name))

    def add_sheet(self, name: str) -> Sheet:
        check_sheet_name(name)
        if any(item.name == name for item in self.sheets):
            raise OoxmlError("工作表重名：{0}".format(name))
        sheet = Sheet(name)
        self.sheets.append(sheet)
        return sheet

    def attach(self, sheet_name: str, chart: Chart) -> None:
        self.sheet(sheet_name)
        self.charts.setdefault(sheet_name, []).append(chart)

    def to_bytes(self) -> bytes:
        parts: Dict[str, str] = {}
        rels: Dict[str, List[Tuple[str, str, str]]] = {}
        chart_seq = 0

        for position, sheet in enumerate(self.sheets, start=1):
            path = "xl/worksheets/sheet{0}.xml".format(position)
            charts = self.charts.get(sheet.name, [])
            drawing_ref: Optional[str] = None
            if charts:
                drawing_ref = "rId1"
                drawing_path = "xl/drawings/drawing{0}.xml".format(position)
                drawing_rels: List[Tuple[str, str, str]] = []
                frames = []
                for chart in charts:
                    chart_seq += 1
                    rid = "rId{0}".format(len(drawing_rels) + 1)
                    chart_part = "chart{0}.xml".format(chart_seq)
                    drawing_rels.append((rid, REL_CHART, "../charts/{0}".format(chart_part)))
                    parts["xl/charts/" + chart_part] = chart_xml(chart)
                    frames.append(drawing_anchor(chart, rid))
                parts[drawing_path] = XML_HEAD + '<xdr:wsDr xmlns:xdr="{0}" xmlns:a="{1}" xmlns:r="{2}">{3}</xdr:wsDr>'.format(
                    XDR_NS, DRAW_NS, OFFICE_R_NS, "".join(frames)
                )
                rels[drawing_path] = drawing_rels
                rels[path] = [(drawing_ref, REL_DRAWING, "../drawings/drawing{0}.xml".format(position))]
            parts[path] = sheet_xml(sheet, drawing_ref)

        workbook_rels: List[Tuple[str, str, str]] = []
        sheet_entries = []
        for position, sheet in enumerate(self.sheets, start=1):
            rid = "rId{0}".format(position)
            workbook_rels.append((rid, REL_WORKSHEET, "worksheets/sheet{0}.xml".format(position)))
            sheet_entries.append(
                '<sheet name="{0}" sheetId="{1}" r:id="{2}"/>'.format(escape(sheet.name), position, rid)
            )
        workbook_rels.append(("rId{0}".format(len(workbook_rels) + 1), REL_STYLES, "styles.xml"))
        parts["xl/workbook.xml"] = XML_HEAD + '<workbook xmlns="{0}" xmlns:r="{1}"><sheets>{2}</sheets></workbook>'.format(
            MAIN_NS, OFFICE_R_NS, "".join(sheet_entries)
        )
        rels["xl/workbook.xml"] = workbook_rels
        parts["xl/styles.xml"] = styles_xml()
        parts["docProps/core.xml"] = core_xml(self.title)
        parts["docProps/app.xml"] = app_xml([sheet.name for sheet in self.sheets])
        parts["_rels/.rels"] = relationships_xml(
            [
                ("rId1", REL_DOC, "xl/workbook.xml"),
                ("rId2", REL_CORE, "docProps/core.xml"),
                ("rId3", REL_APP, "docProps/app.xml"),
            ]
        )
        for source, items in rels.items():
            parts[rel_path_for(source)] = relationships_xml(items)
        parts["[Content_Types].xml"] = content_types_xml(
            len(self.sheets),
            sorted(
                name
                for name in parts
                if name.endswith(".xml")
                and (name.startswith("xl/charts/") or name.startswith("xl/drawings/"))
            ),
        )

        buffer = io.BytesIO()
        archive = zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED)
        try:
            for name in sorted(parts):
                info = zipfile.ZipInfo(name, date_time=ZIP_STAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 0
                info.external_attr = 0o600 << 16
                archive.writestr(info, parts[name].encode("utf-8"), compresslevel=COMPRESS_LEVEL)
        finally:
            archive.close()
        return buffer.getvalue()

    def save(self, path: str) -> str:
        blob = self.to_bytes()
        with open(path, "wb") as handle:
            handle.write(blob)
        return hashlib.sha256(blob).hexdigest()


def rel_path_for(part_path: str) -> str:
    head, _, tail = part_path.rpartition("/")
    return "{0}/_rels/{1}.rels".format(head, tail)


# --------------------------------------------------------------------------- 部件


def relationships_xml(items: Sequence[Tuple[str, str, str]]) -> str:
    body = "".join(
        '<Relationship Id="{0}" Type="{1}" Target="{2}"/>'.format(rid, type_uri, escape(target))
        for rid, type_uri, target in items
    )
    return XML_HEAD + '<Relationships xmlns="{0}">{1}</Relationships>'.format(PACKAGE_R_NS, body)


def content_types_xml(sheet_count: int, media_parts: Sequence[str]) -> str:
    overrides = [
        (
            "/xl/workbook.xml",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        )
    ]
    for position in range(1, sheet_count + 1):
        overrides.append(
            (
                "/xl/worksheets/sheet{0}.xml".format(position),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
            )
        )
    overrides.append(
        ("/xl/styles.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml")
    )
    overrides.append(("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"))
    overrides.append(
        ("/docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml")
    )
    for part in media_parts:
        if part.endswith(".rels") or "_rels/" in part:
            #: 关系部件只靠 `<Default Extension="rels">` 定内容类型；给它们写 Override
            #: 会让 OPC 读成"PackageRelationship 部件内容类型不对"，整个包打不开
            raise OoxmlError("关系部件不得登记 Override：{0}".format(part))
        if part.startswith("xl/charts/"):
            ctype = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
        else:
            ctype = "application/vnd.openxmlformats-officedocument.drawing+xml"
        overrides.append(("/" + part, ctype))
    body = (
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
    )
    body += "".join(
        '<Override PartName="{0}" ContentType="{1}"/>'.format(name, ctype) for name, ctype in overrides
    )
    return XML_HEAD + '<Types xmlns="{0}">{1}</Types>'.format(CONTENT_TYPES_NS, body)


def core_xml(title: str) -> str:
    """身份纪律：creator / lastModifiedBy / created / modified 一律不留真名与真时间。"""
    return (
        XML_HEAD
        + '<cp:coreProperties'
        ' xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:dcterms="http://purl.org/dc/terms/"'
        ' xmlns:xmime="http://www.w3.org/2001/XMLSchema-instance">'
        "<dc:title>{0}</dc:title>"
        "<dc:creator>pmc</dc:creator>"
        "<cp:lastModifiedBy>pmc</cp:lastModifiedBy>"
        '<dcterms:created xmime:type="W3CDTF">1980-01-01T00:00:00Z</dcterms:created>'
        '<dcterms:modified xmime:type="W3CDTF">1980-01-01T00:00:00Z</dcterms:modified>'
        "</cp:coreProperties>"
    ).format(escape(title))


def app_xml(sheet_names: Sequence[str]) -> str:
    body = "".join("<vt:lpstr>{0}</vt:lpstr>".format(escape(name)) for name in sheet_names)
    return (
        XML_HEAD
        + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"'
        ' xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        "<Application>pmc</Application>"
        "<DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop>"
        '<HeadingPairs><vt:vector size="2" baseType="variant">'
        "<vt:lpstr>Worksheets</vt:lpstr><vt:i4>{0}</vt:i4></vt:vector></HeadingPairs>"
        '<TitlesOfParts><vt:vector size="{0}" baseType="lpstr">{1}</vt:vector></TitlesOfParts>'
        "<LinksUpToDate>false</LinksUpToDate><SharedDoc>false</SharedDoc>"
        "<HyperlinksChanged>false</HyperlinksChanged><AppVersion>1.0000</AppVersion>"
        "</Properties>"
    ).format(len(sheet_names), body)


def _number_text(value) -> str:
    if isinstance(value, int):
        return str(value)
    return repr(float(value))


def _row_xml(row_index: int, cells: Sequence[Cell]) -> str:
    parts = []
    for col, cell in enumerate(cells):
        if cell.is_empty() and cell.style == "default":
            continue
        ref = cell_ref(col, row_index)
        style = ' s="{0}"'.format(STYLE_IDS[cell.style]) if cell.style != "default" else ""
        if cell.numeric:
            parts.append('<c r="{0}"{1}><v>{2}</v></c>'.format(ref, style, _number_text(cell.value)))
        elif cell.value is None or cell.value == "":
            parts.append('<c r="{0}"{1}/>'.format(ref, style))
        else:
            parts.append(
                '<c r="{0}"{1} t="inlineStr"><is><t xml:space="preserve">{2}</t></is></c>'.format(
                    ref, style, escape(cell.value)
                )
            )
    return '<row r="{0}">{1}</row>'.format(row_index, "".join(parts))


def sheet_xml(sheet: Sheet, drawing_rid: Optional[str]) -> str:
    head = [
        '<worksheet xmlns="{0}" xmlns:r="{1}">'.format(MAIN_NS, OFFICE_R_NS),
        '<dimension ref="{0}"/>'.format(sheet.dimension()),
        '<sheetViews><sheetView workbookViewId="0">',
    ]
    if sheet.freeze:
        cols, rows = sheet.freeze
        pane = 'xSplit="{0}"'.format(cols) if cols else ""
        pane += ' ySplit="{0}"'.format(rows) if rows else ""
        state = "bottomRight" if (cols and rows) else ("bottomLeft" if cols else "topRight")
        pane += ' topLeftCell="{0}" activePane="{1}" state="frozen"'.format(
            ooxml_top_left(cols, rows), state
        )
        head.append("<pane {0}/>".format(pane))
        head.append(
            '<selection pane="{0}" activeCell="{1}" sqref="{1}"/>'.format(state, ooxml_top_left(cols, rows))
        )
    head.append("</sheetView></sheetViews>")
    head.append('<sheetFormatPr defaultRowHeight="14.5"/>')
    if sheet.widths:
        cols = []
        for index in sorted(sheet.widths):
            cols.append(
                '<col min="{0}" max="{0}" width="{1}" customWidth="1"/>'.format(
                    index + 1, _number_text(round(sheet.widths[index], 3))
                )
            )
        head.append("<cols>{0}</cols>".format("".join(cols)))
    body = "".join(_row_xml(index, row) for index, row in enumerate(sheet.rows, start=1))
    head.append("<sheetData>{0}</sheetData>".format(body))
    if drawing_rid:
        head.append('<drawing r:id="{0}"/>'.format(drawing_rid))
    head.append("</worksheet>")
    return XML_HEAD + "".join(head)


def ooxml_top_left(cols: int, rows: int) -> str:
    if cols and rows:
        return "{0}{1}".format(column_name(cols), rows + 1)
    if cols:
        return "{0}1".format(column_name(cols))
    return "A{0}".format(rows + 1)


def styles_xml() -> str:
    fonts = [
        '<font><sz val="11"/><color theme="1"/><name val="宋体"/></font>',
        '<font><b/><sz val="11"/><color theme="1"/><name val="宋体"/></font>',
        '<font><b/><sz val="16"/><color theme="1"/><name val="宋体"/></font>',
        '<font><b/><sz val="12"/><color theme="1"/><name val="宋体"/></font>',
        '<font><i/><sz val="10"/><color theme="1"/><name val="宋体"/></font>',
        '<font><sz val="11"/><color theme="1"/><name val="宋体"/></font>',
        '<font><sz val="11"/><color theme="1"/><name val="宋体"/></font>',
        '<font><sz val="11"/><color theme="1"/><name val="宋体"/></font>',
        '<font><sz val="11"/><color theme="1"/><name val="宋体"/></font>',
        '<font><sz val="11"/><color theme="1"/><name val="宋体"/></font>',
        '<font><b/><sz val="11"/><color rgb="FF9C0006"/><name val="宋体"/></font>',
    ]
    fills = [
        '<fill><patternFill patternType="none"/></fill>',
        '<fill><patternFill patternType="gray125"/></fill>',
        '<fill><patternFill patternType="solid"><fgColor rgb="FFDDEBF7"/><bgColor indexed="64"/></patternFill></fill>',
    ]
    thin_border = (
        "<border>"
        '<left style="thin"><color indexed="64"/></left>'
        '<right style="thin"><color indexed="64"/></right>'
        '<top style="thin"><color indexed="64"/></top>'
        '<bottom style="thin"><color indexed="64"/></bottom>'
        "<diagonal/>"
        "</border>"
    )
    # (numFmtId, fontId, fillId, borderId, alignment)
    xfs = [
        (0, 0, 0, 0, None),
        (0, 1, 2, 1, 'horizontal="center" vertical="center" wrapText="1"'),
        (0, 2, 0, 0, None),
        (0, 3, 0, 0, None),
        (0, 4, 0, 0, 'wrapText="1" vertical="top"'),
        (0, 5, 0, 0, 'wrapText="1" vertical="top"'),
        (0, 6, 0, 1, 'horizontal="center"'),
        (NUMFMT_INT, 7, 0, 1, None),
        (NUMFMT_DECIMAL3, 8, 0, 1, None),
        (NUMFMT_DECIMAL4, 9, 0, 1, None),
        (0, 10, 0, 1, 'horizontal="center"'),
    ]
    if len(xfs) != len(STYLE_ORDER):
        raise OoxmlError("cellXfs 条数与样式表不一致：{0} vs {1}".format(len(xfs), len(STYLE_ORDER)))
    body = [
        '<numFmts count="3">'
        '<numFmt numFmtId="{0}" formatCode="0.000"/>'
        '<numFmt numFmtId="{1}" formatCode="0.0000"/>'
        '<numFmt numFmtId="{2}" formatCode="0"/>'
        "</numFmts>".format(NUMFMT_DECIMAL3, NUMFMT_DECIMAL4, NUMFMT_INT),
        '<fonts count="{0}">{1}</fonts>'.format(len(fonts), "".join(fonts)),
        '<fills count="{0}">{1}</fills>'.format(len(fills), "".join(fills)),
        '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>{0}</borders>'.format(
            thin_border
        ),
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>',
        '<cellXfs count="{0}">'.format(len(xfs)),
    ]
    for num_fmt, font_id, fill_id, border_id, align in xfs:
        apply_align = ' applyAlignment="1"' if align else ""
        body.append(
            '<xf numFmtId="{0}" fontId="{1}" fillId="{2}" borderId="{3}" xfId="0"{4}{5}/>'.format(
                num_fmt, font_id, fill_id, border_id, apply_align, ""
            )
        )
    body.append("</cellXfs>")
    body.append('<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>')
    body.append('<dxfs count="0"/>')
    body.append('<tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>')
    return XML_HEAD + '<styleSheet xmlns="{0}">{1}</styleSheet>'.format(MAIN_NS, "".join(body))


# --------------------------------------------------------------------------- 图表 XML


def drawing_anchor(chart: Chart, rid: str) -> str:
    def corner(col: int, row: int) -> str:
        return (
            "<xdr:col>{0}</xdr:col><xdr:colOff>0</xdr:colOff>"
            "<xdr:row>{1}</xdr:row><xdr:rowOff>0</xdr:rowOff>"
        ).format(col, row)

    return (
        '<xdr:twoCellAnchor editAs="oneCell">'
        "<xdr:from>{0}</xdr:from><xdr:to>{1}</xdr:to>"
        '<xdr:graphicFrame macro="">'
        "<xdr:nvGraphicFramePr>"
        '<xdr:cNvPr id="2" name="{2}"/><xdr:cNvGraphicFramePr/>'
        "</xdr:nvGraphicFramePr>"
        '<xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm>'
        '<a:graphic><a:graphicData uri="{3}">'
        '<c:chart xmlns:c="{3}" xmlns:r="{4}" r:id="{5}"/>'
        "</a:graphicData></a:graphic>"
        "</xdr:graphicFrame>"
        "<xdr:clientData/>"
        "</xdr:twoCellAnchor>"
    ).format(
        corner(*chart.anchor_from),
        corner(*chart.anchor_to),
        escape(chart.title),
        CHART_NS,
        OFFICE_R_NS,
        rid,
    )


def _str_cache(values: Sequence[str]) -> str:
    body = "".join(
        '<c:pt idx="{0}"><c:v>{1}</c:v></c:pt>'.format(index, escape(value))
        for index, value in enumerate(values)
    )
    return '<c:strCache><c:ptCount val="{0}"/>{1}</c:strCache>'.format(len(values), body)


def _num_cache(values: Sequence[Optional[float]]) -> str:
    body = []
    for index, value in enumerate(values):
        if value is None:
            body.append('<c:pt idx="{0}"><c:v>#N/A</c:v></c:pt>'.format(index))
        else:
            body.append('<c:pt idx="{0}"><c:v>{1}</c:v></c:pt>'.format(index, _number_text(value)))
    return (
        '<c:numCache><c:formatCode>General</c:formatCode><c:ptCount val="{0}"/>{1}</c:numCache>'.format(
            len(values), "".join(body)
        )
    )


def _series_xml(index: int, chart: Chart, series: ChartSeries) -> str:
    line = '<a:ln w="{0}"><a:solidFill><a:srgbClr val="{1}"/></a:solidFill>'.format(
        series.width, series.color
    )
    if series.dashed:
        line += '<a:prstDash val="dash"/>'
    line += "</a:ln>"
    cat = "<c:cat><c:strRef><c:f>{0}</c:f>{1}</c:strRef></c:cat>".format(
        escape(chart.categories_ref), _str_cache(chart.categories_cache)
    )
    val = "<c:val><c:numRef><c:f>{0}</c:f>{1}</c:numRef></c:val>".format(
        escape(series.values_ref), _num_cache(series.cache)
    )
    return (
        "<c:ser>"
        '<c:idx val="{0}"/><c:order val="{0}"/>'
        "<c:tx><c:v>{1}</c:v></c:tx>"
        "<c:spPr>{2}</c:spPr>"
        '<c:marker><c:symbol val="none"/></c:marker>'
        "{3}{4}"
        '<c:smooth val="0"/>'
        "</c:ser>"
    ).format(index, escape(series.name), line, cat, val)


def _title_xml(text: str) -> str:
    return (
        "<c:title><c:tx><c:rich>"
        '<a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{0}</a:t></a:r></a:p>'
        "</c:rich></c:tx></c:title>"
    ).format(escape(text))


def chart_xml(chart: Chart) -> str:
    series = "".join(_series_xml(index, chart, item) for index, item in enumerate(chart.series))
    cat_ax = (
        '<c:catAx><c:axId val="100000001"/>'
        '<c:scaling><c:orientation val="minMax"/></c:scaling>'
        '<c:delete val="0"/><c:axPos val="b"/>'
        '<c:crossAx val="100000002"/>'
        "</c:catAx>"
    )
    val_ax = (
        '<c:valAx><c:axId val="100000002"/>'
        '<c:scaling><c:orientation val="minMax"/></c:scaling>'
        '<c:delete val="0"/><c:axPos val="l"/><c:majorGridlines/>'
        "{0}"
        '<c:crossAx val="100000001"/>'
        "</c:valAx>"
    ).format(_title_xml(chart.axis_title))
    return (
        XML_HEAD
        + '<c:chartSpace xmlns:c="{0}" xmlns:a="{1}" xmlns:r="{2}">'
        '<c:lang val="zh-CN"/><c:roundedCorners val="0"/>'
        "<c:chart>"
        "{3}"
        '<c:autoTitleDeleted val="0"/>'
        "<c:plotArea><c:layout/>"
        '<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>'
        "{4}"
        '<c:marker val="1"/>'
        '<c:axId val="100000001"/><c:axId val="100000002"/>'
        "</c:lineChart>"
        "{5}{6}"
        "</c:plotArea>"
        '<c:legend><c:legendPos val="r"/><c:overlay val="0"/></c:legend>'
        '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/>'
        "</c:chart>"
        '<c:spPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></c:spPr>'
        "</c:chartSpace>"
    ).format(CHART_NS, DRAW_NS, OFFICE_R_NS, _title_xml(chart.title), series, cat_ax, val_ax)
