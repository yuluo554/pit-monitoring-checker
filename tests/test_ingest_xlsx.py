"""XLSX 输入通路：结构坏了就整文件失败，一行填错就行级拒收，且与 CSV 共用同一套口径。

`plan/09 §十` 的落地断言。核心一条：判读口径只有一套 ——
`xlsx.py` 产出的就是 `csvio.parse_rows` 的输入，11 个 reason_code 一个不多一个不少（口径 C15）。
"""

from __future__ import annotations

import csv
import os
import sqlite3

import pytest
from _helpers import DATA, mem_db
from _xlsx import date_serial, write_xlsx

from pmc import cli
from pmc.catalog.items import load_items
from pmc.ingest import csvio, store, xlsx
from pmc.errors import InputError
from pmc.synth import freeze

SEED = 20260107
HEADER = csvio.HEADER


def read_csv_rows(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return [row for row in csv.reader(handle)]


def seeded_db(tmp_path, site="SYN-ZHDQ", sites=2, tag="a"):
    db = str(tmp_path / "ledger-{0}-{1}-{2}.sqlite".format(site, sites, tag))
    assert cli.main(["init", "--db", db]) == 0
    conn = sqlite3.connect(db)
    try:
        datas = freeze.build(str(DATA), SEED, sites)[0]
        freeze.seed_ledger(conn, datas)
    finally:
        conn.close()
    return db


# ---- 与 CSV 的逐行一致 ------------------------------------------------------


def test_same_content_xlsx_imports_identically_to_csv(tmp_path):
    """同一份内容：CSV 与 XLSX 导入后的台账行与回执逐行一致（DoD 第 8 条）。"""
    csv_path = os.path.join(str(DATA), "raw", "SYN-ZHDQ", "round-09.csv")
    rows = read_csv_rows(csv_path)
    xlsx_path = write_xlsx(str(tmp_path / "round-09.xlsx"), rows)

    csv_db = seeded_db(tmp_path, "SYN-ZHDQ", tag="csv")
    xlsx_db = seeded_db(tmp_path, "SYN-ZHDQ", tag="xlsx")
    assert cli.main(
        ["import", csv_path, "--project", "SYN-ZHDQ", "--round", "9", "--db", csv_db]
    ) == 0
    assert cli.main(
        ["import", xlsx_path, "--project", "SYN-ZHDQ", "--round", "9", "--db", xlsx_db]
    ) == 0

    conn_csv, conn_xlsx = sqlite3.connect(csv_db), sqlite3.connect(xlsx_db)
    try:
        left = store.ledger_query(conn_csv, project_code="SYN-ZHDQ", point_code=None,
                                  round_from=9, round_to=9)
        right = store.ledger_query(conn_xlsx, project_code="SYN-ZHDQ", point_code=None,
                                   round_from=9, round_to=9)
        assert left == right and len(left) == 197, "第 9 轮含一条重复上报，链上共 197 行"
        receipts = [
            conn.execute(
                "SELECT rows_total, rows_accepted, rows_rejected FROM import_batch"
            ).fetchall()
            for conn in (conn_csv, conn_xlsx)
        ]
        assert receipts[0] == receipts[1] == [(197, 197, 0)]
        heads = [
            conn.execute(
                "SELECT COUNT(*) FROM observation WHERE superseded_by IS NULL AND revision_seq = 2"
            ).fetchone()[0]
            for conn in (conn_csv, conn_xlsx)
        ]
        assert heads[0] == heads[1] == 1, "修订链形状也要一致"
    finally:
        conn_csv.close()
        conn_xlsx.close()


def test_rejected_rows_carry_the_same_reason_codes_as_csv(tmp_path):
    """六个行级故障在 XLSX 上的原因码与 CSV 完全一致，优先级顺序也不变。"""
    cases = [
        ("9", "2026-03-18", "SYN-TH-01", "top_h_disp", "3.4", "cm", "0", ""),
        ("9", "2026-03-18", "SYN-TH-01", "top_h_disp", "３.４", "mm", "0", ""),
        ("9", "2026-03-18", "SYN-TH-01", "top_h_disp", "3.4", "mm", "2", ""),
        ("9", "2026-03-18", "SYN-TH-01", "top_h_disp", "3.4", "mm", "1", "4.0"),
        ("9", "2026-13-18", "SYN-TH-01", "top_h_disp", "3.4", "mm", "0", ""),
        ("9", "2026-03-18", "TH-01", "top_h_disp", "3.4", "mm", "0", ""),
    ]
    rows = [HEADER] + [tuple(case) for case in cases]
    text = "\n".join(",".join(row) for row in rows) + "\n"
    db = seeded_db(tmp_path, "SYN-ZHDQ", tag="reasons")
    csv_file = str(tmp_path / "bad.csv")
    with open(csv_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    xlsx_file = write_xlsx(str(tmp_path / "bad.xlsx"), rows)

    def receipts(path):
        conn = sqlite3.connect(db)
        try:
            conn.execute("DELETE FROM import_batch")
            conn.execute("DELETE FROM observation")
            conn.commit()
        finally:
            conn.close()
        code = cli.main(
            ["import", path, "--project", "SYN-ZHDQ", "--round", "9", "--db", db]
        )
        conn = sqlite3.connect(db)
        try:
            doc = conn.execute("SELECT receipt_json FROM import_batch").fetchone()[0]
        finally:
            conn.close()
        import json

        receipt = json.loads(doc)
        assert code == 1
        return [(item["source_row"], item["reason_code"]) for item in receipt["rejections"]]

    from_csv = receipts(csv_file)
    from_xlsx = receipts(xlsx_file)
    assert from_csv == from_xlsx
    assert [reason for _, reason in from_csv] == [
        "unit_mismatch",
        "bad_number",
        "missing_flag_conflict",
        "missing_flag_conflict",
        "bad_date",
        "name_rule_violation",
    ]
    assert csvio.REASON_CODES == (
        "name_rule_violation", "unknown_item", "unknown_point", "point_item_mismatch",
        "unit_mismatch", "bad_number", "bad_date", "missing_flag_conflict",
        "round_mismatch", "round_not_monotonic", "duplicate_identical",
    ), "XLSX 通路不许长出新原因码"


# ---- 文件级硬失败 ------------------------------------------------------------


def test_header_mismatch_fails_the_whole_file(tmp_path):
    """表头列名与契约不符：结构不可信，整文件硬失败，不落批次。"""
    rows = [
        (
            "round_index", "observed_on", "point_code", "item_code", "cumulative_value",
            "unit", "missing", "remark",
        ),
        ("1", "2026-03-02", "SYN-TH-01", "top_h_disp", "1.0", "mm", "0", ""),
    ]
    path = write_xlsx(str(tmp_path / "bad-header.xlsx"), rows)
    loaded, sha = xlsx.load_rows(path, len(HEADER))
    assert loaded == [list(row) for row in rows], "读侧只还原矩阵，判读交给 csvio"
    db = seeded_db(tmp_path, "SYN-ZHDQ")
    assert cli.main(
        ["import", path, "--project", "SYN-ZHDQ", "--round", "1", "--db", db]
    ) == 2
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == 0
    finally:
        conn.close()
    with pytest.raises(InputError) as exc:
        csvio.parse_rows(
            loaded,
            source_file=path,
            file_sha256=sha,
            round_index=1,
            items=load_items(str(DATA)),
            points={"SYN-TH-01": "top_h_disp"},
        )
    assert "remark" in str(exc.value) and "raw_text" in str(exc.value)


def test_extra_column_beyond_the_contract_is_a_file_level_failure(tmp_path):
    rows = [HEADER] + [tuple(["1", "2026-03-02", "SYN-TH-01", "top_h_disp", "1.0", "mm", "0", "", "多一列"])]
    path = write_xlsx(str(tmp_path / "wide.xlsx"), rows)
    with pytest.raises(InputError) as exc:
        xlsx.load_rows(path, len(HEADER))
    assert "超出表头" in str(exc.value)


def test_not_a_zip_is_a_file_level_failure(tmp_path):
    path = tmp_path / "fake.xlsx"
    path.write_bytes(b"this is not a zip archive")
    with pytest.raises(InputError) as exc:
        xlsx.load_rows(str(path), len(HEADER))
    assert "不是合法 xlsx" in str(exc.value)


def test_sheet_without_any_row_is_a_file_level_failure(tmp_path):
    path = write_xlsx(str(tmp_path / "empty.xlsx"), [])
    with pytest.raises(InputError) as exc:
        xlsx.load_rows(path, len(HEADER))
    assert "空表" in str(exc.value)


# ---- 单元格形态 --------------------------------------------------------------


def test_date_serial_numbers_become_iso_dates(tmp_path):
    rows = [HEADER, ("1", "2026-03-02", "SYN-TH-01", "top_h_disp", "1.5", "mm", "0", "")]
    path = write_xlsx(str(tmp_path / "dates.xlsx"), rows, date_columns=(1,))
    loaded, _ = xlsx.load_rows(path, len(HEADER))
    assert loaded == [list(row) for row in rows], (
        "序列号换算回来必须与 CSV 文本逐字符相同"
    )
    assert date_serial("2026-03-02") > 60


def test_phantom_leap_serial_is_rejected_as_bad_date(tmp_path):
    """Excel 的 60 号是 1900-02-29，一个不存在的日子：如实交出去，由行级校验拒收。"""
    assert xlsx.serial_to_date_text("60") == "1900-02-29"
    for text in ("1900-01-01", "1900-02-28", "1900-03-01", "2024-02-29", "2026-03-02", "2099-12-31"):
        assert xlsx.serial_to_date_text(str(date_serial(text))) == text
    assert xlsx.serial_to_date_text("45720.75").startswith("2025-03-04"), "带时间的序列号取日部分"
    assert xlsx.serial_to_date_text("abc") == "abc"
    assert xlsx.serial_to_date_text("0") == "0"


def test_inline_strings_and_missing_trailing_cells(tmp_path):
    rows = [
        list(HEADER),
        ["1", "2026-03-02", "SYN-TH-01", "top_h_disp", "1.5", "mm", "0", "备注"],
    ]
    path = write_xlsx(str(tmp_path / "inline.xlsx"), rows, inline_row=2)
    loaded, _ = xlsx.load_rows(path, len(HEADER))
    assert loaded[1][-1] == "备注", "内联字符串也要读得到"
    assert loaded[0] == list(HEADER)


def test_trailing_empty_cells_are_padded_not_ragged(tmp_path):
    """尾随空单元格在 xlsx 里不落盘：补空串后仍是 8 列，交给行级校验而不是结构失败。"""
    rows = [HEADER, ("1", "2026-03-02", "SYN-TH-01", "top_h_disp", "", "mm", "0", "")]
    path = write_xlsx(str(tmp_path / "sparse.xlsx"), rows)
    loaded, _ = xlsx.load_rows(path, len(HEADER))
    assert len(loaded[1]) == 8 and loaded[1][4] == ""
    with pytest.raises(InputError):
        csvio.parse_rows(
            loaded + [["1", "2026-03-02", "x"]],
            source_file=path, file_sha256="0" * 64, round_index=1, items={}, points={},
        )


def test_sha256_is_over_the_container_not_the_sheet(tmp_path):
    path = write_xlsx(str(tmp_path / "h.xlsx"), [HEADER])
    _, first = xlsx.load_rows(path, len(HEADER))
    assert len(first) == 64 and first == first.lower()
    with open(path, "rb") as handle:
        import hashlib

        assert first == hashlib.sha256(handle.read()).hexdigest()
