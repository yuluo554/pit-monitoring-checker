"""命令行内核：一条命令链跑完 台账 → 判定 → 检核 → 报告 → 基准。

子命令面与退出码在 M0 定稿，之后重构不得漂移（tests/test_cli_surface.py 锁定）。
未开工里程碑的命令返回 3 并指回里程碑号 —— 占位不得伪装成成功。
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from pmc import __version__
from pmc.db.schema import apply_schema, missing_tables
from pmc.errors import (
    EXIT_INPUT_UNAVAILABLE,
    EXIT_NOT_IMPLEMENTED,
    PmcError,
)
from pmc.paths import find_data_dir
from pmc.selfcheck import run as run_selfcheck

#: 命令 → 所属里程碑（M0 只实装 selfcheck / init / dict / rulesets）
COMMAND_MILESTONE = {
    "import": "M1",
    "ledger": "M1",
    "check": "M2",
    "audit": "M3",
    "report": "M5",
    "synth": "M1",
    "bench": "M4",
    "gui": "M5",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pmc",
        description="建筑基坑工程监测数据判读与预警（离线内核，判定挂条款号）",
    )
    parser.add_argument("--version", action="version", version="pmc {0}".format(__version__))
    parser.add_argument(
        "--data-dir",
        default=None,
        help="数据目录（含 dict/ rulesets/ clauses/）；缺省按环境变量 PMC_DATA_DIR 与仓库根自动定位",
    )
    sub = parser.add_subparsers(dest="command")

    p_selfcheck = sub.add_parser("selfcheck", help="契约自检：登记表/字典/规则集/DDL/RNG")
    p_selfcheck.add_argument(
        "--json", action="store_true", help="以 JSON 输出统计（供门禁脚本对账退出码）"
    )

    p_init = sub.add_parser("init", help="新建空台账（SQLite，三级：工程→测点→轮次）")
    p_init.add_argument("--db", required=True, help="目标 .sqlite 文件路径")

    sub.add_parser("dict", help="列出监测项目字典及其来源状态")
    sub.add_parser("rulesets", help="列出规则集、版本与启用门控统计")

    for name, help_text in (
        ("import", "导入一轮观测数据并出具导入回执"),
        ("ledger", "台账查询与修订链核对"),
        ("check", "双控阈值报警判定（累计量 + 速率）"),
        ("audit", "监测频率与时效合规检核"),
        ("report", "导出日报/周报/阶段报告（xlsx）"),
        ("synth", "生成合成监测时序与异常事件真值"),
        ("bench", "内置基准评测：召回/误报/首超定位误差"),
        ("gui", "启动桌面界面"),
    ):
        sub.add_parser(name, help=help_text)

    return parser


def _cmd_selfcheck(args: argparse.Namespace) -> int:
    code, lines = run_selfcheck(args.data_dir)
    if args.json:
        import json

        sys.stdout.write(json.dumps({"exit_code": code, "lines": lines}, ensure_ascii=False))
        sys.stdout.write("\n")
    else:
        for line in lines:
            print(line)
    return code


def _cmd_init(args: argparse.Namespace) -> int:
    import sqlite3

    conn = sqlite3.connect(args.db)
    try:
        apply_schema(conn)
        gaps = missing_tables(conn)
    finally:
        conn.close()
    if gaps:
        print("建表不完整，缺：{0}".format(",".join(gaps)), file=sys.stderr)
        return EXIT_INPUT_UNAVAILABLE
    print("台账已建：{0}".format(args.db))
    return 0


def _cmd_dict(args: argparse.Namespace) -> int:
    from pmc.catalog.items import load_items

    items = load_items(find_data_dir(args.data_dir))
    for code in sorted(items):
        item = items[code]
        print(
            "{0}\t{1}\t{2}\t{3}\t来源={4}/{5}".format(
                item.code, item.name, item.unit, item.control_kind,
                item.list_status, ",".join(item.clause_ids) or "-",
            )
        )
    return 0


def _cmd_rulesets(args: argparse.Namespace) -> int:
    from pmc.contract.clauses import load_register
    from pmc.rules.gate import reason_counts, rule_enabled
    from pmc.rules.loader import load_rulesets

    data_dir = find_data_dir(args.data_dir)
    clauses = load_register(data_dir)
    blocked_reasons = {}
    total = 0
    for ruleset in load_rulesets(data_dir):
        print(
            "{0} v{1} ({2} 条规则) sha256={3}".format(
                ruleset.code, ruleset.version, len(ruleset.rules), ruleset.sha256[:12]
            )
        )
        for rule in ruleset.rules:
            total += 1
            ok, reason = rule_enabled(rule, clauses)
            if not ok:
                blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1
    print("规则总数 {0}，不启用 {1}".format(total, sum(blocked_reasons.values())))
    for reason, count in sorted(blocked_reasons.items()):
        print("  {0} = {1}".format(reason, count))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "selfcheck": _cmd_selfcheck,
        "init": _cmd_init,
        "dict": _cmd_dict,
        "rulesets": _cmd_rulesets,
    }
    handler = handlers.get(args.command)
    if handler is None:
        milestone = COMMAND_MILESTONE.get(args.command, "?")
        print(
            "NOT_IMPLEMENTED: pmc {0} 属 {1}，尚未开工（本轮只交付契约级骨架）".format(
                args.command, milestone
            ),
            file=sys.stderr,
        )
        return EXIT_NOT_IMPLEMENTED

    try:
        return handler(args)
    except PmcError as exc:
        print("{0}: {1}".format(type(exc).__name__, exc.message), file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        print("UNEXPECTED {0}: {1}".format(type(exc).__name__, exc), file=sys.stderr)
        return EXIT_INPUT_UNAVAILABLE
