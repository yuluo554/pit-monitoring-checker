"""回退供货的行为证明（M3 DoD 第 5 项）：同一条序列，两种阈值来源，结论必须一致。

模块 2 的选取次序是"档案设计值 → 条文标准回退 → 待定值"（`09 §二`）。
本文件证的是**回退通路本身**：档案空缺时，挂上已核对条文数值的那一档必须产出与档案档相同的结论，
并且判定行带上 `clause_ids`（题面 01 §模块 2 纪律"回退必须写明条款号"）。

规则与条款仍是内存对象（`data/` 的条款全 pending，供货就是撞禁项一）：
这里对账的是通路，不是任何规范数值。
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Tuple

import pytest
from _helpers import mem_db

from pmc.alarm.engine import AlarmEngine
from pmc.catalog.items import MonitoringItem
from pmc.contract.clauses import ClauseEntry
from pmc.contract.records import AlarmRecord, PointKey
from pmc.contract.status import ObsState
from pmc.contract.thresholds import (
    SOURCE_DESIGN,
    SOURCE_STANDARD,
    STATUS_VERIFIED,
    DualControl,
    Threshold,
)
from pmc.errors import ContractError
from pmc.rules.loader import Rule

CLAUSE = "GB50497-2019:alarm-values"
CUM_LIMIT = 25.0
RATE_LIMIT = 2.0
WINDOW_DAYS = 3.0
#: 三轮累计读数：R1 合规、R2 起超累计控制值、速率始终未超
SEQUENCE = [(1, 5.0), (2, 26.0), (3, 27.0)]


def item() -> Dict[str, MonitoringItem]:
    return {
        "top_h_disp": MonitoringItem(
            code="top_h_disp",
            name="SYN·支护结构顶部水平位移",
            unit="mm",
            control_kind="dual",
            window_basis="project_config",
            list_source="fixture",
            list_status="verified",
            clause_ids=[],
        )
    }


def verified_clause(cid: str = CLAUSE) -> ClauseEntry:
    return ClauseEntry(
        id=cid,
        standard_code="GB 50497-2019（夹具，非已核对）",
        standard_title="建筑基坑工程监测技术标准",
        clause_no="夹具条号",
        table_no="夹具表号",
        topic="报警值",
        status="verified",
        channels=[{"channel": "fixture", "locator": "fixture"}],
        retrieved_on="2026-01-01",
    )


def pending_clause(cid: str = CLAUSE) -> ClauseEntry:
    from dataclasses import replace

    return replace(verified_clause(cid), status="pending")


def standard_threshold(value: float, unit: str, cid: str = CLAUSE) -> Threshold:
    return Threshold(
        kind=SOURCE_STANDARD,
        status=STATUS_VERIFIED,
        value=value,
        unit=unit,
        clause_id=cid,
        evidence="data/clauses/register.json#{0}".format(cid),
        channel="fixture",
        url="https://example.invalid/fixture",
        verified_on="2026-01-01",
    )


def cum_rule(threshold: Optional[Threshold] = None) -> Rule:
    return Rule(
        id="fx-cum-top-h",
        name="累计量判据",
        item_code="top_h_disp",
        judgement="alarm",
        basis="cumulative",
        threshold=threshold or standard_threshold(CUM_LIMIT, "mm"),
        clause_ids=[CLAUSE],
    )


def rate_rule(threshold: Optional[Threshold] = None) -> Rule:
    return Rule(
        id="fx-rate-top-h",
        name="速率判据",
        item_code="top_h_disp",
        judgement="alarm",
        basis="rate",
        threshold=threshold or standard_threshold(RATE_LIMIT, "mm/d"),
        clause_ids=[CLAUSE],
        window_days=WINDOW_DAYS,
        window_source={"kind": "project_config", "evidence": "FIXTURE-GRADE:unit"},
    )


def build(conn: sqlite3.Connection, *, archive_values: bool) -> Dict[str, int]:
    """建一条 工程→工况→测点→3 轮 的最小链；`archive_values` 决定阈值挂在哪一档。"""
    conn.execute(
        "INSERT INTO project(code, name, scheme_no, synthetic) VALUES('SYN-P1','SYN·测试基坑',"
        " 'SYN-JK-2026-0001',1)"
    )
    project_id = conn.execute("SELECT id FROM project").fetchone()[0]
    conn.execute(
        "INSERT INTO working_condition(project_id, code, name, excavation_depth, effective_from)"
        " VALUES(?,?,?,?,?)",
        (project_id, "C1", "SYN·一层开挖", 6.0, "2026-03-01"),
    )
    condition_id = conn.execute("SELECT id FROM working_condition").fetchone()[0]
    conn.execute(
        "INSERT INTO point(project_id, code, item_code, install_date, design_cum_value,"
        " design_cum_unit, design_rate_value, design_rate_unit, threshold_source_kind,"
        " threshold_status, threshold_evidence) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            project_id, "SYN-TH-01", "top_h_disp", "2026-03-02",
            CUM_LIMIT if archive_values else None, "mm" if archive_values else None,
            RATE_LIMIT if archive_values else None, "mm/d" if archive_values else None,
            SOURCE_DESIGN if archive_values else "none",
            STATUS_VERIFIED if archive_values else "pending",
            "SYN-JK-2026-0001" if archive_values else None,
        ),
    )
    point_id = conn.execute("SELECT id FROM point").fetchone()[0]
    round_ids: Dict[int, int] = {}
    for index, value in SEQUENCE:
        day = "2026-03-0{0}".format(index + 1)
        conn.execute(
            "INSERT INTO obs_round(project_id, round_index, observed_on, condition_id)"
            " VALUES(?,?,?,?)",
            (project_id, index, day, condition_id),
        )
        round_id = conn.execute(
            "SELECT id FROM obs_round WHERE round_index = ?", (index,)
        ).fetchone()[0]
        round_ids[index] = round_id
        conn.execute(
            "INSERT INTO observation(point_id, round_id, value_cum, unit, missing)"
            " VALUES(?,?,?, ?,0)",
            (point_id, round_id, value, "mm"),
        )
    conn.commit()
    return {"project_id": project_id, "point_id": point_id, "rounds": round_ids}


def judge(conn: sqlite3.Connection, clauses: Dict[str, ClauseEntry], rules: List[Rule]):
    engine = AlarmEngine(
        conn,
        project_code="SYN-P1",
        items=item(),
        rules=rules,
        clauses=clauses,
    )
    rows = engine.judge()
    engine.persist(rows)
    return rows


def columns(rows) -> List[Tuple[object, ...]]:
    """只对账"结论"，不对账来源：来源正是两种档位要区别开的东西。"""
    return [
        (
            row.record.key.round_index,
            row.record.state,
            row.record.trigger_basis,
            row.cum_value,
            row.record.dual.cumulative.value,
            row.rate_value,
            row.record.dual.rate.value,
            row.window_days,
        )
        for row in sorted(rows, key=lambda r: r.record.key.round_index)
    ]


def test_archive_only_and_standard_fallback_reach_the_same_verdict():
    archive = mem_db()
    build(archive, archive_values=True)
    fallback = mem_db()
    build(fallback, archive_values=False)

    clauses = {CLAUSE: verified_clause()}
    rules = [cum_rule(), rate_rule()]
    rows_archive = judge(archive, clauses, rules)
    rows_fallback = judge(fallback, clauses, rules)

    assert columns(rows_archive) == columns(rows_fallback), "同一序列换档不得换结论"
    assert [c[1] for c in columns(rows_archive)] == [
        ObsState.NORMAL, ObsState.ALARM, ObsState.ALARM,
    ]
    assert columns(rows_archive)[1][4] == CUM_LIMIT, "回退档与档案档取到同一个控制值"


def test_fallback_rows_carry_the_clause_id_and_are_readable_from_the_ledger():
    conn = mem_db()
    ids = build(conn, archive_values=False)
    judge(conn, {CLAUSE: verified_clause()}, [cum_rule(), rate_rule()])
    stored = conn.execute(
        "SELECT r.round_index, a.state, a.threshold_source_kind, a.threshold_status,"
        " a.clause_ids, a.unclosed"
        " FROM alarm_state a JOIN obs_round r ON r.id = a.round_id"
        " ORDER BY r.round_index"
    ).fetchall()
    assert [row[2] for row in stored] == [SOURCE_STANDARD] * 3
    for row in stored:
        assert CLAUSE in (row[4] or ""), "按条文回退的判定行必须写明条款号"
    assert [row[1] for row in stored] == ["normal", "alarm", "alarm"]
    assert [row[5] for row in stored] == [0, 1, 1], "回退档同样参与未闭环延续"
    assert ids["point_id"]


def test_unverified_clause_cannot_supply_even_with_a_number_in_the_ruleset():
    """把 M2 的待定值纪律在 M3 的文件里再钉一次：数值来自 pending 条款 = 不生效。"""
    conn = mem_db()
    build(conn, archive_values=False)
    rows = judge(conn, {CLAUSE: pending_clause()}, [cum_rule(), rate_rule()])
    record = rows[1].record
    assert record.state == ObsState.UNDETERMINED
    assert record.dual.cumulative.value is None
    assert "clause_not_verified" in record.disabled_reasons


def test_dto_rejects_a_fallback_record_that_forgets_the_clause():
    """R3 的 DTO 反证：可用阈值来自条文却没写条款号，validate 当场抛。"""
    key = PointKey("SYN-P1", "SYN-TH-01", 2, "top_h_disp")
    dual = DualControl(cumulative=standard_threshold(CUM_LIMIT, "mm"), rate=Threshold.unbound())
    silent = AlarmRecord(
        key=key, state=ObsState.ALARM, trigger_basis="cumulative", dual=dual, clause_ids=[]
    )
    with pytest.raises(ContractError):
        silent.validate()
    documented = AlarmRecord(
        key=key,
        state=ObsState.ALARM,
        trigger_basis="cumulative",
        dual=dual,
        clause_ids=[CLAUSE],
    )
    documented.validate()


def test_ledger_check_still_rejects_a_fallback_row_without_a_clause():
    """DDL 反证（`03 §0` R3）：standard_value 且 clause_ids 为空的行写不进台账。"""
    conn = mem_db()
    ids = build(conn, archive_values=False)
    round_id = ids["rounds"][2]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO alarm_state(project_id, point_id, round_id, item_code, state,"
            " trigger_basis, threshold_source_kind, threshold_status, clause_ids)"
            " VALUES(?,?,?,?,'alarm','cumulative','standard_value','verified',NULL)",
            (ids["project_id"], ids["point_id"], round_id, "top_h_disp"),
        )
