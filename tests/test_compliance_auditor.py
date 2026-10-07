"""模块 3 检核内核的行为断言：四类时序违规的判定、门控与证据。

规则与条款在这里**直接构造内存对象**（口径同 `test_alarm_engine.py`）：
`data/` 的频率条款全是 pending，把任何天数写成 verified 都会撞禁项一，
所以这里证的是通路，不是任何规范数值（plan/08 §五）。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

import pytest
from _helpers import mem_db

from pmc.compliance import auditor
from pmc.compliance.auditor import (
    KIND_MISSED,
    KIND_NO_INTENSIFIED,
    KIND_OVER_INTERVAL,
    KIND_STALE_FREQUENCY,
    REASON_ALARM_STATE_EMPTY,
    REASON_DEPTH_UNRECORDED,
    REASON_NO_CONDITION,
    REASON_NO_INTERVAL_BAND,
    REASON_STALE_UNLABELABLE,
    ComplianceAuditor,
)
from pmc.contract.clauses import ClauseEntry
from pmc.contract.thresholds import (
    SOURCE_STANDARD,
    STATUS_PENDING,
    STATUS_VERIFIED,
    Threshold,
)
from pmc.errors import ContractError, InputError
from pmc.rules.loader import Rule

CLAUSE_FREQ = "GB50497-2019:monitoring-frequency"
CLAUSE_NOTICE = "MOHURD-37"


def entry(cid: str, status: str = STATUS_VERIFIED) -> ClauseEntry:
    return ClauseEntry(
        id=cid,
        standard_code="SYN·夹具标准",
        standard_title="夹具条款（非已核对条文）",
        topic="监测频率",
        status=status,
        channels=[{"channel": "fixture", "locator": "fixture"}],
        retrieved_on="2026-01-01",
    )


def clauses(*ids: str, status: str = STATUS_VERIFIED) -> Dict[str, ClauseEntry]:
    return {cid: entry(cid, status) for cid in ids}


def band(
    limit: Optional[float],
    depth_max: Optional[float] = None,
    rule_id: str = "FREQ-MAX-INTERVAL-TEST",
    status: str = STATUS_VERIFIED,
) -> Rule:
    return Rule(
        id=rule_id,
        name="观测间隔上限（夹具）",
        item_code="all_items",
        judgement="frequency",
        basis="interval",
        depth_max=depth_max,
        threshold=Threshold(
            kind=SOURCE_STANDARD,
            status=status,
            value=limit,
            unit="d" if limit is not None else None,
            clause_id=CLAUSE_FREQ,
            evidence="fixture",
            channel="fixture",
            url="https://example.invalid/fixture",
            verified_on="2026-01-01" if status == STATUS_VERIFIED else None,
        ),
        clause_ids=[CLAUSE_FREQ],
    )


def seq(
    rule_id: str, clause_ids: Sequence[str] = (CLAUSE_FREQ,), status: str = STATUS_VERIFIED
) -> Rule:
    return Rule(
        id=rule_id,
        name="时序检核（夹具）",
        item_code="all_items",
        judgement="frequency",
        basis="sequence",
        threshold=Threshold(kind="none", status=status),
        clause_ids=list(clause_ids),
    )


def all_rules(**kwargs) -> List[Rule]:
    """默认三档深度（≤5m 7 天 / ≤10m 3 天 / 更深 2 天）+ 三条时序规则。"""
    return [
        band(kwargs.get("shallow", 7.0), 5.0, rule_id="FREQ-MAX-INTERVAL-D5"),
        band(kwargs.get("mid", 3.0), 10.0, rule_id="FREQ-MAX-INTERVAL-D10"),
        band(kwargs.get("deep", 2.0), None, rule_id="FREQ-MAX-INTERVAL-DEEP"),
        seq(auditor.RULE_ID_MISSED),
        seq(auditor.RULE_ID_CONDITION_CHANGE),
        seq(auditor.RULE_ID_INTENSIFY_AFTER_ALARM, (CLAUSE_FREQ, CLAUSE_NOTICE)),
    ]


# ---- 台账构造 ---------------------------------------------------------------


def build_project(conn, conditions, rounds, point_codes, observations=()):
    """conditions: [(code, depth, effective_from)]；rounds: [(idx, date, cond_code, intensified)]。

    `observations=None` 给每个"测点 × 轮次"补一行有效读数，让用例只关心自己要测的那一类。
    """
    conn.execute("INSERT INTO project(code, name, synthetic) VALUES('SYN-P1','SYN·测试基坑',1)")
    project_id = conn.execute("SELECT id FROM project").fetchone()[0]
    cond_ids: Dict[str, int] = {}
    for code, depth, effective_from in conditions:
        conn.execute(
            "INSERT INTO working_condition(project_id, code, name, excavation_depth,"
            " effective_from) VALUES(?,?,?,?,?)",
            (project_id, code, "SYN·工况 " + code, depth, effective_from),
        )
        cond_ids[code] = conn.execute(
            "SELECT id FROM working_condition WHERE code = ?", (code,)
        ).fetchone()[0]
    round_ids: Dict[int, int] = {}
    for index, day, cond_code, intensified in rounds:
        conn.execute(
            "INSERT INTO obs_round(project_id, round_index, observed_on, condition_id,"
            " is_intensified) VALUES(?,?,?,?,?)",
            (
                project_id,
                index,
                day,
                None if cond_code is None else cond_ids[cond_code],
                intensified,
            ),
        )
        round_ids[index] = conn.execute(
            "SELECT id FROM obs_round WHERE round_index = ?", (index,)
        ).fetchone()[0]
    point_ids: Dict[str, int] = {}
    for code in point_codes:
        conn.execute(
            "INSERT INTO point(project_id, code, item_code, install_date)"
            " VALUES(?,?,?,?)",
            (project_id, code, "top_h_disp", rounds[0][1]),
        )
        point_ids[code] = conn.execute(
            "SELECT id FROM point WHERE code = ?", (code,)
        ).fetchone()[0]
    if observations is None:
        observations = [
            (code, index, 1.0) for code in point_codes for index, _d, _c, _i in rounds
        ]
    for code, index, value in observations:
        conn.execute(
            "INSERT INTO observation(point_id, round_id, value_cum, unit, missing)"
            " VALUES(?,?,?, ?,0)",
            (point_ids[code], round_ids[index], value, "mm"),
        )
    conn.commit()
    return {"project_id": project_id, "cond_ids": cond_ids, "round_ids": round_ids,
            "point_ids": point_ids}


def mark_missing(conn, point_ids, round_ids, code, index):
    conn.execute(
        "INSERT INTO observation(point_id, round_id, value_cum, unit, raw_text, missing)"
        " VALUES(?,?,NULL,NULL,?,1)",
        (point_ids[code], round_ids[index], "未测"),
    )
    conn.commit()


def set_unclosed_alarm(conn, ids, code, index, first_index=None):
    """直接写一条未闭环报警行：模块 3 只消费模块 2 的落库结果，不在这里重算。"""
    conn.execute(
        "INSERT INTO alarm_state(project_id, point_id, round_id, item_code, state,"
        " trigger_basis, threshold_source_kind, threshold_status, clause_ids, unclosed,"
        " first_alarm_round_id) VALUES(?,?,?,?,'alarm','cumulative','user_input',"
        " 'verified','GB50497-2019:alarm-values',?,?)",
        (
            ids["project_id"],
            ids["point_ids"][code],
            ids["round_ids"][index],
            "top_h_disp",
            1,
            ids["round_ids"][first_index or index],
        ),
    )
    conn.commit()


def set_closed_alarm(conn, ids, code, index):
    """一条已处置（unclosed=0）的判定行：让检核读到"跑过 pmc check"而不是空表。"""
    conn.execute(
        "INSERT INTO alarm_state(project_id, point_id, round_id, item_code, state,"
        " trigger_basis, threshold_source_kind, threshold_status, clause_ids, unclosed)"
        " VALUES(?,?,?,?,'alarm_handled','cumulative','user_input','verified',"
        " 'GB50497-2019:alarm-values',0)",
        (
            ids["project_id"],
            ids["point_ids"][code],
            ids["round_ids"][index],
            "top_h_disp",
        ),
    )
    conn.commit()


def weekly(rounds: int, step: int, start: str = "2026-03-02") -> List[Tuple[int, str, str, int]]:
    from datetime import date, timedelta

    year, month, day = (int(part) for part in start.split("-"))
    base = date(year, month, day)
    return [
        (index, "{0:04d}-{1:02d}-{2:02d}".format(
            *(base + timedelta(days=step * (index - 1))).timetuple()[:3]), None, 0)
        for index in range(1, rounds + 1)
    ]


def audit(conn, rules=None, clauses_arg=None, **kwargs):
    result, _counts = auditor.run_audit(
        conn,
        project_code="SYN-P1",
        rules=rules if rules is not None else all_rules(),
        clauses=clauses_arg if clauses_arg is not None else clauses(CLAUSE_FREQ, CLAUSE_NOTICE),
        **kwargs
    )
    return result


# ---- 漏测 -------------------------------------------------------------------


def test_missed_flags_both_missing_mark_and_absent_row():
    conn = mem_db()
    rows = weekly(3, 7)
    rows[0] = (1, "2026-03-02", "C1", 0)
    ids = build_project(
        conn,
        [("C1", 4.0, "2026-03-01")],
        [(index, day, "C1", 0) for index, day, _c, _i in rows],
        ["SYN-TH-01", "SYN-TH-02"],
        observations=[("SYN-TH-01", 1, 1.0), ("SYN-TH-02", 1, 1.0),
                      ("SYN-TH-01", 2, 1.1), ("SYN-TH-02", 3, 1.2)],
    )
    mark_missing(conn, ids["point_ids"], ids["round_ids"], "SYN-TH-01", 3)

    result = audit(conn)
    missed = [v for v in result.violations if v.kind == KIND_MISSED]
    by = {(v.point_code, v.round_index): v for v in missed}
    assert by[("SYN-TH-02", 2)].evidence["gap"] == "no_effective_observation"
    assert by[("SYN-TH-01", 3)].evidence["gap"] == "missing_flag_marked"
    assert ("SYN-TH-01", 2) not in by and ("SYN-TH-02", 3) not in by, "有有效读数的组合不算漏测"
    assert len(missed) == 2
    assert all(v.rule_id == auditor.RULE_ID_MISSED for v in missed)


def test_whole_round_without_any_row_is_one_project_level_item():
    """整轮零入库更可能是"还没导入"：出一条工程级应核实事项，不逐测点刷屏。"""
    conn = mem_db()
    build_project(
        conn,
        [("C1", 4.0, "2026-03-01")],
        [(1, "2026-03-02", "C1", 0), (2, "2026-03-09", "C1", 0)],
        ["SYN-TH-0{0}".format(n) for n in range(1, 6)],
        observations=[("SYN-TH-0{0}".format(n), 1, 1.0) for n in range(1, 6)],
    )
    result = audit(conn)
    missed = [v for v in result.violations if v.kind == KIND_MISSED]
    assert [v.round_index for v in missed] == [2]
    assert missed[0].point_id is None
    assert missed[0].evidence["gap"] == "round_not_imported"
    assert missed[0].evidence["points_expected"] == 5


def test_missed_ignores_points_installed_after_the_round():
    conn = mem_db()
    ids = build_project(
        conn,
        [("C1", 4.0, "2026-03-01")],
        [(1, "2026-03-02", "C1", 0), (2, "2026-03-09", "C1", 0)],
        ["SYN-TH-01"],
        observations=[("SYN-TH-01", 1, 1.0), ("SYN-TH-01", 2, 1.1)],
    )
    conn.execute("UPDATE point SET install_date = '2026-03-10'")
    conn.commit()
    result = audit(conn)
    assert result.violations == []
    assert ids["project_id"]


# ---- 间隔与工况变更 ----------------------------------------------------------


def test_over_interval_within_a_condition_segment():
    conn = mem_db()
    build_project(
        conn,
        [("C1", 8.0, "2026-03-01")],
        [(index, day, "C1", 0) for index, day, _c, _i in weekly(4, 5)],
        ["SYN-TH-01"],
        observations=None,
    )
    result = audit(conn)
    over = [v for v in result.violations if v.kind == KIND_OVER_INTERVAL]
    assert [v.round_index for v in over] == [2, 3, 4], "首轮没有前一轮可比"
    assert over[0].evidence["gap_days"] == 5
    assert over[0].evidence["limit_days"] == 3.0
    assert over[0].evidence["band_rule_id"] == "FREQ-MAX-INTERVAL-D10"
    assert over[0].rule_id == "FREQ-MAX-INTERVAL-D10"


def test_condition_change_is_judged_against_the_new_limit_that_round():
    conn = mem_db()
    build_project(
        conn,
        [("C1", 4.0, "2026-03-01"), ("C2", 12.0, "2026-03-09")],
        [
            (1, "2026-03-02", "C1", 0),
            (2, "2026-03-09", "C2", 0),
            (3, "2026-03-16", "C2", 0),
        ],
        ["SYN-TH-01"],
        observations=None,
    )
    result = audit(conn)
    kinds = {v.round_index: v.kind for v in result.violations}
    assert kinds == {2: KIND_STALE_FREQUENCY, 3: KIND_OVER_INTERVAL}, "变更当轮即按新频率判"
    stale = [v for v in result.violations if v.kind == KIND_STALE_FREQUENCY][0]
    assert stale.rule_id == auditor.RULE_ID_CONDITION_CHANGE
    assert stale.evidence["prev_condition_code"] == "C1"
    assert stale.evidence["condition_code"] == "C2"
    assert stale.evidence["limit_days"] == 2.0, "变更后的第一轮用新工况的深度档"


def test_tighter_cadence_after_change_is_clean():
    conn = mem_db()
    build_project(
        conn,
        [("C1", 4.0, "2026-03-01"), ("C2", 12.0, "2026-03-03")],
        [
            (1, "2026-03-01", "C1", 0),
            (2, "2026-03-03", "C2", 0),
            (3, "2026-03-05", "C2", 0),
        ],
        ["SYN-TH-01"],
        observations=None,
    )
    assert audit(conn).violations == []


def test_stale_label_falls_back_and_notes_when_change_rule_blocked():
    conn = mem_db()
    build_project(
        conn,
        [("C1", 4.0, "2026-03-01"), ("C2", 12.0, "2026-03-09")],
        [
            (1, "2026-03-02", "C1", 0),
            (2, "2026-03-09", "C2", 0),
        ],
        ["SYN-TH-01"],
        observations=None,
    )
    rules = [r for r in all_rules() if r.id != auditor.RULE_ID_CONDITION_CHANGE]
    result = audit(conn, rules=rules)
    assert [v.kind for v in result.violations] == [KIND_OVER_INTERVAL]
    assert any(code == REASON_STALE_UNLABELABLE for code, _ in result.notes)


def test_unmatched_depth_and_missing_condition_and_depth_are_reported_not_invented():
    conn = mem_db()
    build_project(
        conn,
        [("C1", None, "2026-03-01")],
        [
            (1, "2026-02-01", "C1", 0),
            (2, "2026-03-09", "C1", 0),
        ],
        ["SYN-TH-01"],
        observations=None,
    )
    rules = [r for r in all_rules() if r.depth_max is not None]
    result = audit(conn, rules=rules)
    codes = {code for code, _detail in result.notes}
    assert REASON_DEPTH_UNRECORDED in codes, "档案没登记深度：只能按兜底档判并说明"
    assert REASON_NO_INTERVAL_BAND in codes or result.violations == []

    conn2 = mem_db()
    build_project(
        conn2,
        [("C1", 4.0, "2026-03-05")],
        [(1, "2026-03-02", "C1", 0), (2, "2026-03-09", "C1", 0)],
        ["SYN-TH-01"],
    )
    result2 = audit(conn2)
    assert any(code == REASON_NO_CONDITION for code, _ in result2.notes), "轮次早于工况生效日期不硬编"


def test_band_without_any_fallback_blocks_the_interval_check_entirely():
    conn = mem_db()
    build_project(
        conn,
        [("C1", 30.0, "2026-03-01")],
        [(1, "2026-03-02", "C1", 0), (2, "2026-03-16", "C1", 0)],
        ["SYN-TH-01"],
        observations=None,
    )
    rules = [r for r in all_rules() if r.depth_max is not None]
    result = audit(conn, rules=rules)
    assert result.violations == []
    assert [code for code, _ in result.notes] == [REASON_NO_INTERVAL_BAND], "取不到档就不判，不拿默认天数冒充"


# ---- 报警后加密观测 ----------------------------------------------------------


def test_intensified_round_after_alarm_clears_the_item():
    conn = mem_db()
    ids = build_project(
        conn,
        [("C1", 4.0, "2026-03-01")],
        [
            (1, "2026-03-02", "C1", 0),
            (2, "2026-03-04", "C1", 0),
            (3, "2026-03-05", "C1", 1),
        ],
        ["SYN-TH-01", "SYN-TH-02"],
    )
    set_unclosed_alarm(conn, ids, "SYN-TH-01", 2)
    set_unclosed_alarm(conn, ids, "SYN-TH-02", 2, first_index=2)
    result = audit(conn, round_from=1, round_to=3)
    assert [v for v in result.violations if v.kind == KIND_NO_INTENSIFIED] == []


def test_unclosed_alarm_without_intensified_round_is_flagged_with_clauses():
    conn = mem_db()
    ids = build_project(
        conn,
        [("C1", 4.0, "2026-03-01")],
        [
            (1, "2026-03-02", "C1", 0),
            (2, "2026-03-04", "C1", 0),
            (3, "2026-03-06", "C1", 0),
        ],
        ["SYN-TH-01"],
    )
    set_unclosed_alarm(conn, ids, "SYN-TH-01", 2)
    result = audit(conn)
    flagged = [v for v in result.violations if v.kind == KIND_NO_INTENSIFIED]
    assert len(flagged) == 1
    assert flagged[0].rule_id == auditor.RULE_ID_INTENSIFY_AFTER_ALARM
    assert set(flagged[0].clause_ids) == {CLAUSE_FREQ, CLAUSE_NOTICE}
    assert flagged[0].evidence["first_alarm_round_index"] == 2
    assert flagged[0].evidence["unclosed_rounds"] == 1
    assert flagged[0].evidence["intensified_rounds_in_scope"] == 0


def test_empty_alarm_state_says_so_instead_of_passing():
    """没跑 pmc check 与跑了但一切正常，是两件事：前者必须显式说明。"""
    conn = mem_db()
    build_project(
        conn,
        [("C1", 4.0, "2026-03-01")],
        [(1, "2026-03-02", "C1", 0), (2, "2026-03-04", "C1", 0)],
        ["SYN-TH-01"],
        observations=[("SYN-TH-01", 1, 1.0), ("SYN-TH-01", 2, 1.1)],
    )
    result = audit(conn)
    assert result.violations == []
    assert [code for code, _ in result.notes] == [REASON_ALARM_STATE_EMPTY]
    assert auditor.degraded_exit(result) == 1


def test_closed_alarms_are_not_carried_into_the_list():
    conn = mem_db()
    ids = build_project(
        conn,
        [("C1", 4.0, "2026-03-01")],
        [(1, "2026-03-02", "C1", 0), (2, "2026-03-04", "C1", 0)],
        ["SYN-TH-01"],
    )
    set_unclosed_alarm(conn, ids, "SYN-TH-01", 2)
    conn.execute("UPDATE alarm_state SET unclosed = 0")
    conn.commit()
    result = audit(conn)
    assert [v for v in result.violations if v.kind == KIND_NO_INTENSIFIED] == []


# ---- 门控、落库与范围 --------------------------------------------------------


def test_pending_clause_produces_no_verdict_and_fills_the_queue():
    conn = mem_db()
    build_project(
        conn,
        [("C1", 4.0, "2026-03-01"), ("C2", 12.0, "2026-03-09")],
        [(1, "2026-03-02", "C1", 0), (2, "2026-03-09", "C2", 0)],
        ["SYN-TH-01", "SYN-TH-02"],
    )
    result = audit(conn, clauses_arg=clauses(CLAUSE_FREQ, CLAUSE_NOTICE, status=STATUS_PENDING))
    assert result.violations == []
    assert len(result.blocked) == 6
    assert {reason for _id, reason in result.blocked} == {"clause_not_verified"}
    assert auditor.degraded_exit(result) == 1


def test_violations_are_persisted_with_evidence_and_rerun_replaces_them():
    conn = mem_db()
    build_project(
        conn,
        [("C1", 8.0, "2026-03-01")],
        [(index, day, "C1", 0) for index, day, _c, _i in weekly(3, 5)],
        ["SYN-TH-01"],
        observations=None,
    )
    auditor.run_audit(
        conn,
        project_code="SYN-P1",
        rules=all_rules(),
        clauses=clauses(CLAUSE_FREQ, CLAUSE_NOTICE),
    )
    rows = conn.execute(
        "SELECT kind, rule_id, clause_ids, evidence_json FROM violation ORDER BY kind, round_id"
    ).fetchall()
    assert len(rows) == 2
    for _kind, _rule_id, clause_ids, evidence in rows:
        assert clause_ids == CLAUSE_FREQ
        doc = json.loads(evidence)
        assert {"gap_days", "limit_days", "condition_code", "prev_round_index"} <= set(doc)
    result, counts = auditor.run_audit(
        conn,
        project_code="SYN-P1",
        rules=all_rules(),
        clauses=clauses(CLAUSE_FREQ, CLAUSE_NOTICE),
    )
    assert counts == {"deleted": 2, "inserted": 2}, "整段重算：不累积上一轮的清单"
    assert len(result.violations) == 2


def test_violation_without_clause_cannot_be_built():
    conn = mem_db()
    build_project(
        conn,
        [("C1", 4.0, "2026-03-01")],
        [(1, "2026-03-02", "C1", 0), (2, "2026-03-09", "C1", 0)],
        ["SYN-TH-01"],
    )
    rule = seq(auditor.RULE_ID_MISSED, clause_ids=())
    with pytest.raises(ContractError):
        auditor.make_violation(
            KIND_MISSED,
            rule,
            project_code="SYN-P1",
            clause_ids=(),
            evidence={"detail": "无条款号的检核结论"},
        )
    result = audit(conn, rules=[rule])
    assert result.violations == []
    assert (auditor.RULE_ID_MISSED, "clause_id_missing") in result.blocked


def test_ddl_rejects_a_violation_row_without_clauses():
    """R3 的存储层反证：clause_ids NOT NULL，检核结论不可能无声落库。"""
    conn = mem_db()
    ids = build_project(
        conn,
        [("C1", 4.0, "2026-03-01")],
        [(1, "2026-03-02", "C1", 0)],
        ["SYN-TH-01"],
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO violation(project_id, kind, rule_id, clause_ids, evidence_json)"
            " VALUES(?,?,?,?,?)",
            (ids["project_id"], KIND_MISSED, "FREQ-MISSED-ROUND", None, "{}"),
        )


def test_round_scope_keeps_the_previous_round_as_evidence():
    conn = mem_db()
    build_project(
        conn,
        [("C1", 8.0, "2026-03-01")],
        [(index, day, "C1", 0) for index, day, _c, _i in weekly(4, 5)],
        ["SYN-TH-01"],
        observations=None,
    )
    result = audit(conn, round_from=3, round_to=4)
    over = [v for v in result.violations if v.kind == KIND_OVER_INTERVAL]
    assert [v.round_index for v in over] == [3, 4]
    assert over[0].evidence["prev_round_index"] == 2, "范围只筛清单，不切断时间线"
    assert result.rounds_audited == 2


def test_project_without_rounds_is_input_unavailable():
    conn = mem_db()
    conn.execute("INSERT INTO project(code, name, synthetic) VALUES('SYN-P1','SYN·空',1)")
    conn.commit()
    with pytest.raises(InputError):
        auditor.run_audit(
            conn, project_code="SYN-P1", rules=all_rules(),
            clauses=clauses(CLAUSE_FREQ, CLAUSE_NOTICE),
        )
    with pytest.raises(InputError):
        auditor.run_audit(
            conn, project_code="SYN-NOPE", rules=all_rules(),
            clauses=clauses(CLAUSE_FREQ, CLAUSE_NOTICE),
        )


def test_clean_project_exits_zero():
    conn = mem_db()
    ids = build_project(
        conn,
        [("C1", 4.0, "2026-03-01")],
        [(index, day, "C1", 0) for index, day, _c, _i in weekly(3, 7)],
        ["SYN-TH-01"],
        observations=[("SYN-TH-01", n, 1.0) for n in (1, 2, 3)],
    )
    set_closed_alarm(conn, ids, "SYN-TH-01", 3)
    result = audit(conn)
    assert result.violations == []
    assert result.blocked == []
    assert result.notes == []
    assert auditor.degraded_exit(result) == 0
    assert auditor.kind_counts([result]) == {}


def test_auditor_exposes_grouping_by_clause_for_the_report():
    conn = mem_db()
    ids = build_project(
        conn,
        [("C1", 4.0, "2026-03-01")],
        [(1, "2026-03-02", "C1", 0), (2, "2026-03-04", "C1", 0)],
        ["SYN-TH-01"],
        observations=None,
    )
    set_unclosed_alarm(conn, ids, "SYN-TH-01", 2)
    result = audit(conn)
    grouped = result.grouped_by_clause()
    assert set(grouped) == {CLAUSE_FREQ, CLAUSE_NOTICE}
    assert len(grouped[CLAUSE_NOTICE]) == 1
    assert result.counts_by_kind() == {KIND_NO_INTENSIFIED: 1}


def test_gate_is_the_same_single_point_as_the_alarm_rules():
    """检核不另起炉灶：同一套 gate 决定频率规则能不能进判定路径。"""
    from pmc.rules.gate import rule_enabled

    ok, reason = rule_enabled(band(None, None, status=STATUS_PENDING), clauses(CLAUSE_FREQ))
    assert not ok and reason == "source_not_confirmed"
    with pytest.raises(ContractError):
        rule_enabled(
            seq(auditor.RULE_ID_INTENSIFY_AFTER_ALARM, (CLAUSE_FREQ, "K")),
            clauses(CLAUSE_FREQ),
        ), "条款未在登记表出现就不是可检核的依据"
    ok, reason = rule_enabled(
        seq(auditor.RULE_ID_INTENSIFY_AFTER_ALARM),
        clauses(CLAUSE_FREQ, status=STATUS_PENDING),
    )
    assert not ok and reason == "clause_not_verified"
