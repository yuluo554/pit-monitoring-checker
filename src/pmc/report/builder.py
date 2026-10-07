"""`pmc report` 的报告构造：读**落库行** → 契约层 DTO → xlsx 工作表 + 追溯清单。

四条硬纪律（plan/03 §6、题面 01 §模块 4、HANDOFF-M5 §二）：

* **不重算判定**：状态、触发依据、阈值、原因码全部从 `alarm_state` / `violation` 落库行读出，
  还原成 `AlarmRecord` / `ViolationRecord` 再渲染；基准列直接取传入 `BenchReport` 的 `outcome` 用词；
* **只认链头**：过程线取 `superseded_by IS NULL` 的现行观测行（C16），与判定/检核/评测同一条读法；
* **每个数字都挂追溯**：单元格只要含 ASCII 数字（或本身就是数值），必须带来源 `(表名, 行号, 列名)`，
  缺来源当场 `ReportError` —— "来源不明数字"在构造层就写不出来，而不是靠事后检查；
  少数"派生来源"（指纹、基准逐起）在 `DERIVED_TABLES` 里逐个登记复现方法；
* **层禁令**：本层不得 import `pmc.bench`（`test_layering.py`）—— 基准对象由 CLI 传入，鸭子类型取属性。
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

from pmc.contract.records import (
    AlarmRecord,
    PointKey,
    TraceRow,
    ViolationRecord,
)
from pmc.contract.status import ALL_STATES, ObsState, TRIGGER_BASES, UNCLOSED_STATES
from pmc.contract.thresholds import (
    SOURCE_KIND_LABEL,
    SOURCE_NONE,
    STATUS_PENDING,
    DualControl,
    Threshold,
)
from pmc.errors import EXIT_DEGRADED, InputError
from pmc.report import ooxml
from pmc.report.fingerprint import ledger_fingerprint

REPORT_KINDS: Tuple[str, ...] = ("daily", "weekly", "stage")

KIND_LABELS: Dict[str, str] = {
    "daily": "监测日报",
    "weekly": "监测周报",
    "stage": "监测阶段报告",
}

#: 异常 = 已进入报警/预警生命周期；normal 与 undetermined 不进"异常清单"
ABNORMAL_STATES: Tuple[str, ...] = (
    ObsState.ALARM,
    ObsState.ALARM_CONFIRMED,
    ObsState.ALARM_HANDLED,
    ObsState.PREWARNING,
)

STATE_LABELS: Dict[str, str] = {
    ObsState.NORMAL: "正常",
    ObsState.PREWARNING: "预警",
    ObsState.ALARM: "报警",
    ObsState.ALARM_CONFIRMED: "报警已确认",
    ObsState.ALARM_HANDLED: "报警已处置",
    ObsState.UNDETERMINED: "待定值",
}

BASIS_LABELS: Dict[str, str] = {
    "cumulative": "累计量判据",
    "rate": "速率判据",
    "both": "双控同时触发",
    "none": "无",
}

DISPOSITION_LABELS: Dict[str, str] = {
    "confirm": "已确认",
    "handle": "已处置",
    "reobserve": "已复测",
}

VIOLATION_KIND_LABELS: Dict[str, str] = {
    "missed": "漏测一轮",
    "over_interval": "间隔超限",
    "stale_frequency": "频率不达标",
    "no_intensified_after_alarm": "报警后未加密",
}

#: 边界句（题面 01 §六.1）；句式不含数字，故不占追溯行
DISCLAIMER = (
    "边界声明：本报告只判读该轮次该测点相对已挂来源阈值是否超标，"
    "不判定基坑是否安全，不替代监测、设计、施工、监理的判断与处置决策。"
)
SIGN_BOUNDARY = "签字栏为空白待填：本栏空白即未审核，本报告不含任何已审核或已处置的声明。"
TRACE_NOTE = "追溯清单：本报告每个含数字的单元格对应下列若干行，指向台账真实表、真实行、真实列。"
BENCH_NOTE = "本表逐起结论与 bench 命令的 JSON 输出同源，报告不重算；行号即该 JSON 里 events 的下标。"
CHART_NOTE = "过程线按本报告涉及的测点逐点绘制，数据行与台账现行观测行逐行对应。"
NO_ABNORMAL_NOTE = "本期覆盖范围内没有异常测点（全部为正常或待定值）"

#: 每个测点一张图，最多绘制这么多张（超出只不进图，数据行仍在表里；plan/11 §五 登记）
CHART_SERIES_CAP = 6

#: 不是"一行台账"的派生来源表：复现方法在 `locator_sql` 里逐张写清
DERIVED_TABLES: Tuple[str, ...] = ("bench", "ledger_fingerprint")

#: 无整型主键的表：追溯行号 = 该表按主键排序后的行序（1 起）
ORDERED_INDEX_TABLES: Dict[str, str] = {"ruleset_applied": "code,version"}

SHEET_COVER = "报告说明"
SHEET_ABNORMAL = "异常测点清单"
SHEET_ALARM = "报警台账"
SHEET_UNCLOSED = "未闭环清单"
SHEET_AUDIT = "合规检核"
SHEET_BENCH = "基准对账"
SHEET_CHART = "过程线数据"
SHEET_TRACE = "追溯清单"
SHEET_SIGN = "签字栏"

SHEET_ORDER: Tuple[str, ...] = (
    SHEET_COVER,
    SHEET_ABNORMAL,
    SHEET_ALARM,
    SHEET_UNCLOSED,
    SHEET_AUDIT,
    SHEET_BENCH,
    SHEET_CHART,
    SHEET_SIGN,
    SHEET_TRACE,
)

SIGN_ROLES: Tuple[str, ...] = ("编制人（监测）", "审核人（监测单位）", "总监代表", "业主代表")

#: 允许出现在"无来源单元格"里的固定文案；含数字的一律不许进这张表（`assert_vocabulary` 挡下）
STATIC_TEXTS: Tuple[str, ...] = (
    DISCLAIMER,
    SIGN_BOUNDARY,
    TRACE_NOTE,
    BENCH_NOTE,
    CHART_NOTE,
    NO_ABNORMAL_NOTE,
    "监测日报",
    "监测周报",
    "监测阶段报告",
    "签字栏",
    "异常测点清单（报警、预警与已处置）",
    "报警台账（本期全部判定行，含待定值）",
    "未闭环报警清单（未闭环标记为真的判定行）",
    "合规检核（只出应核实事项，不认定违规）",
    "内置基准逐起对账（结论用词与基准评测同源）",
    "过程线数据（测点、轮次与累计值加阈值）",
    "追溯清单",
    "字段",
    "值",
    "说明",
    "角色",
    "签字",
    "日期",
    "本栏空白即未审核，程序不填写",
    "报告生成时间由使用方填写，不写进文件元数据",
    "签字时核对本指纹，指纹不一致则不是同一数据版本",
    "与报告说明页一致",
    "报告形态由 --kind 指定",
    "逐轮明细见下方各行",
    "台账未登记规则集指纹",
    "台账内无处置记录",
    "未登记",
    "工程编码",
    "工程名称",
    "施工单位",
    "监测单位",
    "监理单位",
    "方案图号",
    "报告形态",
    "覆盖轮次起",
    "覆盖轮次止",
    "覆盖轮次数",
    "台账内容指纹",
    "报告生成时间",
    "规则集版本",
    "报告对应台账指纹",
    "报告单元格",
    "台账表",
    "行号",
    "列名",
    "回到台账的查法",
    "指纹只算台账内容，不含导入时间",
)

ASCII_DIGITS = "0123456789"

_SERIES_COLORS = ("1F4E79", "2E7D32", "C62828", "6A1B9A", "EF6C00", "00695C")

#: 判定行的列序（异常清单与报警台账共用同一套，处置记录只在异常清单追加）
_LEDGER_HEADERS: Tuple[str, ...] = (
    "工程",
    "测点",
    "监测项目",
    "轮次",
    "状态",
    "状态码",
    "触发依据",
    "累计实测值",
    "累计阈值",
    "速率实测值",
    "速率阈值",
    "速率窗口天",
    "阈值来源",
    "条款号",
    "不启用原因码",
    "未闭环",
)

_BENCH_HEADERS = (
    "事件号",
    "工程",
    "测点",
    "监测项目",
    "事件类型",
    "植入轮次",
    "期望报警轮次",
    "实测报警轮次",
    "轮次误差",
    "结论",
    "对账项",
    "期望",
    "实际",
    "结果",
)

_BENCH_FAIL_WORDS = ("漏报", "晚报", "早报", "考题失败", "不可判")

_AUDIT_HEADERS = ("类别", "类别码", "测点", "轮次", "规则", "条款号", "结论", "证据")


class ReportError(Exception):
    """报告构造不自洽：宁可不出文件。"""


Src = Tuple[str, int, str]


def has_ascii_digit(text: object) -> bool:
    return any(ch in ASCII_DIGITS for ch in str(text))


def locator_sql(table: str, row_id: int, column: str) -> str:
    """追溯清单的"回到台账的查法"列：行号翻译回可执行语句或复现说明。"""
    if table == "bench":
        return "python -m pmc bench --json → events[{0}].{1}".format(row_id, column)
    if table == "ledger_fingerprint":
        return "重跑同一条 pmc report 命令；算法见 pmc/report/fingerprint.py"
    if table in ORDERED_INDEX_TABLES:
        return "SELECT {1} FROM {0} ORDER BY {2} LIMIT 1 OFFSET {3}".format(
            table, column, ORDERED_INDEX_TABLES[table], row_id - 1
        )
    return "SELECT {1} FROM {0} WHERE id={2}".format(table, column, row_id)


# --------------------------------------------------------------------------- 写入器


class _Builder(object):
    """工作表写入与追溯登记在同一次调用里完成，杜绝"写了数字忘了挂来源"。"""

    def __init__(self) -> None:
        self.doc = ooxml.XlsxDoc("基坑监测数据判读报告")
        self.traces: List[TraceRow] = []
        self._sheets: Dict[str, ooxml.Sheet] = {}

    def sheet(self, name: str, widths: Optional[Sequence[float]] = None) -> ooxml.Sheet:
        if name in self._sheets:
            return self._sheets[name]
        sheet = self.doc.add_sheet(name)
        if widths:
            sheet.set_widths(widths)
        self._sheets[name] = sheet
        return sheet

    def banner(self, sheet: ooxml.Sheet, text: str, style: str = "subtitle") -> int:
        """标题/说明行：必须是登记过的固定文案，且不含数字。"""
        if text not in STATIC_TEXTS:
            raise ReportError("非固定文案走了 banner：{0}".format(text))
        if has_ascii_digit(text):
            raise ReportError("固定文案含数字，需改为带来源的数据行：{0}".format(text))
        return sheet.add_row([ooxml.C(text, style)])

    def row(self, sheet: ooxml.Sheet, specs: Sequence[Tuple[object, str, object]]) -> int:
        cells: List[ooxml.Cell] = []
        position = sheet.last_row() + 1
        for col, spec in enumerate(specs):
            value, style, src = spec
            cells.append(ooxml.C(value, style))
            self._register(sheet, position, col, value, src)
        return sheet.add_row(cells)

    def _register(self, sheet, position: int, col: int, value, src) -> None:
        if isinstance(value, bool):
            raise ReportError("布尔值不得作为报告单元格：{0}".format(sheet.name))
        address = "{0}!{1}".format(sheet.name, ooxml.cell_ref(col, position))
        numeric = isinstance(value, (int, float))
        if not numeric and not isinstance(value, str):
            raise ReportError("单元格只收数值与文本：{0}".format(address))
        if (numeric or (isinstance(value, str) and has_ascii_digit(value))) and not src:
            raise ReportError("单元格缺台账来源：{0} = {1}".format(address, value))
        for item in _as_list(src):
            trace = TraceRow(
                report_cell=address,
                table_name=str(item[0]),
                row_id=int(item[1]),
                column_name=str(item[2]),
            )
            trace.validate()
            self.traces.append(trace)

    def header(self, sheet: ooxml.Sheet, labels: Sequence[str], freeze: bool = True) -> None:
        for label in labels:
            if has_ascii_digit(label):
                raise ReportError("表头含数字：{0}".format(label))
        sheet.add_texts(labels, "header")
        if freeze:
            sheet.freeze = (0, len(sheet.rows))


def _as_list(src) -> List[Src]:
    if not src:
        return []
    if isinstance(src, tuple):
        return [src]
    return list(src)


def _or_dash(value):
    return "-" if value is None else value


# --------------------------------------------------------------------------- 读台账


def _require_project(conn, code: str) -> Dict[str, object]:
    row = conn.execute(
        "SELECT id, code, name, builder_unit, monitor_unit, supervisor_unit,"
        " scheme_no, start_date, end_date FROM project WHERE code = ?",
        (code,),
    ).fetchone()
    if row is None:
        raise InputError(
            "工程 {0} 不在台账里：先 pmc init --db … 建档，再 pmc synth --db … 或 pmc import".format(code)
        )
    keys = (
        "id",
        "code",
        "name",
        "builder_unit",
        "monitor_unit",
        "supervisor_unit",
        "scheme_no",
        "start_date",
        "end_date",
    )
    return dict(zip(keys, row))


def _all_rounds(conn, project_id: int) -> List[Dict[str, object]]:
    rows = conn.execute(
        "SELECT id, round_index, observed_on, is_intensified FROM obs_round"
        " WHERE project_id = ? ORDER BY round_index",
        (project_id,),
    ).fetchall()
    return [
        {"id": row[0], "round_index": row[1], "observed_on": row[2], "is_intensified": row[3]}
        for row in rows
    ]


_JUDGE_SQL = (
    "SELECT a.id, r.round_index, r.observed_on, p.code, a.item_code, a.state,"
    " a.trigger_basis, a.cum_value, a.cum_threshold, a.rate_value, a.rate_threshold,"
    " a.window_days, a.threshold_source_kind, a.threshold_status, a.clause_ids,"
    " a.disabled_reasons, a.unclosed, a.first_alarm_round_id, a.observation_id,"
    " fr.round_index, p.design_cum_unit, p.design_rate_unit, p.threshold_evidence, p.id"
    " FROM alarm_state a"
    " JOIN point p ON p.id = a.point_id"
    " JOIN obs_round r ON r.id = a.round_id"
    " LEFT JOIN obs_round fr ON fr.id = a.first_alarm_round_id"
    " WHERE a.project_id = ?{0}"
    " ORDER BY r.round_index, p.code, a.item_code, a.id"
)


def _judgments(conn, project_id: int, project_code: str, low: int, high: int) -> List[Dict[str, object]]:
    rows = conn.execute(
        _JUDGE_SQL.format(" AND r.round_index >= ? AND r.round_index <= ?"),
        [project_id, low, high],
    ).fetchall()
    out: List[Dict[str, object]] = []
    for row in rows:
        kind = row[12] or SOURCE_NONE
        status = row[13] or STATUS_PENDING
        record = AlarmRecord(
            key=PointKey(
                project_code=project_code,
                point_code=row[3],
                round_index=row[1],
                item_code=row[4],
            ),
            state=row[5],
            trigger_basis=row[6] or "none",
            dual=DualControl(
                cumulative=Threshold(
                    kind=kind, status=status, value=row[8], unit=row[20], evidence=row[22]
                ),
                rate=Threshold(
                    kind=kind, status=status, value=row[10], unit=row[21], evidence=row[22]
                ),
            ),
            clause_ids=_split(row[14]),
            unclosed_carried=bool(row[16]),
            first_alarm_round_index=row[19],
            disabled_reasons=_split(row[15], ";"),
        )
        out.append(
            {
                "alarm_id": int(row[0]),
                "point_id": int(row[23]),
                "round_index": row[1],
                "observed_on": row[2],
                "record": record,
                "cum_value": row[7],
                "rate_value": row[9],
                "window_days": row[11],
                "unclosed": bool(row[16]),
                "observation_id": row[18],
            }
        )
    return out


def _split(text, sep: str = ",") -> List[str]:
    if not text:
        return []
    return [part for part in str(text).split(sep) if part]


def _violations(conn, project_id: int, project_code: str, low: int, high: int) -> List[Dict[str, object]]:
    rows = conn.execute(
        "SELECT v.id, v.kind, v.rule_id, v.clause_ids, v.evidence_json, p.code, r.round_index"
        " FROM violation v"
        " LEFT JOIN point p ON p.id = v.point_id"
        " LEFT JOIN obs_round r ON r.id = v.round_id"
        " WHERE v.project_id = ?"
        " AND (r.round_index IS NULL OR (r.round_index >= ? AND r.round_index <= ?))"
        " ORDER BY v.id",
        (project_id, low, high),
    ).fetchall()
    out = []
    for row in rows:
        try:
            evidence = json.loads(row[4]) if row[4] else {}
        except ValueError:
            raise InputError("violation {0} 的 evidence_json 不是合法 JSON".format(row[0]))
        record = ViolationRecord(
            key=None
            if row[5] is None
            else PointKey(
                project_code=project_code,
                point_code=row[5],
                round_index=row[6] or 1,
                item_code="unknown",
            ),
            kind=row[1],
            rule_id=row[2],
            clause_ids=_split(row[3]),
            evidence=evidence,
        )
        out.append(
            {
                "violation_id": int(row[0]),
                "record": record,
                "point_code": row[5],
                "round_index": row[6],
            }
        )
    return out


def _dispositions(conn, alarm_ids: Sequence[int]) -> Dict[int, List[Tuple[int, str]]]:
    if not alarm_ids:
        return {}
    marks = ",".join("?" for _ in alarm_ids)
    rows = conn.execute(
        "SELECT id, alarm_id, kind FROM disposition WHERE alarm_id IN ({0}) ORDER BY id".format(marks),
        list(alarm_ids),
    ).fetchall()
    out: Dict[int, List[Tuple[int, str]]] = {}
    for row in rows:
        out.setdefault(int(row[1]), []).append((int(row[0]), DISPOSITION_LABELS.get(row[2], row[2])))
    return out


def _observations(
    conn, project_id: int, point_codes: Sequence[str], high: int
) -> Dict[str, List[Dict[str, object]]]:
    """现行观测行（链头），按测点分组供过程线用（C16）。"""
    if not point_codes:
        return {}
    marks = ",".join("?" for _ in point_codes)
    rows = conn.execute(
        "SELECT o.id, p.code, r.round_index, o.value_cum, o.unit, o.missing"
        " FROM observation o"
        " JOIN point p ON p.id = o.point_id"
        " JOIN obs_round r ON r.id = o.round_id"
        " WHERE p.project_id = ? AND o.superseded_by IS NULL AND r.round_index <= ?"
        " AND p.code IN ({0}) ORDER BY p.code, r.round_index, o.revision_seq".format(marks),
        [project_id, high] + list(point_codes),
    ).fetchall()
    out: Dict[str, List[Dict[str, object]]] = {}
    for row in rows:
        out.setdefault(row[1], []).append(
            {
                "observation_id": int(row[0]),
                "round_index": int(row[2]),
                "value_cum": row[3],
                "unit": row[4],
                "missing": bool(row[5]),
            }
        )
    return out


# --------------------------------------------------------------------------- 入口


class ReportResult(object):
    def __init__(
        self,
        kind: str,
        project_code: str,
        rounds: Sequence[int],
        path: str,
        sha256: str,
        sheet_names: Sequence[str],
        traces: Sequence[TraceRow],
        counters: Dict[str, int],
        exit_code: int,
    ) -> None:
        self.kind = kind
        self.project_code = project_code
        self.rounds = list(rounds)
        self.path = path
        self.sha256 = sha256
        self.sheet_names = list(sheet_names)
        self.traces = list(traces)
        self.counters = dict(counters)
        self.exit_code = exit_code


def scope_label(rounds: Sequence[int]) -> str:
    if not rounds:
        raise ReportError("空轮次范围不出文件名")
    if len(rounds) == 1:
        return "R{0:02d}".format(rounds[0])
    return "R{0:02d}-R{1:02d}".format(rounds[0], rounds[-1])


def report_filename(kind: str, project_code: str, rounds: Sequence[int]) -> str:
    return "pmc-{0}-{1}-{2}.xlsx".format(kind, project_code, scope_label(rounds))


def resolve_weekly_span(indexes: Sequence[int], dates: Dict[int, str]) -> Tuple[int, int]:
    """周报缺省窗口：台账内最晚观测日期往前 7 天（含端点）命中的轮次。"""
    from datetime import date, timedelta

    if not indexes:
        raise InputError("台账里没有轮次档案，无法确定周报窗口")

    def parse(text: str) -> date:
        return date(*(int(part) for part in text.split("-")))

    floor = parse(dates[max(indexes)]) - timedelta(days=6)
    keep = [index for index in indexes if parse(dates[index]) >= floor]
    if not keep:
        raise InputError("按观测日期算不出周报窗口：台账日期列不可解析")
    return min(keep), max(keep)


def build_report(
    conn,
    project_code: str,
    kind: str,
    *,
    round_index: Optional[int] = None,
    round_from: Optional[int] = None,
    round_to: Optional[int] = None,
    out_dir: str = os.path.join("reports", "out"),
    bench_report=None,
    dry_run: bool = False,
) -> ReportResult:
    """产出一份 xlsx 报告；`bench_report` 是 CLI 传进来的基准报告对象（本层不 import 该模块）。"""
    if kind not in REPORT_KINDS:
        raise InputError("报告形态 {0} 不存在：只支持 {1}".format(kind, "/".join(REPORT_KINDS)))
    project = _require_project(conn, project_code)
    project_id = int(project["id"])
    rounds = _all_rounds(conn, project_id)
    if not rounds:
        raise InputError("工程 {0} 没有任何轮次档案：先 pmc synth --db 或 pmc import".format(project_code))
    indexes = [int(item["round_index"]) for item in rounds]
    dates = {int(item["round_index"]): str(item["observed_on"]) for item in rounds}

    if kind == "daily":
        target = round_index if round_index is not None else max(indexes)
        if target not in indexes:
            raise InputError("第 {0} 轮没有轮次档案（工程 {1}）".format(target, project_code))
        low = high = target
    else:
        if round_index is not None:
            raise InputError("--round 只与 --kind daily 搭配；周报与阶段报告用 --from/--to")
        low = round_from if round_from is not None else min(indexes)
        high = round_to if round_to is not None else max(indexes)
        if round_from is None and round_to is None and kind == "weekly":
            low, high = resolve_weekly_span(indexes, dates)
    if low > high:
        raise InputError("轮次区间反向：--from {0} 大于 --to {1}".format(low, high))
    scoped = [item for item in rounds if low <= int(item["round_index"]) <= high]
    scope = [int(item["round_index"]) for item in scoped]

    judgments = _judgments(conn, project_id, project_code, low, high)
    if not judgments:
        raise InputError(
            "工程 {0} 第 {1}–{2} 轮没有任何判定行：先 pmc check --db … --project {0} --rules-dir …".format(
                project_code, low, high
            )
        )
    for entry in judgments:
        entry["record"].validate()
    violations = _violations(conn, project_id, project_code, low, high)
    dispositions = _dispositions(conn, [int(item["alarm_id"]) for item in judgments])
    fingerprint = ledger_fingerprint(conn, project_code)
    rulesets = conn.execute(
        "SELECT code, version, sha256 FROM ruleset_applied ORDER BY code, version"
    ).fetchall()

    builder = _Builder()
    _cover_sheet(builder, project, kind, scoped, fingerprint, rulesets)
    _abnormal_sheet(builder, judgments, dispositions)
    _alarm_sheet(builder, judgments)
    _unclosed_sheet(builder, judgments)
    _audit_sheet(builder, violations)
    if bench_report is not None:
        _bench_sheet(builder, bench_report)
    chart_stats = _chart_sheet(builder, judgments, dates, conn, project_id, high)
    _sign_sheet(builder, kind, scope, fingerprint)
    _trace_sheet(builder)

    name = report_filename(kind, project_code, scope)
    path = os.path.join(out_dir, name)
    blob = builder.doc.to_bytes()
    if not dry_run:
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(blob)
    counters = _count(judgments, violations, builder, scope)
    counters.update(chart_stats)
    return ReportResult(
        kind=kind,
        project_code=project_code,
        rounds=scope,
        path=path,
        sha256=hashlib.sha256(blob).hexdigest(),
        sheet_names=[sheet.name for sheet in builder.doc.sheets],
        traces=builder.traces,
        counters=counters,
        exit_code=report_exit_code(counters, bench_report),
    )


def _count(judgments, violations, builder, scope: Sequence[int]) -> Dict[str, int]:
    counters = {
        "judged": len(judgments),
        "abnormal": 0,
        "unclosed": 0,
        "undetermined": 0,
        "violations": len(violations),
        "traces": len(builder.traces),
        "rounds": len(scope),
    }
    for entry in judgments:
        record = entry["record"]
        if record.state in ABNORMAL_STATES:
            counters["abnormal"] += 1
        if entry["unclosed"]:
            counters["unclosed"] += 1
        if record.state == ObsState.UNDETERMINED:
            counters["undetermined"] += 1
    return counters


# --------------------------------------------------------------------------- 各工作表


def _cover_sheet(builder, project, kind, scoped, fingerprint, rulesets) -> None:
    sheet = builder.sheet(SHEET_COVER, widths=[26, 68, 44])
    builder.banner(sheet, KIND_LABELS[kind], "title")
    builder.banner(sheet, DISCLAIMER, "note")
    builder.row(sheet, [("字段", "header", None), ("值", "header", None), ("说明", "header", None)])
    identity = (
        ("工程编码", project["code"], "code"),
        ("工程名称", project["name"], "name"),
        ("施工单位", project["builder_unit"] or "未登记", "builder_unit"),
        ("监测单位", project["monitor_unit"] or "未登记", "monitor_unit"),
        ("监理单位", project["supervisor_unit"] or "未登记", "supervisor_unit"),
        ("方案图号", project["scheme_no"] or "未登记", "scheme_no"),
    )
    for label, value, column in identity:
        builder.row(
            sheet,
            [
                (label, "default", None),
                (value, "wrap", ("project", project["id"], column)),
                ("project 表列 {0}".format(column), "note", ("project", project["id"], column)),
            ],
        )
    builder.row(
        sheet,
        [
            ("报告形态", "default", None),
            (KIND_LABELS[kind], "wrap", None),
            ("报告形态由 --kind 指定", "note", None),
        ],
    )
    first, last = scoped[0], scoped[-1]
    builder.row(
        sheet,
        [
            ("覆盖轮次起", "default", None),
            (int(first["round_index"]), "num0", ("obs_round", first["id"], "round_index")),
            ("obs_round.round_index", "note", ("obs_round", first["id"], "round_index")),
        ],
    )
    builder.row(
        sheet,
        [
            ("覆盖轮次止", "default", None),
            (int(last["round_index"]), "num0", ("obs_round", last["id"], "round_index")),
            ("obs_round.round_index", "note", ("obs_round", last["id"], "round_index")),
        ],
    )
    builder.row(
        sheet,
        [
            ("覆盖轮次数", "default", None),
            (len(scoped), "num0", [("obs_round", item["id"], "round_index") for item in scoped]),
            ("逐轮明细见下方各行", "note", None),
        ],
    )
    for item in scoped:
        builder.row(
            sheet,
            [
                (
                    "轮次 {0} 观测日期".format(item["round_index"]),
                    "default",
                    ("obs_round", item["id"], "round_index"),
                ),
                (item["observed_on"], "default", ("obs_round", item["id"], "observed_on")),
                (
                    "本轮观测日期（加密轮次）" if item["is_intensified"] else "本轮观测日期",
                    "note",
                    ("obs_round", item["id"], "is_intensified"),
                ),
            ],
        )
    builder.row(
        sheet,
        [
            ("台账内容指纹", "default", None),
            (fingerprint.sha256, "wrap", ("ledger_fingerprint", 1, "sha256")),
            ("指纹只算台账内容，不含导入时间", "note", None),
        ],
    )
    builder.row(
        sheet,
        [
            ("报告生成时间", "default", None),
            ("　", "wrap", None),
            ("报告生成时间由使用方填写，不写进文件元数据", "note", None),
        ],
    )
    for position, row in enumerate(rulesets):
        builder.row(
            sheet,
            [
                ("规则集版本", "default", None),
                (int(row[1]), "num0", ("ruleset_applied", position + 1, "version")),
                (row[2] or "-", "wrap", ("ruleset_applied", position + 1, "sha256")),
            ],
        )
    if not rulesets:
        builder.row(
            sheet,
            [
                ("规则集版本", "default", None),
                ("未登记", "default", None),
                ("台账未登记规则集指纹", "note", None),
            ],
        )


def _state_cells(entry) -> List[Tuple[object, str, object]]:
    record = entry["record"]
    alarm_id = int(entry["alarm_id"])
    dual = record.dual
    style = "flag" if record.state in UNCLOSED_STATES else "center"
    return [
        (record.key.project_code, "default", ("alarm_state", alarm_id, "project_id")),
        (record.key.point_code, "default", ("alarm_state", alarm_id, "point_id")),
        (record.key.item_code, "default", ("alarm_state", alarm_id, "item_code")),
        (record.key.round_index, "num0", ("alarm_state", alarm_id, "round_id")),
        (STATE_LABELS.get(record.state, record.state), style, ("alarm_state", alarm_id, "state")),
        (record.state, "default", ("alarm_state", alarm_id, "state")),
        (BASIS_LABELS.get(record.trigger_basis, record.trigger_basis), "default",
         ("alarm_state", alarm_id, "trigger_basis")),
        (_or_dash(entry["cum_value"]), "num3" if entry["cum_value"] is not None else "center",
         ("alarm_state", alarm_id, "cum_value")),
        (_or_dash(dual.cumulative.value), "num3" if dual.cumulative.value is not None else "center",
         ("alarm_state", alarm_id, "cum_threshold")),
        (_or_dash(entry["rate_value"]), "num4" if entry["rate_value"] is not None else "center",
         ("alarm_state", alarm_id, "rate_value")),
        (_or_dash(dual.rate.value), "num4" if dual.rate.value is not None else "center",
         ("alarm_state", alarm_id, "rate_threshold")),
        (_or_dash(entry["window_days"]), "num3" if entry["window_days"] is not None else "center",
         ("alarm_state", alarm_id, "window_days")),
        (SOURCE_KIND_LABEL.get(dual.cumulative.kind, dual.cumulative.kind), "default",
         ("alarm_state", alarm_id, "threshold_source_kind")),
        ("，".join(record.clause_ids) or "-", "wrap", ("alarm_state", alarm_id, "clause_ids")),
        ("；".join(record.disabled_reasons) or "-", "wrap", ("alarm_state", alarm_id, "disabled_reasons")),
        ("是" if entry["unclosed"] else "否", "center", ("alarm_state", alarm_id, "unclosed")),
    ]


_WIDTHS_JUDGE = [14, 18, 16, 8, 14, 16, 16, 14, 14, 14, 14, 14, 16, 26, 30, 10]


def _abnormal_sheet(builder, judgments, dispositions) -> None:
    sheet = builder.sheet(SHEET_ABNORMAL, widths=_WIDTHS_JUDGE + [18])
    builder.banner(sheet, "异常测点清单（报警、预警与已处置）")
    builder.header(sheet, _LEDGER_HEADERS + ("处置记录",))
    for entry in judgments:
        record = entry["record"]
        if record.state not in ABNORMAL_STATES:
            continue
        specs = _state_cells(entry)
        marks = dispositions.get(int(entry["alarm_id"]), [])
        specs.append(
            (
                "，".join(label for _, label in marks) if marks else "台账内无处置记录",
                "default",
                [("disposition", row_id, "kind") for row_id, _ in marks] if marks else None,
            )
        )
        builder.row(sheet, specs)
    if not [item for item in judgments if item["record"].state in ABNORMAL_STATES]:
        builder.banner(sheet, NO_ABNORMAL_NOTE, "note")


def _alarm_sheet(builder, judgments) -> None:
    sheet = builder.sheet(SHEET_ALARM, widths=_WIDTHS_JUDGE)
    builder.banner(sheet, "报警台账（本期全部判定行，含待定值）")
    builder.header(sheet, _LEDGER_HEADERS)
    for entry in judgments:
        builder.row(sheet, _state_cells(entry))


def _unclosed_sheet(builder, judgments) -> None:
    sheet = builder.sheet(SHEET_UNCLOSED, widths=[18, 16, 12, 16, 16, 18, 12])
    builder.banner(sheet, "未闭环报警清单（未闭环标记为真的判定行）")
    builder.header(
        sheet,
        ("测点", "监测项目", "最新轮次", "最新状态", "首个报警轮次", "本轮是否仍超标", "台账行"),
    )
    for entry in judgments:
        record = entry["record"]
        if not entry["unclosed"]:
            continue
        first = record.first_alarm_round_index
        still = record.state in UNCLOSED_STATES
        builder.row(
            sheet,
            [
                (record.key.point_code, "default", ("alarm_state", entry["alarm_id"], "point_id")),
                (record.key.item_code, "default", ("alarm_state", entry["alarm_id"], "item_code")),
                (record.key.round_index, "num0", ("alarm_state", entry["alarm_id"], "round_id")),
                (STATE_LABELS.get(record.state, record.state), "flag",
                 ("alarm_state", entry["alarm_id"], "state")),
                (_or_dash(first), "num0" if first is not None else "center",
                 ("alarm_state", entry["alarm_id"], "first_alarm_round_id") if first is not None else None),
                ("是" if still else "否（本轮已回落，仍未处置）", "center",
                 ("alarm_state", entry["alarm_id"], "state")),
                (int(entry["alarm_id"]), "num0", ("alarm_state", entry["alarm_id"], "id")),
            ],
        )


def _audit_sheet(builder, violations) -> None:
    sheet = builder.sheet(SHEET_AUDIT, widths=[16, 26, 18, 10, 28, 26, 12, 46])
    builder.banner(sheet, "合规检核（只出应核实事项，不认定违规）")
    builder.header(sheet, _AUDIT_HEADERS)
    for item in violations:
        record = item["record"]
        evidence = record.evidence
        detail = evidence.get("detail") if isinstance(evidence, dict) else None
        builder.row(
            sheet,
            [
                (VIOLATION_KIND_LABELS.get(record.kind, record.kind), "default",
                 ("violation", item["violation_id"], "kind")),
                (record.kind, "default", ("violation", item["violation_id"], "kind")),
                (record.key.point_code if record.key else "-", "default",
                 ("violation", item["violation_id"], "point_id") if record.key else None),
                (_or_dash(item["round_index"]), "num0" if item["round_index"] is not None else "center",
                 ("violation", item["violation_id"], "round_id") if item["round_index"] is not None else None),
                (record.rule_id, "default", ("violation", item["violation_id"], "rule_id")),
                ("，".join(record.clause_ids) or "-", "wrap", ("violation", item["violation_id"], "clause_ids")),
                ("应核实", "center", ("violation", item["violation_id"], "id")),
                (str(detail if detail is not None else _jsonish(evidence)), "wrap",
                 ("violation", item["violation_id"], "evidence_json")),
            ],
        )


def _jsonish(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _bench_sheet(builder, bench_report) -> None:
    sheet = builder.sheet(SHEET_BENCH, widths=[12, 14, 18, 16, 18, 12, 14, 14, 12, 16, 18, 22, 22, 10])
    builder.banner(sheet, "内置基准逐起对账（结论用词与基准评测同源）")
    builder.banner(sheet, BENCH_NOTE, "note")
    builder.header(sheet, _BENCH_HEADERS)
    for position, event in enumerate(bench_report.events, start=1):
        checks = list(event.checks) or [None]
        for check in checks:
            def src(column: str) -> Src:
                return ("bench", position, column)

            specs = [
                (event.event_id, "default", src("event_id")),
                (event.site, "default", src("site")),
                (event.point_code, "default", src("point_code")),
                (event.item_code, "default", src("item_code")),
                (event.event_type, "default", src("event_type")),
                (_or_dash(event.planted_round),
                 "num0" if event.planted_round is not None else "center", src("planted_round")),
                (_or_dash(event.expected_round),
                 "num0" if event.expected_round is not None else "center", src("expected_round")),
                (_or_dash(event.detected_round),
                 "num0" if event.detected_round is not None else "center", src("detected_round")),
                (_or_dash(event.error_rounds),
                 "num0" if event.error_rounds is not None else "center", src("error_rounds")),
                (event.outcome,
                 "flag" if event.outcome in _BENCH_FAIL_WORDS else "center", src("outcome")),
            ]
            if check is None:
                specs.extend([("-", "center", None)] * 4)
            else:
                specs.extend(
                    [
                        (check.name, "default", src("checks.name")),
                        (check.expect, "wrap", src("checks.expect")),
                        (check.actual, "wrap", src("checks.actual")),
                        ("-" if check.ok is None else ("通过" if check.ok else "不通过"), "center",
                         src("checks.ok")),
                    ]
                )
            builder.row(sheet, specs)


def _chart_sheet(builder, judgments, dates, conn, project_id: int, high: int) -> Dict[str, int]:
    """过程线数据 + 每测点一张原生折线图（含阈值虚线）。"""
    pairs: List[Tuple[str, str]] = []
    threshold_of: Dict[Tuple[str, str], Optional[float]] = {}
    point_of: Dict[str, int] = {}
    seen = set()
    for entry in judgments:
        record = entry["record"]
        if record.state not in ABNORMAL_STATES:
            continue
        pair = (record.key.point_code, record.key.item_code)
        point_of[record.key.point_code] = int(entry["point_id"])
        if pair in seen:
            continue
        seen.add(pair)
        pairs.append(pair)
        dual = record.dual
        threshold_of[pair] = dual.cumulative.value if dual.cumulative.usable else None

    sheet = builder.sheet(SHEET_CHART, widths=[10, 16, 18, 16, 16, 10, 16, 14])
    builder.banner(sheet, "过程线数据（测点、轮次与累计值加阈值）")
    builder.banner(sheet, CHART_NOTE, "note")
    builder.header(sheet, ("轮次", "观测日期", "测点", "监测项目", "累计实测值", "单位", "累计阈值", "台账行"))
    if not pairs:
        # 没有异常测点时按测点编码顺序取前若干条，保证"可编辑原生图表"始终存在
        rows = conn.execute(
            "SELECT p.code, a.item_code, p.id FROM alarm_state a JOIN point p ON p.id = a.point_id"
            " WHERE a.project_id = ? GROUP BY p.code, a.item_code ORDER BY p.code, a.item_code",
            (project_id,),
        ).fetchall()
        for row in rows[:CHART_SERIES_CAP]:
            pair = (row[0], row[1])
            pairs.append(pair)
            point_of[row[0]] = int(row[2])
            threshold_of[pair] = None
    plotted = pairs[:CHART_SERIES_CAP]
    observations = _observations(conn, project_id, [code for code, _ in plotted], high)
    rid_of = {row[0]: row[1] for row in conn.execute(
        "SELECT round_index, id FROM obs_round WHERE project_id = ?", (project_id,)
    ).fetchall()}

    blocks = []
    for pair in plotted:
        code, item_code = pair
        archive = threshold_of.get(pair)
        if archive is None:
            row = conn.execute(
                "SELECT design_cum_value FROM point WHERE id = ?", (point_of.get(code, -1),)
            ).fetchone()
            archive = row[0] if row else None
            source: Src = ("point", point_of.get(code, 0), "design_cum_value")
        else:
            source = ("point", point_of.get(code, 0), "design_cum_value")
        start = sheet.last_row() + 1
        values: List[Optional[float]] = []
        cats: List[str] = []
        threshold_line: List[Optional[float]] = []
        for entry in observations.get(code, []):
            if entry["missing"] or entry["value_cum"] is None:
                continue
            observation_id = int(entry["observation_id"])
            round_index = int(entry["round_index"])
            builder.row(
                sheet,
                [
                    (round_index, "num0", ("obs_round", rid_of[round_index], "round_index")),
                    (dates.get(round_index, "-"), "default", ("obs_round", rid_of[round_index], "observed_on")),
                    (code, "default", ("observation", observation_id, "point_id")),
                    (item_code, "default", ("observation", observation_id, "id")),
                    (float(entry["value_cum"]), "num3", ("observation", observation_id, "value_cum")),
                    (entry["unit"] or "-", "center", ("observation", observation_id, "unit")),
                    (_or_dash(archive), "num3" if archive is not None else "center", source),
                    (observation_id, "num0", ("observation", observation_id, "id")),
                ],
            )
            values.append(float(entry["value_cum"]))
            cats.append("R{0:02d}".format(round_index))
            threshold_line.append(None if archive is None else float(archive))
        blocks.append((pair, start, sheet.last_row(), values, cats, threshold_line))

    charts = 0
    for position, (pair, start, end, values, cats, threshold_line) in enumerate(blocks):
        if not values or start > end:
            continue
        series = [
            ooxml.ChartSeries(
                name="{0} 累计值".format(pair[0]),
                values_ref=ooxml.absolute_range(SHEET_CHART, 4, start, 4, end),
                cache=values,
                color=_SERIES_COLORS[position % len(_SERIES_COLORS)],
            )
        ]
        if threshold_line and threshold_line[0] is not None:
            series.append(
                ooxml.ChartSeries(
                    name="{0} 累计控制值".format(pair[0]),
                    values_ref=ooxml.absolute_range(SHEET_CHART, 6, start, 6, end),
                    cache=threshold_line,
                    color="C62828",
                    dashed=True,
                )
            )
        builder.doc.attach(
            SHEET_CHART,
            ooxml.Chart(
                title="{0} 过程线".format(pair[0]),
                axis_title="累计值",
                categories_ref=ooxml.absolute_range(SHEET_CHART, 0, start, 0, end),
                categories_cache=cats,
                series=series,
                anchor_from=(10, 2 + position * 16),
                anchor_to=(20, 15 + position * 16),
            ),
        )
        charts += 1
    return {"chart_count": charts, "chart_points": len(plotted), "abnormal_pairs": len(pairs)}


def _sign_sheet(builder, kind, scope, fingerprint) -> None:
    sheet = builder.sheet(SHEET_SIGN, widths=[26, 30, 22, 46])
    builder.banner(sheet, "签字栏", "title")
    builder.banner(sheet, SIGN_BOUNDARY, "note")
    builder.row(
        sheet,
        [("角色", "header", None), ("签字", "header", None), ("日期", "header", None), ("说明", "header", None)],
    )
    for role in SIGN_ROLES:
        builder.row(
            sheet,
            [
                (role, "default", None),
                ("", "wrap", None),
                ("", "wrap", None),
                ("本栏空白即未审核，程序不填写", "note", None),
            ],
        )
    builder.row(
        sheet,
        [
            ("报告对应台账指纹", "default", None),
            (fingerprint.sha256, "wrap", ("ledger_fingerprint", 1, "sha256")),
            ("签字时核对本指纹，指纹不一致则不是同一数据版本", "note", None),
        ],
    )
    builder.row(
        sheet,
        [
            ("报告形态", "default", None),
            (KIND_LABELS[kind], "wrap", None),
            ("报告形态由 --kind 指定", "note", None),
        ],
    )


def _trace_sheet(builder) -> None:
    sheet = builder.sheet(SHEET_TRACE, widths=[30, 22, 10, 26, 74])
    builder.banner(sheet, "追溯清单")
    builder.banner(sheet, TRACE_NOTE, "note")
    builder.header(sheet, ("报告单元格", "台账表", "行号", "列名", "回到台账的查法"))
    for trace in builder.traces:
        sheet.add_row(
            [
                ooxml.C(trace.report_cell),
                ooxml.C(trace.table_name),
                ooxml.C(int(trace.row_id), "num0"),
                ooxml.C(trace.column_name),
                ooxml.C(locator_sql(trace.table_name, trace.row_id, trace.column_name), "wrap"),
            ]
        )


def report_exit_code(counters: Dict[str, int], bench_report=None) -> int:
    """降级口径（并入 C4）：未闭环 / 待定值 / 应核实 / 基准未达标 任一即降级。"""
    if counters.get("unclosed") or counters.get("undetermined") or counters.get("violations"):
        return EXIT_DEGRADED
    if bench_report is not None and getattr(bench_report, "exit_code", 0):
        return EXIT_DEGRADED
    return 0


def state_label(state: str) -> str:
    return STATE_LABELS.get(state, state)


def assert_vocabulary() -> None:
    """用词与契约枚举同源（C22 / C2d）：多一个少一个都在这里挡下。"""
    if set(STATE_LABELS) != set(ALL_STATES):
        raise ReportError("状态用词与 contract.status 不一致")
    if set(BASIS_LABELS) != set(TRIGGER_BASES):
        raise ReportError("触发依据用词与 contract.status 不一致")
    if set(DISPOSITION_LABELS) != {"confirm", "handle", "reobserve"}:
        raise ReportError("处置用词与 DDL 的 disposition.kind 不一致")
    if set(VIOLATION_KIND_LABELS) != {
        "missed",
        "over_interval",
        "stale_frequency",
        "no_intensified_after_alarm",
    }:
        raise ReportError("检核类别用词与 DDL 的 violation.kind 不一致")
    for text in STATIC_TEXTS:
        if has_ascii_digit(text):
            raise ReportError("固定文案含数字，需改为带来源的数据行：{0}".format(text))
