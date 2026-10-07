"""监测项目字典（data/dict/monitoring_items.json）的装载与结构校验。

字典只描述"这个项目测什么、单位是什么、要按哪种判据合成"，
不含任何阈值数值 —— 阈值一律走 pmc.contract.thresholds 的来源三态登记。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List

from pmc.errors import ContractError

DICT_FILE = os.path.join("dict", "monitoring_items.json")

#: 判据形态：dual = 累计量与速率双控（题面 01 §模块 2 双控合成）
CONTROL_KINDS = ("dual", "cumulative", "rate", "none")

REQUIRED_ENTRY_KEYS = (
    "code",
    "name",
    "unit",
    "control_kind",
    "window_basis",
    "list_source",
)

#: window_basis 说明"速率窗口按监测项目配置"这条题面要求的落点：
#: 窗口长度属项目配置（source=user_input），不属标准条文，故不得伪装成规范值。
WINDOW_BASES = ("project_config", "per_item_fixed", "none")


@dataclass(frozen=True)
class MonitoringItem:
    code: str
    name: str
    unit: str
    control_kind: str
    window_basis: str
    #: 字典条目自身的来源登记（哪一份材料把该项目列进来的）
    list_source: str
    list_status: str
    scope_note: str = ""
    clause_ids: List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.clause_ids is None:
            object.__setattr__(self, "clause_ids", [])

    def validate(self) -> None:
        if self.control_kind not in CONTROL_KINDS:
            raise ContractError(
                "监测项目 {0} 的 control_kind 非法：{1}".format(
                    self.code, self.control_kind
                )
            )
        if self.window_basis not in WINDOW_BASES:
            raise ContractError(
                "监测项目 {0} 的 window_basis 非法：{1}".format(
                    self.code, self.window_basis
                )
            )
        if not self.unit:
            raise ContractError("监测项目 {0} 缺单位".format(self.code))
        if not self.list_source:
            raise ContractError(
                "监测项目 {0} 的字典条目未挂来源（不得凭空列入）".format(self.code)
            )


def load_items(data_dir: str) -> Dict[str, MonitoringItem]:
    path = os.path.join(data_dir, DICT_FILE)
    if not os.path.isfile(path):
        raise ContractError("缺少监测项目字典：{0}".format(path))
    with open(path, "r", encoding="utf-8") as handle:
        doc = json.load(handle)

    entries = doc.get("items")
    if not isinstance(entries, list) or not entries:
        raise ContractError("监测项目字典的 items 为空")

    items: Dict[str, MonitoringItem] = {}
    for raw in entries:
        missing = [k for k in REQUIRED_ENTRY_KEYS if k not in raw]
        if missing:
            raise ContractError(
                "监测项目条目 {0} 缺字段：{1}".format(
                    raw.get("code", "?"), ",".join(missing)
                )
            )
        item = MonitoringItem(
            code=raw["code"],
            name=raw["name"],
            unit=raw["unit"],
            control_kind=raw["control_kind"],
            window_basis=raw["window_basis"],
            list_source=raw["list_source"],
            list_status=raw.get("list_status", "pending"),
            scope_note=raw.get("scope_note", ""),
            clause_ids=list(raw.get("clause_ids", [])),
        )
        if item.code in items:
            raise ContractError("监测项目编码重复：{0}".format(item.code))
        item.validate()
        items[item.code] = item
    return items
