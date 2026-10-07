"""合成时序生成器：三形态 + 7 类事件植入 + 期望轮次反算（plan/07 §四–§九）。

顺序是硬约束，写死在这里：
    形态学斜率 → 事件植入 → 按仪器分辨率量化 → 反算期望触发轮次 → 出 CSV 文本与真值

反算必须用量化后的值（也就是最终落盘的那些数），否则首超轮次与台账里的数对不上，
"定位误差"这个指标就失去意义。生成路径不读时钟、不用 stdlib random、不依赖 set 迭代序。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from pmc.catalog.items import MonitoringItem
from pmc.errors import SynthError
from pmc.synth import profile
from pmc.synth.rng import DetRng
from pmc.synth.sites import (
    DRIFT,
    DUPLICATE_REPORT,
    MISSING,
    OUTLIER,
    RATE_BURST,
    STEP,
    UNIT_ERROR,
    EventSpec,
    SiteSpec,
)

#: 判据比较容差：量化值与档位都是一位小数级，浮点尾差不得改变结论
GE_EPS = 1e-9

BASIS_CUM = "cumulative"
BASIS_RATE = "rate"
BASIS_BOTH = "both"

_BASIS_TOKEN = {
    BASIS_CUM: "alarm_basis=cumulative",
    BASIS_RATE: "alarm_basis=rate",
    BASIS_BOTH: "alarm_basis=both",
}

#: 缺测标记在报送表里的原文（白名单内，不写人名与真实表号）
MISSING_MARK = "未测"

ROUND_ORDER_HINT = "东南西北"


@dataclass(frozen=True)
class ObservationRow:
    """CSV 里的一行观测，列序见 plan/07 §十。"""

    round_index: int
    observed_on: str
    point_code: str
    item_code: str
    cumulative_value: Optional[str]
    unit: str
    missing: int
    raw_text: str
    revision: int = 1

    def as_cells(self) -> Tuple[str, ...]:
        return (
            str(self.round_index),
            self.observed_on,
            self.point_code,
            self.item_code,
            self.cumulative_value or "",
            self.unit,
            str(self.missing),
            self.raw_text,
        )


@dataclass(frozen=True)
class TruthRow:
    """真值文件的一行，7 列语义见 plan/07 §6.1。"""

    event_id: str
    point_id: str
    round_index: int
    event_type: str
    injected_magnitude: Optional[str]
    expected_first_alarm_round: Optional[int]
    also_expect: Tuple[str, ...]

    def as_cells(self) -> Tuple[str, ...]:
        return (
            self.event_id,
            self.point_id,
            str(self.round_index),
            self.event_type,
            self.injected_magnitude or "",
            "" if self.expected_first_alarm_round is None else str(self.expected_first_alarm_round),
            ";".join(self.also_expect),
        )


@dataclass(frozen=True)
class PointRecord:
    code: str
    item_code: str
    unit: str
    location: str
    install_date: str


@dataclass(frozen=True)
class RoundRecord:
    round_index: int
    observed_on: str
    condition_code: str
    is_intensified: int

    def note(self) -> Optional[str]:
        return "加密观测轮次" if self.is_intensified else None


@dataclass
class PointSeries:
    """一个测点的基线与植入后序列（量化值），供自检与反算复用。"""

    point: PointRecord
    baseline: Dict[int, float]
    final: Dict[int, float]
    events: List[EventSpec] = field(default_factory=list)

    def has_event(self) -> bool:
        return bool(self.events)


@dataclass
class SiteData:
    site: SiteSpec
    seed: int = 0
    points: List[PointRecord] = field(default_factory=list)
    rounds: List[RoundRecord] = field(default_factory=list)
    rows: Dict[int, List[ObservationRow]] = field(default_factory=dict)
    truth: List[TruthRow] = field(default_factory=list)
    series: List[PointSeries] = field(default_factory=list)

    def total_rows(self) -> int:
        return sum(len(self.rows.get(r, [])) for r in self.rows)


def _span_days(site: SiteSpec, rounds: int) -> int:
    return max(site.interval_days * (rounds - 1), 1)


def _condition_gain(site: SiteSpec, round_index: int) -> float:
    return site.condition_for_round(round_index).gain


def baseline_values(
    site: SiteSpec, item_code: str, seed: int, point_code: str, rounds: int
) -> Dict[int, float]:
    """三形态基线：开挖影响段 / 回弹段 / 流变段（plan/07 §4.1）。

    首测读数即基准，所以第 1 轮的变化量恒为 0.0；此后每轮的斜率 =
    `base × 工况增益 × 间隔天数`，回弹项目取反向，末段按指数衰减，水位叠季节项。
    """
    cum_thr = float(profile.thresholds(item_code)["cum"])
    base_per_day = site.target_ratio * cum_thr / _span_days(site, rounds)
    morph = profile.ITEM_MORPH[item_code]
    rheology_start = rounds - int(round(rounds * profile.RHEOLOGY_TAIL_RATIO)) + 1
    amplitude = profile.SEASON_AMPLITUDE * cum_thr

    rng = DetRng(seed, "morph/{0}/{1}".format(site.code, point_code))
    values: Dict[int, float] = {1: 0.0}
    trend = 0.0
    for r in range(2, rounds + 1):
        slope = base_per_day * _condition_gain(site, r) * site.interval_days
        if morph == profile.MORPH_REBOUND:
            slope *= profile.REBOUND_FACTOR
        if r >= rheology_start:
            slope *= math.exp(-(r - rheology_start) / profile.RHEOLOGY_TAU)
        slope *= rng.uniform(1.0 - profile.JITTER, 1.0 + profile.JITTER)
        trend += slope
        values[r] = trend
        if morph == profile.MORPH_SEASONAL:
            values[r] = trend + amplitude * math.sin(2.0 * math.pi * r / 12.0)
    return values


def apply_event(
    spec: EventSpec, values: Dict[int, float], rounds: int, interval_days: int
) -> None:
    """把事件形态写进数值序列（量化之前）。"""
    start = spec.round_index
    if spec.event_type == STEP:
        shift = float(spec.magnitude or 0.0)
        for r in range(start, rounds + 1):
            values[r] = values.get(r, 0.0) + shift
        # 回撤是永久水位变化：每轮撤一次并保留到末轮，否则下一轮会出现反向跳增，
        # "回落但未闭环"就被跳增触发的速率判据污染了。
        if spec.recovery_per_round:
            for i in range(1, spec.recovery_rounds + 1):
                from_round = start + i
                for r in range(from_round, rounds + 1):
                    values[r] = values.get(r, 0.0) + float(spec.recovery_per_round)
        return
    if spec.event_type == RATE_BURST:
        boost = float(spec.per_round or 0.0)
        for r in range(start, rounds + 1):
            values[r] = values.get(r, 0.0) + boost * min(r - start + 1, spec.span_rounds)
        return
    if spec.event_type == DRIFT:
        boost = float(spec.per_round or 0.0)
        for r in range(start, rounds + 1):
            values[r] = values.get(r, 0.0) + boost * (r - start + 1)
        return
    if spec.event_type == OUTLIER:
        values[start] = values.get(start, 0.0) + float(spec.magnitude or 0.0)
        return
    if spec.event_type == DUPLICATE_REPORT:
        shift = float(spec.magnitude or 0.0)
        for r in range(start, rounds + 1):
            values[r] = values.get(r, 0.0) + shift
        return
    if spec.event_type in (MISSING, UNIT_ERROR):
        return
    raise SynthError("未知事件类型：{0}".format(spec.event_type))


def first_alarm_round(
    site: SiteSpec, item_code: str, values: Dict[int, float]
) -> Tuple[Optional[int], Optional[str]]:
    """首个应触发报警的轮次与触发判据（plan/07 §七）。

    速率只在该轮窗口内**实际存在**的读数之间取最大斜率：缺测与被拒收的轮次不参与，也不补值。
    """
    thr = profile.thresholds(item_code)
    cum_thr = float(thr["cum"])
    rate_thr = thr["rate"]
    ordered = sorted(values)
    for r in ordered:
        bases: List[str] = []
        if abs(values[r]) >= cum_thr - GE_EPS:
            bases.append(BASIS_CUM)
        if rate_thr is not None:
            best = None  # type: Optional[float]
            for j in ordered:
                if j >= r:
                    continue
                gap_days = (r - j) * site.interval_days
                if gap_days > site.window_days:
                    continue
                slope = abs(values[r] - values[j]) / float(gap_days)
                if best is None or slope > best:
                    best = slope
            if best is not None and best >= float(rate_thr) - GE_EPS:
                bases.append(BASIS_RATE)
        if bases:
            return r, (BASIS_BOTH if len(bases) == 2 else bases[0])
    return None, None


def _also_expect(
    event_type: str, basis: Optional[str], extra_tokens: Sequence[str]
) -> Tuple[str, ...]:
    tokens: List[str] = []
    if basis:
        tokens.append(_BASIS_TOKEN[basis])
    if event_type == MISSING:
        tokens += ["no_interpolation", "no_row"]
    elif event_type == UNIT_ERROR:
        tokens += ["no_row", "reject=unit_mismatch"]
    elif event_type == OUTLIER:
        tokens += ["no_alarm", "persist_exact"]
    elif event_type == DUPLICATE_REPORT:
        tokens.append("import_revision=2")
    elif basis is None:
        tokens.append("no_alarm")
    tokens.extend(extra_tokens)
    return tuple(sorted(set(tokens)))


def generate_site(
    site: SiteSpec, items: Dict[str, MonitoringItem], seed: int, rounds: Optional[int] = None
) -> SiteData:
    """生成一座基坑的全部产物，并在返回之前跑完 plan/07 §九 的自检（不过就拒绝落盘）。"""
    span = site.rounds if rounds is None else int(rounds)
    if span < 2:
        raise SynthError("--rounds 至少为 2，否则没有速率窗口可反算")
    if site.events and span < max(ev.round_index for ev in site.events):
        raise SynthError(
            "--rounds {0} 不够放下 {1} 的事件植入轮次（冒烟测试请配 --sites 1）".format(
                span, site.code
            )
        )
    data = SiteData(site=site, seed=seed)
    intensify = set(site.intensified_rounds)
    data.rounds = [
        RoundRecord(
            round_index=r,
            observed_on=site.observed_on(r),
            condition_code=site.condition_for_round(r).code,
            is_intensified=1 if r in intensify else 0,
        )
        for r in range(1, span + 1)
    ]
    first_date = data.rounds[0].observed_on
    for r in range(1, span + 1):
        data.rows[r] = []

    pending: List[Tuple[EventSpec, TruthRow]] = []
    for item_code, count in site.items:
        item = items.get(item_code)
        if item is None:
            raise SynthError(
                "监测项目 {0} 不在字典里，无法生成 {1}".format(item_code, site.code)
            )
        if item.unit not in profile.UNIT_QUANTIZATION:
            raise SynthError("单位 {0} 未登记分辨率，落盘文本不确定".format(item.unit))
        for seq in range(1, count + 1):
            code = profile.point_code(item_code, seq)
            point = PointRecord(
                code=code,
                item_code=item_code,
                unit=item.unit,
                location="{0}侧 第{1:02d}号".format(ROUND_ORDER_HINT[(seq - 1) % 4], seq),
                install_date=first_date,
            )
            _emit_point(site, data, item, point, seq, seed, span, pending)

    _finalize_truth(data, pending)
    _sort_rows(data)
    selfcheck_site(site, data, span)
    return data


def _finalize_truth(data: SiteData, pending: List[Tuple[EventSpec, TruthRow]]) -> None:
    """真值按 (植入轮次, 测点编号) 排序后编号 E01…：顺序只由数据本身决定，与生成先后无关。"""
    ordered = sorted(pending, key=lambda pair: (pair[1].round_index, pair[1].point_id))
    for index, (_, row) in enumerate(ordered, start=1):
        data.truth.append(
            TruthRow(
                event_id="{0}-E{1:02d}".format(data.site.code, index),
                point_id=row.point_id,
                round_index=row.round_index,
                event_type=row.event_type,
                injected_magnitude=row.injected_magnitude,
                expected_first_alarm_round=row.expected_first_alarm_round,
                also_expect=row.also_expect,
            )
        )


def _sort_rows(data: SiteData) -> None:
    for r in data.rows:
        data.rows[r] = sorted(data.rows[r], key=lambda row: (row.point_code, row.revision))


def _emit_point(
    site: SiteSpec,
    data: SiteData,
    item: MonitoringItem,
    point: PointRecord,
    seq: int,
    seed: int,
    span: int,
    pending: List[Tuple[EventSpec, TruthRow]],
) -> None:
    unit = item.unit
    events = [ev for ev in site.events if ev.item_code == item.code and ev.point_seq == seq]
    baseline_raw = baseline_values(site, item.code, seed, point.code, span)
    check_attributable(site, item.code, unit, baseline_raw, span, events)

    values = dict(baseline_raw)
    for spec in events:
        apply_event(spec, values, span, site.interval_days)
    baseline = {r: profile.quantize(v, unit) for r, v in sorted(baseline_raw.items())}
    quantized = {r: profile.quantize(v, unit) for r, v in sorted(values.items())}

    holes = {
        ev.round_index: ev.event_type
        for ev in events
        if ev.event_type in (MISSING, UNIT_ERROR)
    }
    final = {r: v for r, v in quantized.items() if r not in holes}
    expected, basis = first_alarm_round(site, item.code, final)

    data.points.append(point)
    data.series.append(PointSeries(point=point, baseline=baseline, final=final, events=events))

    for spec in events:
        pending.append(
            (
                spec,
                truth_row(site, spec, point, unit, baseline, final, quantized, expected, basis),
            )
        )

    for r in range(1, span + 1):
        observed_on = site.observed_on(r)
        if r in holes:
            kind = holes[r]
            if kind == MISSING:
                data.rows[r].append(
                    ObservationRow(
                        round_index=r,
                        observed_on=observed_on,
                        point_code=point.code,
                        item_code=item.code,
                        cumulative_value=None,
                        unit=unit,
                        missing=1,
                        raw_text=MISSING_MARK,
                    )
                )
            else:
                data.rows[r].append(
                    ObservationRow(
                        round_index=r,
                        observed_on=observed_on,
                        point_code=point.code,
                        item_code=item.code,
                        cumulative_value=profile.format_value(quantized[r], unit),
                        # 单位错误：数值照抄、单位栏填了非字典单位（"把 mm 读数填进 cm 栏"）
                        unit=next(
                            ev.wrong_unit or unit
                            for ev in events
                            if ev.event_type == UNIT_ERROR and ev.round_index == r
                        ),
                        missing=0,
                        raw_text="",
                    )
                )
            continue
        dup = [ev for ev in events if ev.event_type == DUPLICATE_REPORT and ev.round_index == r]
        if dup:
            first = profile.quantize(
                quantized[r] + float(dup[0].first_report_delta or 0.0), unit
            )
            for revision, text in ((1, profile.format_value(first, unit)), (2, profile.format_value(quantized[r], unit))):
                data.rows[r].append(
                    ObservationRow(
                        round_index=r,
                        observed_on=observed_on,
                        point_code=point.code,
                        item_code=item.code,
                        cumulative_value=text,
                        unit=unit,
                        missing=0,
                        raw_text="",
                        revision=revision,
                    )
                )
            continue
        data.rows[r].append(
            ObservationRow(
                round_index=r,
                observed_on=observed_on,
                point_code=point.code,
                item_code=item.code,
                cumulative_value=profile.format_value(final[r], unit),
                unit=unit,
                missing=0,
                raw_text="",
            )
        )


def truth_row(
    site: SiteSpec,
    spec: EventSpec,
    point: PointRecord,
    unit: str,
    baseline: Dict[int, float],
    final: Dict[int, float],
    quantized: Dict[int, float],
    expected: Optional[int],
    basis: Optional[str],
) -> TruthRow:
    """injected_magnitude = 首个应触发轮次上相对基线多出的量；无报警期望的按植入轮次计。"""
    anchor = expected if expected is not None else spec.round_index
    magnitude = None  # type: Optional[str]
    if spec.event_type in (STEP, RATE_BURST, DRIFT, DUPLICATE_REPORT):
        extra = final.get(anchor, quantized.get(anchor, 0.0)) - baseline.get(anchor, 0.0)
        magnitude = profile.format_value(profile.quantize(extra, unit), unit)
    elif spec.event_type == OUTLIER:
        magnitude = profile.format_value(float(spec.magnitude or 0.0), unit)
    tokens: List[str] = []
    if spec.event_type == STEP and spec.recovery_rounds:
        tokens.append("unclosed>={0}".format(spec.recovery_rounds))
    return TruthRow(
        event_id="pending",
        point_id=point.code,
        round_index=spec.round_index,
        event_type=spec.event_type,
        injected_magnitude=magnitude,
        expected_first_alarm_round=expected,
        also_expect=_also_expect(spec.event_type, basis, tokens),
    )


def check_attributable(
    site: SiteSpec,
    item_code: str,
    unit: str,
    baseline: Dict[int, float],
    span: int,
    events: List[EventSpec],
) -> None:
    """基线不得触发；同点两事件不得靠得太近，否则首超轮次无法归因。"""
    if not events:
        return
    quantized = {r: profile.quantize(v, unit) for r, v in sorted(baseline.items())}
    hit, _ = first_alarm_round(site, item_code, quantized)
    if hit is not None:
        raise SynthError(
            "{0}/{1} 基线在 R{2} 就触发报警，植入事件无法归因".format(site.code, item_code, hit)
        )
    ordered = sorted(ev.round_index for ev in events)
    for a, b in zip(ordered, ordered[1:]):
        if (b - a) * site.interval_days <= site.window_days:
            raise SynthError(
                "{0} {1} 的两起事件相距 {2} 轮，落在同一速率窗口内".format(
                    site.code, item_code, b - a
                )
            )
    seen = set()
    for ev in events:
        if ev.round_index > span:
            raise SynthError(
                "事件轮次 R{0} 超出本站轮次 {1}".format(ev.round_index, span)
            )
        if (ev.point_seq, ev.round_index) in seen:
            raise SynthError(
                "同一测点同一轮次植入多起事件：{0} R{1}".format(site.code, ev.round_index)
            )
        seen.add((ev.point_seq, ev.round_index))
        if ev.event_type == MISSING:
            for other in events:
                if other is not ev and other.round_index == ev.round_index:
                    raise SynthError(
                        "缺测与数值类事件同轮互斥：{0} R{1}".format(site.code, ev.round_index)
                    )


def selfcheck_site(site: SiteSpec, data: SiteData, span: int) -> None:
    """plan/07 §九：断言不过就拒绝落盘，而不是留一堆对不上的真值给 M4。"""
    duplicates = len([ev for ev in site.events if ev.event_type == DUPLICATE_REPORT])
    expected_rows = site.point_count() * span + duplicates
    if data.total_rows() != expected_rows:
        raise SynthError(
            "{0} 行数 {1} ≠ 测点数×轮次（+重复上报行）{2}".format(
                site.code, data.total_rows(), expected_rows
            )
        )
    if len(data.truth) != len(site.events):
        raise SynthError(
            "{0} 真值 {1} 行，事件 {2} 起，两者必须一一对应".format(
                site.code, len(data.truth), len(site.events)
            )
        )
    present = {(row.point_code, row.round_index) for r in data.rows for row in data.rows[r]}
    for row in data.truth:
        if (row.point_id, row.round_index) not in present:
            raise SynthError(
                "真值 {0} 指向不存在的观测行：{1} R{2}".format(
                    row.event_id, row.point_id, row.round_index
                )
            )
        if row.expected_first_alarm_round is not None and (
            row.expected_first_alarm_round < row.round_index
            or row.expected_first_alarm_round > span
        ):
            raise SynthError(
                "真值 {0} 的期望轮次 {1} 不在植入轮次之后或超出本站轮次".format(
                    row.event_id, row.expected_first_alarm_round
                )
            )
    for series in data.series:
        if series.has_event():
            continue
        hit, _ = first_alarm_round(site, series.point.item_code, series.final)
        if hit is not None:
            raise SynthError(
                "无事件测点 {0} 在 R{1} 触发报警，误报率分母不干净".format(series.point.code, hit)
            )
    units = {point.code: point.unit for point in data.points}
    for r, rows in data.rows.items():
        per_point = {}  # type: Dict[str, List[int]]
        for row in rows:
            unit = units[row.point_code]
            if row.cumulative_value is not None:
                number = float(row.cumulative_value)
                if profile.format_value(profile.quantize(number, unit), unit) != row.cumulative_value:
                    raise SynthError(
                        "{0} R{1} 的落盘文本不是量化值：{2}".format(
                            row.point_code, r, row.cumulative_value
                        )
                    )
            per_point.setdefault(row.point_code, []).append(row.revision)
        for code, revisions in per_point.items():
            if len(revisions) > 2 or revisions not in ([1], [1, 2]):
                raise SynthError(
                    "{0} R{1} 修订链形状非法：{2}".format(code, r, revisions)
                )
    for row in data.truth:
        has_basis = any(token.startswith("alarm_basis=") for token in row.also_expect)
        if (row.expected_first_alarm_round is not None) != has_basis:
            raise SynthError(
                "真值 {0} 的期望轮次与 also_expect 判据 token 不自洽".format(row.event_id)
            )
