"""台账 DDL 的结构与 CHECK 断言：纪律落到存储层，而不是文档承诺。"""

from __future__ import annotations

import sqlite3

import pytest
from _helpers import mem_db, seed_project_chain

from pmc import SCHEMA_VERSION
from pmc.contract.thresholds import SOURCE_DESIGN, SOURCE_NONE, SOURCE_STANDARD
from pmc.db.schema import TABLE_NAMES, apply_schema, missing_tables
from pmc.errors import ContractError


def _alarm(conn, ids, **kw):
    row = dict(
        project_id=ids["project_id"],
        point_id=ids["point_id"],
        round_id=ids["round_id"],
        item_code="top_h_disp",
        state="undetermined",
        trigger_basis="none",
        threshold_source_kind=SOURCE_NONE,
        threshold_status="pending",
        unclosed=0,
    )
    row.update(kw)
    cols = ",".join(row)
    marks = ",".join("?" * len(row))
    conn.execute(
        "INSERT INTO alarm_state({0}) VALUES({1})".format(cols, marks), tuple(row.values())
    )


def test_apply_schema_creates_every_table():
    conn = mem_db()
    assert missing_tables(conn) == []
    assert len(TABLE_NAMES) == 11
    conn.close()


def test_apply_schema_is_idempotent_and_version_checked():
    conn = mem_db()
    apply_schema(conn)
    assert missing_tables(conn) == []
    conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    conn.commit()
    with pytest.raises(ContractError):
        apply_schema(conn)
    conn.close()


def test_schema_version_matches_package_constant():
    conn = mem_db()
    got = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    assert got == str(SCHEMA_VERSION)
    conn.close()


def test_state_enum_is_enforced_by_check():
    conn = mem_db()
    ids = seed_project_chain(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _alarm(conn, ids, state="超预警")
    conn.close()


def test_undetermined_row_must_have_empty_thresholds():
    conn = mem_db()
    ids = seed_project_chain(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _alarm(conn, ids, cum_threshold=12.0)
    conn.close()


def test_undetermined_row_must_not_carry_trigger_basis():
    conn = mem_db()
    ids = seed_project_chain(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _alarm(conn, ids, trigger_basis="cumulative")
    conn.close()


def test_decided_row_must_have_threshold_source():
    conn = mem_db()
    ids = seed_project_chain(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _alarm(conn, ids, state="alarm", trigger_basis="cumulative", cum_value=3.0,
               cum_threshold=2.0)
    conn.close()


def test_decided_row_with_design_source_is_accepted():
    conn = mem_db()
    ids = seed_project_chain(conn)
    _alarm(
        conn,
        ids,
        state="alarm",
        trigger_basis="cumulative",
        cum_value=3.0,
        cum_threshold=2.0,
        threshold_source_kind=SOURCE_DESIGN,
        threshold_status="verified",
        clause_ids=None,
    )
    conn.close()


def test_standard_source_row_must_carry_clause_ids():
    conn = mem_db()
    ids = seed_project_chain(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _alarm(
            conn,
            ids,
            state="alarm",
            trigger_basis="cumulative",
            cum_threshold=2.0,
            threshold_source_kind=SOURCE_STANDARD,
            threshold_status="verified",
            clause_ids="",
        )
    conn.close()


def test_duplicate_round_report_goes_to_revision_chain_not_overwrite():
    conn = mem_db()
    ids = seed_project_chain(conn)
    conn.execute(
        "INSERT INTO observation(point_id, round_id, revision_seq, value_cum, unit, source_row)"
        " VALUES(?,?,?,?,?,?)",
        (ids["point_id"], ids["round_id"], 1, 2.5, "mm", 2),
    )
    cur = conn.execute(
        "INSERT INTO observation(point_id, round_id, revision_seq, value_cum, unit, source_row)"
        " VALUES(?,?,?,?,?,?)",
        (ids["point_id"], ids["round_id"], 2, 2.7, "mm", 2),
    )
    conn.execute(
        "UPDATE observation SET superseded_by=? WHERE point_id=? AND round_id=? AND revision_seq=1",
        (cur.lastrowid, ids["point_id"], ids["round_id"]),
    )
    rows = conn.execute(
        "SELECT revision_seq, value_cum, superseded_by FROM observation ORDER BY revision_seq"
    ).fetchall()
    assert [r[0] for r in rows] == [1, 2]
    assert rows[0][2] == cur.lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO observation(point_id, round_id, revision_seq, value_cum, unit)"
            " VALUES(?,?,?,?,?)",
            (ids["point_id"], ids["round_id"], 1, 9.9, "mm"),
        )
    conn.close()


def test_missing_flag_and_value_are_mutually_exclusive():
    conn = mem_db()
    ids = seed_project_chain(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO observation(point_id, round_id, revision_seq, value_cum, unit, missing)"
            " VALUES(?,?,?,?,?,1)",
            (ids["point_id"], ids["round_id"], 1, 3.0, "mm"),
        )
    conn.execute(
        "INSERT INTO observation(point_id, round_id, revision_seq, missing, raw_text)"
        " VALUES(?,?,?,1,?)",
        (ids["point_id"], ids["round_id"], 1, "—"),
    )
    conn.close()


def test_import_receipt_must_balance_in_db():
    conn = mem_db()
    ids = seed_project_chain(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO import_batch(project_id, source_file, file_sha256, rows_total,"
            " rows_accepted, rows_rejected, receipt_json) VALUES(?,?,?,?,?,?,?)",
            (ids["project_id"], "a.csv", "0" * 64, 5, 3, 1, "{}"),
        )
    conn.close()


def test_disposition_requires_existing_alarm_and_actor_role():
    conn = mem_db()
    ids = seed_project_chain(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO disposition(alarm_id, kind, actor_role) VALUES(?,?,?)",
            (999, "handle", "监测单位专业工程师"),
        )
    _alarm(conn, ids)
    alarm_id = conn.execute("SELECT id FROM alarm_state").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO disposition(alarm_id, kind, actor_role) VALUES(?,?,?)",
            (alarm_id, "handle", None),
        )
    conn.execute(
        "INSERT INTO disposition(alarm_id, kind, actor_role) VALUES(?,?,?)",
        (alarm_id, "handle", "监测单位专业工程师"),
    )
    conn.close()
