"""模块 3：监测频率与时效合规检核（题面 01 §模块 3，契约见 `03 §5`，细则见 `plan/08 §五`）。

四条纪律决定这个文件长什么样：

* **只读台账事实**：工况时间线 + 轮次日期 + 观测有效性 + 模块 2 落库的 `alarm_state.unclosed`；
  报警本身一律不重算 —— 不 import `pmc.alarm`，判定的唯一出口仍是模块 2（`plan/08 §五.4`）；
* **规则不生效就不出结论**：频率规则同样过 `rules/gate` 单点门控，被挡住的规则进"应核实"队列，
  而不是被默认值顶替（工况上限天数没有已核对的来源 = 不判，与待定值同一条纪律）；
* **每条违规必须挂 `rule_id` + `clause_ids` + `evidence_json`**：缺条款号写不进库（DDL NOT NULL 兜住）；
* **结论只写"应核实"**：本工具不认定违规、不判定基坑安全（题面 §六.4）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from pmc.contract.clauses import ClauseEntry
from pmc.contract.records import PointKey, ViolationRecord
from pmc.errors import ContractError, InputError
from pmc.rules.gate import rule_enabled
from pmc.rules.loader import Rule

#: 违规类别：与 `db/schema.py` 的 violation.kind CHECK 同源（改这里必须同时改 DDL 与文档）
KIND_MISSED = "missed"
KIND_OVER_INTERVAL = "over_interval"
KIND_STALE_FREQUENCY = "stale_frequency"
KIND_NO_INTENSIFIED = "no_intensified_after_alarm"
VIOLATION_KINDS: Tuple[str, ...] = (
    KIND_MISSED,
    KIND_OVER_INTERVAL,
    KIND_STALE_FREQUENCY,
    KIND_NO_INTENSIFIED,
)

#: 三类时序检核各由一条固定 id 的规则驱动（`plan/08 §四`）：id 即契约，换 id 就是换口径，
#: `test_m3_discipline.py` 要求这些 id 在生产频率规则集里存在且 basis=sequence。
#: 间隔上限不在这张表里：它可以按开挖深度分档成多条 `interval` 规则，
#: 供出上限的那条规则 id 直接写进违规记录，不需要固定名字。
RULE_ID_MISSED = "FREQ-MISSED-ROUND"
RULE_ID_CONDITION_CHANGE = "FREQ-CONDITION-CHANGE"
RULE_ID_INTENSIFY_AFTER_ALARM = "FREQ-INTENSIFY-AFTER-ALARM"
SEQUENCE_RULE_IDS: Tuple[str, ...] = (
    RULE_ID_MISSED,
    RULE_ID_CONDITION_CHANGE,
    RULE_ID_INTENSIFY_AFTER_ALARM,
)

#: 检核轴原因码：与阈值轴（`contract/thresholds.py` 9 个）、引擎轴（`alarm/engine.py` 2 个）分账
REASON_NO_INTERVAL_BAND = "no_applicable_interval_band"
REASON_DEPTH_UNRECORDED = "excavation_depth_unrecorded"
REASON_NO_CONDITION = "condition_not_recorded"
REASON_ALARM_STATE_EMPTY = "alarm_state_empty"
REASON_STALE_UNLABELABLE = "condition_change_blocked"
COMPLIANCE_REASON_CODES: Tuple[str, ...] = (
    REASON_NO_INTERVAL_BAND,
    REASON_DEPTH_UNRECORDED,
    REASON_NO_CONDITION,
    REASON_ALARM_STATE_EMPTY,
    REASON_STALE_UNLABELABLE,
)

#: 结论用词（题面 §六.4）：检核只提出"应核实"，不写"已确认违规"。
#: 结论列的取值集合就一项 —— 越界用词没有可以进来的位置。
VERDICT = "应核实"
CONCLUSION_CHOICES: Tuple[str, ...] = (VERDICT,)

DATE_JOINER = "-"


@dataclass(frozen=True)
class AuditRow:
    """一条应核实事项 = 契约层 `ViolationRecord` + 落库要用的台账坐标。

    DTO 用 `contract/records.py::ViolationRecord`（M0 就定好的模块 3 输出对象），
    这里不另立第二套口径；台账坐标（point_id/round_id）属数据库，不属契约。
    """

    record: ViolationRecord
    point_id: Optional[int]
    round_id: Optional[int]
    round_index: Optional[int]

    @property
    def kind(self) -> str:
        return self.record.kind

    @property
    def rule_id(self) -> str:
        return self.record.rule_id

    @property
    def clause_ids(self) -> Tuple[str, ...]:
        return tuple(self.record.clause_ids)

    @property
    def evidence(self) -> Dict[str, object]:
        return self.record.evidence

    @property
    def point_code(self) -> Optional[str]:
        return None if self.record.key is None else self.record.key.point_code

    @property
    def sort_key(self) -> Tuple[str, str, int, str]:
        return (
            self.kind,
            self.point_code or "",
            0 if self.round_index is None else self.round_index,
            self.rule_id,
        )


def make_violation(
    kind: str,
    rule: Rule,
    *,
    project_code: str,
    clause_ids: Sequence[str],
    point_code: Optional[str] = None,
    item_code: Optional[str] = None,
    origin_round_index: Optional[int] = None,
    point_id: Optional[int] = None,
    round_id: Optional[int] = None,
    round_index: Optional[int] = None,
    evidence: Dict[str, object],
) -> AuditRow:
    """构造即校验：`ViolationRecord.validate()` 守住"无条款号不得进清单"（R3）。"""
    key = None
    if point_code and item_code and origin_round_index is not None:
        key = PointKey(project_code, point_code, origin_round_index, item_code)
        key.validate()
    record = ViolationRecord(
        key=key,
        kind=kind,
        rule_id=rule.id,
        clause_ids=list(clause_ids),
        evidence=dict(evidence),
    )
    record.validate()
    return AuditRow(
        record=record, point_id=point_id, round_id=round_id, round_index=round_index
    )


@dataclass
class AuditResult:
    """一个工程一次检核的全部产出。"""

    project_code: str
    violations: List[AuditRow] = field(default_factory=list)
    #: (rule_id, 原因码)：不生效的规则 = 应核实队列，不是"没问题"
    blocked: List[Tuple[str, str]] = field(default_factory=list)
    #: (原因码, 说明)：需要数值/工况/判定结果才能判、但当前缺位的场合
    notes: List[Tuple[str, str]] = field(default_factory=list)
    rounds_audited: int = 0
    round_span: Tuple[Optional[int], Optional[int]] = (None, None)
    intensified_rounds: Tuple[int, ...] = ()
    persisted: Dict[str, int] = field(default_factory=dict)

    def counts_by_kind(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in self.violations:
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return counts

    def grouped_by_clause(self) -> Dict[str, List[AuditRow]]:
        grouped: Dict[str, List[Violation]] = {}
        for item in self.violations:
            for clause_id in item.clause_ids:
                grouped.setdefault(clause_id, []).append(item)
        return grouped


@dataclass(frozen=True)
class _Round:
    round_id: int
    round_index: int
    observed_on: str
    is_intensified: bool

    @property
    def day_ordinal(self) -> int:
        year, month, day = (int(part) for part in self.observed_on.split(DATE_JOINER))
        return date(year, month, day).toordinal()


@dataclass(frozen=True)
class _Condition:
    condition_id: int
    code: str
    excavation_depth: Optional[float]
    effective_from: str

    @property
    def effective_ordinal(self) -> int:
        year, month, day = (int(part) for part in self.effective_from.split(DATE_JOINER))
        return date(year, month, day).toordinal()


@dataclass(frozen=True)
class _Point:
    point_id: int
    code: str
    item_code: str
    install_ordinal: Optional[int]
    abandon_ordinal: Optional[int]

    def active_on(self, day_ordinal: int) -> bool:
        if self.install_ordinal is not None and day_ordinal < self.install_ordinal:
            return False
        if self.abandon_ordinal is not None and day_ordinal > self.abandon_ordinal:
            return False
        return True


def _day_ordinal(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    parts = text.split(DATE_JOINER)
    if len(parts) != 3:
        raise ContractError("台账日期格式非法：{0}（应为 YYYY-MM-DD）".format(text))
    year, month, day = (int(part) for part in parts)
    return date(year, month, day).toordinal()


class ComplianceAuditor:
    def __init__(
        self,
        conn,
        *,
        project_code: str,
        rules: Sequence[Rule],
        clauses: Dict[str, ClauseEntry],
        round_from: Optional[int] = None,
        round_to: Optional[int] = None,
    ) -> None:
        self.conn = conn
        self.project_code = project_code
        self.round_from = round_from
        self.round_to = round_to
        self.project_id = self._resolve_project()
        self.rules = list(rules)
        self.clauses = clauses
        self.conditions = self._load_conditions()
        self.all_rounds = self._load_rounds()
        if not self.all_rounds:
            raise InputError(
                "工程 {0} 没有轮次档案，无从检核：先 pmc synth --db 或 pmc init".format(
                    project_code
                )
            )
        self.points = self._load_points()
        self._interval_rules: List[Rule] = []
        self.result = AuditResult(
            project_code=project_code,
            round_span=(self.all_rounds[0].round_index, self.all_rounds[-1].round_index),
        )

    # ---- 台账装载 -------------------------------------------------------------

    def _resolve_project(self) -> int:
        row = self.conn.execute(
            "SELECT id FROM project WHERE code = ?", (self.project_code,)
        ).fetchone()
        if row is None:
            raise InputError("工程 {0} 不在台账里".format(self.project_code))
        return int(row[0])

    def _load_conditions(self) -> List[_Condition]:
        out: List[_Condition] = []
        for row in self.conn.execute(
            "SELECT id, code, excavation_depth, effective_from FROM working_condition"
            " WHERE project_id = ? ORDER BY effective_from, code",
            (self.project_id,),
        ):
            out.append(
                _Condition(
                    condition_id=int(row[0]),
                    code=row[1],
                    excavation_depth=None if row[2] is None else float(row[2]),
                    effective_from=row[3],
                )
            )
        return out

    def _load_rounds(self) -> List[_Round]:
        out: List[_Round] = []
        for row in self.conn.execute(
            "SELECT id, round_index, observed_on, is_intensified FROM obs_round"
            " WHERE project_id = ? ORDER BY round_index",
            (self.project_id,),
        ):
            out.append(
                _Round(
                    round_id=int(row[0]),
                    round_index=int(row[1]),
                    observed_on=row[2],
                    is_intensified=bool(row[3]),
                )
            )
        return out

    def _load_points(self) -> List[_Point]:
        out: List[_Point] = []
        for row in self.conn.execute(
            "SELECT id, code, item_code, install_date, abandon_date FROM point"
            " WHERE project_id = ? ORDER BY code",
            (self.project_id,),
        ):
            out.append(
                _Point(
                    point_id=int(row[0]),
                    code=row[1],
                    item_code=row[2],
                    install_ordinal=_day_ordinal(row[3]),
                    abandon_ordinal=_day_ordinal(row[4]),
                )
            )
        return out

    def _in_scope(self, round_index: int) -> bool:
        if self.round_from is not None and round_index < self.round_from:
            return False
        if self.round_to is not None and round_index > self.round_to:
            return False
        return True

    # ---- 门控 -----------------------------------------------------------------

    def gate(self) -> None:
        """只检核 judgement=frequency 的规则：报警规则归模块 2，不在这里重复判定。"""
        for rule in sorted(
            (r for r in self.rules if r.judgement == "frequency"), key=lambda r: r.id
        ):
            ok, reason = rule_enabled(rule, self.clauses)
            if not ok:
                self.result.blocked.append((rule.id, reason))
            elif rule.basis == "interval":
                self._interval_rules.append(rule)
        self.result.blocked.sort()

    def _enabled_sequence_rule(self, rule_id: str) -> Optional[Rule]:
        """时序检核要对应的固定 id 规则生效才做：没有依据的规则 = 这一类不判。"""
        for rule in self.rules:
            if rule.id == rule_id and rule.judgement == "frequency":
                ok, _reason = rule_enabled(rule, self.clauses)
                if ok:
                    return rule
        return None

    def _effective_condition(self, round_row: _Round) -> Optional[_Condition]:
        """工况时间线按 `effective_from` 日期定档：变更当轮起新工况即生效（`03 §2`）。"""
        chosen: Optional[_Condition] = None
        for condition in self.conditions:
            if condition.effective_ordinal <= round_row.day_ordinal:
                chosen = condition
            else:
                break
        return chosen

    def _interval_band(
        self, depth: Optional[float]
    ) -> Tuple[Optional[Rule], Optional[float]]:
        """按开挖深度取间隔上限档：`depth_max` 升序匹配，空值档是兜底档。

        取不到档就**不判**，而不是拿一个默认天数冒充规范值 —— 后者正是题面 §二 纪律要防的事。
        """
        ordered = sorted(
            self._interval_rules,
            key=lambda r: (r.depth_max is None, -1.0 if r.depth_max is None else r.depth_max),
        )
        if depth is not None:
            for candidate in ordered:
                if candidate.depth_max is None:
                    break
                if depth <= float(candidate.depth_max) + 1e-9:
                    return candidate, candidate.depth_max
        fallback = [r for r in ordered if r.depth_max is None]
        return (fallback[0], None) if fallback else (None, None)

    # ---- 四类检核 -------------------------------------------------------------

    def check_missed(self) -> None:
        rule = self._enabled_sequence_rule(RULE_ID_MISSED)
        if rule is None:
            return
        rounds = self._scope_rounds()
        if not rounds or not self.points:
            return
        effective: Dict[Tuple[int, int], int] = {}
        rows_in_round: Dict[int, int] = {}
        for row in self.conn.execute(
            "SELECT point_id, round_id, missing FROM observation"
            " WHERE superseded_by IS NULL AND round_id IN ({0})".format(
                ",".join(str(r.round_id) for r in rounds)
            ),
        ):
            effective[(int(row[0]), int(row[1]))] = int(row[2] or 0)
            rows_in_round[int(row[1])] = rows_in_round.get(int(row[1]), 0) + 1
        for round_row in rounds:
            condition = self._effective_condition(round_row)
            if not rows_in_round.get(round_row.round_id):
                # 整轮一行都没入库：更可能是"这轮还没导入"而不是 196 个测点各自漏测，
                # 所以只出一条工程级的应核实事项，点名到轮次，不逐测点刷屏。
                self.result.violations.append(
                    make_violation(
                        KIND_MISSED,
                        rule,
                        project_code=self.project_code,
                        clause_ids=rule.clause_ids,
                        point_id=None,
                        round_id=round_row.round_id,
                        round_index=round_row.round_index,
                        evidence={
                            "round_index": round_row.round_index,
                            "observed_on": round_row.observed_on,
                            "condition_code": condition.code if condition else None,
                            "gap": "round_not_imported",
                            "points_expected": len(self.points),
                            "detail": "排定轮次 R{0}（{1}）台账内一行观测都没有：应核实是没测还是没导入".format(
                                round_row.round_index, round_row.observed_on
                            ),
                        },
                    )
                )
                continue
            for point in self.points:
                if not point.active_on(round_row.day_ordinal):
                    continue
                mark = effective.get((point.point_id, round_row.round_id))
                if mark is None:
                    gap = "no_effective_observation"
                elif mark:
                    gap = "missing_flag_marked"
                else:
                    continue
                self.result.violations.append(
                    make_violation(
                        KIND_MISSED,
                        rule,
                        project_code=self.project_code,
                        clause_ids=rule.clause_ids,
                        point_id=point.point_id,
                        point_code=point.code,
                        item_code=point.item_code,
                        origin_round_index=round_row.round_index,
                        round_id=round_row.round_id,
                        round_index=round_row.round_index,
                        evidence={
                            "item_code": point.item_code,
                            "round_index": round_row.round_index,
                            "observed_on": round_row.observed_on,
                            "condition_code": condition.code if condition else None,
                            "gap": gap,
                            "detail": "该轮该测点链上无有效读数：{0}".format(
                                "带缺测标记" if gap == "missing_flag_marked" else "无入库行"
                            ),
                        },
                    )
                )

    def check_intervals(self) -> None:
        """间隔检核：逐对相邻轮次，按**后一轮生效工况**的深度取上限档。

        上限来自生效的 `interval` 规则（可按开挖深度分档），没有上限档就没有结论。
        工况变更后的第一轮仍超限的，归类为 `stale_frequency`（变更当轮即按新频率判，`03 §2`）；
        同工况段内的超限归 `over_interval`。同一次间隔只归一个类别，不重复计数。
        """
        if not self._interval_rules:
            return
        prev: Optional[_Round] = None
        prev_condition: Optional[_Condition] = None
        for round_row in self.all_rounds:
            condition = self._effective_condition(round_row)
            if condition is None:
                self._note(
                    REASON_NO_CONDITION,
                    "轮次 R{0}（{1}）早于台账内任何工况生效日期：按哪个频率判无从确定".format(
                        round_row.round_index, round_row.observed_on
                    ),
                )
            elif prev is not None:
                self._judge_interval_pair(prev, prev_condition, round_row, condition)
            prev = round_row
            prev_condition = condition

    def _judge_interval_pair(
        self,
        prev: _Round,
        prev_condition: Optional[_Condition],
        round_row: _Round,
        condition: _Condition,
    ) -> None:
        if condition.excavation_depth is None:
            self._note(
                REASON_DEPTH_UNRECORDED,
                "工况 {0} 未登记开挖深度：间隔上限只能按兜底档判".format(condition.code),
            )
        band, depth_max = self._interval_band(condition.excavation_depth)
        if band is None or band.threshold.value is None:
            self._note(
                REASON_NO_INTERVAL_BAND,
                "工况 {0}（深度 {1}）没有可用的间隔上限档".format(
                    condition.code, condition.excavation_depth
                ),
            )
            return
        changed = (
            prev_condition is None
            or prev_condition.condition_id != condition.condition_id
        )
        driver = band
        kind = KIND_OVER_INTERVAL
        if changed:
            stale_rule = self._enabled_sequence_rule(RULE_ID_CONDITION_CHANGE)
            if stale_rule is None:
                self._note(
                    REASON_STALE_UNLABELABLE,
                    "工况 {0} 在 R{1} 变更，但变更归因规则不生效：只按超限类别报".format(
                        condition.code, round_row.round_index
                    ),
                )
            else:
                kind = KIND_STALE_FREQUENCY
                driver = stale_rule
                clause_extra = tuple(
                    c for c in stale_rule.clause_ids if c not in band.clause_ids
                )
                clause_ids = tuple(band.clause_ids) + clause_extra
                self._emit_interval(
                    kind, driver, clause_ids, band, depth_max,
                    prev, prev_condition, round_row, condition,
                )
                return
        self._emit_interval(
            kind, driver, tuple(band.clause_ids), band, depth_max,
            prev, prev_condition, round_row, condition,
        )

    def _emit_interval(
        self,
        kind: str,
        driver: Rule,
        clause_ids: Tuple[str, ...],
        band: Rule,
        depth_max: Optional[float],
        prev: _Round,
        prev_condition: Optional[_Condition],
        round_row: _Round,
        condition: _Condition,
    ) -> None:
        if not self._in_scope(round_row.round_index):
            return
        limit = float(band.threshold.value or 0.0)
        gap_days = round_row.day_ordinal - prev.day_ordinal
        if gap_days <= limit:
            return
        self.result.violations.append(
            make_violation(
                kind,
                driver,
                project_code=self.project_code,
                clause_ids=clause_ids,
                point_id=None,
                round_id=round_row.round_id,
                round_index=round_row.round_index,
                evidence={
                    "gap_days": gap_days,
                    "limit_days": limit,
                    "band_rule_id": band.id,
                    "band_depth_max": depth_max,
                    "prev_round_index": prev.round_index,
                    "prev_observed_on": prev.observed_on,
                    "observed_on": round_row.observed_on,
                    "condition_code": condition.code,
                    "prev_condition_code": (
                        prev_condition.code if prev_condition else None
                    ),
                    "excavation_depth": condition.excavation_depth,
                    "detail": "间隔 {0} 天 > 上限 {1:g} 天（工况 {2}）".format(
                        gap_days, limit, condition.code
                    ),
                },
            )
        )

    def check_intensified_after_alarm(self) -> None:
        rule = self._enabled_sequence_rule(RULE_ID_INTENSIFY_AFTER_ALARM)
        if rule is None:
            return
        unclosed: Dict[Tuple[int, str], Dict[str, object]] = {}
        rows = self.conn.execute(
            "SELECT a.point_id, a.item_code, r.round_index,"
            " COALESCE(f.round_index, r.round_index) AS origin_index, p.code AS point_code"
            " FROM alarm_state a"
            " JOIN obs_round r ON r.id = a.round_id"
            " JOIN point p ON p.id = a.point_id"
            " LEFT JOIN obs_round f ON f.id = a.first_alarm_round_id"
            " WHERE a.project_id = ? AND a.unclosed = 1",
            (self.project_id,),
        ).fetchall()
        if not rows and self._alarm_state_total() == 0:
            self._note(
                REASON_ALARM_STATE_EMPTY,
                "工程 {0} 无判定结果：先 pmc check，报警后加密观测无从对账".format(
                    self.project_code
                ),
            )
            return
        for row in rows:
            key = (int(row[0]), row[1])
            entry = unclosed.setdefault(
                key,
                {
                    "point_code": row[4],
                    "origin_index": int(row[3]),
                    "unclosed_rounds": 0,
                    "latest_index": int(row[3]),
                },
            )
            entry["unclosed_rounds"] = int(entry["unclosed_rounds"]) + 1
            entry["origin_index"] = min(int(entry["origin_index"]), int(row[3]))
            entry["latest_index"] = max(int(entry["latest_index"]), int(row[2]))
        scope_rounds = self._scope_rounds()
        if not scope_rounds:
            return
        scope_end = max(r.round_index for r in scope_rounds)
        intensified_in_scope = [
            r.round_index for r in scope_rounds if r.is_intensified
        ]
        for (point_id, item_code), entry in sorted(
            unclosed.items(), key=lambda kv: (str(kv[1]["point_code"]), kv[0][1])
        ):
            origin = int(entry["origin_index"])
            if origin > scope_end:
                continue
            if any(index >= origin for index in intensified_in_scope):
                continue
            self.result.violations.append(
                make_violation(
                    KIND_NO_INTENSIFIED,
                    rule,
                    project_code=self.project_code,
                    clause_ids=rule.clause_ids,
                    point_id=point_id,
                    point_code=str(entry["point_code"]),
                    item_code=item_code,
                    origin_round_index=origin,
                    round_id=None,
                    round_index=origin,
                    evidence={
                        "item_code": item_code,
                        "first_alarm_round_index": origin,
                        "unclosed_rounds": int(entry["unclosed_rounds"]),
                        "latest_unclosed_round_index": int(entry["latest_index"]),
                        "audited_to_round_index": scope_end,
                        "intensified_rounds_in_scope": len(intensified_in_scope),
                        "detail": "自 R{0} 起未闭环，至 R{1} 台账内无加密观测轮次".format(
                            origin, scope_end
                        ),
                    },
                )
            )

    def _scope_rounds(self) -> List[_Round]:
        return [r for r in self.all_rounds if self._in_scope(r.round_index)]

    def _alarm_state_total(self) -> int:
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM alarm_state WHERE project_id = ?", (self.project_id,)
            ).fetchone()[0]
        )

    def _note(self, code: str, detail: str) -> None:
        pair = (code, detail)
        if pair not in self.result.notes:
            self.result.notes.append(pair)

    # ---- 执行与落库 -----------------------------------------------------------

    def audit(self) -> AuditResult:
        result = self.result
        in_scope = self._scope_rounds()
        result.rounds_audited = len(in_scope)
        result.intensified_rounds = tuple(
            r.round_index for r in in_scope if r.is_intensified
        )
        self.gate()
        self.check_missed()
        self.check_intervals()
        self.check_intensified_after_alarm()
        result.violations.sort(key=lambda v: v.sort_key)
        return result

    def persist(self, result: AuditResult) -> Dict[str, int]:
        """整段重算后替换该工程的违规清单（口径同 C20：不做增量，避免上一轮残留）。"""
        deleted = self.conn.execute(
            "DELETE FROM violation WHERE project_id = ?", (self.project_id,)
        ).rowcount
        inserted = 0
        for item in result.violations:
            self.conn.execute(
                "INSERT INTO violation(project_id, point_id, round_id, kind, rule_id,"
                " clause_ids, evidence_json) VALUES(?,?,?,?,?,?,?)",
                (
                    self.project_id,
                    item.point_id,
                    item.round_id,
                    item.kind,
                    item.rule_id,
                    ",".join(item.clause_ids),
                    json.dumps(item.evidence, ensure_ascii=False, sort_keys=True),
                ),
            )
            inserted += 1
        self.conn.commit()
        return {"deleted": int(deleted), "inserted": int(inserted)}


def project_codes_with_rounds(conn) -> List[str]:
    return [
        str(row[0])
        for row in conn.execute(
            "SELECT p.code FROM project p"
            " WHERE EXISTS (SELECT 1 FROM obs_round r WHERE r.project_id = p.id)"
            " ORDER BY p.code"
        )
    ]


def run_audit(
    conn,
    *,
    project_code: str,
    rules: Sequence[Rule],
    clauses: Dict[str, ClauseEntry],
    round_from: Optional[int] = None,
    round_to: Optional[int] = None,
    write: bool = True,
) -> Tuple[AuditResult, Dict[str, int]]:
    auditor = ComplianceAuditor(
        conn,
        project_code=project_code,
        rules=rules,
        clauses=clauses,
        round_from=round_from,
        round_to=round_to,
    )
    result = auditor.audit()
    result.persisted = auditor.persist(result) if write else {"deleted": 0, "inserted": 0}
    return result, result.persisted


def degraded_exit(result: AuditResult) -> int:
    """退出码 1 的触发集合（`plan/08 §七`）：有应核实事项、有规则被门控挡住、有检核缺口，三者任一。"""
    if result.violations:
        return 1
    if result.blocked:
        return 1
    if result.notes:
        return 1
    return 0


def kind_counts(results: Sequence[AuditResult]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for result in results:
        for kind, count in result.counts_by_kind().items():
            counts[kind] = counts.get(kind, 0) + count
    return counts
