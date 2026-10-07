"""跨模块数据流记录（模块 1→5 的接口契约）。

每个记录类型都带 validate()，由 pmc selfcheck 与 tests/test_contracts.py 在 M0 就实跑校验；
业务生产方在各里程碑接入（见各记录的 milestone 注释）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pmc.contract.status import ALL_STATES, TRIGGER_BASES, ObsState
from pmc.contract.thresholds import DualControl
from pmc.errors import ContractError


@dataclass(frozen=True)
class PointKey:
    """判定的最小粒度主键：测点 × 轮次 × 监测项目（题面 01 §模块 2 输出）。"""

    project_code: str
    point_code: str
    round_index: int
    item_code: str

    def validate(self) -> None:
        if not self.project_code or not self.point_code or not self.item_code:
            raise ContractError("PointKey 三元组不得为空")
        if self.round_index < 1:
            raise ContractError("轮次序号从 1 起：{0}".format(self.round_index))

    def __str__(self) -> str:
        return "{0}/{1}/R{2}/{3}".format(
            self.project_code, self.point_code, self.round_index, self.item_code
        )


@dataclass(frozen=True)
class Observation:
    """一条观测读数（模块 1 入库对象，milestone=M1）。"""

    point_code: str
    item_code: str
    round_index: int
    cumulative_value: Optional[float] = None
    unit: Optional[str] = None
    raw_text: Optional[str] = None
    #: 缺测标记：不许自动插值，缺失就是缺失（题面 01 §模块 1 要点）
    missing: bool = False
    #: 同一测点同一轮次的重复上报进修订链，seq=1 为先到，后到只追加不覆盖
    revision_seq: int = 1
    superseded_by: Optional[int] = None
    source_file: Optional[str] = None
    source_row: Optional[int] = None
    unit_flag: Optional[str] = None

    def validate(self) -> None:
        if self.missing and self.cumulative_value is not None:
            raise ContractError("缺测标记与数值互斥：{0}".format(self.point_code))
        if not self.missing and self.cumulative_value is None and not self.raw_text:
            raise ContractError("既无缺测标记、又无数值与原文：{0}".format(self.point_code))
        if not self.missing and self.cumulative_value is not None and not self.unit:
            raise ContractError("有数值必须有单位（供单位一致性校验）：{0}".format(self.point_code))
        if self.revision_seq < 1:
            raise ContractError("修订序号从 1 起")


@dataclass(frozen=True)
class Rejection:
    """导入回执里的一条拒绝记录（模块 1 输出，milestone=M1）。"""

    source_row: int
    reason_code: str
    detail: str = ""


@dataclass(frozen=True)
class ImportReceipt:
    """导入回执：哪些行入库、哪些行被拒及原因（题面 01 §模块 1 输出）。"""

    source_file: str
    file_sha256: str
    rows_total: int
    rows_accepted: int
    rows_rejected: int
    rejections: List[Rejection] = field(default_factory=list)

    def validate(self) -> None:
        if self.rows_accepted + self.rows_rejected != self.rows_total:
            raise ContractError(
                "回执不平：{0} 入库+拒绝 ≠ 总行数".format(self.source_file)
            )
        if len(self.rejections) != self.rows_rejected:
            raise ContractError("拒绝明细条数与 rows_rejected 不一致")


@dataclass(frozen=True)
class AlarmRecord:
    """模块 2 的一条判定输出（milestone=M2）：状态 + 触发依据 + 条款号。"""

    key: PointKey
    state: str
    trigger_basis: str
    dual: DualControl
    clause_ids: List[str] = field(default_factory=list)
    #: 上一轮报警未闭环时，本轮即使回落也保持此标记（必须持久化才能得到）
    unclosed_carried: bool = False
    first_alarm_round_index: Optional[int] = None
    #: 不启用原因码（阈值未挂来源/未核对等），供核对队列统计
    disabled_reasons: List[str] = field(default_factory=list)

    def validate(self) -> None:
        self.key.validate()
        if self.state not in ALL_STATES:
            raise ContractError("非法状态：{0}".format(self.state))
        if self.trigger_basis not in TRIGGER_BASES:
            raise ContractError("非法触发依据：{0}".format(self.trigger_basis))
        if self.state == ObsState.UNDETERMINED:
            if self.trigger_basis != "none":
                raise ContractError("待定值不得带触发依据")
            if not self.disabled_reasons:
                raise ContractError("待定值必须写明不启用原因码")
            if self.dual.cumulative.value is not None or self.dual.rate.value is not None:
                raise ContractError("待定值记录的阈值字段必须为空")
            return
        if self.state in (ObsState.NORMAL, ObsState.PREWARNING, ObsState.ALARM,
                          ObsState.ALARM_CONFIRMED, ObsState.ALARM_HANDLED):
            if not self.dual.any_usable:
                raise ContractError(
                    "无可用阈值却输出了 {0} 状态：违反待定值纪律".format(self.state)
                )
            if not self.clause_ids and self._uses_standard():
                raise ContractError("按标准条文回退的判定必须写明条款号")

    def _uses_standard(self) -> bool:
        from pmc.contract.thresholds import SOURCE_STANDARD

        return any(
            thr.kind == SOURCE_STANDARD and thr.usable
            for thr in (self.dual.cumulative, self.dual.rate)
        )


@dataclass(frozen=True)
class ViolationRecord:
    """模块 3 的一条合规违规（milestone=M3）：逐条挂条款号。"""

    key: Optional[PointKey]
    kind: str
    rule_id: str
    clause_ids: List[str]
    #: 判定依据的原始时间/序号差，报告里要能回溯（M3 落地：间隔天数、上一轮时间、生效工况 code）
    evidence: Dict[str, object] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.clause_ids:
            raise ContractError("合规违规必须挂条款号：{0}".format(self.kind))
        if not self.rule_id:
            raise ContractError("违规必须能指回触发的规则 id")


@dataclass(frozen=True)
class TraceRow:
    """模块 4 报告追溯清单的一行（milestone=M5）：报告里的每个数字回到台账行。"""

    report_cell: str
    table_name: str
    row_id: int
    column_name: str

    def validate(self) -> None:
        if self.row_id < 1:
            raise ContractError("追溯必须指向真实台账行")
        if not self.table_name or not self.column_name:
            raise ContractError("追溯清单缺表名或列名")


@dataclass(frozen=True)
class LedgerFingerprint:
    """数据版本指纹（报告页眉 + 签字栏对账用，milestone=M5）。"""

    algorithm: str
    sha256: str
    #: 生成时间由用户在报告中手填或取系统时间，但不写进任何入仓冻结产物
    generated_at: Optional[str] = None

    def validate(self) -> None:
        if self.algorithm != "sha256":
            raise ContractError("指纹算法口径固定为 sha256")
        if len(self.sha256) != 64:
            raise ContractError("指纹必须是 64 位十六进制")
