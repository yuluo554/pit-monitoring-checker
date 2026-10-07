"""CSV 导入器的拒收通路（plan/07 §三、§八）。

`unit_error` 与 `duplicate_report` 两类植入本来就是给导入器出的考题：
前者必须被单位校验拦下并写进回执，后者必须进修订链而不是覆盖。
这里逐个原因码跑反证，另跑阳性对照（合法行必须被接受）。
"""

from __future__ import annotations

import pytest
from _helpers import DATA

from pmc.catalog.items import load_items
from pmc.contract.records import ImportReceipt
from pmc.errors import InputError
from pmc.ingest import csvio
from pmc.synth.sites import get_site

HEADER = ",".join(csvio.HEADER)


@pytest.fixture(scope="module")
def items():
    return load_items(str(DATA))


@pytest.fixture(scope="module")
def points():
    """测点档案：从站点模板的 (项目, 数量) 反推出编号表，与生成器用的是同一套命名规则。"""
    from pmc.synth.profile import point_code

    archive = {}
    for item_code, count in get_site("SYN-ZHDQ").items:
        for seq in range(1, count + 1):
            archive[point_code(item_code, seq)] = item_code
    assert len(archive) == 196
    return archive


def build(rows, header=HEADER):
    lines = [header] if header else []
    lines.extend(rows)
    return "\n".join(lines) + "\n"


def parse(text, items, points, round_index=9):
    return csvio.parse_csv(
        text,
        source_file="memory://test.csv",
        file_sha256="0" * 64,
        round_index=round_index,
        items=items,
        points=points,
    )


def good(point="SYN-TH-01", value="3.4", unit="mm", missing="0", raw="", round_index=9, on="2026-03-18"):
    return "{0},{1},{2},top_h_disp,{3},{4},{5},{6}".format(
        round_index, on, point, value, unit, missing, raw
    )


def test_reason_code_set_is_locked():
    assert csvio.REASON_CODES == (
        "name_rule_violation",
        "unknown_item",
        "unknown_point",
        "point_item_mismatch",
        "unit_mismatch",
        "bad_number",
        "bad_date",
        "missing_flag_conflict",
        "round_mismatch",
        "round_not_monotonic",
        "duplicate_identical",
    )


def test_valid_row_is_accepted(items, points):
    parsed = parse(build([good()]), items, points)
    assert parsed.rows_total == 1
    assert parsed.rows_accepted == 1 and parsed.rows_rejected == 0
    obs = parsed.observations[0]
    assert obs.cumulative_value == 3.4 and obs.unit == "mm"
    assert obs.unit_flag == "dict" and obs.revision_seq == 1


@pytest.mark.parametrize(
    "row,reason",
    [
        ("9,2026-03-18,TH-01,top_h_disp,3.4,mm,0,", "name_rule_violation"),
        ("9,2026-03-18,SYN-TH-01,no_such_item,3.4,mm,0,", "unknown_item"),
        ("9,2026-03-18,SYN-TH-99,top_h_disp,3.4,mm,0,", "unknown_point"),
        ("9,2026-03-18,SYN-TH-01,water_level,3.4,mm,0,", "point_item_mismatch"),
        ("9,2026-03-18,SYN-WL-01,top_h_disp,3.4,m,0,", "point_item_mismatch"),
        ("9,2026-03-18,SYN-TH-01,top_h_disp,3.4,cm,0,", "unit_mismatch"),
        ("9,2026-03-18,SYN-TH-01,top_h_disp,3.4,,0,", "unit_mismatch"),
        ("10,2026-03-18,SYN-TH-01,top_h_disp,3.4,mm,0,", "round_mismatch"),
        ("9,2026-13-18,SYN-TH-01,top_h_disp,3.4,mm,0,", "bad_date"),
        ("9,2026-02-30,SYN-TH-01,top_h_disp,3.4,mm,0,", "bad_date"),
        ("9,2026-03-18,SYN-TH-01,top_h_disp,abc,mm,0,", "bad_number"),
        ("9,2026-03-18,SYN-TH-01,top_h_disp,３.４,mm,0,", "bad_number"),
        ('9,2026-03-18,SYN-TH-01,top_h_disp,"1,234",mm,0,', "bad_number"),
        ("9,2026-03-18,SYN-TH-01,top_h_disp,1.2.3,mm,0,", "bad_number"),
        ("9,2026-03-18,SYN-TH-01,top_h_disp,nan,mm,0,", "bad_number"),
        ("9,2026-03-18,SYN-TH-01,top_h_disp,inf,mm,0,", "bad_number"),
        ("9,2026-03-18,SYN-TH-01,top_h_disp,3.4,mm,1,", "missing_flag_conflict"),
        ("9,2026-03-18,SYN-TH-01,top_h_disp,,mm,0,", "missing_flag_conflict"),
        ("9,2026-03-18,SYN-TH-01,top_h_disp,3.4,mm,2,", "missing_flag_conflict"),
    ],
)
def test_each_reason_code(row, reason, items, points):
    parsed = parse(build([row]), items, points)
    assert parsed.rows_accepted == 0, reason
    assert parsed.rows_rejected == 1
    assert parsed.rejections[0].reason_code == reason
    assert parsed.rejections[0].source_row == 2


def test_source_row_counts_the_physical_line(items, points):
    bad = "9,2026-03-18,SYN-ZZZZ-01,top_h_disp,3.4,mm,0,"
    parsed = parse(build([good("SYN-TH-02"), bad]), items, points)
    assert parsed.rejections[0].source_row == 3
    assert parsed.rejections[0].reason_code == "name_rule_violation"


def test_well_formed_but_unregistered_point_is_unknown_point(items, points):
    parsed = parse(build(["9,2026-03-18,SYN-ZZ-01,top_h_disp,3.4,mm,0,"]), items, points)
    assert parsed.rejections[0].reason_code == "unknown_point"


def test_missing_row_is_accepted_without_value(items, points):
    row = "9,2026-03-18,SYN-TH-01,top_h_disp,,mm,1,未测"
    parsed = parse(build([row]), items, points)
    assert parsed.rows_accepted == 1
    obs = parsed.observations[0]
    assert obs.missing is True and obs.cumulative_value is None
    assert obs.raw_text == "未测"


def test_text_only_row_without_value_is_allowed(items, points):
    row = "9,2026-03-18,SYN-TH-01,top_h_disp,,mm,0,仪器故障"
    parsed = parse(build([row]), items, points)
    assert parsed.rows_accepted == 1
    assert parsed.observations[0].raw_text == "仪器故障"


def test_in_file_revision_chain(items, points):
    rows = [good(value="8.3"), good(value="26.0")]
    parsed = parse(build(rows), items, points)
    assert parsed.rows_accepted == 2
    assert [o.revision_seq for o in parsed.observations] == [1, 2]


def test_identical_repeat_is_rejected_not_chained(items, points):
    rows = [good(value="8.3"), good(value="8.3"), good(value="8.4")]
    parsed = parse(build(rows), items, points)
    assert parsed.rows_rejected == 1
    assert parsed.rejections[0].reason_code == "duplicate_identical"
    assert [o.revision_seq for o in parsed.observations] == [1, 2]


def test_receipt_balances_across_mixed_rows(items, points):
    rows = [
        good("SYN-TH-01"),
        good("SYN-TH-02"),
        "9,2026-03-18,SYN-TH-01,top_h_disp,3.4,cm,0,",
        "9,2026-03-18,SYN-ZZ-01,top_h_disp,3.4,mm,0,",
    ]
    parsed = parse(build(rows), items, points)
    receipt = ImportReceipt(
        source_file=parsed.source_file,
        file_sha256=parsed.file_sha256,
        rows_total=parsed.rows_total,
        rows_accepted=len(parsed.observations),
        rows_rejected=len(parsed.rejections),
        rejections=list(parsed.rejections),
    )
    receipt.validate()
    assert receipt.rows_total == 4 == receipt.rows_accepted + receipt.rows_rejected


def test_two_dates_in_one_file_is_a_hard_failure(items, points):
    rows = [good("SYN-TH-01"), good("SYN-TH-02", on="2026-03-19")]
    with pytest.raises(InputError):
        parse(build(rows), items, points)


@pytest.mark.parametrize(
    "text",
    [
        build([good()], header="round,observed_on,point_code,item_code,cumulative_value,unit,missing,raw_text"),
        build([good()], header=",".join(csvio.HEADER[:-1])),
        build(["9,2026-03-18,SYN-TH-01,top_h_disp,3.4,mm,0,extra,more"]),
        "",
    ],
    ids=["列名漂移", "缺列", "行内多列", "空文件"],
)
def test_structural_failures_are_file_level(text, items, points):
    with pytest.raises(InputError):
        parse(text, items, points)


def test_header_mismatch_reports_which_columns(tmp_path, items, points):
    path = tmp_path / "bad.csv"
    path.write_text(build([good()], header="round_index,observed_on,point_code"), encoding="utf-8")
    text, _sha = csvio.load_file(str(path))
    with pytest.raises(InputError) as exc:
        parse(text, items, points)
    assert "缺列" in exc.value.message and "cumulative_value" in exc.value.message


def test_bom_and_non_utf8_are_refused(tmp_path):
    with pytest.raises(InputError) as bom:
        csvio.load_file(_write(tmp_path, "bom.csv", b"\xef\xbb\xbf" + HEADER.encode("utf-8") + b"\n"))
    assert "BOM" in bom.value.message
    with pytest.raises(InputError) as gbk:
        payload = (HEADER + "\n" + "9,2026-03-18,SYN-TH-01,支护结构顶部水平位移,3.4,mm,0,").encode("gbk")
        csvio.load_file(_write(tmp_path, "gbk.csv", payload))
    assert "UTF-8" in gbk.value.message


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_bytes(payload)
    return str(path)


def test_frozen_unit_error_row_is_rejected_by_the_real_importer(items, points):
    """站 SYN-ZHDQ 第 12 轮里那行 planted unit_error 必须被同一套校验拦下（不是只写在文档里）。"""
    from _helpers import ROOT

    path = ROOT / "data" / "raw" / "SYN-ZHDQ" / "round-12.csv"
    text, sha = csvio.load_file(str(path))
    parsed = parse(text, items, points, round_index=12)
    parsed.file_sha256 = sha
    assert parsed.rows_total == 196
    assert len(parsed.rejections) == 1
    assert parsed.rejections[0].reason_code == "unit_mismatch"
    assert parsed.rejections[0].source_row == 128
