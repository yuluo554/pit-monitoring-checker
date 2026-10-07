"""判定内核的行为断言：有效阈值选取次序、双控合成、速率窗口、待定值脱空。

规则与条款在这里**直接构造内存对象**：`data/` 的条款全是 pending，
把任何数值写成 verified 都会撞禁项一，所以标准回退档只在内存里证通路。
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

import pytest
from _helpers import mem_db

from pmc.alarm.engine import (
    BASIS_BOTH,
    BASIS_CUM,
    BASIS_RATE,
    REASON_NO_APPLICABLE_RULE,
    REASON_UNIT_INCONSISTENT,
    AlarmEngine,
)
from pmc.catalog.items import MonitoringItem
from pmc.contract.clauses import ClauseEntry
from pmc.contract.status import ObsState
from pmc.contract.thresholds import (
    REASON_CLAUSE_MISSING,
    REASON_NOT_VERIFIED,
    SOURCE_DESIGN,
    SOURCE_STANDARD,
    SOURCE_USER,
    STATUS_PENDING,
    STATUS_VERIFIED,
    Threshold,
)
from pmc.errors import ContractError, InputError
from pmc.rules.loader import Rule

CLAUSE = "GB50497-2019:alarm-values"


def item(code="top_h_disp", unit="mm", control="dual") -> MonitoringItem:
    return MonitoringItem(
        code=code,
        name="SYN·测试项目",
        unit=unit,
        control_kind=control,
        window_basis="project_config" if control in ("dual", "rate") else "none",
        list_source="fixture",
        list_status="verified",
        clause_ids=[],
    )


ITEMS = {
    "top_h_disp": item(),
    "crack_width": item("crack_width", "mm", "cumulative"),
    "water_level": item("water_level", "m"),
}


def verified_clause(cid=CLAUSE) -> ClauseEntry:
    return ClauseEntry(
        id=cid,
        standard_code="GB 50497-2019",
        standard_title="建筑基坑工程监测技术标准",
        topic="报警值",
        status="verified",
        channels=[{"channel": "fixture", "locator": "fixture"}],
        retrieved_on="2026-01-01",
    )


def pending_clause(cid=CLAUSE) -> ClauseEntry:
    from dataclasses import replace

    return replace(verified_clause(cid), status="pending")


def std_threshold(value=25.0, unit="mm", status=STATUS_VERIFIED, cid=CLAUSE) -> Threshold:
    return Threshold(
        kind=SOURCE_STANDARD,
        status=status,
        value=value,
        unit=unit,
        clause_id=cid,
        evidence="data/clauses/register.json#{0}".format(cid),
        channel="fixture",
        url="https://example.invalid/fixture",
        verified_on="2026-01-01" if status == STATUS_VERIFIED else None,
    )


def rate_rule(window=3.0, threshold=None, code="top_h_disp", cid=CLAUSE) -> Rule:
    return Rule(
        id="fx-rate-{0}".format(code),
        name="速率判据",
        item_code=code,
        judgement="alarm",
        basis="rate",
        threshold=threshold if threshold is not None else std_threshold(1.5, "mm/d"),
        clause_ids=[cid] if cid else [],
        window_days=window,
        window_source={"kind": "project_config", "evidence": "FIXTURE-GRADE:unit"},
    )


def cum_rule(threshold=None, code="top_h_disp", cid=CLAUSE) -> Rule:
    return Rule(
        id="fx-cum-{0}".format(code),
        name="累计量判据",
        item_code=code,
        judgement="alarm",
        basis="cumulative",
        threshold=threshold if threshold is not None else std_threshold(25.0, "mm"),
        clause_ids=[cid] if cid else [],
    )


def ratio_rule(value=0.7, usable=True, code="top_h_disp") -> Rule:
    threshold = Threshold(
        kind=SOURCE_USER,
        status=STATUS_VERIFIED if usable else STATUS_PENDING,
        value=value if usable else None,
        unit="ratio",
        evidence="FIXTURE-GRADE:unit" if usable else None,
    )
    return Rule(
        id="fx-ratio-{0}".format(code),
        name="预警比例",
        item_code=code,
        judgement="prewarning",
        basis="ratio",
        threshold=threshold,
    )


# ---- 台账最小链 --------------------------------------------------------------


def build(project_code="SYN-P1"):
    conn = mem_db()
    conn.execute(
        "INSERT INTO project(code, name, synthetic) VALUES(?, 'SYN·测试基坑', 1)", (project_code,)
    )
    project_id = conn.execute("SELECT id FROM project").fetchone()[0]
    conn.execute(
        "INSERT INTO working_condition(project_id, code, name, effective_from)"
        " VALUES(?,?,?,?)",
        (project_id, "C1", "SYN·工况", "2026-03-01"),
    )
    condition_id = conn.execute("SELECT id FROM working_condition").fetchone()[0]
    conn.commit()
    return conn, project_id, condition_id


def add_round(conn, project_id, index, observed_on, condition_id):
    conn.execute(
        "INSERT INTO obs_round(project_id, round_index, observed_on, condition_id)"
        " VALUES(?,?,?,?)",
        (project_id, index, observed_on, condition_id),
    )
    return conn.execute("SELECT id FROM obs_round WHERE round_index=?", (index,)).fetchone()[0]


def add_point(
    conn,
    project_id,
    code,
    item_code="top_h_disp",
    cum=None,
    rate=None,
    cum_unit="mm",
    rate_unit="mm/d",
    kind="none",
    status=STATUS_PENDING,
    evidence=None,
    clause_ids=None,
):
    conn.execute(
        "INSERT INTO point(project_id, code, item_code, design_cum_value, design_cum_unit,"
        " design_rate_value, design_rate_unit, threshold_source_kind, threshold_status,"
        " threshold_evidence, clause_ids) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            project_id,
            code,
            item_code,
            cum,
            cum_unit if cum is not None else None,
            rate,
            rate_unit if rate is not None else None,
            kind,
            status,
            evidence,
            clause_ids,
        ),
    )
    return conn.execute("SELECT id FROM point WHERE code=?", (code,)).fetchone()[0]


def observe(conn, point_id, round_id, value=None, missing=False, raw=None):
    if missing:
        conn.execute(
            "INSERT INTO observation(point_id, round_id, value_cum, unit, raw_text, missing)"
            " VALUES(?,?,NULL,NULL,?,1)",
            (point_id, round_id, "未测"),
        )
    else:
        conn.execute(
            "INSERT INTO observation(point_id, round_id, value_cum, unit, missing)"
            " VALUES(?,?,?, ?,0)",
            (point_id, round_id, value, "mm"),
        )
    return conn.execute(
        "SELECT id FROM observation WHERE point_id=? AND round_id=?"
        " ORDER BY revision_seq DESC",
        (point_id, round_id),
    ).fetchone()[0]


def judge(conn, project_code, rules, clauses=None, items=None):
    engine = AlarmEngine(
        conn,
        project_code=project_code,
        items=items or ITEMS,
        rules=rules,
        clauses=clauses if clauses is not None else {CLAUSE: verified_clause()},
    )
    return engine.judge()


def by_round(rows):
    return {row.record.key.round_index: row for row in rows}


# ---- 选取次序 ----------------------------------------------------------------


def test_archive_value_beats_ruleset_fallback():
    conn, project_id, condition_id = build()
    round_id = add_round(conn, project_id, 1, "2026-03-01", condition_id)
    point_id = add_point(
        conn,
        project_id,
        "SYN-TH-01",
        cum=10.0,
        kind=SOURCE_DESIGN,
        status=STATUS_VERIFIED,
        evidence="SYN-JK-2026-0001",
    )
    observe(conn, point_id, round_id, value=12.0)
    rows = judge(conn, "SYN-P1", [cum_rule(std_threshold(25.0))])
    record = rows[0].record
    assert record.dual.cumulative.value == 10.0, "档案设计值优先于规则回退档"
    assert record.state == ObsState.ALARM
    assert record.trigger_basis == BASIS_CUM


def test_standard_fallback_needs_a_verified_clause_and_carries_the_clause_id():
    conn, project_id, condition_id = build()
    round_id = add_round(conn, project_id, 1, "2026-03-01", condition_id)
    point_id = add_point(conn, project_id, "SYN-TH-01")
    observe(conn, point_id, round_id, value=30.0)
    rows = judge(conn, "SYN-P1", [cum_rule(), rate_rule()])
    record = rows[0].record
    assert record.state == ObsState.ALARM
    assert record.dual.cumulative.kind == SOURCE_STANDARD
    assert CLAUSE in record.clause_ids, "按条文回退的判定必须写明条款号"


def test_pending_clause_blocks_the_fallback_and_says_why():
    conn, project_id, condition_id = build()
    round_id = add_round(conn, project_id, 1, "2026-03-01", condition_id)
    point_id = add_point(conn, project_id, "SYN-TH-01")
    observe(conn, point_id, round_id, value=30.0)
    rows = judge(
        conn, "SYN-P1", [cum_rule(), rate_rule()], clauses={CLAUSE: pending_clause()}
    )
    record = rows[0].record
    assert record.state == ObsState.UNDETERMINED
    assert record.dual.cumulative.value is None, "待定值的阈值列必须为空"
    assert record.dual.rate.value is None
    assert "clause_not_verified" in record.disabled_reasons, "条文未核对不得供货数值"


def test_everything_unbound_is_undetermined_with_reasons():
    conn, project_id, condition_id = build()
    round_id = add_round(conn, project_id, 1, "2026-03-01", condition_id)
    point_id = add_point(conn, project_id, "SYN-TH-01")
    observe(conn, point_id, round_id, value=30.0)
    rows = judge(conn, "SYN-P1", [cum_rule(std_threshold(25.0, status=STATUS_PENDING))])
    record = rows[0].record
    assert record.state == ObsState.UNDETERMINED
    assert REASON_NOT_VERIFIED in record.disabled_reasons
    assert REASON_NO_APPLICABLE_RULE in record.disabled_reasons, "累计判据没有规则可回退"
    assert record.trigger_basis == "none"
    with pytest.raises(ContractError):
        record.__class__(
            key=record.key,
            state=ObsState.UNDETERMINED,
            trigger_basis="cumulative",
            dual=record.dual,
            disabled_reasons=[REASON_NO_APPLICABLE_RULE],
        ).validate()


def test_partial_basis_keeps_conclusion_and_records_reason():
    """累计可判、速率没配窗口：按累计出结论，待定侧阈值留空并记 window_unconfigured（降级 1）。"""
    conn, project_id, condition_id = build()
    for index, day in ((1, "2026-03-01"), (2, "2026-03-02")):
        add_round(conn, project_id, index, day, condition_id)
    point_id = add_point(
        conn,
        project_id,
        "SYN-TH-01",
        cum=25.0,
        kind=SOURCE_USER,
        status=STATUS_VERIFIED,
        evidence="FIXTURE-GRADE:unit",
    )
    r1, r2 = (
        conn.execute("SELECT id FROM obs_round WHERE round_index=?", (i,)).fetchone()[0]
        for i in (1, 2)
    )
    observe(conn, point_id, r1, value=1.0)
    observe(conn, point_id, r2, value=26.0)
    rows = by_round(judge(conn, "SYN-P1", [cum_rule(std_threshold(999.0))]))
    first, second = rows[1].record, rows[2].record
    assert first.state == ObsState.NORMAL and second.state == ObsState.ALARM
    assert "window_unconfigured" in second.disabled_reasons
    assert second.dual.rate.value is None, "待定侧的阈值列脱空"
    assert second.dual.cumulative.value == 25.0


def test_unit_inconsistent_archive_value_is_not_supplied():
    conn, project_id, condition_id = build()
    round_id = add_round(conn, project_id, 1, "2026-03-01", condition_id)
    point_id = add_point(
        conn,
        project_id,
        "SYN-TH-01",
        cum=25.0,
        cum_unit="cm",
        kind=SOURCE_DESIGN,
        status=STATUS_VERIFIED,
        evidence="SYN-JK-2026-0001",
    )
    observe(conn, point_id, round_id, value=30.0)
    rows = judge(conn, "SYN-P1", [])
    record = rows[0].record
    assert record.state == ObsState.UNDETERMINED
    assert REASON_UNIT_INCONSISTENT in record.disabled_reasons


def test_archive_registered_as_standard_value_cannot_supply():
    """point 表没有 channel/url/verified_on 三列：档案登记成标准条文一律不供货（plan/09 §2.1）。"""
    conn, project_id, condition_id = build()
    round_id = add_round(conn, project_id, 1, "2026-03-01", condition_id)
    point_id = add_point(
        conn,
        project_id,
        "SYN-TH-01",
        cum=25.0,
        kind=SOURCE_STANDARD,
        status=STATUS_VERIFIED,
        evidence="x",
        clause_ids=CLAUSE,
    )
    observe(conn, point_id, round_id, value=30.0)
    rows = judge(conn, "SYN-P1", [])
    assert REASON_CLAUSE_MISSING in rows[0].record.disabled_reasons or rows[
        0
    ].record.state == ObsState.UNDETERMINED
    assert rows[0].record.state == ObsState.UNDETERMINED


# ---- 速率窗口 ----------------------------------------------------------------


def test_rate_window_is_calendar_days_and_skips_the_missing_round(conn=None):
    """窗口按日历日：缺测的中间轮不补值，速率照旧跨空洞取窗口内实际存在的读数。"""
    conn, project_id, condition_id = build()
    days = {1: "2026-03-01", 2: "2026-03-02", 3: "2026-03-03"}
    for index, day in sorted(days.items()):
        add_round(conn, project_id, index, day, condition_id)
    point_id = add_point(
        conn,
        project_id,
        "SYN-TH-01",
        rate=1.5,
        kind=SOURCE_USER,
        status=STATUS_VERIFIED,
        evidence="FIXTURE-GRADE:unit",
    )
    rounds = {
        index: conn.execute(
            "SELECT id FROM obs_round WHERE round_index=?", (index,)
        ).fetchone()[0]
        for index in days
    }
    observe(conn, point_id, rounds[1], value=0.0)
    observe(conn, point_id, rounds[2], missing=True)
    observe(conn, point_id, rounds[3], value=4.5)
    rows = by_round(judge(conn, "SYN-P1", [rate_rule(window=3.0)]))
    assert 2 not in rows, "缺测轮次不出判定行，也不被插值补出一个数"
    assert rows[3].record.state == ObsState.ALARM
    assert rows[3].record.trigger_basis == BASIS_RATE
    assert rows[3].rate_value == pytest.approx(2.25), "4.5 mm 跨 2 天 = 2.25 mm/d"
    assert "window_unconfigured" not in rows[3].record.disabled_reasons


def test_rate_window_drops_a_reading_outside_the_calendar_window():
    """窗口右端点固定为本轮观测日：4 天前的读数即使轮次相邻也不进窗口。"""
    conn, project_id, condition_id = build()
    days = {1: "2026-03-01", 2: "2026-03-05"}
    for index, day in sorted(days.items()):
        add_round(conn, project_id, index, day, condition_id)
    point_id = add_point(
        conn,
        project_id,
        "SYN-TH-01",
        rate=1.5,
        kind=SOURCE_USER,
        status=STATUS_VERIFIED,
        evidence="FIXTURE-GRADE:unit",
    )
    rounds = {
        index: conn.execute(
            "SELECT id FROM obs_round WHERE round_index=?", (index,)
        ).fetchone()[0]
        for index in days
    }
    observe(conn, point_id, rounds[1], value=0.0)
    observe(conn, point_id, rounds[2], value=9.0)
    rows = by_round(judge(conn, "SYN-P1", [rate_rule(window=3.0)]))
    assert rows[2].rate_value is None and rows[2].record.state == ObsState.NORMAL


def test_window_only_counts_readings_actually_present():
    conn, project_id, condition_id = build()
    for index in (1, 2, 3):
        add_round(conn, project_id, index, "2026-03-0{0}".format(index), condition_id)
    point_id = add_point(
        conn,
        project_id,
        "SYN-TH-01",
        rate=1.5,
        kind=SOURCE_USER,
        status=STATUS_VERIFIED,
        evidence="FIXTURE-GRADE:unit",
    )
    rounds = {
        i: conn.execute("SELECT id FROM obs_round WHERE round_index=?", (i,)).fetchone()[0]
        for i in (1, 2, 3)
    }
    observe(conn, point_id, rounds[1], value=0.0)
    observe(conn, point_id, rounds[2], value=1.0)
    observe(conn, point_id, rounds[3], value=3.5)
    rows = by_round(judge(conn, "SYN-P1", [rate_rule(window=3.0)]))
    assert rows[1].rate_value is None, "首测轮次窗口内没有先前读数"
    assert rows[1].record.state == ObsState.NORMAL
    assert rows[2].rate_value == pytest.approx(1.0)
    assert rows[3].rate_value == pytest.approx(2.5), "取窗口内最大斜率而不是相邻两轮"
    assert rows[3].record.state == ObsState.ALARM


def test_cum_and_rate_both_hit_reports_both():
    conn, project_id, condition_id = build()
    for index in (1, 2):
        add_round(conn, project_id, index, "2026-03-0{0}".format(index), condition_id)
    point_id = add_point(
        conn,
        project_id,
        "SYN-TH-01",
        cum=25.0,
        rate=1.5,
        kind=SOURCE_USER,
        status=STATUS_VERIFIED,
        evidence="FIXTURE-GRADE:unit",
    )
    rounds = {
        i: conn.execute("SELECT id FROM obs_round WHERE round_index=?", (i,)).fetchone()[0]
        for i in (1, 2)
    }
    observe(conn, point_id, rounds[1], value=0.0)
    observe(conn, point_id, rounds[2], value=30.0)
    rows = by_round(judge(conn, "SYN-P1", [cum_rule(std_threshold(25.0)), rate_rule()]))
    assert rows[2].record.trigger_basis == BASIS_BOTH
    assert rows[2].window_days == 3.0


# ---- 预警比例 ----------------------------------------------------------------


def test_prewarning_ratio_downgrades_normal_only():
    conn, project_id, condition_id = build()
    for index, value in ((1, 0.0), (2, 18.0), (3, 26.0)):
        add_round(conn, project_id, index, "2026-03-0{0}".format(index), condition_id)
    point_id = add_point(
        conn,
        project_id,
        "SYN-TH-01",
        cum=25.0,
        kind=SOURCE_USER,
        status=STATUS_VERIFIED,
        evidence="FIXTURE-GRADE:unit",
    )
    rounds = {
        i: conn.execute("SELECT id FROM obs_round WHERE round_index=?", (i,)).fetchone()[0]
        for i in (1, 2, 3)
    }
    for index, value in ((1, 0.0), (2, 18.0), (3, 26.0)):
        observe(conn, point_id, rounds[index], value=value)
    rows = by_round(judge(conn, "SYN-P1", [cum_rule(), ratio_rule(0.7)]))
    assert rows[2].record.state == ObsState.PREWARNING, "18/25 = 0.72 ≥ 0.7 → 预警"
    assert rows[3].record.state == ObsState.ALARM
    assert rows[1].record.state == ObsState.NORMAL
    blocked = by_round(judge(conn, "SYN-P1", [cum_rule(), ratio_rule(0.7, usable=False)]))
    assert blocked[2].record.state == ObsState.NORMAL, "预警比例未核对时不得凭默认系数出预警"


# ---- 装载与契约 --------------------------------------------------------------


def test_unknown_point_item_raises():
    conn, project_id, condition_id = build()
    add_round(conn, project_id, 1, "2026-03-01", condition_id)
    conn.execute(
        "INSERT INTO point(project_id, code, item_code) VALUES(?,?,?)",
        (project_id, "SYN-XX-01", "no_such_item"),
    )
    conn.commit()
    with pytest.raises(InputError):
        judge(conn, "SYN-P1", [])


def test_missing_project_raises_input_error():
    conn = mem_db()
    with pytest.raises(InputError):
        AlarmEngine(conn, project_code="SYN-NOPE", items=ITEMS, rules=[], clauses={})


def test_cumulative_only_item_ignores_rate_basis():
    conn, project_id, condition_id = build()
    add_round(conn, project_id, 1, "2026-03-01", condition_id)
    point_id = add_point(
        conn,
        project_id,
        "SYN-CW-01",
        item_code="crack_width",
        cum=4.0,
        cum_unit="mm",
        kind=SOURCE_USER,
        status=STATUS_VERIFIED,
        evidence="FIXTURE-GRADE:unit",
    )
    round_id = conn.execute("SELECT id FROM obs_round").fetchone()[0]
    conn.execute(
        "INSERT INTO observation(point_id, round_id, value_cum, unit, missing) VALUES(?,?,?, ?,0)",
        (point_id, round_id, 4.0, "mm"),
    )
    rows = judge(conn, "SYN-P1", [])
    record = rows[0].record
    assert record.state == ObsState.ALARM
    assert record.trigger_basis == BASIS_CUM
    assert "window_unconfigured" not in record.disabled_reasons, "该项根本没有速率判据"
    assert record.dual.rate.value is None
