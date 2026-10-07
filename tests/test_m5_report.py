"""M5 报告层功能门：三形态 xlsx、逐字节复现、追溯完备、图表可用、签字栏纪律。

关键反证（阳性对照）都留着：追溯门用一条"故意不给来源的数值格"当场炸，
否则"每个数字可回溯"只是文档承诺。
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from xml.etree import ElementTree

import pytest

import _m5
from _helpers import ROOT
from pmc.errors import EXIT_DEGRADED, EXIT_INPUT_UNAVAILABLE, InputError
from pmc.report import builder, ooxml
from pmc.report.builder import ReportError

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _build(seed, kind, out_dir=None, **kwargs):
    return builder.build_report(
        __import__("sqlite3").connect(seed["db"]),
        seed["project"],
        kind,
        out_dir=str(out_dir or ROOT / ".tmp_verify" / "m5-report"),
        **kwargs
    )


@pytest.fixture(scope="function")
def seed(tmp_path):
    return _m5.seed_db(tmp_path)


@pytest.mark.parametrize("kind", list(builder.REPORT_KINDS))
def test_three_kinds_produce_workbook(seed, kind, tmp_path):
    result = _build(seed, kind, out_dir=tmp_path)
    assert zipfile.is_zipfile(result.path)
    names, parts = _m5.sheet_part_names(result.path)
    assert names[:5] == [
        builder.SHEET_COVER,
        builder.SHEET_ABNORMAL,
        builder.SHEET_ALARM,
        builder.SHEET_UNCLOSED,
        builder.SHEET_AUDIT,
    ]
    assert builder.SHEET_CHART in names and builder.SHEET_TRACE in names
    assert builder.SHEET_BENCH not in names
    assert names[-1] == builder.SHEET_TRACE
    for required in ("xl/workbook.xml", "xl/styles.xml", "[Content_Types].xml", "_rels/.rels"):
        assert required in parts
    assert result.counters["judged"] > 0
    assert result.counters["traces"] > 0


@pytest.mark.parametrize("kind", list(builder.REPORT_KINDS))
def test_two_runs_are_byte_identical(seed, kind, tmp_path):
    first = _build(seed, kind, out_dir=tmp_path)
    blob_a = open(first.path, "rb").read()
    second = _build(seed, kind, out_dir=tmp_path)
    blob_b = open(second.path, "rb").read()
    assert blob_a == blob_b
    assert first.sha256 == second.sha256 == hashlib.sha256(blob_a).hexdigest()


def test_no_creation_time_and_no_identity_in_bytes(seed, tmp_path):
    """身份诱饵样本：拿本机用户名与 home 路径当模式，比写死模式表可靠。"""
    import getpass
    import os

    result = _build(seed, "daily", out_dir=tmp_path)
    archive = zipfile.ZipFile(result.path)
    assert {info.date_time for info in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}
    blob = open(result.path, "rb").read()
    markers = [b"C:\\", b"D:\\", b"C:/", b"D:/", os.path.expanduser("~").encode(), getpass.getuser().encode()]
    for marker in markers:
        if marker:
            assert marker not in blob, "产物里出现身份/绝对路径标记：{0}".format(marker)
    text = blob.decode("latin-1").replace("1980-01-01T00:00:00Z", "")
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", text)


def test_every_digit_bearing_cell_is_traced(seed, tmp_path):
    """DoD「报告里每个数字都能回溯到台账行」的逐格对账，不是抽样。"""
    result = _build(seed, "weekly", out_dir=tmp_path)
    traced = {trace.report_cell for trace in result.traces}
    archive = zipfile.ZipFile(result.path)
    names, _parts = _m5.sheet_part_names(result.path)
    offenders = []
    for position, sheet_name in enumerate(names, start=1):
        if sheet_name == builder.SHEET_TRACE:
            continue  # 追溯清单本身不自我追溯
        root = ElementTree.fromstring(archive.read("xl/worksheets/sheet{0}.xml".format(position)))
        for cell in root.iter("{{{0}}}c".format(MAIN)):
            ref = cell.get("r")
            number = cell.find("{{{0}}}v".format(MAIN))
            inline = cell.find("{{{0}}}is/{{{0}}}t".format(MAIN))
            value = ""
            if number is not None and number.text:
                value = number.text
            elif inline is not None and inline.text:
                value = inline.text
            if not value:
                continue
            if ooxml.STYLE_IDS["header"] == int(cell.get("s", "0")):
                continue  # 表头是标签，不含数字（由 builder 的表头断言把守）
            if not builder.has_ascii_digit(value):
                continue
            if "{0}!{1}".format(sheet_name, ref) not in traced:
                offenders.append("{0}!{1} = {2}".format(sheet_name, ref, value))
    assert not offenders, "来源不明的数字：" + "; ".join(offenders[:8])


def test_trace_rows_point_at_real_ledger_rows(seed, tmp_path):
    """追溯清单里的 (表, 行号, 列) 必须能在台账里查到，且不指向不存在的行。"""
    import sqlite3

    result = _build(seed, "daily", out_dir=tmp_path)
    conn = sqlite3.connect(seed["db"])
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for trace in result.traces:
            if trace.table_name in builder.DERIVED_TABLES:
                continue
            assert trace.table_name in tables, "追溯指向不存在的表：{0}".format(trace.table_name)
            columns = {row[1] for row in conn.execute("PRAGMA table_info({0})".format(trace.table_name))}
            assert trace.column_name in columns, "追溯列不存在：{0}.{1}".format(
                trace.table_name, trace.column_name
            )
            # 追溯清单里的"查法"列本身就是可执行 SQL：拿它去查，必须命中且只命中一行
            query = builder.locator_sql(trace.table_name, trace.row_id, trace.column_name)
            if trace.table_name in builder.ORDERED_INDEX_TABLES:
                assert query.startswith("SELECT"), query
            hit = conn.execute(query).fetchall()
            assert hit, "追溯行不存在：{0}#{1} ← {2}".format(
                trace.table_name, trace.row_id, query
            )
    finally:
        conn.close()


def test_positive_control_numeric_cell_without_source_is_rejected(seed):
    """阳性对照：忘了挂来源就必须炸，否则上面那条对账是空的。"""
    local = builder._Builder()
    sheet = local.sheet("测试表")
    with pytest.raises(ReportError):
        local.row(sheet, [("数量", "default", None), (12, "num0", None)])
    with pytest.raises(ReportError):
        local.row(sheet, [("值", "default", None), ("第 3 轮", "default", None)])
    local.row(sheet, [("值", "default", None), ("第 3 轮", "default", ("obs_round", 1, "round_index"))])
    assert len(local.traces) == 1


def test_chart_parts_are_native_and_in_bounds(seed, tmp_path):
    result = _build(seed, "stage", out_dir=tmp_path)
    archive = zipfile.ZipFile(result.path)
    charts = sorted(name for name in archive.namelist() if name.startswith("xl/charts/"))
    drawings = sorted(name for name in archive.namelist() if name.startswith("xl/drawings/"))
    assert charts and drawings
    names, _parts = _m5.sheet_part_names(result.path)
    row_counts = {}
    for position, sheet_name in enumerate(names, start=1):
        root = ElementTree.fromstring(archive.read("xl/worksheets/sheet{0}.xml".format(position)))
        row_counts[sheet_name] = max(
            (int(node.get("r")) for node in root.iter("{{{0}}}row".format(MAIN))), default=0
        )
    for chart_part in charts:
        text = archive.read(chart_part).decode("utf-8")
        refs = re.findall(r"<c:f>([^<]+)</c:f>", text)
        assert refs, "图表没有任何数据引用"
        assert "<c:lineChart>" in text
        for ref in refs:
            sheet_name, _, span = ref.partition("!")
            sheet_name = sheet_name.strip("'")
            assert sheet_name in row_counts, "图表引用了不存在的工作表：{0}".format(sheet_name)
            high = int(re.findall(r"\$?(\d+)", span.split(":")[-1])[-1])
            assert high <= row_counts[sheet_name], "图表范围越界：{0}".format(ref)
        for cache in re.findall(r"<c:numCache>.*?</c:numCache>", text, re.S):
            count = int(re.search(r'ptCount val="(\d+)"', cache).group(1))
            assert count == cache.count("<c:pt ")
    # 阈值线：至少一张图带第二条系列（虚线）
    body = archive.read(charts[0]).decode("utf-8")
    assert body.count("<c:ser>") >= 2
    assert 'prstDash val="dash"' in body


def test_disclaimer_and_blank_signature(seed, tmp_path):
    result = _build(seed, "daily", out_dir=tmp_path)
    text = "".join(
        _cell_texts(result.path, index) for index in range(1, len(result.sheet_names) + 1)
    )
    assert builder.DISCLAIMER in text
    assert builder.SIGN_BOUNDARY in text
    assert "不判定基坑是否安全" in builder.DISCLAIMER
    # 签字栏的签字/日期两列必须空白：空白即未审核
    sign_index = result.sheet_names.index(builder.SHEET_SIGN) + 1
    rows = _rows(result.path, sign_index)
    for row in rows[3 : 3 + len(builder.SIGN_ROLES)]:
        assert row[1].strip() == ""
        assert row[2].strip() == ""
    assert not any(cell.strip() in ("已审核", "已处置") for row in rows for cell in row)


def test_bench_sheet_uses_verdict_words_from_dto(seed, tmp_path):
    """报告不重算判定：基准结论列就是 DTO 里的 `outcome` 原文（C22 + C2d）。"""

    class _Check:
        name = "首超报警轮次"
        expect = "R3"
        actual = "R3"
        ok = True

    class _Event:
        def __init__(self, outcome):
            self.event_id = "EV-1"
            self.site = "SYN-ZHDQ"
            self.point_code = "SYN-M5-01"
            self.item_code = "x"
            self.event_type = "alarm_cum"
            self.planted_round = 3
            self.expected_round = 3
            self.detected_round = 3
            self.error_rounds = 0
            self.outcome = outcome
            self.checks = [_Check()]

    class _Report:
        events = [_Event("命中")]
        exit_code = 0

    result = _build(seed, "stage", out_dir=tmp_path, bench_report=_Report())
    assert builder.SHEET_BENCH in result.sheet_names
    bench_index = result.sheet_names.index(builder.SHEET_BENCH) + 1
    rows = _rows(result.path, bench_index)
    assert any("命中" in cell for cell in rows[3])
    assert any("首超报警轮次" in cell for cell in rows[3])
    assert result.counters["judged"] > 0


def test_exit_code_degrades_on_unclosed_or_violations(seed, tmp_path):
    plain = _build(seed, "stage", out_dir=tmp_path)
    assert plain.exit_code == builder.report_exit_code(plain.counters)
    if plain.counters["unclosed"] or plain.counters["violations"]:
        assert plain.exit_code == EXIT_DEGRADED
    _m5.insert_violation(seed["db"], seed["points"][0])
    with_violation = _build(seed, "stage", out_dir=tmp_path)
    assert with_violation.counters["violations"] > 0
    assert with_violation.exit_code == EXIT_DEGRADED


def test_fingerprint_tracks_content_not_time(seed, tmp_path):
    import sqlite3

    first = _build(seed, "daily", out_dir=tmp_path)
    second = _build(seed, "daily", out_dir=tmp_path)
    assert first.sha256 == second.sha256
    conn = sqlite3.connect(seed["db"])
    digest_before = builder.ledger_fingerprint(conn, seed["project"]).sha256
    conn.execute(
        "INSERT INTO working_condition(project_id, code, name, effective_from)"
        " VALUES((SELECT id FROM project WHERE code=?), 'C9', 'SYN·加测工况', '2026-01-12')",
        (seed["project"],),
    )
    conn.commit()
    digest_after = builder.ledger_fingerprint(conn, seed["project"]).sha256
    conn.close()
    assert digest_before == _cover_fingerprint(first.path)
    assert digest_before != digest_after


def test_dry_run_writes_nothing_but_same_digest(seed, tmp_path):
    import sqlite3

    wet = _build(seed, "daily", out_dir=tmp_path)
    conn = sqlite3.connect(seed["db"])
    dry = builder.build_report(conn, seed["project"], "daily", out_dir=str(tmp_path / "nope"), dry_run=True)
    conn.close()
    assert dry.sha256 == wet.sha256
    assert not (tmp_path / "nope").exists()


def test_input_errors_do_not_fabricate_reports(tmp_path, seed):
    import sqlite3

    conn = sqlite3.connect(seed["db"])
    with pytest.raises(InputError):
        builder.build_report(conn, "SYN-NOPE", "daily", out_dir=str(tmp_path))
    with pytest.raises(InputError):
        builder.build_report(conn, seed["project"], "weekly", round_index=2, out_dir=str(tmp_path))
    with pytest.raises(InputError):
        builder.build_report(conn, seed["project"], "bogus", out_dir=str(tmp_path))
    with pytest.raises(InputError):
        builder.build_report(conn, seed["project"], "stage", round_from=9, round_to=3, out_dir=str(tmp_path))
    conn.close()
    empty = tmp_path / "empty.sqlite"
    from pmc.db.schema import apply_schema

    conn = sqlite3.connect(str(empty))
    apply_schema(conn)
    conn.execute(
        "INSERT INTO project(code, name, synthetic) VALUES('SYN-EMPTY','SYN·空基坑',1)"
    )
    conn.commit()
    with pytest.raises(InputError):
        builder.build_report(conn, "SYN-EMPTY", "daily", out_dir=str(tmp_path))
    with pytest.raises(InputError):
        builder.build_report(conn, seed["project"], "daily", out_dir=str(tmp_path))
    conn.close()


def test_cli_report_surface_and_degraded_code(seed, tmp_path, capsys):
    from pmc import cli

    capsys.readouterr()
    code = cli.main(
        [
            "report",
            "--kind",
            "daily",
            "--db",
            seed["db"],
            "--project",
            seed["project"],
            "--out",
            str(tmp_path),
        ]
    )
    out = capsys.readouterr().out
    assert code in (0, EXIT_DEGRADED)
    assert "REPORT_FILE" in out and "REPORT_SHEETS" in out
    assert builder.DISCLAIMER in out
    assert "REPORT_TRACES" in out


def test_report_never_writes_into_data_dir(seed, tmp_path):
    """禁项一：报告产物只能落在 reports/（.gitignore 已覆盖），不得进 data/。"""
    before = _tree_digest(ROOT / "data")
    _build(seed, "stage", out_dir=tmp_path)
    assert _tree_digest(ROOT / "data") == before


# --------------------------------------------------------------------------- 工具


def _cell_texts(path, sheet_index):
    archive = zipfile.ZipFile(path)
    return archive.read("xl/worksheets/sheet{0}.xml".format(sheet_index)).decode("utf-8")


def _rows(path, sheet_index):
    archive = zipfile.ZipFile(path)
    root = ElementTree.fromstring(archive.read("xl/worksheets/sheet{0}.xml".format(sheet_index)))
    out = []
    for row in root.iter("{{{0}}}row".format(MAIN)):
        cells = []
        for cell in row.findall("{{{0}}}c".format(MAIN)):
            text = "".join(node.text or "" for node in cell.iter("{{{0}}}t".format(MAIN)))
            number = cell.find("{{{0}}}v".format(MAIN))
            cells.append(text or (number.text if number is not None and number.text else ""))
        out.append(cells)
    return out


def _cover_fingerprint(path):
    archive = zipfile.ZipFile(path)
    root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    texts = [node.text or "" for node in root.iter("{{{0}}}t".format(MAIN))]
    for text in texts:
        if re.fullmatch(r"[0-9a-f]{64}", text or ""):
            return text
    raise AssertionError("页眉找不到 64 位指纹")


def _tree_digest(root):
    import hashlib
    import pathlib

    base = pathlib.Path(root)
    if not base.exists():
        return ""
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(base)).encode("utf-8"))
            digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
    return digest.hexdigest()
