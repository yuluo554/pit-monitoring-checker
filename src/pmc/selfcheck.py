"""契约自检：把"数据是否可入判定路径"做成一条命令，而不是一句文档承诺。

检查项（全部只用标准库）：
  1. 数据目录可定位（源码树 / 冻结内嵌走同一通路）
  2. 依据登记表结构与查证状态合法
  3. 监测项目字典结构合法（字典本身不含阈值数值）
  4. 规则集结构合法 + 来源门控统计：多少条能判定、多少条在等核对、各卡在哪个原因码
  5. 规则引用的监测项目必须存在于字典
  6. 台账 DDL 可执行、表齐全、schema 版本匹配
  7. 确定性 RNG 可复现且流间隔离

退出码：0 契约自洽；2 契约不合法。
"存在未核对依据"本身不是自检失败（M0 全库阈值都待核对，仍应出 0），
它由 pmc check / audit / report 作为降级退出码 1 暴露 —— 见 pmc/errors.py。
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Tuple

from pmc import SCHEMA_VERSION
from pmc.catalog.items import MonitoringItem, load_items
from pmc.contract.clauses import load_register
from pmc.contract.thresholds import STATUS_LOCATED, STATUS_PENDING, STATUS_VERIFIED
from pmc.db.schema import TABLE_NAMES, apply_schema, missing_tables
from pmc.paths import find_data_dir
from pmc.rules.gate import reason_counts, split_rulesets
from pmc.rules.loader import SCOPES, RuleSet, load_rulesets
from pmc.synth.rng import DetRng


def run(data_dir_arg=None) -> Tuple[int, List[str]]:
    lines: List[str] = []
    try:
        data_dir = find_data_dir(data_dir_arg)
    except FileNotFoundError as exc:
        return 2, ["SELF_CHECK_FAIL: {0}".format(exc)]
    lines.append("data_dir = {0}".format(data_dir))

    try:
        clauses = load_register(data_dir)
    except Exception as exc:
        return 2, lines + ["SELF_CHECK_FAIL 依据登记表：{0}".format(exc)]
    counts: Dict[str, int] = {}
    for entry in clauses.values():
        counts[entry.status] = counts.get(entry.status, 0) + 1
    lines.append(
        "条款登记 = {0} 条（verified {1} / located {2} / pending {3}）".format(
            len(clauses),
            counts.get(STATUS_VERIFIED, 0),
            counts.get(STATUS_LOCATED, 0),
            counts.get(STATUS_PENDING, 0),
        )
    )

    try:
        items: Dict[str, MonitoringItem] = load_items(data_dir)
    except Exception as exc:
        return 2, lines + ["SELF_CHECK_FAIL 监测项目字典：{0}".format(exc)]
    lines.append("监测项目字典 = {0} 项".format(len(items)))

    try:
        rulesets: List[RuleSet] = load_rulesets(data_dir)
    except Exception as exc:
        return 2, lines + ["SELF_CHECK_FAIL 规则集：{0}".format(exc)]

    enabled, blocked = split_rulesets(rulesets, clauses)
    total_rules = sum(len(rs.rules) for rs in rulesets)
    lines.append(
        "规则集 = {0} 份 / 规则 {1} 条，可参与判定 {2} 条，不启用 {3} 条".format(
            len(rulesets), total_rules, len(enabled), len(blocked)
        )
    )
    for reason, count in sorted(reason_counts(blocked).items()):
        lines.append("  不启用原因 {0} = {1} 条".format(reason, count))

    dangling = sorted(
        {
            rule.item_code
            for rs in rulesets
            for rule in rs.rules
            if rule.item_code not in items and rule.item_code not in SCOPES
        }
    )
    if dangling:
        return 2, lines + [
            "SELF_CHECK_FAIL 规则引用了字典中不存在的监测项目：{0}".format(
                ",".join(dangling)
            )
        ]

    try:
        conn = sqlite3.connect(":memory:")
        apply_schema(conn)
        gaps = missing_tables(conn)
        version = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()[0]
        conn.close()
    except Exception as exc:
        return 2, lines + ["SELF_CHECK_FAIL 台账 DDL：{0}".format(exc)]
    if gaps:
        return 2, lines + ["SELF_CHECK_FAIL 台账缺表：{0}".format(",".join(gaps))]
    if version != str(SCHEMA_VERSION):
        return 2, lines + [
            "SELF_CHECK_FAIL schema 版本 {0} != 代码 {1}".format(version, SCHEMA_VERSION)
        ]
    lines.append("台账 DDL = {0} 张表，schema_version = {1}".format(len(TABLE_NAMES), version))

    same_seed = [DetRng(42, "probe").next_u64() for _ in range(3)]
    again = [DetRng(42, "probe").next_u64() for _ in range(3)]
    other_stream = [DetRng(42, "other").next_u64() for _ in range(3)]
    if same_seed != again or same_seed == other_stream:
        return 2, lines + ["SELF_CHECK_FAIL 确定性 RNG 不可复现或流未隔离"]
    lines.append("确定性 RNG = 同 seed 同流可复现，异流隔离")

    lines.append("SELF_CHECK_OK")
    return 0, lines
