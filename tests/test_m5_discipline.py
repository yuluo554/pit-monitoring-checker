"""M5 纪律门：报告/界面的用词、分层、零依赖与时钟禁令都必须能在文档里对上。

与 `test_m4_discipline.py` 同一条思路：**代码里的常量必须逐个在 plan/11 出现**，
文档是单一事实源；改了常量不写文档就是红，而不是慢慢漂移。
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from _helpers import ROOT, SRC
from pmc.db.schema import TABLE_NAMES
from pmc.report import builder, ooxml

PLAN11 = ROOT / "plan" / "11-报告与打包.md"


def _src(rel: str) -> pathlib.Path:
    return SRC / "pmc" / rel


def _tree(rel: str) -> ast.Module:
    return ast.parse(_src(rel).read_text(encoding="utf-8"), filename=rel)


def _imports(rel: str):
    found = set()
    for node in ast.walk(_tree(rel)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
    return found


def report_layer_files():
    return sorted(str(path.relative_to(SRC / "pmc")).replace("\\", "/")
                  for path in (SRC / "pmc" / "report").rglob("*.py"))


def gui_layer_files():
    return sorted(str(path.relative_to(SRC / "pmc")).replace("\\", "/")
                  for path in (SRC / "pmc" / "gui").rglob("*.py"))


# --------------------------------------------------------------------------- 零依赖


@pytest.mark.parametrize("rel", report_layer_files())
def test_report_layer_is_stdlib_only(rel):
    allowed = ("pmc.", "__future__", "zipfile", "io", "hashlib", "json", "os", "typing", "datetime", "abc")
    for name in _imports(rel):
        if name.startswith(allowed):
            continue
        raise AssertionError("{0} 引入了非标准库依赖：{1}".format(rel, name))


def test_no_third_party_xlsx_library_anywhere():
    """按 import 语句判，不按全文本判——模块注释里点名"不装什么"是文档，不是依赖。"""
    banned = {"openpyxl", "xlsxwriter", "xlwt", "xlrd", "pandas", "numpy"}
    hits = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [(node.module or "").split(".")[0]]
            for name in names:
                if name in banned:
                    hits.append("{0}:{1}".format(path.name, name))
    assert not hits, "禁第三方 xlsx 库（06 D03/D34）：" + ",".join(hits)


@pytest.mark.parametrize("rel", report_layer_files())
def test_report_layer_has_no_clock_and_no_random(rel):
    """逐字节一致的根：展示层不读时钟、不掷骰子，生成时间由用户手填。"""
    banned = {"now", "today", "utcnow", "time", "localtime", "random", "urandom"}
    offenders = []
    for node in ast.walk(_tree(rel)):
        if isinstance(node, ast.Attribute) and node.attr in banned:
            offenders.append("{0}.{1}".format(rel, node.attr))
        if isinstance(node, ast.Name) and node.id in ("random", "time", "socket", "urllib", "http"):
            offenders.append("{0}:{1}".format(rel, node.id))
    assert not offenders, "报告层禁止时钟/随机/网络：" + ",".join(offenders)


def test_zip_stamp_is_epoch_and_compression_is_fixed():
    assert ooxml.ZIP_STAMP == (1980, 1, 1, 0, 0, 0)
    assert isinstance(ooxml.COMPRESS_LEVEL, int) and 1 <= ooxml.COMPRESS_LEVEL <= 9


# --------------------------------------------------------------------------- 分层与 DTO


def test_report_does_not_import_bench_or_synth():
    for rel in report_layer_files():
        names = _imports(rel)
        for name in names:
            assert not name.startswith("pmc.bench"), "{0} 不得 import 基准层".format(rel)
            assert not name.startswith("pmc.synth"), "{0} 不得 import 合成层（C5）".format(rel)
            assert not name.startswith("pmc.gui"), "{0} 不得 import 界面层".format(rel)


def test_gui_only_consumes_the_cli_surface():
    """界面不另起第二套行为：gui 层的 pmc 依赖只允许 cli / errors / gui 内部。"""
    for rel in gui_layer_files():
        for name in _imports(rel):
            if not name.startswith("pmc."):
                continue
            tail = name.split(".")[1] if name.count(".") else ""
            assert tail in ("cli", "errors", "gui"), "{0} 越层依赖：{1}".format(rel, name)


def test_report_does_not_compute_anything_the_engine_owns():
    """报告只读落库行：不得 import 判定/检核/规则装载层。"""
    for rel in report_layer_files():
        for name in _imports(rel):
            tail = name.split(".")[1] if name.count(".") else ""
            assert tail not in ("alarm", "compliance", "rules", "selfcheck"), (
                "{0} 在报告层重算了业务判定：{1}".format(rel, name)
            )


def test_trace_rows_are_the_contract_dto():
    """C22：追溯清单只用 contract.records.TraceRow，报告层不另立 DTO。"""
    from pmc.contract import records

    assert hasattr(records, "TraceRow") and hasattr(records, "LedgerFingerprint")
    classes = {
        node.name
        for rel in report_layer_files()
        for node in ast.walk(_tree(rel))
        if isinstance(node, ast.ClassDef)
    }
    for name in classes:
        assert not name.startswith("Trace"), "报告层另立了追溯 DTO：{0}".format(name)
    assert any(
        "pmc.contract.records" in _imports(rel) for rel in report_layer_files()
    ), "报告层没有复用契约记录"


def test_derived_trace_tables_are_declared_and_documented():
    assert set(builder.DERIVED_TABLES) <= set(re.findall(r"`([a-z_]+)`", PLAN11_TEXT))
    for table in builder.DERIVED_TABLES:
        assert table not in TABLE_NAMES
        assert builder.locator_sql(table, 1, "x"), table
    # 台账表用 id 定位，派生表用复现说明定位
    assert "WHERE id=2" in builder.locator_sql("alarm_state", 2, "cum_value")
    assert "OFFSET 0" in builder.locator_sql("ruleset_applied", 1, "version")


# --------------------------------------------------------------------------- 用词与文档


PLAN11_TEXT = PLAN11.read_text(encoding="utf-8") if PLAN11.exists() else ""


def _assert_documented(token: str, where: str) -> None:
    assert token in PLAN11_TEXT, "{0}「{1}」未写进 plan/11（文档是单一事实源）".format(where, token)


def test_report_vocabulary_is_documented():
    assert PLAN11_TEXT, "缺 plan/11 打包与报告口径文档"
    for kind in builder.REPORT_KINDS:
        _assert_documented(kind, "报告形态")
    for sheet in builder.SHEET_ORDER:
        _assert_documented(sheet, "工作表名")
    for role in builder.SIGN_ROLES:
        _assert_documented(role, "签字角色")
    for reason in builder.STATE_LABELS:
        _assert_documented(reason, "状态码")
    for table in builder.DERIVED_TABLES:
        _assert_documented(table, "派生追溯表")


def test_static_text_constants_have_no_digits():
    builder.assert_vocabulary()
    for text in builder.STATIC_TEXTS:
        assert not builder.has_ascii_digit(text), text


@pytest.mark.parametrize("sentence", [builder.DISCLAIMER, builder.SIGN_BOUNDARY])
def test_boundary_sentences_are_the_required_kind(sentence):
    assert "基坑是否安全" in sentence or "未审核" in sentence
    assert not re.search(r"已确认违规|已认定超标|认定违规", sentence)


def test_state_labels_cover_exactly_the_contract_enum():
    from pmc.contract.status import ALL_STATES

    builder.assert_vocabulary()
    assert set(builder.STATE_LABELS) == set(ALL_STATES)
    assert set(builder.ABNORMAL_STATES) <= set(ALL_STATES)
    from pmc.contract.status import UNCLOSED_STATES

    assert set(UNCLOSED_STATES) <= set(builder.ABNORMAL_STATES)


def test_report_exit_code_is_documented_as_part_of_c4():
    _assert_documented("REPORT", "退出码")
    for word in ("未闭环", "待定值", "应核实"):
        assert word in PLAN11_TEXT, "报告降级口径未写全：{0}".format(word)


def test_gui_tabs_are_the_five_named_pages():
    """页签清单与规格同源：TAB_TITLES 与 pages.PAGE_SPECS 的键必须逐字一致。"""
    from pmc.gui.app import TAB_TITLES

    assert TAB_TITLES == ("台账", "导入", "判定", "检核", "报告")
    source = (SRC / "pmc" / "gui" / "pages.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="pages.py")
    keys = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "PAGE_SPECS":
            keys = [k.value for k in node.value.keys]
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", "") == "PAGE_SPECS":
                    keys = [k.value for k in node.value.keys]
    assert tuple(keys) == TAB_TITLES, "页签规格与 TAB_TITLES 漂移：{0}".format(keys)


def test_ooxml_part_names_are_deterministic():
    doc = ooxml.XlsxDoc("t")
    sheet = doc.add_sheet("表一")
    sheet.add_texts(["甲", "乙"], "header")
    doc.add_sheet("表二").add_row([ooxml.C(1, "num0")])
    first = doc.to_bytes()
    assert first == doc.to_bytes()
    import io
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(first)).namelist()
    assert names == sorted(names), "ZIP 条目顺序不固定会让字节对账失去意义"


def test_content_types_rejects_rel_override():
    with pytest.raises(ooxml.OoxmlError):
        ooxml.content_types_xml(1, ["xl/drawings/_rels/drawing1.xml.rels"])
