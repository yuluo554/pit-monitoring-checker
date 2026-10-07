"""阈值来源三态与"待定值不出货"的行为断言（题面 01 §二 通用纪律、§模块 2 纪律）。"""

from __future__ import annotations

import pytest

from pmc.contract.records import AlarmRecord, ImportReceipt, PointKey, Rejection
from pmc.contract.status import (
    ALLOWED_TRANSITIONS,
    UNCLOSED_STATES,
    ObsState,
    can_transition,
    is_unclosed,
    worse,
)
from pmc.contract.thresholds import (
    REASON_CLAUSE_MISSING,
    REASON_EVIDENCE_MISSING,
    REASON_LOCATED,
    REASON_NOT_VERIFIED,
    REASON_NO_SOURCE,
    REASON_VALUE_MISSING,
    SOURCE_DESIGN,
    SOURCE_STANDARD,
    SOURCE_USER,
    STATUS_LOCATED,
    STATUS_PENDING,
    STATUS_VERIFIED,
    DualControl,
    Threshold,
)
from pmc.errors import ContractError


def _std(status=STATUS_VERIFIED, **kw):
    base = dict(
        kind=SOURCE_STANDARD,
        status=status,
        value=1.0,
        unit="mm",
        clause_id="GB50497-2019:alarm-values",
        evidence="x",
        channel="c",
        url="https://example.com/x",
        verified_on="2026-01-01",
    )
    base.update(kw)
    return Threshold(**base)


def test_no_source_is_undetermined():
    thr = Threshold.unbound()
    assert thr.disabled_reason() == REASON_NO_SOURCE
    assert thr.is_undetermined
    assert not thr.usable


def test_design_value_needs_evidence_and_value():
    assert Threshold(kind=SOURCE_DESIGN, status=STATUS_VERIFIED, value=12.0, unit="mm").disabled_reason() == REASON_EVIDENCE_MISSING
    assert Threshold(
        kind=SOURCE_DESIGN, status=STATUS_VERIFIED, value=12.0, unit="mm", evidence="SYN-JK-2026-0001"
    ).usable
    assert Threshold(
        kind=SOURCE_DESIGN, status=STATUS_VERIFIED, unit="mm", evidence="SYN-JK-2026-0001"
    ).disabled_reason() == REASON_VALUE_MISSING


def test_standard_value_requires_verified_status():
    assert _std(status=STATUS_PENDING).disabled_reason() == REASON_NOT_VERIFIED
    assert _std(status=STATUS_LOCATED).disabled_reason() == REASON_LOCATED


def test_standard_value_requires_channel_evidence_triple():
    thr = Threshold(
        kind=SOURCE_STANDARD,
        status=STATUS_VERIFIED,
        value=1.0,
        unit="mm",
        clause_id="GB50497-2019:alarm-values",
        evidence="x",
    )
    assert thr.disabled_reason() == REASON_EVIDENCE_MISSING


def test_standard_value_requires_clause():
    thr = _std(clause_id=None)
    assert thr.disabled_reason() == REASON_CLAUSE_MISSING


def test_user_input_value_is_legitimate_source():
    thr = Threshold(
        kind=SOURCE_USER,
        status=STATUS_VERIFIED,
        value=0.8,
        unit="ratio",
        evidence="监测方案会审记录 2026-01-03，专业工程师录入",
    )
    assert thr.usable


def test_blank_output_empties_numbers():
    thr = Threshold(kind=SOURCE_USER, status=STATUS_PENDING, value=9.0, unit="mm")
    blank = thr.blank_output()
    assert blank.value is None and blank.unit is None
    assert blank.kind == SOURCE_USER


def test_dual_control_partial_source_ok():
    dual = DualControl(cumulative=Threshold.unbound(), rate=_std())
    assert dual.any_usable and not dual.all_usable


def test_alarm_record_undetermined_must_have_empty_thresholds():
    key = PointKey("SYN-P1", "SYN-DH-01", 1, "top_h_disp")
    ok = AlarmRecord(
        key=key,
        state=ObsState.UNDETERMINED,
        trigger_basis="none",
        dual=DualControl(),
        disabled_reasons=[REASON_NO_SOURCE],
    )
    ok.validate()

    bad = AlarmRecord(
        key=key,
        state=ObsState.UNDETERMINED,
        trigger_basis="cumulative",
        dual=DualControl(cumulative=_std()),
        disabled_reasons=[REASON_NO_SOURCE],
    )
    with pytest.raises(ContractError):
        bad.validate()


def test_alarm_record_cannot_claim_alarm_without_usable_threshold():
    key = PointKey("SYN-P1", "SYN-DH-01", 1, "top_h_disp")
    bad = AlarmRecord(
        key=key, state=ObsState.ALARM, trigger_basis="cumulative", dual=DualControl()
    )
    with pytest.raises(ContractError):
        bad.validate()


def test_alarm_record_standard_source_needs_clause():
    key = PointKey("SYN-P1", "SYN-DH-01", 1, "top_h_disp")
    bad = AlarmRecord(
        key=key,
        state=ObsState.ALARM,
        trigger_basis="cumulative",
        dual=DualControl(cumulative=_std()),
    )
    with pytest.raises(ContractError):
        bad.validate()


def test_import_receipt_must_balance():
    ok = ImportReceipt("a.csv", "0" * 64, 3, 2, 1, [Rejection(3, "unit_mismatch")])
    ok.validate()
    bad = ImportReceipt("a.csv", "0" * 64, 3, 2, 0, [])
    with pytest.raises(ContractError):
        bad.validate()


def test_unclosed_carries_across_rounds_by_definition():
    assert ObsState.ALARM in UNCLOSED_STATES
    assert ObsState.ALARM_CONFIRMED in UNCLOSED_STATES
    assert not is_unclosed(ObsState.ALARM_HANDLED)
    assert is_unclosed(ObsState.ALARM)


def test_transition_matrix_covers_every_state():
    from pmc.contract.status import ALL_STATES

    assert set(ALLOWED_TRANSITIONS) == set(ALL_STATES)
    assert can_transition(ObsState.ALARM, ObsState.ALARM_CONFIRMED)
    assert can_transition(ObsState.ALARM, ObsState.ALARM_HANDLED)
    # 已处置的报警不能被静默改回未确认
    assert not can_transition(ObsState.ALARM_HANDLED, ObsState.ALARM_CONFIRMED)


def test_worse_picks_more_severe():
    assert worse(ObsState.NORMAL, ObsState.ALARM) == ObsState.ALARM
    assert worse(ObsState.PREWARNING, ObsState.NORMAL) == ObsState.PREWARNING
    assert ObsState.UNDETERMINED not in UNCLOSED_STATES
