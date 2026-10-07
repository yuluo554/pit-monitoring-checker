"""阈值来源三态与"待定值不出货"纪律（模块 2 的核心防线，题面 01 §二 通用确定性纪律）。

一条阈值只有同时满足：挂来源（三选一）+ 已确认（status=verified）+ 有数值 + 有出处凭证，
才允许进入计算与报警判定。任何一项缺失即"待定值"，输出状态 UNDETERMINED，
且结果对象的阈值数值字段必须为空（不是 0、不是 NaN、不是"仅供参考"）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

SOURCE_DESIGN = "design_value"
SOURCE_STANDARD = "standard_value"
SOURCE_USER = "user_input"
SOURCE_NONE = "none"

SOURCE_KINDS = (SOURCE_DESIGN, SOURCE_STANDARD, SOURCE_USER, SOURCE_NONE)

SOURCE_KIND_LABEL = {
    SOURCE_DESIGN: "设计文件值",
    SOURCE_STANDARD: "标准条文值",
    SOURCE_USER: "用户手填值",
    SOURCE_NONE: "无来源",
}

STATUS_PENDING = "pending"
STATUS_LOCATED = "located"
STATUS_VERIFIED = "verified"

VERIFY_STATUSES = (STATUS_PENDING, STATUS_LOCATED, STATUS_VERIFIED)

#: 不启用原因码：报告与 selfcheck 按这些码统计"有多少判定是在等核对"，
#: 也用来把"解析能力欠缺"与"等核对"分开（两者混在一起会让核对队列失去优先级信息）。
REASON_NO_SOURCE = "no_source"
REASON_NOT_VERIFIED = "source_not_confirmed"
REASON_LOCATED = "located_value_missing"
REASON_VALUE_MISSING = "value_missing"
REASON_EVIDENCE_MISSING = "evidence_missing"
REASON_CLAUSE_MISSING = "clause_id_missing"
REASON_UNIT_MISSING = "unit_missing"
#: 速率判据没有窗口长度就无法计算：与"阈值未核对"分开统计，两者解耦路径不同
REASON_WINDOW_UNCONFIGURED = "window_unconfigured"


@dataclass(frozen=True)
class Threshold:
    """一条控制值（累计控制值或速率控制值）。

    evidence 的含义随来源变化，三者都必须填：
      design_value  → 设计文件/专项方案编号（如 SYN-JK-2026-0001）
      standard_value→ 标准条文号在 data/clauses/register.json 中的登记键
      user_input    → 录入登记（录入人角色 + 录入时间）
    """

    kind: str = SOURCE_NONE
    status: str = STATUS_PENDING
    value: Optional[float] = None
    unit: Optional[str] = None
    clause_id: Optional[str] = None
    evidence: Optional[str] = None
    #: 以下三项仅 standard_value 需要（外部标准的可直连原文凭证）
    channel: Optional[str] = None
    url: Optional[str] = None
    verified_on: Optional[str] = None
    note: Optional[str] = None

    def disabled_reason(self) -> Optional[str]:
        """返回 None 表示可参与判定；否则返回不启用的第一个原因码。"""
        if self.kind not in SOURCE_KINDS:
            return REASON_NO_SOURCE
        if self.kind == SOURCE_NONE:
            return REASON_NO_SOURCE
        if self.status == STATUS_LOCATED:
            return REASON_LOCATED
        if self.status != STATUS_VERIFIED:
            return REASON_NOT_VERIFIED
        if not self.evidence:
            return REASON_EVIDENCE_MISSING
        if self.value is None:
            return REASON_VALUE_MISSING
        if not self.unit:
            return REASON_UNIT_MISSING
        if self.kind == SOURCE_STANDARD:
            if not self.clause_id:
                return REASON_CLAUSE_MISSING
            if not (self.channel and self.url and self.verified_on):
                return REASON_EVIDENCE_MISSING
        return None

    @property
    def usable(self) -> bool:
        return self.disabled_reason() is None

    @property
    def is_undetermined(self) -> bool:
        return not self.usable

    @staticmethod
    def unbound(note: str = "") -> "Threshold":
        """无来源占位：永远不可用，供上层输出"待定值"状态。"""
        return Threshold(kind=SOURCE_NONE, status=STATUS_PENDING, note=note)

    def blank_output(self) -> "Threshold":
        """不启用时对外输出的脱空副本：数值与单位一律清空，只留来源与原因。

        这是"blocked 结果对象数值字段全空"这条纪律的落地点，
        防止下游拿 0 或 NaN 当阈值继续算。
        """
        return Threshold(
            kind=self.kind,
            status=self.status,
            value=None,
            unit=None,
            clause_id=self.clause_id,
            evidence=self.evidence,
            channel=self.channel,
            url=self.url,
            verified_on=self.verified_on,
            note=self.note,
        )


@dataclass(frozen=True)
class DualControl:
    """双控阈值对：累计控制值 + 速率控制值。

    两条判据各自独立挂来源：允许"累计量已挂设计值、速率无来源"这类真实处境，
    此时累计判据参与判定、速率判据输出待定值并计入核对队列。
    """

    cumulative: Threshold = field(default_factory=Threshold.unbound)
    rate: Threshold = field(default_factory=Threshold.unbound)

    @property
    def any_usable(self) -> bool:
        return self.cumulative.usable or self.rate.usable

    @property
    def all_usable(self) -> bool:
        return self.cumulative.usable and self.rate.usable
