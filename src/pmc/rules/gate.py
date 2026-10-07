"""规则启用门控：一条规则能不能进判定路径，由此处单点决定。

数据文件里不得自带"是否启用"字段 —— 否则规则可以自称启用，整套核对纪律形同虚设。
有效档位取 min(阈值来源登记, 条款登记状态)。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from pmc.contract.clauses import ClauseEntry, require
from pmc.contract.thresholds import (
    REASON_WINDOW_UNCONFIGURED,
    SOURCE_STANDARD,
    Threshold,
)
from pmc.rules.loader import Rule, RuleSet


def threshold_block_reason(
    threshold: Threshold, clauses: Dict[str, ClauseEntry]
) -> Tuple[bool, str]:
    """返回 (可用, 原因码)。原因码为空字符串表示可用。"""
    reason = threshold.disabled_reason()
    if reason:
        return False, reason
    if threshold.kind == SOURCE_STANDARD and threshold.clause_id:
        entry = require(clauses, threshold.clause_id)
        if not entry.can_supply_values:
            return False, "clause_not_verified"
    return True, ""


#: 只有需要数值的判据才过阈值门控；sequence（如"报警后未加密观测"）靠条款时序定义，
#: 它的门控是"条款是否已核对"，把两者混为一谈会让纯时序规则被误判成"等数值核对"。
NUMERIC_BASES = ("cumulative", "rate", "ratio", "interval")


def rule_enabled(rule: Rule, clauses: Dict[str, ClauseEntry]) -> Tuple[bool, str]:
    if rule.basis in NUMERIC_BASES:
        ok, reason = threshold_block_reason(rule.threshold, clauses)
        if not ok:
            return False, reason
    elif not rule.clause_ids:
        return False, "clause_id_missing"

    if rule.basis == "rate" and rule.window_days is None:
        return False, REASON_WINDOW_UNCONFIGURED

    for clause_id in rule.clause_ids:
        entry = require(clauses, clause_id)
        if not entry.can_supply_values:
            return False, "clause_not_verified"
    return True, ""


def split_rulesets(
    sets: List[RuleSet], clauses: Dict[str, ClauseEntry]
) -> Tuple[List[Rule], List[Tuple[Rule, str]]]:
    enabled: List[Rule] = []
    blocked: List[Tuple[Rule, str]] = []
    for ruleset in sets:
        for rule in ruleset.rules:
            ok, reason = rule_enabled(rule, clauses)
            (enabled if ok else blocked).append((rule, reason) if not ok else rule)
    return enabled, blocked


def reason_counts(blocked: List[Tuple[Rule, str]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for _, reason in blocked:
        counts[reason] = counts.get(reason, 0) + 1
    return counts
