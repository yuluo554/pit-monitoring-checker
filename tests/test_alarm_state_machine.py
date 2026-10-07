"""状态机的行为断言：迁移表落地成"非法就报错"，未闭环的确切置位与清除。

`ALLOWED_TRANSITIONS` 不是文档里的图，而是引擎每轮都要过的一道检查（plan/09 §五）。
处置记录靠 `alarm_state.id` 外键，所以这里走"判定落库 → 写处置 → 整段重算"的真实次序。
"""

from __future__ import annotations

import pytest
from test_alarm_engine import (
    ITEMS,
    add_point,
    add_round,
    build,
    cum_rule,
    observe,
    rate_rule,
    std_threshold,
    verified_clause,
)

from pmc.alarm.engine import AlarmEngine
from pmc.contract.status import ALLOWED_TRANSITIONS, UNCLOSED_STATES, ObsState
from pmc.contract.thresholds import SOURCE_USER, STATUS_VERIFIED
from pmc.errors import ContractError

ROUND_DAYS = {1: "2026-03-01", 2: "2026-03-02", 3: "2026-03-03", 4: "2026-03-04"}


def _engine(conn, rules):
    return AlarmEngine(
        conn,
        project_code="SYN-P1",
        items=ITEMS,
        rules=rules,
        clauses={"GB50497-2019:alarm-values": verified_clause()},
    )


def run(conn, rules, point_code="SYN-TH-01"):
    """判定 + 落库，返回 轮次 → 判定行。每次调用都新建引擎，处置记录才会被重新读到。"""
    engine = _engine(conn, rules)
    rows = engine.judge()
    engine.persist(rows)
    return {row.record.key.round_index: row for row in rows}


def alarm_id(conn, point_code, round_index):
    return conn.execute(
        "SELECT a.id FROM alarm_state a JOIN point p ON p.id = a.point_id"
        " JOIN obs_round r ON r.id = a.round_id"
        " WHERE p.code = ? AND r.round_index = ?",
        (point_code, round_index),
    ).fetchone()[0]


def dispose(conn, point_code, round_index, kind, actor_role=None):
    conn.execute(
        "INSERT INTO disposition(alarm_id, kind, note, actor_role) VALUES(?,?,?,?)",
        (alarm_id(conn, point_code, round_index), kind, "SYN·处置说明", actor_role),
    )
    conn.commit()


def history(point_code="SYN-TH-01", values=(0.0, 30.0, 1.0, 1.5), withdraw_after=None,
            cum_only=False):
    """一条最小序列：第 2 轮越线，第 3/4 轮回落到两判据之下。

    `cum_only` 用来造"回落但速率不再越线"的干净场景：回落本身也是一次大斜率，
    留着速率判据会把"已处置之后的新报警"混进来，测不到迁移表那一条边。
    """
    conn, project_id, condition_id = build()
    rounds = {}
    for index, day in sorted(ROUND_DAYS.items()):
        rounds[index] = add_round(conn, project_id, index, day, condition_id)
    archive = dict(
        kind=SOURCE_USER,
        status=STATUS_VERIFIED,
        evidence="FIXTURE-GRADE:unit",
        cum=25.0,
        rate=1.5,
    )
    if withdraw_after is not None:
        archive["cum"] = None
        archive["rate"] = None
    point_id = add_point(conn, project_id, point_code, **archive)
    for index, value in zip(sorted(ROUND_DAYS), values):
        observe(conn, point_id, rounds[index], value=value)
    conn.commit()
    if cum_only:
        conn.execute(
            "UPDATE point SET design_rate_value = NULL, design_rate_unit = NULL"
            " WHERE code = ?", (point_code,),
        )
        conn.commit()
        rules = [cum_rule(std_threshold(25.0))]
    else:
        rules = [cum_rule(std_threshold(25.0)), rate_rule(window=3.0)]
    if withdraw_after is not None:
        rules = []
    return conn, rules


def test_alarm_state_survives_a_falling_back_series_without_closing():
    """未闭环期间状态保持：回落不改判正常，起点仍是第 2 轮。"""
    conn, rules = history()
    rows = run(conn, rules)
    assert rows[2].record.state == ObsState.ALARM
    assert rows[2].record.first_alarm_round_index == 2
    for index in (3, 4):
        assert rows[index].record.state == ObsState.ALARM, "回落轮次仍标 alarm"
        assert rows[index].record.unclosed_carried is True
        assert rows[index].record.first_alarm_round_index == 2
        assert abs(rows[index].cum_value) < rows[index].record.dual.cumulative.value
    stored = conn.execute(
        "SELECT unclosed, first_alarm_round_id, state FROM alarm_state a"
        " JOIN obs_round r ON r.id = a.round_id WHERE r.round_index = 4"
    ).fetchone()
    assert stored[0] == 1 and stored[1] is not None and stored[2] == "alarm"


def test_confirm_keeps_it_unclosed_and_handle_closes_it():
    conn, rules = history()
    run(conn, rules)
    dispose(conn, "SYN-TH-01", 2, "confirm", actor_role="监测")
    rows = run(conn, rules)
    assert rows[2].record.state == ObsState.ALARM_CONFIRMED
    assert rows[3].record.state == ObsState.ALARM_CONFIRMED, "已确认仍是未闭环"
    assert rows[3].record.unclosed_carried is True

    dispose(conn, "SYN-TH-01", 4, "handle", actor_role="施工")
    rows = run(conn, rules)
    assert rows[4].record.state == ObsState.ALARM_HANDLED
    assert rows[4].record.unclosed_carried is False
    assert rows[4].record.first_alarm_round_index is None


def test_reobserve_does_not_change_the_state():
    conn, rules = history()
    run(conn, rules)
    dispose(conn, "SYN-TH-01", 3, "reobserve")
    rows = run(conn, rules)
    assert rows[3].record.state == ObsState.ALARM
    assert rows[3].record.unclosed_carried is True


def test_a_closed_alarm_cannot_be_reopened_as_confirmed():
    """已处置不得回退成已确认：后续行没有自发报警就进不了报警族，引擎报错不静默纠正。"""
    conn, rules = history(cum_only=True)
    run(conn, rules)
    dispose(conn, "SYN-TH-01", 2, "handle", actor_role="施工")
    dispose(conn, "SYN-TH-01", 3, "confirm", actor_role="监理")
    with pytest.raises(ContractError) as exc:
        run(conn, rules)
    message = str(exc.value)
    assert ("非法状态迁移" in message) or ("未报警的判定行" in message)
    assert "SYN-TH-01" in message and "R3" in message


def test_confirmed_alarm_is_sticky_across_a_new_exceedance():
    """已确认的行再越线也不降回 alarm：确认是人的动作，自动判定不替它撤销。"""
    conn, rules = history(values=(0.0, 30.0, 40.0, 1.0))
    run(conn, rules)
    dispose(conn, "SYN-TH-01", 2, "confirm", actor_role="监测")
    rows = run(conn, rules)
    assert rows[3].record.state == ObsState.ALARM_CONFIRMED
    assert rows[3].record.trigger_basis == "both", "本轮自己越线，判据写本轮的"
    assert rows[3].record.first_alarm_round_index == 2, "仍在同一条未闭环报警里"
    assert rows[4].record.state == ObsState.ALARM_CONFIRMED


def test_disposition_on_a_row_that_never_alarmed_is_rejected():
    conn, rules = history(values=(0.0, 1.0, 1.2, 1.4))
    run(conn, rules)
    dispose(conn, "SYN-TH-01", 2, "handle", actor_role="施工")
    with pytest.raises(ContractError) as exc:
        run(conn, rules)
    assert "未报警的判定行" in str(exc.value)
    assert "SYN-TH-01" in str(exc.value) and "R2" in str(exc.value)


def test_handle_beats_confirm_on_the_same_row():
    conn, rules = history()
    run(conn, rules)
    dispose(conn, "SYN-TH-01", 2, "confirm", actor_role="监测")
    dispose(conn, "SYN-TH-01", 2, "handle", actor_role="施工")
    rows = run(conn, rules)
    assert rows[2].record.state == ObsState.ALARM_HANDLED


def test_withdrawn_threshold_withdraws_the_conclusion():
    """阈值被撤回：结论作废成待定值，未闭环标记归零，不允许把待定当"已处置"。"""
    conn, rules = history(withdraw_after=1)
    rows = run(conn, rules)
    assert [row.record.state for row in rows.values()] == [ObsState.UNDETERMINED] * 4
    assert all(not row.record.unclosed_carried for row in rows.values())
    stored = conn.execute(
        "SELECT state, trigger_basis, cum_threshold, rate_threshold, unclosed,"
        " disabled_reasons FROM alarm_state WHERE round_id ="
        " (SELECT id FROM obs_round WHERE round_index = 1)"
    ).fetchone()
    assert stored[0] == "undetermined" and stored[1] == "none"
    assert stored[2] is None and stored[3] is None and stored[4] == 0
    assert stored[5], "作废也要留下为什么作废"


def test_realarm_after_closure_starts_a_new_lifecycle():
    conn, rules = history(values=(0.0, 30.0, 1.0, 40.0))
    run(conn, rules)
    dispose(conn, "SYN-TH-01", 3, "handle", actor_role="施工")
    rows = run(conn, rules)
    assert rows[3].record.state == ObsState.ALARM_HANDLED
    assert rows[4].record.state == ObsState.ALARM
    assert rows[4].record.first_alarm_round_index == 4, "闭环后再超标是新报警，不接老起点"


def test_transition_table_is_exercised_not_reimplemented():
    """引擎只照表办事：把表里有的边逐条走一遍，非法的逐条报错。"""
    assert ObsState.ALARM in UNCLOSED_STATES
    assert ObsState.ALARM_HANDLED not in UNCLOSED_STATES
    assert ObsState.NORMAL not in ALLOWED_TRANSITIONS[ObsState.ALARM], (
        "回落不改判正常这条纪律的前提就是表里没有 alarm→normal 这条边"
    )
    assert ObsState.ALARM_CONFIRMED not in ALLOWED_TRANSITIONS[ObsState.ALARM_HANDLED]
