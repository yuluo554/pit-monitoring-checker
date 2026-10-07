"""修订链、导入回执落库与"拒收就是拒收"（plan/07 §八、03 §2.1/§2.2）。

Excel 重算会静默覆盖上一版；本题要求后到只追加、前一条可查、缺测不补值。
所以这里一半的断言是反证：想覆盖、想插值、想重复入库，都必须被拦下来。
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from _helpers import DATA, ROOT

from pmc.catalog.items import load_items
from pmc.errors import InputError
from pmc.ingest import csvio, store
from pmc.synth import freeze


@pytest.fixture(scope="module")
def items():
    return load_items(str(DATA))


@pytest.fixture(scope="module")
def archive(items):
    """一座基坑的档案：只生成一次，每个用例重建一个内存台账。"""
    from pmc.synth.generator import generate_site
    from pmc.synth.sites import get_site

    return [generate_site(get_site("SYN-ZHDQ"), items, 20260107, None)]


@pytest.fixture()
def conn(archive):
    connection = sqlite3.connect(":memory:")
    freeze.seed_ledger(connection, archive)
    return connection


def import_file(connection, path, project, round_index, items, dry_run=False):
    text, sha = csvio.load_file(path)
    archive = store.point_archive(connection, store.project_id_for(connection, project))
    parsed = csvio.parse_csv(
        text,
        source_file=path,
        file_sha256=sha,
        round_index=round_index,
        items=items,
        points=archive,
    )
    return store.write_batch(
        connection,
        project_code=project,
        round_index=round_index,
        parsed=parsed,
        dry_run=dry_run,
    )


def rows_of(connection, point_code, round_index):
    return connection.execute(
        "SELECT o.id, o.revision_seq, o.superseded_by, o.value_cum, o.missing, o.unit, o.source_row,"
        " o.batch_id FROM observation o"
        " JOIN point p ON p.id = o.point_id"
        " JOIN obs_round r ON r.id = o.round_id"
        " WHERE p.code = ? AND r.round_index = ? ORDER BY o.revision_seq",
        (point_code, round_index),
    ).fetchall()


def test_ledger_seeding_creates_no_thresholds(conn):
    row = conn.execute(
        "SELECT COUNT(*) FROM point WHERE design_cum_value IS NOT NULL"
        " OR design_rate_value IS NOT NULL OR threshold_source_kind <> 'none'"
    ).fetchone()
    assert row[0] == 0, "合成建档不得写任何控制值数值"


def test_import_accepts_clean_round(conn, items):
    receipt = import_file(
        conn, str(ROOT / "data/raw/SYN-ZHDQ/round-01.csv"), "SYN-ZHDQ", 1, items
    )
    assert receipt.rows_total == 196 and receipt.rows_accepted == 196
    assert receipt.rows_rejected == 0
    assert conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == 196


def test_dry_run_writes_nothing(conn, items):
    receipt = import_file(
        conn, str(ROOT / "data/raw/SYN-ZHDQ/round-01.csv"), "SYN-ZHDQ", 1, items, dry_run=True
    )
    assert receipt.rows_accepted == 196
    assert conn.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0] == 0


def test_planted_duplicate_report_becomes_revision_chain(conn, items):
    receipt = import_file(
        conn, str(ROOT / "data/raw/SYN-ZHDQ/round-09.csv"), "SYN-ZHDQ", 9, items
    )
    assert receipt.rows_total == 197 == receipt.rows_accepted + receipt.rows_rejected
    chain = rows_of(conn, "SYN-TH-18", 9)
    assert len(chain) == 2
    first, second = chain
    assert first[1] == 1 and first[2] == second[0], "前一条必须指向后一条，而不是被覆盖"
    assert second[1] == 2 and second[2] is None
    assert first[3] < second[3] < 40.0
    assert conn.execute(
        "SELECT COUNT(*) FROM observation WHERE superseded_by IS NOT NULL"
    ).fetchone()[0] == 1


def test_only_the_latest_revision_is_effective(conn, items):
    import_file(conn, str(ROOT / "data/raw/SYN-ZHDQ/round-09.csv"), "SYN-ZHDQ", 9, items)
    out = store.ledger_query(conn, project_code="SYN-ZHDQ", point_code="SYN-TH-18",
                             round_from=9, round_to=9)
    assert [row["revision_seq"] for row in out] == [1, 2]
    assert [row["effective"] for row in out] == [False, True]


def test_later_upload_extends_the_chain(conn, items, tmp_path):
    import_file(conn, str(ROOT / "data/raw/SYN-ZHDQ/round-09.csv"), "SYN-ZHDQ", 9, items)
    revised = "\n".join(
        [
            ",".join(csvio.HEADER),
            "9,2026-03-18,SYN-TH-18,top_h_disp,30.2,mm,0,",
        ]
    ) + "\n"
    path = tmp_path / "re-upload-09.csv"
    path.write_text(revised, encoding="utf-8")
    receipt = import_file(conn, str(path), "SYN-ZHDQ", 9, items)
    assert receipt.rows_accepted == 1
    chain = rows_of(conn, "SYN-TH-18", 9)
    assert [row[1] for row in chain] == [1, 2, 3]
    assert chain[1][2] == chain[2][0]
    assert chain[2][3] == 30.2


def test_same_file_cannot_be_imported_twice(conn, items):
    import_file(conn, str(ROOT / "data/raw/SYN-ZHDQ/round-01.csv"), "SYN-ZHDQ", 1, items)
    with pytest.raises(InputError) as exc:
        import_file(conn, str(ROOT / "data/raw/SYN-ZHDQ/round-01.csv"), "SYN-ZHDQ", 1, items)
    assert "同哈希" in exc.value.message
    assert conn.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0] == 1


def test_identical_re_upload_in_new_file_is_rejected(conn, items, tmp_path):
    import_file(conn, str(ROOT / "data/raw/SYN-ZHDQ/round-09.csv"), "SYN-ZHDQ", 9, items)
    same = "\n".join(
        [",".join(csvio.HEADER), "9,2026-03-18,SYN-TH-18,top_h_disp,26.0,mm,0,"]
    ) + "\n"
    path = tmp_path / "same-value-09.csv"
    path.write_text(same, encoding="utf-8")
    receipt = import_file(conn, str(path), "SYN-ZHDQ", 9, items)
    assert receipt.rows_accepted == 0 and receipt.rows_rejected == 1
    assert receipt.rejections[0].reason_code == "duplicate_identical"
    assert len(rows_of(conn, "SYN-TH-18", 9)) == 2, "同值重复不得产生空修订"


def test_planted_unit_error_never_reaches_the_ledger(conn, items):
    receipt = import_file(
        conn, str(ROOT / "data/raw/SYN-ZHDQ/round-12.csv"), "SYN-ZHDQ", 12, items
    )
    assert receipt.rows_rejected == 1
    assert receipt.rejections[0].reason_code == "unit_mismatch"
    assert rows_of(conn, "SYN-TH-15", 12) == []
    assert receipt.rows_total == receipt.rows_accepted + receipt.rows_rejected


def test_missing_round_is_stored_as_hole(conn, items):
    import_file(conn, str(ROOT / "data/raw/SYN-ZHDQ/round-16.csv"), "SYN-ZHDQ", 16, items)
    chain = rows_of(conn, "SYN-PF-09", 16)
    assert len(chain) == 1
    assert chain[0][4] == 1 and chain[0][3] is None
    assert chain[0][5] is None, "缺测行不得带单位"


def test_ddl_refuses_interpolated_missing_value(conn, items):
    import_file(conn, str(ROOT / "data/raw/SYN-ZHDQ/round-16.csv"), "SYN-ZHDQ", 16, items)
    pid = conn.execute("SELECT id FROM point WHERE code='SYN-PF-09'").fetchone()[0]
    rid = conn.execute(
        "SELECT id FROM obs_round WHERE round_index=17 AND project_id=("
        "SELECT id FROM project WHERE code='SYN-ZHDQ')"
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO observation(point_id, round_id, revision_seq, value_cum, unit, missing)"
            " VALUES(?,?,?,5.0,'mm',1)",
            (pid, rid, 1),
        )


def test_receipt_json_is_balanced_and_auditable(conn, items):
    import_file(conn, str(ROOT / "data/raw/SYN-ZHDQ/round-12.csv"), "SYN-ZHDQ", 12, items)
    doc = json.loads(conn.execute("SELECT receipt_json FROM import_batch").fetchone()[0])
    assert doc["rows_total"] == 196
    assert doc["rows_accepted"] + doc["rows_rejected"] == doc["rows_total"]
    assert len(doc["rejections"]) == doc["rows_rejected"]
    assert len(doc["revisions"]) == doc["rows_accepted"]
    assert doc["rejections"][0]["source_row"] == 128
    assert doc["file_sha256"] and len(doc["file_sha256"]) == 64
    stored = conn.execute("SELECT rows_total, rows_accepted, rows_rejected FROM import_batch").fetchone()
    assert stored == (196, 195, 1)


def test_batch_counts_cannot_lie(conn, items):
    """CHECK 兜底：回执不平的批次根本写不进库。"""
    import_file(conn, str(ROOT / "data/raw/SYN-ZHDQ/round-01.csv"), "SYN-ZHDQ", 1, items)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO import_batch(project_id, source_file, file_sha256, rows_total,"
            " rows_accepted, rows_rejected, receipt_json) VALUES(1,'x','y',10,9,0,'{}')",
        )


def test_unknown_project_and_round_are_refused(conn, items):
    with pytest.raises(InputError):
        import_file(conn, str(ROOT / "data/raw/SYN-ZHDQ/round-01.csv"), "SYN-NOPE", 1, items)
    with pytest.raises(InputError):
        import_file(conn, str(ROOT / "data/raw/SYN-ZHDQ/round-01.csv"), "SYN-ZHDQ", 99, items)


def test_round_date_conflict_rejects_whole_batch(conn, items, tmp_path):
    path = tmp_path / "wrong-date-03.csv"
    path.write_text(
        "\n".join([",".join(csvio.HEADER), "3,2026-03-05,SYN-TH-01,top_h_disp,1.0,mm,0,"]) + "\n",
        encoding="utf-8",
    )
    receipt = import_file(conn, str(path), "SYN-ZHDQ", 3, items)
    assert receipt.rows_accepted == 0
    assert receipt.rejections[0].reason_code == "round_not_monotonic"


def test_ledger_query_filters_by_round_window(conn, items):
    import_file(conn, str(ROOT / "data/raw/SYN-ZHDQ/round-09.csv"), "SYN-ZHDQ", 9, items)
    import_file(conn, str(ROOT / "data/raw/SYN-ZHDQ/round-10.csv"), "SYN-ZHDQ", 10, items)
    out = store.ledger_query(conn, project_code="SYN-ZHDQ", point_code="SYN-CV-01",
                             round_from=10, round_to=10)
    assert [row["round_index"] for row in out] == [10]
    assert out[0]["is_intensified"] == 1
    assert out[0]["condition_code"] == "C2"
