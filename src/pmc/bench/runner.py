"""内置基准评测内核（M4）：真值 → 装配 → 判定 → 四态指标 → 漏报清单 → golden 位级对账。

口径的单一事实源是 `plan/10-基准与评测.md`，本文件只落地，不复述理由。六条最容易被"顺手简化"的纪律：

  * 期望值一律来自 `data/truth/*.truth.csv`，代码里不硬编事件条数（真值面缩水由 golden 对账当场暴露）；
  * 评测读的是**落库后**的 `alarm_state / observation / import_batch`，不是内存 DTO，
    且只认链上最新行（C16）；
  * 通路未通 =「不可用」，分母为 0 =「不可判」，两者都不是「未达标」，更不是 0 分；
  * `unit_error` 与 `duplicate_report` 属导入器与修订链的考题，永不进漏报清单（`04 §2.3`）；
  * 合成自证档位只从 `pmc.synth.profile` / `pmc.synth.sites` 进来，一个数值都不写进 `data/`；
    `bench` 是装配点（与 `cli` 同一例外），生成器模块 `pmc.synth.generator` 不在许可名单里；
  * 台账建在 `:memory:`：评测不产生工作树文件，写 golden 必须显式 `--write-golden`。

时钟与随机：本模块不读当前时间、不用 `random`，产物字节只由 seed + 台账内容决定。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from pmc.alarm import engine
from pmc.catalog.items import load_items
from pmc.contract.clauses import load_register
from pmc.contract.status import UNCLOSED_STATES, ObsState
from pmc.contract.thresholds import (
    SOURCE_NONE,
    SOURCE_USER,
    STATUS_PENDING,
    STATUS_VERIFIED,
    Threshold,
)
from pmc.db.schema import apply_schema
from pmc.errors import EXIT_DEGRADED, EXIT_OK, InputError
from pmc.ingest import csvio, store
from pmc.rules.loader import Rule, load_rulesets
from pmc.synth import freeze
from pmc.synth.profile import PROFILE_ID, SYNTH_PROFILE
from pmc.synth.sites import get_site, site_codes

SEED = 20260107
JSON_SCHEMA = "pmc-bench/1"
GOLDEN_SCHEMA = "pmc-golden-1"
GOLDEN_REL_PATH = "golden/bench_synth.json"
ROUND_FILE_TEMPLATE = "round-{0:02d}.csv"

#: 平面对外只有两个词（plan/10 §二.1）
PLANE_SYNTH = "synth"
PLANE_LEDGER = "ledger"
PLANES: Tuple[str, ...] = (PLANE_SYNTH, PLANE_LEDGER)

#: 档位凭证：自述"合成自证档位"，既不冒充设计文件值也不冒充规范条文值
GRADE_EVIDENCE = "SYNTH-GRADE:{0}（合成自证档位：非设计文件值、非规范条文值）".format(PROFILE_ID)
#: 预警比例与 M2 夹具同值：只改 normal/prewarning 的分界，不参与四项基准指标
SYNTH_PREWARNING_RATIO = 0.7
RULE_CLAUSE_IDS = ["GB50497-2019:alarm-values"]
#: 装配许可：bench 只准拿档位、站档案与建档入口，不碰生成器
SYNTH_ALLOWED_MODULES: Tuple[str, ...] = ("profile", "sites", "freeze")

#: 指标四态（`04 §四` 的归类落到代码；元组顺序即优先级判定用到的全集）
STATE_MET = "达标"
STATE_UNMET = "未达标"
STATE_UNDETERMINED = "不可判"
STATE_UNAVAILABLE = "不可用"
METRIC_STATES: Tuple[str, ...] = (STATE_MET, STATE_UNMET, STATE_UNDETERMINED, STATE_UNAVAILABLE)

RECALL_GATE = 0.95

#: 结论词（逐起对账表"结论"列的全部取值）
OUTCOME_HIT = "命中"
OUTCOME_LATE = "晚报"
OUTCOME_EARLY = "早报"
OUTCOME_MISS = "漏报"
OUTCOME_NO_ALARM = "无报警符合"
OUTCOME_GUARD_FAIL = "考题失败"
OUTCOME_UNJUDGEABLE = "不可判"
OUTCOMES: Tuple[str, ...] = (
    OUTCOME_HIT, OUTCOME_LATE, OUTCOME_EARLY, OUTCOME_MISS,
    OUTCOME_NO_ALARM, OUTCOME_GUARD_FAIL, OUTCOME_UNJUDGEABLE,
)
ALARM_OUTCOMES: Tuple[str, ...] = (OUTCOME_HIT, OUTCOME_LATE, OUTCOME_EARLY, OUTCOME_MISS)

#: 对账项名：判定依赖四项 + 导入层四项（`plan/10 §四`）
CHECK_FIRST_ROUND = "首超报警轮次"
CHECK_BASIS = "触发依据"
CHECK_UNCLOSED = "未闭环延续"
CHECK_NO_ALARM = "无报警"
CHECK_NO_ROW = "该轮不出判定行"
CHECK_NO_INTERP = "缺测不补值"
CHECK_UNIT_REJECT = "单位不一致被拒收"
CHECK_REVISION = "修订链长度"
CHECK_PERSIST = "落盘数值不变"
JUDGMENT_CHECKS: Tuple[str, ...] = (
    CHECK_FIRST_ROUND, CHECK_BASIS, CHECK_UNCLOSED, CHECK_NO_ALARM, CHECK_NO_ROW,
)
IMPORT_CHECKS: Tuple[str, ...] = (
    CHECK_NO_INTERP, CHECK_UNIT_REJECT, CHECK_REVISION, CHECK_PERSIST,
)

#: `also_expect` token → 对账项名（白名单外即 InputError：先登记再使用，C12）
TOKEN_TO_CHECK = {
    "alarm_basis=cumulative": CHECK_BASIS,
    "alarm_basis=rate": CHECK_BASIS,
    "alarm_basis=both": CHECK_BASIS,
    "no_alarm": CHECK_NO_ALARM,
    "no_row": CHECK_NO_ROW,
    "no_interpolation": CHECK_NO_INTERP,
    "persist_exact": CHECK_PERSIST,
    "reject=unit_mismatch": CHECK_UNIT_REJECT,
    "import_revision=2": CHECK_REVISION,
}

#: 评测轴原因码（第四轴，与阈值轴 / 引擎轴 / 检核轴分账）
NOTE_SYNTHETIC_PLANE = "plane_is_synthetic"
NOTE_CLAUSES_UNVERIFIED = "clauses_unverified"
NOTE_TRUTH_SHRUNK = "truth_shrunk"
NOTE_GOLDEN_MISSING = "golden_missing"
NOTE_GOLDEN_WRITTEN = "golden_written"
NOTE_GOLDEN_DRIFT = "golden_drift"
NOTE_SMALL_SAMPLE = "small_sample_p95"
NOTE_SUBSET = "subset_skips_aggregate_golden"
NOTE_UNJUDGEABLE = "no_shippable_conclusion"
NOTE_CODES: Tuple[str, ...] = (
    NOTE_SYNTHETIC_PLANE, NOTE_CLAUSES_UNVERIFIED, NOTE_TRUTH_SHRUNK,
    NOTE_GOLDEN_MISSING, NOTE_GOLDEN_WRITTEN, NOTE_GOLDEN_DRIFT, NOTE_SMALL_SAMPLE,
    NOTE_SUBSET, NOTE_UNJUDGEABLE,
)

METRIC_RECALL = "recall"
METRIC_FALSE_ALARM = "false_alarm"
METRIC_LOCATION = "location_error"
METRIC_MISSED = "missed_list"
METRIC_UNCLOSED = "unclosed_carry"
METRIC_BLANK = "undetermined_blank"
METRIC_RECEIPT = "receipt_balance"
#: README 指标节用词与这里逐行对齐（`plan/10 §6.2`）
METRIC_NAMES = {
    METRIC_RECALL: "报警召回率",
    METRIC_FALSE_ALARM: "误报率",
    METRIC_LOCATION: "首超报警轮次定位误差",
    METRIC_MISSED: "漏报清单",
    METRIC_UNCLOSED: "未闭环跨轮次延续",
    METRIC_BLANK: "待定值阈值列脱空",
    METRIC_RECEIPT: "导入回执完备率",
}
METRIC_ORDER: Tuple[str, ...] = (
    METRIC_RECALL, METRIC_FALSE_ALARM, METRIC_LOCATION, METRIC_MISSED,
    METRIC_UNCLOSED, METRIC_BLANK, METRIC_RECEIPT,
)
BENCHMARK_METRICS: Tuple[str, ...] = (
    METRIC_RECALL, METRIC_FALSE_ALARM, METRIC_LOCATION, METRIC_MISSED,
)
GATE_TEXT = {
    METRIC_RECALL: "≥0.95",
    METRIC_FALSE_ALARM: "=0",
    METRIC_LOCATION: "中位=0 且 P95≤1",
    METRIC_MISSED: "=0 起",
    METRIC_UNCLOSED: "=1",
    METRIC_BLANK: "=0 条",
    METRIC_RECEIPT: "=1",
}
#: 只有合成面把门槛与数值写进说明列；台账面的说明列不得出现门槛数值，
#: 否则 README 指标节会读成"凭空的指标数值"（`test_readme_honesty.py`）
UNJUDGEABLE_DETAIL = "通路未通：无一条可判判据（阈值全部未挂已核对来源），量不了"
REPRO_FULL = "python -m pmc bench"
REPRO_JSON = "python -m pmc bench --json"


@dataclass
class Metric:
    code: str
    state: str
    value: Optional[float] = None
    numerator: Optional[int] = None
    denominator: Optional[int] = None
    detail: str = ""

    @property
    def name(self) -> str:
        return METRIC_NAMES[self.code]

    @property
    def gate(self) -> str:
        return GATE_TEXT[self.code]

    @property
    def value_text(self) -> str:
        return "-" if self.value is None else "{0:.3f}".format(self.value)

    def as_dict(self) -> Dict[str, object]:
        return {
            "code": self.code,
            "name": self.name,
            "state": self.state,
            "gate": self.gate,
            "value": self.value,
            "value_text": self.value_text,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "detail": self.detail,
        }


@dataclass
class Check:
    name: str
    expect: str
    actual: str
    #: 三态：None = 该面无出货行，判不了（既不算 pass 也不算 fail）
    ok: Optional[bool] = None

    def as_dict(self) -> Dict[str, object]:
        return {"name": self.name, "expect": self.expect, "actual": self.actual, "ok": self.ok}


@dataclass
class EventReport:
    event_id: str
    site: str
    point_code: str
    item_code: str
    event_type: str
    planted_round: Optional[int]
    expected_round: Optional[int]
    detected_round: Optional[int]
    outcome: str
    checks: List[Check] = field(default_factory=list)

    @property
    def error_rounds(self) -> Optional[int]:
        if self.expected_round is None or self.detected_round is None:
            return None
        return self.detected_round - self.expected_round

    def as_dict(self) -> Dict[str, object]:
        return {
            "event_id": self.event_id,
            "site": self.site,
            "point_code": self.point_code,
            "item_code": self.item_code,
            "event_type": self.event_type,
            "planted_round": self.planted_round,
            "expected_round": self.expected_round,
            "detected_round": self.detected_round,
            "error_rounds": self.error_rounds,
            "outcome": self.outcome,
            "checks": [c.as_dict() for c in self.checks],
        }


@dataclass
class BenchReport:
    plane: str
    sites: List[str]
    seed: int
    data_dir: str
    truth_sha256: Dict[str, str]
    events: List[EventReport]
    metrics: List[Metric]
    counts: Dict[str, int]
    notes: List[Dict[str, str]]
    golden_state: str
    golden_problems: List[str]
    exit_code: int

    def metric(self, code: str) -> Metric:
        for item in self.metrics:
            if item.code == code:
                return item
        raise KeyError("指标 {0} 未产出".format(code))

    @property
    def misses(self) -> List[EventReport]:
        return [e for e in self.events if e.outcome == OUTCOME_MISS]

    @property
    def guards(self) -> List[Dict[str, str]]:
        """导入层与修订链考题的失败项：与漏报清单永不相交（`04 §2.3`）。"""
        out = []
        for event in self.events:
            for check in event.checks:
                if check.name in IMPORT_CHECKS and check.ok is False:
                    out.append(
                        {
                            "event_id": event.event_id,
                            "site": event.site,
                            "point_code": event.point_code,
                            "check": check.name,
                            "expect": check.expect,
                            "actual": check.actual,
                        }
                    )
        return out

    def as_dict(self) -> Dict[str, object]:
        return {
            "schema": JSON_SCHEMA,
            "plane": self.plane,
            "grade_profile": PROFILE_ID if self.plane == PLANE_SYNTH else None,
            "seed": self.seed,
            "sites": list(self.sites),
            "data_dir": self.data_dir.replace("\\", "/"),
            "truth": {
                "sha256": dict(sorted(self.truth_sha256.items())),
                "events": len(self.events),
                "alarm_expectant": sum(1 for e in self.events if e.expected_round is not None),
                "no_alarm_expectant": sum(1 for e in self.events if e.expected_round is None),
            },
            "counts": dict(sorted(self.counts.items())),
            "metrics": [m.as_dict() for m in self.metrics],
            "events": [e.as_dict() for e in self.events],
            "misses": [
                {
                    "event_id": e.event_id,
                    "site": e.site,
                    "point_code": e.point_code,
                    "item_code": e.item_code,
                    "expected_round": e.expected_round,
                }
                for e in self.misses
            ],
            "guards": self.guards,
            "notes": self.notes,
            "golden": {
                "path": GOLDEN_REL_PATH,
                "state": self.golden_state,
                "problems": list(self.golden_problems),
            },
            "exit_code": self.exit_code,
        }

    def markdown(self) -> List[str]:
        flag = "" if self.plane == PLANE_SYNTH else " --plane {0}".format(self.plane)
        lines = ["| 指标 | 状态 | 说明 | 复现命令 |", "|---|---|---|---|"]
        for metric in self.metrics:
            base = REPRO_JSON if metric.code == METRIC_MISSED else REPRO_FULL
            command = "`{0}{1}`".format(base, flag)
            if self.plane == PLANE_SYNTH:
                detail = "{0}｜{1}".format(metric.detail, metric.gate)
            else:
                detail = metric.detail
            lines.append(
                "| {0} | {1} | {2} | {3} |".format(
                    metric.name, metric.state, detail.replace("|", "/"), command
                )
            )
        return lines


# --------------------------------------------------------------------------- 真值


@dataclass
class TruthEvent:
    event_id: str
    site: str
    point_code: str
    round_index: Optional[int]
    event_type: str
    magnitude: Optional[float]
    expected_round: Optional[int]
    tokens: Tuple[str, ...]

    @property
    def expects_alarm(self) -> bool:
        return self.expected_round is not None

    def basis_expectation(self) -> Optional[str]:
        for token in self.tokens:
            if token.startswith("alarm_basis="):
                return token.split("=", 1)[1]
        return None

    def unclosed_floor(self) -> Optional[int]:
        for token in self.tokens:
            if token.startswith("unclosed>="):
                return int(token.split(">=", 1)[1])
        return None


def _token_check(token: str) -> Optional[str]:
    """token → 对账项名；带参数的 token 按前缀展开（`07 §6.1` 的白名单形式）。"""
    if token in TOKEN_TO_CHECK:
        return TOKEN_TO_CHECK[token]
    if token.startswith("unclosed>=") and token.split(">=", 1)[1].isdigit():
        return CHECK_UNCLOSED
    return None


def _optional_int(text: str) -> Optional[int]:
    return None if text == "" else int(text)


def _optional_float(text: str) -> Optional[float]:
    return None if text == "" else float(text)


def load_truth(data_dir: str, sites: Sequence[str]) -> List[TruthEvent]:
    """读 `data/truth/<site>.truth.csv`：表头必须逐列等于 7 列冻结契约（C14）。"""
    out: List[TruthEvent] = []
    for site in sites:
        path = os.path.join(data_dir, "truth", site + ".truth.csv")
        if not os.path.isfile(path):
            raise InputError("真值文件缺失：{0}".format(path.replace("\\", "/")))
        with open(path, newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = tuple(next(reader))
            if header != freeze.TRUTH_HEADER:
                raise InputError(
                    "真值表头不是 7 列契约：期望 [{0}]，实为 [{1}]".format(
                        ",".join(freeze.TRUTH_HEADER), ",".join(header)
                    )
                )
            for row in reader:
                if len(row) != len(freeze.TRUTH_HEADER):
                    raise InputError(
                        "真值行 {0} 列数 {1} 与表头不符".format(row[0] if row else "?", len(row))
                    )
                tokens = tuple(t for t in row[6].split(";") if t)
                unknown = sorted(t for t in tokens if _token_check(t) is None)
                if unknown:
                    raise InputError(
                        "真值 {0} 的 also_expect 出现未登记 token：{1}"
                        "（`07 §6.1` 白名单外先登记再使用）".format(row[0], ",".join(unknown))
                    )
                out.append(
                    TruthEvent(
                        event_id=row[0],
                        site=site,
                        point_code=row[1],
                        round_index=_optional_int(row[2]),
                        event_type=row[3],
                        magnitude=_optional_float(row[4]),
                        expected_round=_optional_int(row[5]),
                        tokens=tokens,
                    )
                )
    if not out:
        raise InputError("所选工程在真值面里一起事件都没有：基准无从对账")
    return out


def truth_sha256(data_dir: str, sites: Sequence[str]) -> Dict[str, str]:
    out = {}  # type: Dict[str, str]
    for site in sites:
        with open(os.path.join(data_dir, "truth", site + ".truth.csv"), "rb") as handle:
            out[site] = hashlib.sha256(handle.read()).hexdigest()
    return out


# ------------------------------------------------------------------- 装配（内存台账）


def _blank_threshold() -> Threshold:
    """判据形态规则：值一律脱空（数值属测点档案第一档，不属规则集）。"""
    return Threshold(kind=SOURCE_NONE, status=STATUS_PENDING, value=None, unit=None,
                     note="判据形态规则：不供货数值")


def _ratio_threshold() -> Threshold:
    return Threshold(
        kind=SOURCE_USER,
        status=STATUS_VERIFIED,
        value=SYNTH_PREWARNING_RATIO,
        unit="ratio",
        evidence=GRADE_EVIDENCE,
        note="预警比例本身也是阈值：按合成自证档位登记，不内置默认系数冒充规范值",
    )


def synth_rules(site_code: str) -> List[Rule]:
    """合成面的判据形态：窗口取站点档案（`07 §4.1` 的 3/5/7 天是工程配置事实）。"""
    spec = get_site(site_code)
    rules: List[Rule] = []
    for item_code in sorted(spec.item_codes()):
        rules.append(
            Rule(
                id="bench-cum-{0}".format(item_code),
                name="{0} 累计量报警（合成自证档位形态）".format(item_code),
                item_code=item_code,
                judgement="alarm",
                basis="cumulative",
                clause_ids=list(RULE_CLAUSE_IDS),
                threshold=_blank_threshold(),
            )
        )
        rules.append(
            Rule(
                id="bench-rate-{0}".format(item_code),
                name="{0} 速率报警（合成窗口 {1} 天）".format(item_code, spec.window_days),
                item_code=item_code,
                judgement="alarm",
                basis="rate",
                clause_ids=list(RULE_CLAUSE_IDS),
                window_days=float(spec.window_days),
                window_source={"kind": "project_config", "evidence": GRADE_EVIDENCE},
                threshold=_blank_threshold(),
            )
        )
        rules.append(
            Rule(
                id="bench-ratio-{0}".format(item_code),
                name="{0} 预警比例（合成自证档位 {1}）".format(item_code, SYNTH_PREWARNING_RATIO),
                item_code=item_code,
                judgement="prewarning",
                basis="ratio",
                clause_ids=[],
                threshold=_ratio_threshold(),
            )
        )
    for rule in rules:
        rule.validate()
    return rules


def attach_synth_grades(conn, items, sites: Sequence[str]) -> int:
    """把合成自证档位挂进内存台账的 `point` 档案：`data/` 里一个数值都不出现（C7）。"""
    updated = 0
    for site in sites:
        row = conn.execute("SELECT id FROM project WHERE code = ?", (site,)).fetchone()
        if row is None:
            raise InputError("工程 {0} 未建档，挂不上合成档位".format(site))
        for item_code in sorted(SYNTH_PROFILE):
            if item_code not in items:
                raise InputError("监测项目 {0} 不在字典里，合成档位挂不上档案".format(item_code))
            grade = SYNTH_PROFILE[item_code]
            unit = items[item_code].unit
            cur = conn.execute(
                "UPDATE point SET design_cum_value=?, design_cum_unit=?,"
                " design_rate_value=?, design_rate_unit=?, threshold_source_kind=?,"
                " threshold_status=?, threshold_evidence=?"
                " WHERE project_id=? AND item_code=?",
                (
                    grade["cum"],
                    unit,
                    grade["rate"],
                    None if grade["rate"] is None else "{0}/d".format(unit),
                    SOURCE_USER,
                    STATUS_VERIFIED,
                    GRADE_EVIDENCE,
                    row[0],
                    item_code,
                ),
            )
            updated += cur.rowcount
    conn.commit()
    return updated


def import_all_rounds(conn, data_dir: str, sites: Sequence[str],
                     rounds_by_site: Dict[str, int]) -> Dict[str, int]:
    """逐轮走 M1 的导入通路（`csvio` → `store.write_batch`）：评测吃的就是现场实况。"""
    items = load_items(data_dir)
    counts = {"batches": 0, "rejected_rows": 0}
    for site in sites:
        project_id = store.project_id_for(conn, site)
        points = store.point_archive(conn, project_id)
        for round_index in range(1, rounds_by_site[site] + 1):
            path = os.path.join(data_dir, "raw", site, ROUND_FILE_TEMPLATE.format(round_index))
            if not os.path.isfile(path):
                raise InputError(
                    "轮次产物缺失：{0}（跑 pmc synth --check 确认冻结产物完整）".format(
                        path.replace("\\", "/")
                    )
                )
            text, sha256 = csvio.load_file(path)
            parsed = csvio.parse_csv(
                text,
                source_file=path,
                file_sha256=sha256,
                round_index=round_index,
                items=items,
                points=points,
            )
            receipt = store.write_batch(
                conn, project_code=site, round_index=round_index, parsed=parsed
            )
            counts["batches"] += 1
            counts["rejected_rows"] += receipt.rows_rejected
    return counts


# --------------------------------------------------------------- 落库回读（只认链头）


def read_judged_rows(conn) -> List[Dict[str, object]]:
    rows = conn.execute(
        "SELECT pr.code, p.code, a.item_code, r.round_index, a.state, a.trigger_basis,"
        " a.unclosed, a.threshold_source_kind"
        " FROM alarm_state a"
        " JOIN point p ON p.id = a.point_id"
        " JOIN project pr ON pr.id = a.project_id"
        " JOIN obs_round r ON r.id = a.round_id"
        " ORDER BY pr.code, p.code, a.item_code, r.round_index"
    ).fetchall()
    keys = ("site", "point_code", "item_code", "round_index", "state",
            "trigger_basis", "unclosed", "source_kind")
    return [dict(zip(keys, row)) for row in rows]


def read_observations(conn) -> Dict[Tuple[str, str, int], List[Dict[str, object]]]:
    """(工程, 测点, 轮次) → 修订链各行：评测按链上最新行下结论（C16）。"""
    rows = conn.execute(
        "SELECT pr.code, p.code, r.round_index, o.revision_seq, o.superseded_by,"
        " o.value_cum, o.missing"
        " FROM observation o"
        " JOIN point p ON p.id = o.point_id"
        " JOIN project pr ON pr.id = p.project_id"
        " JOIN obs_round r ON r.id = o.round_id"
        " ORDER BY pr.code, p.code, r.round_index, o.revision_seq"
    ).fetchall()
    grouped = {}  # type: Dict[Tuple[str, str, int], List[Dict[str, object]]]
    for row in rows:
        grouped.setdefault((row[0], row[1], int(row[2])), []).append(
            {
                "revision_seq": int(row[3]),
                "head": row[4] is None,
                "value_cum": row[5],
                "missing": int(row[6]),
            }
        )
    return grouped


def read_receipt_rejections(conn) -> Dict[Tuple[str, int], List[Dict[str, object]]]:
    out = {}  # type: Dict[Tuple[str, int], List[Dict[str, object]]]
    rows = conn.execute(
        "SELECT pr.code, r.round_index, b.receipt_json FROM import_batch b"
        " JOIN project pr ON pr.id = b.project_id"
        " JOIN obs_round r ON r.id = b.round_id"
    ).fetchall()
    for site, round_index, raw in rows:
        out[(site, int(round_index))] = list(json.loads(raw).get("rejections", []))
    return out


def read_receipt_balance(conn) -> Tuple[int, int]:
    """(平衡批次数, 批次数)：按 `import_batch` 三列与回执 JSON 两侧同时核对。"""
    rows = conn.execute(
        "SELECT rows_total, rows_accepted, rows_rejected, receipt_json FROM import_batch"
    ).fetchall()
    balanced = 0
    for total, accepted, rejected, raw in rows:
        doc = json.loads(raw)
        ok = (
            int(accepted) + int(rejected) == int(total)
            and int(doc.get("rows_total", -1)) == int(total)
            and int(doc.get("rows_accepted", -1)) == int(accepted)
            and int(doc.get("rows_rejected", -1)) == int(rejected)
        )
        balanced += 1 if ok else 0
    return balanced, len(rows)


def read_csv_literals(data_dir: str, site: str, round_index: int) -> Dict[str, str]:
    """轮次 CSV 里各测点的 `cumulative_value` 字面：`persist_exact` 要逐位相等，不清洗不四舍五入。"""
    path = os.path.join(data_dir, "raw", site, ROUND_FILE_TEMPLATE.format(round_index))
    with open(path, newline="", encoding="utf-8") as handle:
        return {row["point_code"]: row["cumulative_value"] for row in csv.DictReader(handle)}


# -------------------------------------------------------------------- 逐起对账


def _group_by_point(rows: Sequence[Dict[str, object]]) -> Dict[Tuple[str, str, str], List[Dict[str, object]]]:
    grouped = {}  # type: Dict[Tuple[str, str, str], List[Dict[str, object]]]
    for row in rows:
        grouped.setdefault((row["site"], row["point_code"], row["item_code"]), []).append(row)
    return grouped


def _unclosed_rounds(series: Sequence[Dict[str, object]]) -> List[int]:
    return sorted(int(row["round_index"]) for row in series if row["state"] in UNCLOSED_STATES)


def _head(chain: Sequence[Dict[str, object]]) -> Optional[Dict[str, object]]:
    for row in chain:
        if row["head"]:
            return row
    return None


def reconcile_events(truth: Sequence[TruthEvent], judged_rows: Sequence[Dict[str, object]],
                     observations, rejections, data_dir: str) -> List[EventReport]:
    """逐起事件对账：判定依赖项与导入层项分开评，通路未通时判定项记 `ok=None` 而不是蒙 pass。"""
    grouped = _group_by_point(judged_rows)
    item_by_point = {}  # type: Dict[Tuple[str, str], str]
    for row in judged_rows:
        item_by_point.setdefault((row["site"], row["point_code"]), row["item_code"])
    judgeable = any(row["state"] != ObsState.UNDETERMINED for row in judged_rows)

    reports: List[EventReport] = []
    for event in sorted(truth, key=lambda e: (e.site, e.event_id)):
        item_code = item_by_point.get((event.site, event.point_code))
        if item_code is None:
            raise InputError(
                "真值 {0} 的测点 {1} 在判定面里一行都没有：工程名或测点编号与台账不一致".format(
                    event.event_id, event.point_code
                )
            )
        key = (event.site, event.point_code, item_code)
        series = grouped.get(key, [])
        rounds = _unclosed_rounds(series)
        detected = rounds[0] if rounds else None
        checks: List[Check] = []

        if event.expects_alarm:
            expected = int(event.expected_round)
            if not judgeable:
                checks.append(Check(CHECK_FIRST_ROUND, "R{0}".format(expected), UNJUDGEABLE_DETAIL, None))
                outcome = OUTCOME_UNJUDGEABLE
            elif detected is None:
                checks.append(Check(CHECK_FIRST_ROUND, "R{0}".format(expected), "无报警行", False))
                outcome = OUTCOME_MISS
            else:
                error = detected - expected
                checks.append(
                    Check(CHECK_FIRST_ROUND, "R{0}".format(expected), "R{0}".format(detected), True)
                )
                outcome = (
                    OUTCOME_HIT if error == 0 else OUTCOME_LATE if error > 0 else OUTCOME_EARLY
                )
        else:
            outcome = OUTCOME_NO_ALARM

        for token in event.tokens:
            checks.append(
                _run_check(token, event, series, rounds, detected, observations,
                           rejections, data_dir, judgeable)
            )

        hard_fail = [c for c in checks if c.ok is False]
        if not event.expects_alarm and hard_fail:
            outcome = OUTCOME_GUARD_FAIL
        reports.append(
            EventReport(
                event_id=event.event_id,
                site=event.site,
                point_code=event.point_code,
                item_code=item_code,
                event_type=event.event_type,
                planted_round=event.round_index,
                expected_round=event.expected_round,
                detected_round=detected,
                outcome=outcome,
                checks=checks,
            )
        )
    return reports


def _chain_of(observations, event: TruthEvent) -> List[Dict[str, object]]:
    if event.round_index is None:
        return []
    return observations.get((event.site, event.point_code, event.round_index), [])


def _run_check(token, event, series, rounds, detected, observations, rejections,
               data_dir, judgeable) -> Check:
    name = _token_check(token) or CHECK_NO_ALARM
    if name in JUDGMENT_CHECKS and not judgeable:
        return Check(name, token, UNJUDGEABLE_DETAIL, None)

    if name == CHECK_BASIS:
        want = event.basis_expectation()
        row = next((r for r in series if detected is not None
                    and int(r["round_index"]) == detected), None)
        actual = "-" if row is None else str(row["trigger_basis"])
        return Check(name, token, actual, detected is not None and actual == want)

    if name == CHECK_UNCLOSED:
        floor = event.unclosed_floor() or 0
        carried = sum(
            1 for r in series
            if int(r["unclosed"]) == 1 and detected is not None
            and int(r["round_index"]) >= detected
        )
        return Check(name, token, "{0} 行".format(carried), carried >= floor)

    if name == CHECK_NO_ALARM:
        return Check(name, token, "未闭环 {0} 行".format(len(rounds)), not rounds)

    if name == CHECK_NO_ROW:
        present = any(int(r["round_index"]) == (event.round_index or -1) for r in series)
        return Check(name, token, "有判定行" if present else "无判定行", not present)

    chain = _chain_of(observations, event)
    head = _head(chain)

    if name == CHECK_NO_INTERP:
        ok = head is not None and int(head["missing"]) == 1 and head["value_cum"] is None
        actual = "无观测行" if head is None else "missing={0} value={1}".format(
            head["missing"], "-" if head["value_cum"] is None else head["value_cum"]
        )
        return Check(name, token, actual, ok)

    if name == CHECK_UNIT_REJECT:
        site_rejects = rejections.get((event.site, event.round_index or -1), [])
        hit = [
            r for r in site_rejects
            if r.get("reason_code") == "unit_mismatch"
            and event.point_code in str(r.get("detail", ""))
        ]
        return Check(
            name, token,
            "拒收 {0} 条 / 观测行 {1} 条".format(len(hit), len(chain)),
            bool(hit) and not chain,
        )

    if name == CHECK_REVISION:
        seqs = [int(row["revision_seq"]) for row in chain]
        head_seq = None if head is None else int(head["revision_seq"])
        return Check(
            name, token,
            "seq={0} 链头={1}".format(seqs, "-" if head_seq is None else head_seq),
            seqs == [1, 2] and head_seq == 2,
        )

    literals = read_csv_literals(data_dir, event.site, event.round_index or 1)
    literal = literals.get(event.point_code, "")
    stored = "" if head is None or head["value_cum"] is None else "{0}".format(head["value_cum"])
    exact = head is not None and head["value_cum"] is not None and literal != "" \
        and float(literal) == float(stored)
    return Check(name, token, "CSV={0} 落盘={1}".format(literal or "-", stored or "-"), exact)


# ---------------------------------------------------------------------- 指标


def compute_metrics(truth: Sequence[TruthEvent], reports: Sequence[EventReport],
                    judged_rows: Sequence[Dict[str, object]], receipt_balance: Tuple[int, int]
                    ) -> List[Metric]:
    grouped = _group_by_point(judged_rows)
    judgeable = any(row["state"] != ObsState.UNDETERMINED for row in judged_rows)
    item_by_point = {}  # type: Dict[Tuple[str, str], str]
    for row in judged_rows:
        item_by_point.setdefault((row["site"], row["point_code"]), row["item_code"])

    #: 支撑集按真值算（不是按检出算）：早报的那一轮因此既计进误报分子又计进定位误差的负号
    supported = set()  # type: set
    for event in truth:
        if event.expected_round is None:
            continue
        prefix = (event.site, event.point_code, item_by_point.get((event.site, event.point_code)))
        for row in judged_rows:
            if (row["site"], row["point_code"], row["item_code"]) == prefix \
                    and int(row["round_index"]) >= int(event.expected_round):
                supported.add(prefix + (int(row["round_index"]),))

    outside = [
        row for row in judged_rows
        if (row["site"], row["point_code"], row["item_code"], int(row["round_index"])) not in supported
    ]
    fp_rows = [row for row in outside if row["state"] in UNCLOSED_STATES]
    shipped = [row for row in judged_rows if row["state"] != ObsState.UNDETERMINED]

    expectant = [e for e in truth if e.expected_round is not None]
    by_id = {r.event_id: r for r in reports}
    detected = [by_id[e.event_id] for e in expectant if by_id[e.event_id].detected_round is not None]
    missed = [by_id[e.event_id] for e in expectant if by_id[e.event_id].detected_round is None]

    metrics: List[Metric] = []

    # 1) 召回率
    if not judgeable:
        metrics.append(Metric(METRIC_RECALL, STATE_UNAVAILABLE, detail=UNJUDGEABLE_DETAIL))
    elif not expectant:
        metrics.append(Metric(METRIC_RECALL, STATE_UNDETERMINED, detail="应报警事件 0 起：分母为 0"))
    else:
        value = len(detected) / float(len(expectant))
        metrics.append(
            Metric(
                METRIC_RECALL,
                STATE_MET if value >= RECALL_GATE else STATE_UNMET,
                value=value,
                numerator=len(detected),
                denominator=len(expectant),
                detail="检出 {0}/{1} 起，漏报 {2} 起".format(len(detected), len(expectant), len(missed)),
            )
        )

    # 2) 误报率
    if not judgeable:
        metrics.append(Metric(METRIC_FALSE_ALARM, STATE_UNAVAILABLE, detail=UNJUDGEABLE_DETAIL))
    elif not outside:
        metrics.append(Metric(METRIC_FALSE_ALARM, STATE_UNDETERMINED, detail="正常观测轮次 0 行：分母为 0"))
    else:
        value = len(fp_rows) / float(len(outside))
        metrics.append(
            Metric(
                METRIC_FALSE_ALARM,
                STATE_MET if not fp_rows else STATE_UNMET,
                value=value,
                numerator=len(fp_rows),
                denominator=len(outside),
                detail="无真值支撑的报警行 {0} 条 / 正常观测 {1} 行".format(len(fp_rows), len(outside)),
            )
        )

    # 3) 定位误差
    errors = sorted(int(r.error_rounds) for r in detected)
    if not judgeable:
        metrics.append(Metric(METRIC_LOCATION, STATE_UNAVAILABLE, detail=UNJUDGEABLE_DETAIL))
    elif not errors:
        metrics.append(Metric(METRIC_LOCATION, STATE_UNDETERMINED, detail="检出事件 0 起：无分布可算"))
    else:
        median = _median(errors)
        p95 = _p95(errors)
        strict = sum(1 for e in errors if e == 0) / float(len(errors))
        state = STATE_MET if median == 0.0 and p95 <= 1.0 else STATE_UNMET
        metrics.append(
            Metric(
                METRIC_LOCATION, state,
                value=median,
                numerator=sum(1 for e in errors if e == 0),
                denominator=len(errors),
                detail="中位 {0} / P95 {1} / 样本 {2} 起 / 严格首超命中 {3:.3f}（正=晚报，负=早报）".format(
                    _num_text(median), _num_text(p95), len(errors), strict
                ),
            )
        )

    # 4) 漏报清单
    if not judgeable:
        metrics.append(Metric(METRIC_MISSED, STATE_UNAVAILABLE, detail=UNJUDGEABLE_DETAIL))
    elif not expectant:
        metrics.append(Metric(METRIC_MISSED, STATE_UNDETERMINED, detail="应报警事件 0 起：分母为 0"))
    else:
        metrics.append(
            Metric(
                METRIC_MISSED,
                STATE_MET if not missed else STATE_UNMET,
                value=float(len(missed)),
                numerator=len(missed),
                denominator=len(expectant),
                detail="漏报 {0} 起（晚报与早报不在此列，见逐起对账表）".format(len(missed)),
            )
        )

    # 5) 未闭环跨轮次延续
    pairs = 0
    carried = 0
    for series in grouped.values():
        ordered = sorted(series, key=lambda r: int(r["round_index"]))
        for previous, current in zip(ordered, ordered[1:]):
            if previous["state"] in UNCLOSED_STATES:
                pairs += 1
                if current["state"] in UNCLOSED_STATES:
                    carried += 1
    if not judgeable:
        metrics.append(Metric(METRIC_UNCLOSED, STATE_UNAVAILABLE, detail=UNJUDGEABLE_DETAIL))
    elif pairs == 0:
        metrics.append(Metric(METRIC_UNCLOSED, STATE_UNDETERMINED, detail="未闭环相邻轮次对 0 对：分母为 0"))
    else:
        value = carried / float(pairs)
        metrics.append(
            Metric(
                METRIC_UNCLOSED, STATE_MET if carried == pairs else STATE_UNMET,
                value=value, numerator=carried, denominator=pairs,
                detail="报警后仍带未闭环标记 {0}/{1} 对".format(carried, pairs),
            )
        )

    # 6) 待定值阈值列脱空（常驻断言）
    blank_hits = [row for row in shipped if row["source_kind"] == SOURCE_NONE]
    metrics.append(
        Metric(
            METRIC_BLANK, STATE_MET if not blank_hits else STATE_UNMET,
            value=float(len(blank_hits)),
            numerator=len(blank_hits),
            denominator=len(shipped),
            detail="出货行里来源为 none 的 {0} 条（出货行 {1} 行）".format(len(blank_hits), len(shipped)),
        )
    )

    # 7) 导入回执完备率
    balanced, batches = receipt_balance
    if batches == 0:
        metrics.append(Metric(METRIC_RECEIPT, STATE_UNDETERMINED, detail="导入批次 0 个：分母为 0"))
    else:
        value = balanced / float(batches)
        metrics.append(
            Metric(
                METRIC_RECEIPT, STATE_MET if balanced == batches else STATE_UNMET,
                value=value, numerator=balanced, denominator=batches,
                detail="accepted+rejected=total 的批次 {0}/{1}".format(balanced, batches),
            )
        )

    return [next(m for m in metrics if m.code == code) for code in METRIC_ORDER]


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        return 0.0
    mid = count // 2
    if count % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _p95(values: Sequence[float]) -> float:
    """最近秩法（`ceil(0.95n)` 位置的实测值，不做插值）：小样本下插值会造出数据里没有的数。"""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = int(math.ceil(0.95 * len(ordered)))
    return float(ordered[min(max(index, 1), len(ordered)) - 1])


def _num_text(value: float) -> str:
    return "{0:g}".format(value)


# ------------------------------------------------------------------ golden


def golden_doc(report: "BenchReport") -> Dict[str, object]:
    by_site = {}  # type: Dict[str, object]
    for site in report.sites:
        events = [e.as_dict() for e in report.events if e.site == site]
        by_site[site] = {
            "events": events,
            "truth_sha256": report.truth_sha256[site],
            "outcomes": sorted(set(str(e["outcome"]) for e in events)),
        }
    return {
        "schema": GOLDEN_SCHEMA,
        "notice": (
            "M4 基准评测期望结果：由 `pmc bench --plane {0} --write-golden` 生成，字节冻结入仓。"
            "只记评测结论与指标，不记任何阈值数值（合成自证档位属 pmc/synth/profile.py，"
            "数值不得进 data/）；也不记时间戳（plan/04 §三）。改生成器、改判定或改真值都会让本文件漂移，"
            "重基线必须在 plan/05 的偏差表里说明原因。".format(report.plane)
        ),
        "plane": report.plane,
        "grade_profile": PROFILE_ID,
        "seed": report.seed,
        "sites": list(report.sites),
        "counts": dict(sorted(report.counts.items())),
        "metrics": [
            {
                "code": m.code,
                "name": m.name,
                "state": m.state,
                "value_text": m.value_text,
                "numerator": m.numerator,
                "denominator": m.denominator,
            }
            for m in report.metrics
        ],
        "by_site": by_site,
    }


def canonical_bytes(doc: Dict[str, object]) -> bytes:
    return (
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def golden_path(data_dir: str) -> str:
    return os.path.join(data_dir, *GOLDEN_REL_PATH.split("/"))


def check_golden(report: "BenchReport", data_dir: str) -> Tuple[str, List[str]]:
    """位级对账：全三座比字节；取子集时只比对应站点那一段（`plan/10 §八`）。"""
    path = golden_path(data_dir)
    if report.plane != PLANE_SYNTH:
        return "skipped", []
    if not os.path.isfile(path):
        return "missing", ["golden 不存在：{0}（跑 pmc bench --write-golden 落盘）".format(
            path.replace("\\", "/"))]
    with open(path, "rb") as handle:
        stored = handle.read()
    doc = golden_doc(report)
    if report.sites == site_codes():
        produced = canonical_bytes(doc)
        if produced == stored:
            return "ok", []
        return "drift", _drift_problems(stored, produced)
    try:
        stored_doc = json.loads(stored.decode("utf-8"))
    except ValueError as exc:
        return "drift", ["golden 不是合法 JSON：{0}".format(exc)]
    problems = []
    stored_by_site = stored_doc.get("by_site", {})
    for site in report.sites:
        if site not in stored_by_site:
            problems.append("golden 里没有工程 {0} 的段".format(site))
            continue
        want = canonical_bytes({site: doc["by_site"][site]})
        have = canonical_bytes({site: stored_by_site[site]})
        if want != have:
            problems.append("工程 {0} 的逐起对账段漂移".format(site))
    return ("ok" if not problems else "drift"), problems


def _drift_problems(stored: bytes, produced: bytes) -> List[str]:
    try:
        stored_doc = json.loads(stored.decode("utf-8"))
    except ValueError:
        return ["golden 不是合法 JSON"]
    problems = []  # type: List[str]
    stored_by_site = stored_doc.get("by_site", {})
    produced_doc = json.loads(produced.decode("utf-8"))
    produced_by_site = produced_doc.get("by_site", {})
    for site in sorted(set(stored_by_site) | set(produced_by_site)):
        if site not in stored_by_site:
            problems.append("多出工程段 {0}".format(site))
            continue
        if site not in produced_by_site:
            problems.append("缺工程段 {0}".format(site))
            continue
        old_events = {e["event_id"]: e for e in stored_by_site[site]["events"]}
        new_events = {e["event_id"]: e for e in produced_by_site[site]["events"]}
        for event_id in sorted(set(old_events) - set(new_events)):
            problems.append("真值面缩水：{0} 在本轮结果里消失".format(event_id))
        for event_id in sorted(set(new_events) - set(old_events)):
            problems.append("多出事件 {0}（真值面新增，先归因再重基线）".format(event_id))
        for event_id in sorted(set(old_events) & set(new_events)):
            if old_events[event_id] != new_events[event_id]:
                problems.append(
                    "逐起对账漂移 {0}：结论 {1}→{2}，检出轮 {3}→{4}".format(
                        event_id,
                        old_events[event_id].get("outcome"),
                        new_events[event_id].get("outcome"),
                        old_events[event_id].get("detected_round"),
                        new_events[event_id].get("detected_round"),
                    )
                )
    for old, new in zip(stored_doc.get("metrics", []), produced_doc.get("metrics", [])):
        if old != new:
            problems.append("指标漂移 {0}：{1}→{2}".format(old.get("code"), old.get("state"),
                                                          new.get("state")))
    return problems


def write_golden(report: "BenchReport", data_dir: str) -> str:
    path = golden_path(data_dir)
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "wb") as handle:
        handle.write(canonical_bytes(golden_doc(report)))
    return path


# -------------------------------------------------------------------- 主入口


def resolve_sites(raw: Optional[str]) -> List[str]:
    if not raw:
        return site_codes()
    wanted = [item.strip() for item in raw.split(",") if item.strip()]
    known = site_codes()
    out: List[str] = []
    for code in wanted:
        if code not in known:
            raise InputError(
                "工程 {0} 不在虚拟基坑登记顺序里：可选 {1}".format(code, ",".join(known))
            )
        if code not in out:
            out.append(code)
    return [code for code in known if code in out]


def degraded_exit(report: "BenchReport") -> int:
    """退出码（`plan/10 §七`）：达标且 golden 一致才 0。"""
    if any(metric.state != STATE_MET for metric in report.metrics):
        return EXIT_DEGRADED
    if report.misses or report.guards:
        return EXIT_DEGRADED
    if any(check.ok is False for event in report.events for check in event.checks):
        return EXIT_DEGRADED
    if report.plane == PLANE_SYNTH and report.golden_state in ("missing", "drift"):
        return EXIT_DEGRADED
    return EXIT_DEGRADED if report.plane == PLANE_LEDGER else EXIT_OK


def run_bench(data_dir: str, plane: str = PLANE_SYNTH, sites: Optional[str] = None,
              seed: int = SEED, write: bool = False) -> BenchReport:
    if plane not in PLANES:
        raise InputError("评测平面 {0} 非法：只支持 {1}".format(plane, ",".join(PLANES)))
    selected = resolve_sites(sites)
    if write and (plane != PLANE_SYNTH or selected != site_codes()):
        raise InputError(
            "重基线只在合成面全三座基坑时允许：先跑 pmc bench --plane {0} --write-golden".format(
                PLANE_SYNTH
            )
        )

    items = load_items(data_dir)
    clauses = load_register(data_dir)
    conn = sqlite3.connect(":memory:")
    try:
        apply_schema(conn)
        datas, _blobs = freeze.build(data_dir, seed, len(site_codes()))
        chosen = [data for data in datas if data.site.code in selected]
        if len(chosen) != len(selected):
            raise InputError("建档缺工程：期望 {0}".format(",".join(selected)))
        freeze.seed_ledger(conn, chosen)
        rounds_by_site = {data.site.code: len(data.rounds) for data in chosen}
        import_counts = import_all_rounds(conn, data_dir, selected, rounds_by_site)

        #: 规则必须一站一套：速率窗口是工程配置事实（日报 3 / 隔日 5 / 周报 7 天），
        #: 混成一套会让周报站拿 5 天窗口算速率，窗口内没有先前读数 → 晚报一轮、判据也归错
        if plane == PLANE_SYNTH:
            attached = attach_synth_grades(conn, items, selected)
            for site in selected:
                engine.run_check(
                    conn,
                    project_code=site,
                    items=items,
                    rules=synth_rules(site),
                    clauses=clauses,
                    write=True,
                )
        else:
            attached = 0
            rules = []
            for ruleset in load_rulesets(data_dir):
                rules.extend(ruleset.rules)
            for site in selected:
                engine.run_check(
                    conn,
                    project_code=site,
                    items=items,
                    rules=rules,
                    clauses=clauses,
                    write=True,
                )

        truth = load_truth(data_dir, selected)
        judged_rows = read_judged_rows(conn)
        observations = read_observations(conn)
        rejections = read_receipt_rejections(conn)
        balance = read_receipt_balance(conn)
    finally:
        conn.close()

    reports = reconcile_events(truth, judged_rows, observations, rejections, data_dir)
    metrics = compute_metrics(truth, reports, judged_rows, balance)
    undetermined = sum(1 for row in judged_rows if row["state"] == ObsState.UNDETERMINED)
    counts = {
        "batches": import_counts["batches"],
        "import_rejected_rows": import_counts["rejected_rows"],
        "judged_rows": len(judged_rows),
        "judgeable_rows": len(judged_rows) - undetermined,
        "undetermined_rows": undetermined,
        "truth_events": len(truth),
        "graded_points": attached,
    }

    report = BenchReport(
        plane=plane,
        sites=list(selected),
        seed=seed,
        data_dir=data_dir,
        truth_sha256=truth_sha256(data_dir, selected),
        events=reports,
        metrics=metrics,
        counts=counts,
        notes=[],
        golden_state="skipped",
        golden_problems=[],
        exit_code=EXIT_OK,
    )

    notes: List[Dict[str, str]] = []
    if plane == PLANE_SYNTH:
        notes.append(
            {
                "code": NOTE_SYNTHETIC_PLANE,
                "text": "本面标尺是合成自证档位 {0}（非规范值）：全对不等于符合规范，"
                        "只等于判定与真值自洽".format(PROFILE_ID),
            }
        )
    verified = sum(1 for entry in clauses.values() if entry.can_supply_values)
    if verified == 0:
        notes.append(
            {
                "code": NOTE_CLAUSES_UNVERIFIED,
                "text": "依据登记表里可供货条款 0 条（verified=0）：生产数据面出不了数值结论",
            }
        )
    if counts["judgeable_rows"] == 0:
        notes.append({"code": NOTE_UNJUDGEABLE, "text": UNJUDGEABLE_DETAIL})
    location = report.metric(METRIC_LOCATION)
    if location.state in (STATE_MET, STATE_UNMET) and (location.denominator or 0) < 20:
        notes.append(
            {
                "code": NOTE_SMALL_SAMPLE,
                "text": "定位误差样本 {0} 起（<20）：P95 用最近秩法实测值，不做插值".format(
                    location.denominator
                ),
            }
        )

    if write:
        path = write_golden(report, data_dir)
        report.golden_state = "written"
        notes.append(
            {"code": NOTE_GOLDEN_WRITTEN, "text": "已重基线：{0}".format(path.replace("\\", "/"))}
        )
    else:
        state, problems = check_golden(report, data_dir)
        report.golden_state = state
        report.golden_problems = problems
        if state == "missing":
            notes.append({"code": NOTE_GOLDEN_MISSING, "text": "; ".join(problems)})
        elif state == "drift":
            notes.append(
                {
                    "code": NOTE_GOLDEN_DRIFT,
                    "text": "与仓内 golden 不一致：{0}".format("; ".join(problems)),
                }
            )
            shrunk = [text for text in problems if "缩水" in text]
            if shrunk:
                notes.append(
                    {
                        "code": NOTE_TRUTH_SHRUNK,
                        "text": "真值面比 golden 少：{0}".format("; ".join(shrunk)),
                    }
                )
        elif plane == PLANE_SYNTH and selected != site_codes():
            notes.append(
                {"code": NOTE_SUBSET, "text": "子集运行：只比对 golden 内对应工程段，聚合指标不比对"}
            )

    report.notes = notes
    report.exit_code = degraded_exit(report)
    return report


# -------------------------------------------------------------------- 输出


def text_lines(report: BenchReport) -> List[str]:
    doc = report.as_dict()
    lines = [
        "BENCH_PLANE {0} 工程 {1} seed={2} 档位={3} 数据目录={4}".format(
            report.plane,
            "/".join(report.sites),
            report.seed,
            doc["grade_profile"] or "台账现有来源",
            report.data_dir.replace("\\", "/"),
        ),
        "BENCH_TRUTH 事件 {0} 起：应报警 {1}、无报警期望 {2}；真值 sha256 {3}".format(
            doc["truth"]["events"],
            doc["truth"]["alarm_expectant"],
            doc["truth"]["no_alarm_expectant"],
            " ".join(
                "{0}={1}".format(site, digest[:12])
                for site, digest in sorted(report.truth_sha256.items())
            ),
        ),
        "BENCH_SUMMARY 导入批次 {0}（拒收行 {1}）判定行 {2}：可判 {3} / 待定 {4}；挂档测点 {5}".format(
            report.counts["batches"],
            report.counts["import_rejected_rows"],
            report.counts["judged_rows"],
            report.counts["judgeable_rows"],
            report.counts["undetermined_rows"],
            report.counts["graded_points"],
        ),
    ]
    for metric in report.metrics:
        lines.append(
            "BENCH_METRIC {0} {1} {2} 分子 {3} / 分母 {4}｜{5}".format(
                metric.code, metric.name, metric.state,
                _fmt_num(metric.numerator), _fmt_num(metric.denominator),
                metric.detail,
            )
        )
    lines.append("工程\t事件\t测点\t项目\t类型\t植入轮\t期望轮\t检出轮\t误差\t结论\t对账项")
    for event in report.events:
        failed = [c.name for c in event.checks if c.ok is False]
        pending = [c.name for c in event.checks if c.ok is None]
        marks = "对账 {0} 项".format(len(event.checks))
        if failed:
            marks += " 失败：" + "、".join(failed)
        if pending:
            marks += " 判不了：" + "、".join(pending)
        lines.append(
            "\t".join(
                (
                    event.site,
                    event.event_id,
                    event.point_code,
                    event.item_code,
                    event.event_type,
                    _fmt_round(event.planted_round),
                    _fmt_round(event.expected_round),
                    _fmt_round(event.detected_round),
                    "-" if event.error_rounds is None else "{0:+d}".format(event.error_rounds),
                    event.outcome,
                    marks,
                )
            )
        )
    lines.append("BENCH_MISSES 引擎漏报 {0} 起".format(len(report.misses)))
    for event in report.misses:
        lines.append(
            "  {0} {1} {2} {3} 期望 R{4} 未检出".format(
                event.site, event.event_id, event.point_code, event.item_code, event.expected_round
            )
        )
    guards = report.guards
    lines.append("BENCH_GUARDS 导入器与修订链考题失败 {0} 项".format(len(guards)))
    for item in guards:
        lines.append(
            "  {0} {1} {2} 期望 {3} 实为 {4}".format(
                item["site"], item["event_id"], item["check"], item["expect"], item["actual"]
            )
        )
    if report.plane == PLANE_SYNTH:
        lines.append(
            "BENCH_GOLDEN {0} {1}".format(
                report.golden_state.upper(), GOLDEN_REL_PATH
            )
        )
        for problem in report.golden_problems:
            lines.append("  {0}".format(problem))
    lines.append(
        "BENCH_NOTES {0} 条：{1}".format(
            len(report.notes), "；".join(note["code"] for note in report.notes) or "无"
        )
    )
    for note in report.notes:
        lines.append("  {0}：{1}".format(note["code"], note["text"]))
    lines.append("BENCH_EXIT {0} {1}".format(report.exit_code, _exit_meaning(report.exit_code)))
    return lines


def _exit_meaning(code: int) -> str:
    return {EXIT_OK: "全部达标", EXIT_DEGRADED: "有降级项"}.get(code, "输入不可用")


def _fmt_num(value: Optional[int]) -> str:
    return "-" if value is None else "{0}".format(value)


def _fmt_round(value: Optional[int]) -> str:
    return "-" if value is None else "R{0}".format(value)
