"""规则集文件（data/rulesets/*.json）的装载与结构校验。

规则集是 versioned 的：文件内容变 → version 必须 +1 并在 plan/06 记一行口径变更，
否则已生成的报告与判定结果无法对账（题面 01 §模块 3 "规则集"）。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pmc.contract.thresholds import SOURCE_DESIGN, Threshold
from pmc.errors import ContractError

SCHEMA_TAG = "pmc-ruleset/1"
RULESET_DIR = "rulesets"

#: 频率/时序类规则可以作用于"全部测点"而不绑定单个监测项目
SCOPES = ("all_items",)

#: judgement：这条规则产出什么。alarm/prewarning 进模块 2，frequency 进模块 3。
JUDGEMENTS = ("alarm", "prewarning", "frequency")

#: basis：判据。ratio = 按报警控制值的比例提前预警（预警比例本身也是阈值，同样要挂来源）。
BASES = ("cumulative", "rate", "ratio", "interval", "sequence")

#: 速率窗口长度属项目配置，不得伪装成规范值：出现数值就必须挂来源
WINDOW_SOURCE_KINDS = ("project_config", "design_value", "user_input")

#: 规则对象的键白名单：出现未定义键即失败，防止规则文件悄悄加进引擎不认的字段
ALLOWED_RULE_KEYS = (
    "id",
    "name",
    "item_code",
    "judgement",
    "basis",
    "threshold",
    "clause_ids",
    "window_days",
    "window_source",
    "note",
)

REQUIRED_RULE_KEYS = (
    "id",
    "name",
    "item_code",
    "judgement",
    "basis",
    "threshold",
)

THRESHOLD_KEYS = (
    "kind",
    "status",
    "value",
    "unit",
    "clause_id",
    "evidence",
    "channel",
    "url",
    "verified_on",
    "note",
)


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    item_code: str
    judgement: str
    basis: str
    threshold: Threshold
    clause_ids: List[str] = field(default_factory=list)
    window_days: Optional[float] = None
    window_source: Optional[Dict[str, str]] = None
    note: str = ""

    def validate(self) -> None:
        if self.judgement not in JUDGEMENTS:
            raise ContractError("规则 {0} judgement 非法：{1}".format(self.id, self.judgement))
        if self.basis not in BASES:
            raise ContractError("规则 {0} basis 非法：{1}".format(self.id, self.basis))
        if not self.item_code:
            raise ContractError("规则 {0} 未指定监测项目或作用域".format(self.id))
        if self.threshold.kind == SOURCE_DESIGN:
            raise ContractError(
                "规则 {0} 不得登记设计文件值：设计报警值属测点档案（point 表），"
                "规则集只描述判据形态与标准回退档".format(self.id)
            )
        if self.basis == "ratio" and self.judgement != "prewarning":
            raise ContractError("比例判据只能用于预警规则：{0}".format(self.id))
        if self.window_days is not None:
            if self.window_days <= 0:
                raise ContractError("速率窗口必须为正数：{0}".format(self.id))
            src = self.window_source or {}
            if src.get("kind") not in WINDOW_SOURCE_KINDS:
                raise ContractError(
                    "规则 {0} 的窗口长度未挂配置来源（窗口不是规范值，必须登记谁定的）".format(
                        self.id
                    )
                )
            if not src.get("evidence"):
                raise ContractError("规则 {0} 的窗口来源缺凭证".format(self.id))


@dataclass(frozen=True)
class RuleSet:
    code: str
    title: str
    version: int
    schema: str
    path: str
    sha256: str
    rules: List[Rule]
    notice: str = ""

    def rule_ids(self) -> List[str]:
        return [r.id for r in self.rules]


def _threshold_from(raw: Dict) -> Threshold:
    unknown = sorted(set(raw) - set(THRESHOLD_KEYS))
    if unknown:
        raise ContractError("阈值登记出现未定义键：{0}".format(",".join(unknown)))
    return Threshold(
        kind=raw.get("kind", "none"),
        status=raw.get("status", "pending"),
        value=raw.get("value"),
        unit=raw.get("unit"),
        clause_id=raw.get("clause_id"),
        evidence=raw.get("evidence"),
        channel=raw.get("channel"),
        url=raw.get("url"),
        verified_on=raw.get("verified_on"),
        note=raw.get("note", "") if "note" in raw else None,
    )


def parse_ruleset(path: str) -> RuleSet:
    with open(path, "rb") as handle:
        blob = handle.read()
    doc = json.loads(blob.decode("utf-8"))
    top_unknown = sorted(
        set(doc) - {"schema", "code", "title", "version", "notice", "rules"}
    )
    if top_unknown:
        raise ContractError("{0} 顶层出现未定义键：{1}".format(path, ",".join(top_unknown)))
    if doc.get("schema") != SCHEMA_TAG:
        raise ContractError(
            "{0} 的 schema 应为 {1}，实为 {2}".format(path, SCHEMA_TAG, doc.get("schema"))
        )
    if not isinstance(doc.get("version"), int) or doc["version"] < 1:
        raise ContractError("{0} 的 version 必须是 >=1 的整数".format(path))

    rules: List[Rule] = []
    seen = set()
    for raw in doc.get("rules", []):
        missing = [k for k in REQUIRED_RULE_KEYS if k not in raw]
        if missing:
            raise ContractError(
                "规则 {0} 缺字段：{1}".format(raw.get("id", "?"), ",".join(missing))
            )
        unknown_rule_keys = sorted(set(raw) - set(ALLOWED_RULE_KEYS))
        if unknown_rule_keys:
            raise ContractError(
                "规则 {0} 出现未定义键：{1}".format(raw["id"], ",".join(unknown_rule_keys))
            )
        rule = Rule(
            id=raw["id"],
            name=raw["name"],
            item_code=raw["item_code"],
            judgement=raw["judgement"],
            basis=raw["basis"],
            threshold=_threshold_from(raw["threshold"]),
            clause_ids=list(raw.get("clause_ids", [])),
            window_days=raw.get("window_days"),
            window_source=raw.get("window_source"),
            note=raw.get("note", ""),
        )
        if rule.id in seen:
            raise ContractError("规则 id 重复：{0}".format(rule.id))
        seen.add(rule.id)
        rule.validate()
        rules.append(rule)
    if not rules:
        raise ContractError("{0} 规则集为空".format(path))

    return RuleSet(
        code=doc["code"],
        title=doc["title"],
        version=doc["version"],
        schema=doc["schema"],
        path=os.path.abspath(path),
        sha256=hashlib.sha256(blob).hexdigest(),
        rules=rules,
        notice=doc.get("notice", ""),
    )


def load_rulesets(data_dir: str) -> List[RuleSet]:
    dir_path = os.path.join(data_dir, RULESET_DIR)
    if not os.path.isdir(dir_path):
        raise ContractError("缺少规则集目录：{0}".format(dir_path))
    names = sorted(n for n in os.listdir(dir_path) if n.endswith(".json"))
    if not names:
        raise ContractError("规则集目录为空：任何判定都无规则可执行")
    sets = [parse_ruleset(os.path.join(dir_path, n)) for n in names]
    codes = {}  # type: Dict[str, int]
    for rs in sets:
        if rs.code in codes:
            raise ContractError("规则集 code 重复：{0}".format(rs.code))
        codes[rs.code] = rs.version
    return sets
