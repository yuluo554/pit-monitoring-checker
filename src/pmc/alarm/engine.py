"""报警判定内核（模块 2）：有效阈值选取 → 双控合成 → 状态机 → 落 `alarm_state`。

细则的单一事实源是 `plan/09`，本文件只做落地，不复述理由。四条最容易被"顺手简化"的纪律：

  * 待定值的阈值列必须为空：不供货的判据一律走 `Threshold.blank_output()`，
    下游（报告、界面、模块 3）拿不到 0 也拿不到 NaN；
  * 判定只读链上最新行（`superseded_by IS NULL`），读错行就会按被取代的旧值出结论；
  * 未闭环期间状态保持：`ALLOWED_TRANSITIONS` 里 `alarm` 的后继没有 `normal`，
    数值回落不能替用户消警，回落事实由 `cum_value`/`rate_value` 如实显示；
  * 整段重算而不是增量：修订链是追加式的，第 9 轮的修正会改写第 10 轮之后的 `unclosed`。

时钟与随机：本模块只用 `date` 的日历差（`observed_on` 是台账里的档案事实），不读当前时间。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence, Set, Tuple

from pmc.catalog.items import MonitoringItem
from pmc.contract.clauses import ClauseEntry
from pmc.contract.records import AlarmRecord, PointKey
from pmc.contract.status import ObsState, can_transition, is_unclosed
from pmc.contract.thresholds import (
    REASON_WINDOW_UNCONFIGURED,
    SOURCE_NONE,
    SOURCE_STANDARD,
    DualControl,
    Threshold,
)
from pmc.errors import ContractError, InputError
from pmc.rules.gate import threshold_block_reason
from pmc.rules.loader import Rule

#: 判据比较容差：落盘值与阈值都是一位小数级，浮点尾差不得改变结论（同 `07 §七`）
GE_EPS = 1e-9

BASIS_CUM = "cumulative"
BASIS_RATE = "rate"
BASIS_BOTH = "both"
BASIS_NONE = "none"

#: 引擎轴原因码（plan/09 §2.4）：与 `contract/thresholds` 的 9 个阈值轴码分开统计
REASON_NO_APPLICABLE_RULE = "no_applicable_rule"
REASON_UNIT_INCONSISTENT = "unit_inconsistent"
ENGINE_REASON_CODES: Tuple[str, ...] = (REASON_NO_APPLICABLE_RULE, REASON_UNIT_INCONSISTENT)

RATE_UNIT_SUFFIX = "/d"
REASON_JOINER = ";"
CLAUSE_JOINER = ","


@dataclass(frozen=True)
class PointArchive:
    """`point` 表里判定要用的那几列。"""

    point_id: int
    code: str
    item_code: str
    design_cum_value: Optional[float]
    design_cum_unit: Optional[str]
    design_rate_value: Optional[float]
    design_rate_unit: Optional[str]
    source_kind: str
    source_status: str
    evidence: Optional[str]
    clause_ids: Tuple[str, ...]


@dataclass(frozen=True)
class RoundRow:
    round_id: int
    round_index: int
    observed_on: str

    @property
    def day_ordinal(self) -> int:
        year, month, day = (int(part) for part in self.observed_on.split("-"))
        return date(year, month, day).toordinal()


@dataclass
class BasisResolution:
    """一条判据的选取结果（plan/09 §二）。"""

    basis: str
    effective: Optional[Threshold] = None
    rule: Optional[Rule] = None
    window_days: Optional[float] = None
    reasons: List[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.effective is not None


@dataclass
class JudgedRow:
    """一条判定：对外 DTO 加上落库要用的台账坐标。"""

    record: AlarmRecord
    point_id: int
    round_id: int
    observation_id: Optional[int]
    cum_value: Optional[float]
    rate_value: Optional[float]
    window_days: Optional[float]
    rule_ids: Tuple[str, ...]

    @property
    def state(self) -> str:
        return self.record.state


def applicable_bases(item: MonitoringItem) -> Tuple[str, ...]:
    """判据适用范围由字典的 `control_kind` 决定（plan/09 §一）。"""
    if item.control_kind == "dual":
        return (BASIS_CUM, BASIS_RATE)
    if item.control_kind == "cumulative":
        return (BASIS_CUM,)
    if item.control_kind == "rate":
        return (BASIS_RATE,)
    return ()


def index_rules(
    rules: Sequence[Rule],
) -> Tuple[Dict[Tuple[str, str], Rule], Dict[str, Rule]]:
    """(item, basis) → 报警规则；item → 预警比例规则。同键多条时取 id 字典序第一条。"""
    alarms: Dict[Tuple[str, str], Rule] = {}
    ratios: Dict[str, Rule] = {}
    for rule in sorted(rules, key=lambda r: (r.id, r.item_code, r.basis, r.judgement)):
        if rule.judgement == "alarm" and rule.basis in (BASIS_CUM, BASIS_RATE):
            alarms.setdefault((rule.item_code, rule.basis), rule)
        elif rule.judgement == "prewarning" and rule.basis == "ratio":
            ratios.setdefault(rule.item_code, rule)
    return alarms, ratios


def archive_threshold(
    archive: PointArchive, basis: str, item: MonitoringItem
) -> Tuple[Optional[Threshold], Optional[str]]:
    """第一档：测点档案登记的现场控制值。返回 (可用阈值, 不启用原因码)。"""
    if basis == BASIS_CUM:
        value, unit = archive.design_cum_value, archive.design_cum_unit
        expected_unit = item.unit
    else:
        value, unit = archive.design_rate_value, archive.design_rate_unit
        expected_unit = "{0}{1}".format(item.unit, RATE_UNIT_SUFFIX)
    if value is None:
        #: 档案没登记这条判据：不是"待定"，是"还没轮到第二档"，所以不出原因码
        return None, None
    threshold = Threshold(
        kind=archive.source_kind,
        status=archive.source_status,
        value=float(value),
        unit=unit,
        clause_id=archive.clause_ids[0] if archive.clause_ids else None,
        evidence=archive.evidence,
        note="point_archive",
    )
    reason = threshold.disabled_reason()
    if reason:
        return None, reason
    if unit != expected_unit:
        #: 单位写错的控制值不得供货：宁可待定，也不拿 cm 当 mm 用
        return None, REASON_UNIT_INCONSISTENT
    return threshold, None


def resolve_basis(
    basis: str,
    archive: PointArchive,
    item: MonitoringItem,
    alarm_rules: Dict[Tuple[str, str], Rule],
    clauses: Dict[str, ClauseEntry],
) -> BasisResolution:
    """按 plan/09 §二 的次序解析一条判据，收集两档各自被卡住的原因码。"""
    out = BasisResolution(basis=basis)
    rule = alarm_rules.get((item.code, basis))
    out.rule = rule

    effective, reason = archive_threshold(archive, basis, item)
    reasons: List[str] = []
    if reason:
        reasons.append(reason)
    if effective is None and rule is not None:
        ok, rule_reason = threshold_block_reason(rule.threshold, clauses)
        if ok:
            effective = rule.threshold
        elif rule_reason:
            reasons.append(rule_reason)
    if effective is None and rule is None:
        reasons.append(REASON_NO_APPLICABLE_RULE)

    if basis == BASIS_RATE:
        if rule is not None and rule.window_days is not None:
            out.window_days = float(rule.window_days)
        else:
            #: 没有窗口就无从计算速率：即使阈值本身可判，这条判据也不启用（plan/09 §三）
            reasons.append(REASON_WINDOW_UNCONFIGURED)
            effective = None

    out.effective = effective
    out.reasons = sorted(set(reasons))
    return out


def max_window_slope(
    values: Dict[int, float],
    rounds: Dict[int, RoundRow],
    round_index: int,
    window_days: float,
) -> Optional[float]:
    """窗口内到本轮的最大变化速率；窗口内没有可用先前读数时返回 None（plan/09 §三）。"""
    here = rounds[round_index]
    best = None  # type: Optional[float]
    for other in sorted(rounds.values(), key=lambda r: r.round_index):
        if other.round_index >= here.round_index:
            continue
        gap_days = float(here.day_ordinal - other.day_ordinal)
        if gap_days <= 0.0 or gap_days > window_days:
            continue
        slope = abs(values[here.round_index] - values[other.round_index]) / gap_days
        if best is None or slope > best:
            best = slope
    return best


def collect_clause_ids(
    archive: PointArchive,
    item: MonitoringItem,
    resolutions: Sequence[BasisResolution],
) -> List[str]:
    """条款号来源：档案、字典条目、以及参与判定的规则登记键，合并去重后按字典序。"""
    collected: Set[str] = set(archive.clause_ids)
    collected.update(item.clause_ids or [])
    for resolution in resolutions:
        rule = resolution.rule
        if rule is None:
            continue
        collected.update(rule.clause_ids)
        if rule.threshold.kind == SOURCE_STANDARD and rule.threshold.clause_id:
            collected.add(rule.threshold.clause_id)
    return sorted(c for c in collected if c)


def row_source(record: AlarmRecord) -> Tuple[str, str]:
    """行上的来源与查证态：可供货的判据优先；标准回退档先暴露（DDL 要它带条款号）。"""
    thresholds = (record.dual.cumulative, record.dual.rate)
    for threshold in thresholds:
        if threshold.usable and threshold.kind == SOURCE_STANDARD:
            return SOURCE_STANDARD, threshold.status
    for threshold in thresholds:
        if threshold.usable:
            return threshold.kind, threshold.status
    for threshold in thresholds:
        if threshold.kind not in (SOURCE_NONE, ""):
            return threshold.kind, threshold.status
    return SOURCE_NONE, "pending"


class AlarmEngine:
    """一个工程的判定引擎：装载台账与规则，整段重算，再 upsert 落库。"""

    def __init__(
        self,
        conn,
        *,
        project_code: str,
        items: Dict[str, MonitoringItem],
        rules: Sequence[Rule],
        clauses: Dict[str, ClauseEntry],
    ):
        self.conn = conn
        self.project_code = project_code
        self.items = items
        self.alarm_rules, self.ratio_rules = index_rules(rules)
        self.clauses = clauses
        self.project_id = self._project_id()
        self.rounds = self._load_rounds()
        self.archives = self._load_archives()
        self.observations = self._load_observations()
        self.dispositions = self._load_dispositions()

    # ---- 装载 ----------------------------------------------------------------

    def _project_id(self) -> int:
        row = self.conn.execute(
            "SELECT id FROM project WHERE code = ?", (self.project_code,)
        ).fetchone()
        if row is None:
            raise InputError("工程 {0} 不在台账里，无从判定".format(self.project_code))
        return int(row[0])

    def _load_rounds(self) -> Dict[int, RoundRow]:
        rows = self.conn.execute(
            "SELECT id, round_index, observed_on FROM obs_round WHERE project_id = ?"
            " ORDER BY round_index",
            (self.project_id,),
        ).fetchall()
        return {
            int(index): RoundRow(int(round_id), int(index), observed_on)
            for round_id, index, observed_on in rows
        }

    def _load_archives(self) -> List[PointArchive]:
        rows = self.conn.execute(
            "SELECT id, code, item_code, design_cum_value, design_cum_unit,"
            " design_rate_value, design_rate_unit, threshold_source_kind,"
            " threshold_status, threshold_evidence, clause_ids"
            " FROM point WHERE project_id = ? ORDER BY code, item_code",
            (self.project_id,),
        ).fetchall()
        out = []
        for row in rows:
            out.append(
                PointArchive(
                    point_id=int(row[0]),
                    code=row[1],
                    item_code=row[2],
                    design_cum_value=None if row[3] is None else float(row[3]),
                    design_cum_unit=row[4],
                    design_rate_value=None if row[5] is None else float(row[5]),
                    design_rate_unit=row[6],
                    source_kind=row[7],
                    source_status=row[8],
                    evidence=row[9],
                    clause_ids=tuple(c for c in (row[10] or "").split(CLAUSE_JOINER) if c),
                )
            )
        return out

    def _load_observations(self) -> Dict[int, Dict[int, Tuple[int, Optional[float]]]]:
        """point_id → round_index → (observation_id, value)；只取链上最新行，缺测不出值。"""
        rows = self.conn.execute(
            "SELECT o.point_id, r.round_index, o.id, o.value_cum, o.missing"
            " FROM observation o JOIN obs_round r ON r.id = o.round_id"
            " WHERE r.project_id = ? AND o.superseded_by IS NULL",
            (self.project_id,),
        ).fetchall()
        out: Dict[int, Dict[int, Tuple[int, Optional[float]]]] = {}
        for point_id, round_index, obs_id, value_cum, missing in rows:
            value = None if (missing or value_cum is None) else float(value_cum)
            out.setdefault(int(point_id), {})[int(round_index)] = (int(obs_id), value)
        return out

    def _load_dispositions(self) -> Dict[Tuple[int, int, str], Set[str]]:
        rows = self.conn.execute(
            "SELECT a.point_id, a.round_id, a.item_code, d.kind FROM alarm_state a"
            " JOIN disposition d ON d.alarm_id = a.id WHERE a.project_id = ?",
            (self.project_id,),
        ).fetchall()
        out: Dict[Tuple[int, int, str], Set[str]] = {}
        for point_id, round_id, item_code, kind in rows:
            out.setdefault((int(point_id), int(round_id), item_code), set()).add(kind)
        return out

    # ---- 判定 ----------------------------------------------------------------

    def judge(self, upto_round: Optional[int] = None) -> List[JudgedRow]:
        rows: List[JudgedRow] = []
        for archive in self.archives:
            rows.extend(self.judge_point(archive, upto_round))
        return rows

    def judge_point(
        self, archive: PointArchive, upto_round: Optional[int] = None
    ) -> List[JudgedRow]:
        item = self.items.get(archive.item_code)
        if item is None:
            raise InputError(
                "测点 {0} 的监测项目 {1} 不在字典里，无法判定".format(
                    archive.code, archive.item_code
                )
            )
        readings = self.observations.get(archive.point_id, {})
        #: 无有效读数的轮次不出行，状态机跳过它（plan/09 §2.3 末行）
        present = {
            index: self.rounds[index]
            for index in sorted(self.rounds)
            if index in readings
            and readings[index][1] is not None
            and (upto_round is None or index <= upto_round)
        }
        values = {index: readings[index][1] for index in sorted(present)}
        resolutions = [
            resolve_basis(basis, archive, item, self.alarm_rules, self.clauses)
            for basis in applicable_bases(item)
        ]
        clause_ids = collect_clause_ids(archive, item, resolutions)

        out: List[JudgedRow] = []
        previous_state: Optional[str] = None
        origin_round_id: Optional[int] = None
        origin_round_index: Optional[int] = None
        origin_basis = BASIS_NONE
        for round_index in sorted(present):
            round_row = present[round_index]
            observation_id, value = readings[round_index]
            reasons: List[str] = []
            hits: List[str] = []
            rate_value: Optional[float] = None
            for resolution in resolutions:
                reasons.extend(resolution.reasons)
                if not resolution.usable:
                    continue
                if resolution.basis == BASIS_CUM:
                    if abs(value) >= float(resolution.effective.value) - GE_EPS:
                        hits.append(BASIS_CUM)
                else:
                    rate_value = max_window_slope(
                        values, present, round_index, resolution.window_days
                    )
                    if (
                        rate_value is not None
                        and rate_value >= float(resolution.effective.value) - GE_EPS
                    ):
                        hits.append(BASIS_RATE)

            trigger, base_state = self._settle(hits, resolutions, previous_state, value, item)
            state = self._apply_disposition(archive, round_row, item.code, base_state)
            where = "{0} R{1} {2}".format(archive.code, round_index, item.code)
            if previous_state is not None and not can_transition(previous_state, base_state):
                raise ContractError(
                    "非法状态迁移：{0} 轮次间 {1} -> {2}".format(where, previous_state, base_state)
                )
            if state != base_state and not can_transition(base_state, state):
                raise ContractError(
                    "非法状态迁移：{0} 处置把 {1} 改成了 {2}（迁移表里没有这条边）".format(
                        where, base_state, state
                    )
                )
            #: 生命周期起点只在本行成为未闭环族的第一行时写入；
            #: 后续轮次即使又自己越线一次，也仍是同一条未闭环报警的延续，不得把起点推后。
            continuing = (
                is_unclosed(state)
                and previous_state is not None
                and is_unclosed(previous_state)
            )
            if not is_unclosed(state):
                origin_round_id = None
                origin_round_index = None
                origin_basis = BASIS_NONE
                carried = False
            elif continuing:
                carried = True
            else:
                origin_round_id = round_row.round_id
                origin_round_index = round_index
                origin_basis = trigger
                carried = False
            #: 本轮自己越线就写本轮的判据；纯延续行写生命周期的原始判据
            row_basis = trigger if trigger != BASIS_NONE else origin_basis

            record = AlarmRecord(
                key=PointKey(self.project_code, archive.code, round_index, item.code),
                state=state,
                trigger_basis=row_basis,
                dual=self._dual(resolutions),
                clause_ids=list(clause_ids),
                unclosed_carried=bool(carried),
                first_alarm_round_index=origin_round_index,
                disabled_reasons=sorted(set(reasons)),
            )
            record.validate()
            out.append(
                JudgedRow(
                    record=record,
                    point_id=archive.point_id,
                    round_id=round_row.round_id,
                    observation_id=observation_id,
                    cum_value=value,
                    rate_value=rate_value,
                    window_days=self._window_days(resolutions),
                    rule_ids=self._rule_ids(resolutions),
                )
            )
            previous_state = state
        return out

    def _settle(
        self,
        hits: Sequence[str],
        resolutions: Sequence[BasisResolution],
        previous_state: Optional[str],
        value: Optional[float],
        item: MonitoringItem,
    ) -> Tuple[str, str]:
        """返回 (本轮判据, 本轮基础状态)；处置只在基础状态之上按迁移表往前走一步（plan/09 §5.1）。"""
        previous_open = previous_state is not None and is_unclosed(previous_state)
        if hits:
            basis = BASIS_BOTH if len(hits) > 1 else hits[0]
            if previous_open:
                #: 粘性：已确认的行不因新一轮越线而降回 alarm，已报警的行也不因越线被自动确认
                return basis, previous_state
            return basis, ObsState.ALARM
        if not any(resolution.usable for resolution in resolutions):
            #: 阈值被撤回/未核对：结论作废，延续也就无从谈起（§5.3 末行）
            return BASIS_NONE, ObsState.UNDETERMINED
        if previous_open:
            #: 未闭环期间状态保持：报警已发出，没有处置记录就不能改判"正常"
            return BASIS_NONE, previous_state
        if self._prewarning_hit(item, resolutions, value):
            return BASIS_NONE, ObsState.PREWARNING
        return BASIS_NONE, ObsState.NORMAL

    def _prewarning_hit(
        self,
        item: MonitoringItem,
        resolutions: Sequence[BasisResolution],
        value: Optional[float],
    ) -> bool:
        rule = self.ratio_rules.get(item.code)
        cum = next((r for r in resolutions if r.basis == BASIS_CUM), None)
        if rule is None or cum is None or not cum.usable or value is None:
            return False
        ok, _ = threshold_block_reason(rule.threshold, self.clauses)
        if not ok:
            return False
        return abs(value) + GE_EPS >= float(rule.threshold.value) * float(cum.effective.value)

    @staticmethod
    def _dual(resolutions: Sequence[BasisResolution]) -> DualControl:
        by_basis = {r.basis: r for r in resolutions}
        picked = {}
        for basis, key in ((BASIS_CUM, "cumulative"), (BASIS_RATE, "rate")):
            resolution = by_basis.get(basis)
            if resolution is None:
                picked[key] = Threshold.unbound()
            elif resolution.usable:
                picked[key] = resolution.effective
            else:
                #: 待定侧一律脱空：下游拿不到数值，也就拿不到 0 或 NaN
                picked[key] = (
                    resolution.rule.threshold.blank_output()
                    if resolution.rule is not None
                    else Threshold.unbound()
                )
        return DualControl(cumulative=picked["cumulative"], rate=picked["rate"])

    @staticmethod
    def _window_days(resolutions: Sequence[BasisResolution]) -> Optional[float]:
        for resolution in resolutions:
            if resolution.basis == BASIS_RATE and resolution.window_days is not None:
                return resolution.window_days
        return None

    @staticmethod
    def _rule_ids(resolutions: Sequence[BasisResolution]) -> Tuple[str, ...]:
        return tuple(sorted({r.rule.id for r in resolutions if r.rule is not None}))

    def _apply_disposition(
        self,
        archive: PointArchive,
        round_row: RoundRow,
        item_code: str,
        state: str,
    ) -> str:
        kinds = self.dispositions.get((archive.point_id, round_row.round_id, item_code))
        if not kinds:
            return state
        if not (is_unclosed(state) or state == ObsState.ALARM_HANDLED):
            raise ContractError(
                "处置记录 {0} 指向未报警的判定行：{1} R{2} {3}（状态 {4}）".format(
                    REASON_JOINER.join(sorted(kinds)),
                    archive.code,
                    round_row.round_index,
                    item_code,
                    state,
                )
            )
        if "handle" in kinds:
            return ObsState.ALARM_HANDLED
        if "confirm" in kinds:
            return ObsState.ALARM_CONFIRMED
        return state

    # ---- 落库 ----------------------------------------------------------------

    def persist(self, rows: Sequence[JudgedRow]) -> Dict[str, int]:
        """upsert 保 id：处置记录外键指向 `alarm_state.id`，重算不能把它甩掉。"""
        counts = {"inserted": 0, "updated": 0}
        for row in rows:
            record = row.record
            kind, status = row_source(record)
            first_round = record.first_alarm_round_index
            params = (
                self.project_id,
                row.point_id,
                row.round_id,
                row.observation_id,
                record.key.item_code,
                record.state,
                record.trigger_basis,
                row.cum_value,
                record.dual.cumulative.value,
                row.rate_value,
                record.dual.rate.value,
                row.window_days,
                kind,
                status,
                CLAUSE_JOINER.join(record.clause_ids) or None,
                REASON_JOINER.join(record.disabled_reasons) or None,
                1 if is_unclosed(record.state) else 0,
                None if first_round is None else self.rounds[first_round].round_id,
            )
            existing = self.conn.execute(
                "SELECT id FROM alarm_state WHERE point_id = ? AND round_id = ?"
                " AND item_code = ?",
                (row.point_id, row.round_id, record.key.item_code),
            ).fetchone()
            if existing is None:
                self.conn.execute(
                    "INSERT INTO alarm_state(project_id, point_id, round_id, observation_id,"
                    " item_code, state, trigger_basis, cum_value, cum_threshold, rate_value,"
                    " rate_threshold, window_days, threshold_source_kind, threshold_status,"
                    " clause_ids, disabled_reasons, unclosed, first_alarm_round_id)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    params,
                )
                counts["inserted"] += 1
            else:
                self.conn.execute(
                    "UPDATE alarm_state SET project_id=?, point_id=?, round_id=?,"
                    " observation_id=?, item_code=?, state=?, trigger_basis=?, cum_value=?,"
                    " cum_threshold=?, rate_value=?, rate_threshold=?, window_days=?,"
                    " threshold_source_kind=?, threshold_status=?, clause_ids=?,"
                    " disabled_reasons=?, unclosed=?, first_alarm_round_id=?"
                    " WHERE point_id=? AND round_id=? AND item_code=?",
                    params
                    + (row.point_id, row.round_id, record.key.item_code),
                )
                counts["updated"] += 1
        self.conn.commit()
        return counts


def run_check(
    conn,
    *,
    project_code: str,
    items: Dict[str, MonitoringItem],
    rules: Sequence[Rule],
    clauses: Dict[str, ClauseEntry],
    upto_round: Optional[int] = None,
    write: bool = True,
) -> Tuple[List[JudgedRow], Dict[str, int]]:
    engine = AlarmEngine(
        conn,
        project_code=project_code,
        items=items,
        rules=rules,
        clauses=clauses,
    )
    if not engine.rounds:
        raise InputError("工程 {0} 没有轮次档案，无从判定".format(project_code))
    if not engine.archives:
        raise InputError("工程 {0} 没有测点档案，无从判定".format(project_code))
    rows = engine.judge(upto_round)
    if not rows:
        raise InputError(
            "工程 {0} 在判定范围内没有任何链上有效读数：先 pmc import".format(project_code)
        )
    counts = engine.persist(rows) if write else {"inserted": 0, "updated": 0}
    return rows, counts


def degraded_exit(rows: Sequence[JudgedRow]) -> int:
    """退出码 1 的触发集合（plan/09 §七）：未闭环、待定值、部分判据不启用，三者任一。"""
    for row in rows:
        record = row.record
        if is_unclosed(record.state):
            return 1
        if record.state == ObsState.UNDETERMINED:
            return 1
        if record.disabled_reasons:
            return 1
    return 0


def state_counts(rows: Sequence[JudgedRow]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row.record.state] = counts.get(row.record.state, 0) + 1
    return counts


def unclosed_list(rows: Sequence[JudgedRow]) -> List[Dict[str, object]]:
    """未闭环清单：每个未闭环生命周期的最新一行，带延续轮数与本轮是否回落。"""
    latest: Dict[Tuple[str, str], JudgedRow] = {}
    span: Dict[Tuple[str, str], int] = {}
    for row in sorted(
        rows,
        key=lambda r: (
            r.record.key.point_code,
            r.record.key.item_code,
            r.record.key.round_index,
        ),
    ):
        if not is_unclosed(row.record.state):
            continue
        key = (row.record.key.point_code, row.record.key.item_code)
        latest[key] = row
        span[key] = span.get(key, 0) + 1
    out = []
    for key in sorted(latest):
        row = latest[key]
        cum_threshold = row.record.dual.cumulative.value
        out.append(
            {
                "point_code": key[0],
                "item_code": key[1],
                "state": row.record.state,
                "first_alarm_round_index": row.record.first_alarm_round_index,
                "carried_rounds": span[key],
                "latest_round_index": row.record.key.round_index,
                "fell_back": (
                    row.cum_value is not None
                    and cum_threshold is not None
                    and abs(row.cum_value) < float(cum_threshold) - GE_EPS
                ),
            }
        )
    return out
