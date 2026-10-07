"""合成时序与真值的生成纪律（plan/07 §四、§六、§七、§九）。

这一组断言守的是"数据能不能自证"：形态学、量化、事件植入的可归因性、真值列语义。
基准（M4）拿到的每一个期望轮次，都必须能在这里被反算出来，而不是抄植入轮次。
"""

from __future__ import annotations

import dataclasses

import pytest
from _helpers import DATA

from pmc.catalog.items import load_items
from pmc.errors import SynthError
from pmc.synth import profile
from pmc.synth.generator import first_alarm_round, generate_site
from pmc.synth.sites import (
    DRIFT,
    DUPLICATE_REPORT,
    EVENT_TYPES,
    MISSING,
    OUTLIER,
    RATE_BURST,
    STEP,
    UNIT_ERROR,
    EventSpec,
    get_site,
    site_codes,
)


@pytest.fixture(scope="module")
def items():
    return load_items(str(DATA))


@pytest.fixture(scope="module")
def datas(items):
    return {code: generate_site(get_site(code), items, 20260107) for code in site_codes()}


def _series(data, point_code):
    for series in data.series:
        if series.point.code == point_code:
            return series
    raise AssertionError("测点 {0} 不在这座基坑里".format(point_code))


def test_three_sites_with_declared_scale(datas):
    assert sorted(datas) == ["SYN-LJ3", "SYN-YYCG", "SYN-ZHDQ"]
    for code, rounds in (("SYN-LJ3", 8), ("SYN-ZHDQ", 20), ("SYN-YYCG", 30)):
        data = datas[code]
        assert len(data.rounds) == rounds
        assert len(data.points) == 196
        assert data.total_rows() == 196 * rounds + sum(
            1 for row in data.truth if row.event_type == DUPLICATE_REPORT
        )


def test_morphology_directions(datas):
    """开挖影响段单调增长、回弹段反向、流变段收敛——三形态都要在数据里看得见。"""
    growth = _series(datas["SYN-LJ3"], "SYN-TH-01").final
    assert growth[1] == 0.0
    assert all(growth[r] > growth[r - 1] for r in range(2, len(growth)))

    rebound = _series(datas["SYN-LJ3"], "SYN-CV-01").final
    assert all(rebound[r] < 0.0 for r in range(2, len(rebound) + 1))

    values = [growth[r] for r in sorted(growth)]
    slopes = [values[i] - values[i - 1] for i in range(1, len(values))]
    assert slopes[-1] < max(slopes), "末段没有衰减，流变段没落地"


def test_values_are_quantized_to_instrument_resolution(datas):
    offenders = []
    planted_wrong_units = []
    for data in datas.values():
        for rows in data.rows.values():
            for row in rows:
                if row.cumulative_value is None:
                    continue
                if row.unit not in profile.UNIT_QUANTIZATION:
                    planted_wrong_units.append((data.site.code, row.point_code, row.unit))
                    continue
                decimals = profile.decimals(row.unit)
                text = row.cumulative_value
                fraction = text.split(".")[1] if "." in text else ""
                if len(fraction) != decimals:
                    offenders.append("{0}={1}".format(row.point_code, text))
    assert not offenders, "落盘数值不符合该项小数位：" + ",".join(offenders[:6])
    # 非字典单位只准出现在植入的 unit_error 行里，且必须就是那一起事件
    assert planted_wrong_units == [("SYN-ZHDQ", "SYN-TH-15", "cm")]


def test_all_seven_event_types_are_injected(datas):
    covered = set()
    for data in datas.values():
        covered.update(row.event_type for row in data.truth)
    assert covered == set(EVENT_TYPES), "缺事件类型：" + ",".join(sorted(set(EVENT_TYPES) - covered))


def test_truth_rows_match_injected_events(datas):
    for code, data in datas.items():
        assert len(data.truth) == len(data.site.events), code
        present = {(row.point_code, row.round_index) for r in data.rows for row in data.rows[r]}
        for row in data.truth:
            assert (row.point_id, row.round_index) in present, "{0} 指向不存在的观测".format(row.event_id)
            assert row.event_type in EVENT_TYPES
            assert row.event_id.startswith(code + "-E")


def test_drift_expectation_is_back_computed_not_copied(datas):
    """drift 的期望轮次必须晚于植入轮次：这是"反算而非复制"最直接的证据。"""
    data = datas["SYN-ZHDQ"]
    drift = [row for row in data.truth if row.event_type == DRIFT]
    assert len(drift) == 1
    row = drift[0]
    assert row.expected_first_alarm_round is not None
    assert row.expected_first_alarm_round > row.round_index
    assert "alarm_basis=" in ";".join(row.also_expect)


def test_event_attribution_no_alarm_before_injection(datas):
    """事件测点在植入之前不得已经超标，否则首超轮次归因不到这次植入。"""
    for data in datas.values():
        site = data.site
        for series in data.series:
            if not series.has_event():
                continue
            expected, _ = first_alarm_round(site, series.point.item_code, series.final)
            injected = min(ev.round_index for ev in series.events)
            if expected is None:
                continue
            assert expected >= injected, "{0} 的期望轮次早于植入".format(series.point.code)


def test_events_that_must_not_alarm(datas):
    for data in datas.values():
        for row in data.truth:
            if row.event_type in (MISSING, UNIT_ERROR, OUTLIER):
                assert row.expected_first_alarm_round is None, row.event_id
                tokens = set(row.also_expect)
                if row.event_type == OUTLIER:
                    assert {"no_alarm", "persist_exact"} <= tokens
                if row.event_type == MISSING:
                    assert {"no_interpolation", "no_row"} <= tokens
                if row.event_type == UNIT_ERROR:
                    assert {"no_row", "reject=unit_mismatch"} <= tokens
            else:
                assert row.expected_first_alarm_round is not None, row.event_id
                assert any(t.startswith("alarm_basis=") for t in row.also_expect)


def test_duplicate_report_leaves_two_rows_and_revision_token(datas):
    data = datas["SYN-ZHDQ"]
    dup = [row for row in data.truth if row.event_type == DUPLICATE_REPORT]
    assert len(dup) == 1
    assert "import_revision=2" in dup[0].also_expect
    rows = [
        row
        for row in data.rows[dup[0].round_index]
        if row.point_code == dup[0].point_id
    ]
    assert len(rows) == 2
    assert rows[0].revision == 1 and rows[1].revision == 2
    assert rows[0].cumulative_value != rows[1].cumulative_value


def test_unclosed_case_falls_back_below_threshold(datas):
    """报警那一跳之后要真的回落到阈值以下，否则"未闭环延续"是靠持续超标蒙的。"""
    data = datas["SYN-YYCG"]
    step = [row for row in data.truth if row.event_type == STEP][0]
    assert any(token.startswith("unclosed>=") for token in step.also_expect)
    series = _series(data, step.point_id)
    thr = profile.thresholds(series.point.item_code)
    after = step.expected_first_alarm_round
    for r in range(after + 1, after + 4):
        assert abs(series.final[r]) < thr["cum"], "R{0} 仍超标，回落没落地".format(r)
        earlier = [j for j in series.final if j < r and (r - j) * data.site.interval_days <= data.site.window_days]
        for j in earlier:
            slope = abs(series.final[r] - series.final[j]) / float((r - j) * data.site.interval_days)
            assert slope < thr["rate"], "R{0} 相对 R{1} 仍触发速率".format(r, j)


def test_missing_and_unit_error_leave_a_hole_not_a_value(datas):
    data = datas["SYN-ZHDQ"]
    for row in data.truth:
        if row.event_type == MISSING:
            cells = [c for c in data.rows[row.round_index] if c.point_code == row.point_id]
            assert len(cells) == 1
            assert cells[0].missing == 1 and cells[0].cumulative_value is None
            assert cells[0].raw_text == "未测"
            series = _series(data, row.point_id)
            assert row.round_index not in series.final, "缺测轮次不许被插值"
        elif row.event_type == UNIT_ERROR:
            cells = [c for c in data.rows[row.round_index] if c.point_code == row.point_id]
            assert len(cells) == 1
            assert cells[0].unit == "cm", "单位错误植入必须写非字典单位"
            assert cells[0].cumulative_value is not None


def test_normal_points_never_trigger(datas):
    for data in datas.values():
        for series in data.series:
            if series.has_event():
                continue
            hit, _ = first_alarm_round(data.site, series.point.item_code, series.final)
            assert hit is None, "{0} 无事件却在 R{1} 触发".format(series.point.code, hit)


def test_rounds_are_monotonic_dates(datas):
    for data in datas.values():
        dates = [row.observed_on for row in data.rounds]
        assert dates == sorted(dates)
        assert len(set(dates)) == len(dates)


def test_condition_timeline_changes(datas):
    codes = [row.condition_code for row in datas["SYN-YYCG"].rounds]
    assert len(set(codes)) == 4
    assert codes[0] == "C1" and codes[-1] == "C4"


def test_intensified_rounds_only_where_declared(datas):
    assert all(r.is_intensified == 0 for r in datas["SYN-YYCG"].rounds)
    flagged = [r.round_index for r in datas["SYN-ZHDQ"].rounds if r.is_intensified]
    assert flagged == [10, 11, 12]


# --------------------------------------------------------------------------- 反证


def test_baseline_that_alarms_is_refused(items):
    site = dataclasses.replace(get_site("SYN-ZHDQ"), target_ratio=1.6)
    with pytest.raises(SynthError):
        generate_site(site, items, 20260107)


def test_events_inside_same_window_are_refused(items):
    evs = list(get_site("SYN-ZHDQ").events)
    evs.append(EventSpec(RATE_BURST, "top_h_disp", 3, 12, per_round=6.0, span_rounds=1))
    site = dataclasses.replace(get_site("SYN-ZHDQ"), events=tuple(evs))
    with pytest.raises(SynthError):
        generate_site(site, items, 20260107)


def test_missing_and_step_same_round_is_refused(items):
    evs = list(get_site("SYN-ZHDQ").events)
    evs.append(EventSpec(STEP, "prop_force", 9, 16, magnitude=20.0))
    site = dataclasses.replace(get_site("SYN-ZHDQ"), events=tuple(evs))
    with pytest.raises(SynthError):
        generate_site(site, items, 20260107)


def test_event_beyond_rounds_is_refused(items):
    evs = list(get_site("SYN-ZHDQ").events)
    evs.append(EventSpec(OUTLIER, "water_level", 12, 99, magnitude=0.2))
    site = dataclasses.replace(get_site("SYN-ZHDQ"), events=tuple(evs))
    with pytest.raises(SynthError):
        generate_site(site, items, 20260107)


def test_smoke_rounds_too_short_is_refused(items):
    with pytest.raises(SynthError):
        generate_site(get_site("SYN-ZHDQ"), items, 20260107, 5)


def test_unknown_item_in_site_template_is_refused(items):
    site = dataclasses.replace(get_site("SYN-ZHDQ"), items=(("no_such_item", 4),) + get_site("SYN-ZHDQ").items[1:])
    with pytest.raises(SynthError):
        generate_site(site, items, 20260107)


def test_profile_covers_every_dictionary_item(items):
    assert set(profile.SYNTH_PROFILE) == set(items)
    assert set(profile.POINT_PREFIX) == set(items)
    assert set(profile.ITEM_MORPH) == set(items)
    for code, thr in profile.SYNTH_PROFILE.items():
        assert thr["cum"] and thr["cum"] > 0
        if items[code].control_kind == "dual":
            assert thr["rate"], "双控项目的速率档位不得为空"
        else:
            assert thr["rate"] is None, "非双控项目不该有速率判据"
