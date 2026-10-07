"""依据登记表（提交材料第 5 项）与条款引用契约。

数据文件：data/clauses/register.json
登记每一条被引用的标准/规章的编号、名称、条款、查证状态与渠道。

纪律：
  - status=verified 才允许该条款向计算路径供数值；
  - located（条号/表号已按渠道原文逐字定位、数值未取到）与 pending 一样不得供货；
  - 任何状态都必须挂渠道与检索日期，缺字段即契约校验失败（selfcheck 报错，不静默）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from pmc.contract.thresholds import VERIFY_STATUSES, STATUS_VERIFIED
from pmc.errors import ContractError

REGISTER_FILE = os.path.join("clauses", "register.json")

#: 一条登记记录必填的键（缺任一即 ContractError）
REQUIRED_KEYS = (
    "id",
    "standard_code",
    "standard_title",
    "topic",
    "status",
    "channels",
    "retrieved_on",
)

#: 允许出现在登记文件顶层的键
ALLOWED_TOP_KEYS = ("schema", "notice", "entries")


@dataclass(frozen=True)
class ClauseEntry:
    id: str
    standard_code: str
    standard_title: str
    topic: str
    status: str
    channels: List[dict]
    retrieved_on: str
    clause_no: Optional[str] = None
    table_no: Optional[str] = None
    note: Optional[str] = None

    @property
    def can_supply_values(self) -> bool:
        return self.status == STATUS_VERIFIED

    def validate(self) -> None:
        if self.status not in VERIFY_STATUSES:
            raise ContractError("条款 {0} 的 status 非法：{1}".format(self.id, self.status))
        if not self.channels:
            raise ContractError(
                "条款 {0} 未挂查证渠道，不得进入登记表".format(self.id)
            )
        for ch in self.channels:
            missing = [k for k in ("channel", "locator") if not ch.get(k)]
            if missing:
                raise ContractError(
                    "条款 {0} 的渠道记录缺字段：{1}".format(self.id, ",".join(missing))
                )


def load_register(data_dir: str) -> Dict[str, ClauseEntry]:
    """读取依据登记表。文件不存在或结构非法都抛 ContractError（不降级、不猜测）。"""
    path = os.path.join(data_dir, REGISTER_FILE)
    if not os.path.isfile(path):
        raise ContractError("缺少依据登记表：{0}".format(path))
    with open(path, "r", encoding="utf-8") as handle:
        doc = json.load(handle)

    unknown = sorted(set(doc) - set(ALLOWED_TOP_KEYS))
    if unknown:
        raise ContractError("依据登记表顶层出现未定义键：{0}".format(",".join(unknown)))

    entries: Dict[str, ClauseEntry] = {}
    for raw in doc.get("entries", []):
        missing = [k for k in REQUIRED_KEYS if k not in raw]
        if missing:
            raise ContractError(
                "条款记录缺必填字段：{0}".format(",".join(missing))
            )
        entry = ClauseEntry(
            id=raw["id"],
            standard_code=raw["standard_code"],
            standard_title=raw["standard_title"],
            topic=raw["topic"],
            status=raw["status"],
            channels=list(raw["channels"]),
            retrieved_on=raw["retrieved_on"],
            clause_no=raw.get("clause_no"),
            table_no=raw.get("table_no"),
            note=raw.get("note"),
        )
        if entry.id in entries:
            raise ContractError("条款登记键重复：{0}".format(entry.id))
        entry.validate()
        entries[entry.id] = entry
    if not entries:
        raise ContractError("依据登记表为空：任何阈值都将无来源，工具无法供货")
    return entries


def require(entries: Dict[str, ClauseEntry], clause_id: Optional[str]) -> ClauseEntry:
    if not clause_id:
        raise ContractError("引用条款的键为空")
    if clause_id not in entries:
        raise ContractError("条款 {0} 未在依据登记表登记".format(clause_id))
    return entries[clause_id]
