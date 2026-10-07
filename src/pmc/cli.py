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
    EXIT_DEGRADED,
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

    p_import = sub.add_parser("import", help="导入一轮观测数据并出具导入回执")
    p_import.add_argument("file", help="CSV / XLSX 文件（8 列列序见 plan/07 §十，XLSX 见 plan/09 §十）")
    p_import.add_argument("--project", required=True, help="工程编码（须已在台账建档）")
    p_import.add_argument("--round", required=True, type=int, help="轮次序号（须已在台账建档）")
    p_import.add_argument("--db", required=True, help="台账 .sqlite 路径")
    p_import.add_argument(
        "--dry-run", action="store_true", help="只出回执与逐行判定，不写台账"
    )

    p_ledger = sub.add_parser("ledger", help="台账查询与修订链核对")
    p_ledger.add_argument("--db", required=True, help="台账 .sqlite 路径")
    p_ledger.add_argument("--project", default=None, help="按工程编码过滤")
    p_ledger.add_argument("--point", default=None, help="按测点编号过滤")
    p_ledger.add_argument("--from", dest="round_from", type=int, default=None, help="起始轮次")
    p_ledger.add_argument("--to", dest="round_to", type=int, default=None, help="结束轮次")

    p_check = sub.add_parser("check", help="双控阈值报警判定（累计量 + 速率）")
    p_check.add_argument("--db", required=True, help="台账 .sqlite 路径")
    p_check.add_argument("--project", required=True, help="工程编码（须已在台账建档）")
    p_check.add_argument(
        "--round",
        dest="round_index",
        type=int,
        default=None,
        help="只打印该轮（判据仍从第 1 轮算起，未闭环是跨轮次状态）",
    )
    p_check.add_argument(
        "--rules-dir",
        default=None,
        help="规则集目录（缺省 data/rulesets）；夹具档位与按工程的速率窗口从这里进来",
    )
    p_check.add_argument("--dry-run", action="store_true", help="只判不写 alarm_state")

    p_audit = sub.add_parser("audit", help="监测频率与时效合规检核（只报应核实，不认定违规）")
    p_audit.add_argument("--db", required=True, help="台账 .sqlite 路径")
    p_audit.add_argument(
        "--project", default=None, help="工程编码；缺省检核台账内所有有轮次档案的工程"
    )
    p_audit.add_argument(
        "--from",
        dest="round_from",
        type=int,
        default=None,
        help="起始轮次（清单只列该轮起；间隔判据仍读上一轮事实）",
    )
    p_audit.add_argument(
        "--to", dest="round_to", type=int, default=None, help="结束轮次（加密观测对账到此轮）"
    )
    p_audit.add_argument(
        "--rules-dir",
        default=None,
        help="规则集目录（缺省 data/rulesets）；按工程配置的频率分档从这里进来",
    )

    p_synth = sub.add_parser("synth", help="生成合成监测时序与异常事件真值")
    p_synth.add_argument("--seed", type=int, default=20260107, help="固定 seed（冻结产物的根）")
    p_synth.add_argument("--sites", type=int, default=3, help="按登记顺序取前 N 座基坑")
    p_synth.add_argument(
        "--rounds", type=int, default=None, help="统一覆盖轮次（只用于冒烟测试，冻结产物不传）"
    )
    p_synth.add_argument("--force", action="store_true", help="允许覆盖已有产物")
    p_synth.add_argument("--check", action="store_true", help="与仓内冻结产物逐字节对账，不写盘")
    p_synth.add_argument(
        "--db", default=None, help="同时把工程/工况/测点/轮次档案写进这个台账"
    )

    p_bench = sub.add_parser("bench", help="内置基准评测：召回/误报/首超定位误差/漏报清单（四态）")
    p_bench.add_argument(
        "--plane",
        choices=["synth", "ledger"],
        default="synth",
        help="synth=合成自证档位面（默认，出数值）；ledger=只用台账现有来源（反证「依据未核对就不出货」）",
    )
    p_bench.add_argument(
        "--sites", default=None, help="逗号分隔的工程编码（缺省三座全跑）"
    )
    p_bench.add_argument(
        "--all", action="store_true", help="显式跑全部三座虚拟基坑（等价于缺省）"
    )
    p_bench.add_argument("--seed", type=int, default=20260107, help="合成数据 seed（冻结产物的根）")
    p_bench.add_argument("--json", action="store_true", help="输出 JSON 报告（字段契约见 plan/10 §6.1）")
    p_bench.add_argument(
        "--markdown", action="store_true", help="只输出 README 指标节的四列表格（列序一致，可直接替换）"
    )
    p_bench.add_argument(
        "--write-golden",
        action="store_true",
        help="把合成面结果写进 data/golden/bench_synth.json 重基线（仅全三座时允许）",
    )

    p_report = sub.add_parser("report", help="导出日报/周报/阶段报告（xlsx，标准库直写 OOXML）")
    p_report.add_argument("--kind", required=True, choices=["daily", "weekly", "stage"], help="报告形态")
    p_report.add_argument("--db", required=True, help="台账 .sqlite 路径")
    p_report.add_argument("--project", required=True, help="工程编码（须已判定落库）")
    p_report.add_argument("--round", dest="round_index", type=int, default=None, help="日报的目标轮次")
    p_report.add_argument("--from", dest="round_from", type=int, default=None, help="周报/阶段报告起始轮次")
    p_report.add_argument("--to", dest="round_to", type=int, default=None, help="周报/阶段报告结束轮次")
    p_report.add_argument(
        "--out",
        default=None,
        help="输出目录（缺省 reports/out，已在 .gitignore 里）；不写进 data/，禁项一",
    )
    p_report.add_argument(
        "--bench-plane",
        dest="bench_plane",
        choices=["synth", "ledger"],
        default=None,
        help="附带基准逐起表（阶段报告用）：跑一次 bench 并把结论原样进报告",
    )
    p_report.add_argument("--seed", type=int, default=20260107, help="附带的基准评测 seed")
    p_report.add_argument("--dry-run", action="store_true", help="只算字节与统计，不落盘")

    p_gui = sub.add_parser("gui", help="启动桌面界面（PySide6 五页签，只消费本命令链）")
    p_gui.add_argument("--db", default="", help="台账 .sqlite 路径")
    p_gui.add_argument("--project", default="", help="默示工程编码")
    p_gui.add_argument(
        "--smoke", action="store_true", help="只构造窗口不进事件循环（offscreen 冒烟用）"
    )

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


def _cmd_synth(args: argparse.Namespace) -> int:
    import os

    from pmc.errors import InputError
    from pmc.synth import freeze

    data_dir = find_data_dir(args.data_dir)
    datas, blobs = freeze.build(data_dir, args.seed, args.sites, args.rounds)

    if args.check:
        ok, problems = freeze.check_blobs(data_dir, blobs)
        for problem in problems:
            print("SYNTH_CHECK_FAIL {0}".format(problem), file=sys.stderr)
        if not ok:
            print(
                "重生成用 seed={0} sites={1}{2}；改生成器必须整目录 --force 重新生成并一并提交".format(
                    args.seed, args.sites, "" if args.rounds is None else " rounds={0}".format(args.rounds)
                ),
                file=sys.stderr,
            )
            return EXIT_INPUT_UNAVAILABLE
        print("SYNTH_CHECK_OK {0} 个产物逐字节一致".format(len(blobs)))
        return 0

    written = freeze.write_blobs(data_dir, blobs, force=args.force)
    for data in datas:
        print(
            "{0} 轮次 {1} 测点 {2} 观测行 {3} 事件 {4}".format(
                data.site.code, len(data.rounds), len(data.points), data.total_rows(), len(data.truth)
            )
        )
    print("已写产物 {0} 个文件（seed={1}, profile=syn-fixture-grade-1）".format(len(written), args.seed))
    if args.db:
        if not os.path.isfile(args.db):
            raise InputError("台账 {0} 不存在：先跑 pmc init --db {0}".format(args.db))
        import sqlite3

        conn = sqlite3.connect(args.db)
        try:
            counts = freeze.seed_ledger(conn, datas)
        finally:
            conn.close()
        print(
            "建档：工程 {project} 工况 {condition} 测点 {point} 轮次 {round}".format(**counts)
        )
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    import os
    import sqlite3

    from pmc.catalog.items import load_items
    from pmc.errors import InputError
    from pmc.ingest import csvio, store

    if not os.path.isfile(args.db):
        raise InputError("台账 {0} 不存在：先跑 pmc init --db {0}".format(args.db))
    if not os.path.isfile(args.file):
        raise InputError(
            "观测文件 {0} 不存在：路径按仓库根或 --data-dir 相对定位".format(args.file.replace("\\", "/"))
        )
    data_dir = find_data_dir(args.data_dir)
    items = load_items(data_dir)
    is_xlsx = args.file.lower().endswith(".xlsx")
    if is_xlsx:
        from pmc.ingest import xlsx as xlsxio

        rows, sha256 = xlsxio.load_rows(args.file, len(csvio.HEADER))
    else:
        text, sha256 = csvio.load_file(args.file)
        rows = None

    conn = sqlite3.connect(args.db)
    try:
        from pmc.db.schema import missing_tables

        gaps = missing_tables(conn)
        if gaps:
            raise InputError("台账缺表：{0}（先 pmc init）".format(",".join(gaps)))
        project_id = store.project_id_for(conn, args.project)
        points = store.point_archive(conn, project_id)
        shared = dict(
            source_file=args.file,
            file_sha256=sha256,
            round_index=args.round,
            items=items,
            points=points,
        )
        #: CSV 与 XLSX 走同一套行级校验与同一组 reason_code（plan/09 §十），不长出第二套判读口径
        if is_xlsx:
            parsed = csvio.parse_rows(rows, **shared)
        else:
            parsed = csvio.parse_csv(text, **shared)
        receipt = store.write_batch(
            conn,
            project_code=args.project,
            round_index=args.round,
            parsed=parsed,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()

    print(
        "IMPORT_RECEIPT {0} file_sha256={1} 总行 {2} 入库 {3} 拒收 {4}{5}".format(
            receipt.source_file.replace("\\", "/"),
            receipt.file_sha256[:12],
            receipt.rows_total,
            receipt.rows_accepted,
            receipt.rows_rejected,
            "（dry-run，未写台账）" if args.dry_run else "",
        )
    )
    for item in receipt.rejections:
        print(
            "  REJECT row={0} reason={1} {2}".format(
                item.source_row, item.reason_code, item.detail
            )
        )
    return 0 if receipt.rows_rejected == 0 else EXIT_DEGRADED


def _cmd_ledger(args: argparse.Namespace) -> int:
    import os
    import sqlite3

    from pmc.errors import InputError
    from pmc.ingest import store

    if not os.path.isfile(args.db):
        raise InputError("台账 {0} 不存在：先跑 pmc init --db {0}".format(args.db))
    conn = sqlite3.connect(args.db)
    try:
        from pmc.db.schema import missing_tables

        gaps = missing_tables(conn)
        if gaps:
            raise InputError("台账缺表：{0}（先 pmc init）".format(",".join(gaps)))
        if args.project:
            store.project_id_for(conn, args.project)
        rows = store.ledger_query(
            conn,
            project_code=args.project,
            point_code=args.point,
            round_from=args.round_from,
            round_to=args.round_to,
        )
    finally:
        conn.close()

    if not rows:
        print("LEDGER_EMPTY 没有匹配的观测行", file=sys.stderr)
        return EXIT_INPUT_UNAVAILABLE
    print(
        "工程\t测点\t项目\t轮次\t日期\trev\t状态\t值\t单位\t工况\t加密"
    )
    for row in rows:
        if row["missing"]:
            value, state = "-", "缺测"
        else:
            value = "-" if row["value_cum"] is None else "{0}".format(row["value_cum"])
            state = "现行" if row["effective"] else "已被取代"
        print(
            "{0}\t{1}\t{2}\tR{3}\t{4}\t{5}\t{6}\t{7}\t{8}\t{9}\t{10}".format(
                row["project_code"],
                row["point_code"],
                row["item_code"],
                row["round_index"],
                row["observed_on"],
                row["revision_seq"],
                state,
                value,
                row["unit"] or "-",
                row["condition_code"] or "-",
                "是" if row["is_intensified"] else "",
            )
        )
    superseded = sum(1 for row in rows if not row["effective"])
    missing = sum(1 for row in rows if row["missing"])
    print(
        "共 {0} 行：现行 {1} / 被取代 {2} / 缺测 {3}".format(
            len(rows), len(rows) - superseded, superseded, missing
        )
    )
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    import os
    import sqlite3

    from pmc.alarm import engine
    from pmc.catalog.items import load_items
    from pmc.contract.clauses import load_register
    from pmc.contract.status import ALL_STATES
    from pmc.db.schema import missing_tables
    from pmc.errors import InputError
    from pmc.rules.loader import load_rulesets, load_rulesets_from

    if not os.path.isfile(args.db):
        raise InputError("台账 {0} 不存在：先跑 pmc init --db {0}".format(args.db))
    data_dir = find_data_dir(args.data_dir)
    items = load_items(data_dir)
    clauses = load_register(data_dir)
    if args.rules_dir:
        if not os.path.isdir(args.rules_dir):
            raise InputError(
                "规则集目录 {0} 不存在：夹具档位按工程各指一个目录（plan/09 §六）".format(args.rules_dir)
            )
        sets = load_rulesets_from(args.rules_dir)
    else:
        sets = load_rulesets(data_dir)
    rules = [rule for ruleset in sets for rule in ruleset.rules]

    conn = sqlite3.connect(args.db)
    try:
        gaps = missing_tables(conn)
        if gaps:
            raise InputError("台账缺表：{0}（先 pmc init）".format(",".join(gaps)))
        rows, counts = engine.run_check(
            conn,
            project_code=args.project,
            items=items,
            rules=rules,
            clauses=clauses,
            upto_round=args.round_index,
            write=not args.dry_run,
        )
    finally:
        conn.close()

    printed = [
        row
        for row in rows
        if args.round_index is None or row.record.key.round_index == args.round_index
    ]
    print(
        "CHECK_SCOPE 工程 {0} 判据自第 1 轮整段重算 {1} 行，打印 {2} 行；落库 新增 {3} / 更新 {4}{5}".format(
            args.project,
            len(rows),
            len(printed),
            counts["inserted"],
            counts["updated"],
            "（dry-run，未写 alarm_state）" if args.dry_run else "",
        )
    )
    summary = engine.state_counts(printed)
    print(
        "CHECK_SUMMARY " + " ".join(
            "{0}={1}".format(state, summary.get(state, 0)) for state in ALL_STATES
        )
    )
    print("工程\t测点\t项目\t轮次\t状态\t触发依据\t累计值/阈值\t速率值/阈值\t窗口\t来源\t条款号\t原因码")
    for row in printed:
        record = row.record
        print(
            "\t".join(
                (
                    record.key.project_code,
                    record.key.point_code,
                    record.key.item_code,
                    "R{0}".format(record.key.round_index),
                    record.state,
                    record.trigger_basis,
                    "{0}/{1}".format(_num(row.cum_value), _num(record.dual.cumulative.value)),
                    "{0}/{1}".format(_num(row.rate_value), _num(record.dual.rate.value)),
                    _num(row.window_days),
                    "{0}/{1}".format(*engine.row_source(record)),
                    ",".join(record.clause_ids) or "-",
                    ";".join(record.disabled_reasons) or "-",
                )
            )
        )
    unclosed = engine.unclosed_list(rows)
    print("UNCLOSED_LIST 未闭环报警 {0} 处".format(len(unclosed)))
    for entry in unclosed:
        print(
            "  {0} {1} 状态={2} 首个报警轮次=R{3} 已延续 {4} 轮 最新读数 R{5}{6}".format(
                entry["point_code"],
                entry["item_code"],
                entry["state"],
                entry["first_alarm_round_index"],
                entry["carried_rounds"],
                entry["latest_round_index"],
                "（本轮已回落，仍未处置）" if entry["fell_back"] else "（本轮仍超标）",
            )
        )
    return engine.degraded_exit(rows)


def _cmd_bench(args: argparse.Namespace) -> int:
    import json

    from pmc.bench import runner

    if args.all and args.sites:
        print("BENCH_INPUT --all 与 --sites 二选一", file=sys.stderr)
        return EXIT_INPUT_UNAVAILABLE
    data_dir = find_data_dir(args.data_dir)
    report = runner.run_bench(
        data_dir,
        plane=args.plane,
        sites=args.sites,
        seed=args.seed,
        write=args.write_golden,
    )
    if args.markdown:
        for line in report.markdown():
            print(line)
    elif args.json:
        sys.stdout.write(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        sys.stdout.write("\n")
    else:
        for line in runner.text_lines(report):
            print(line)
    return report.exit_code


def _cmd_report(args: argparse.Namespace) -> int:
    import os
    import sqlite3

    from pmc.db.schema import missing_tables
    from pmc.errors import InputError
    from pmc.report import builder as report_builder

    report_builder.assert_vocabulary()
    if not os.path.isfile(args.db):
        raise InputError("台账 {0} 不存在：先跑 pmc init --db {0}".format(args.db))
    data_dir = find_data_dir(args.data_dir)
    if args.round_index is not None and args.kind != "daily":
        raise InputError("--round 只与 --kind daily 搭配")
    if args.round_from is not None and args.round_to is not None and args.round_from > args.round_to:
        raise InputError("--from 不得大于 --to")

    bench_report = None
    if args.bench_plane:
        from pmc.bench import runner

        bench_report = runner.run_bench(data_dir, plane=args.bench_plane, seed=args.seed)

    out_dir = args.out or os.path.join("reports", "out")
    conn = sqlite3.connect(args.db)
    try:
        gaps = missing_tables(conn)
        if gaps:
            raise InputError("台账缺表：{0}（先 pmc init）".format(",".join(gaps)))
        result = report_builder.build_report(
            conn,
            args.project,
            args.kind,
            round_index=args.round_index,
            round_from=args.round_from,
            round_to=args.round_to,
            out_dir=out_dir,
            bench_report=bench_report,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()

    counters = result.counters
    print(
        "REPORT_{0} {1} sha256={2}{3}".format(
            "DRYRUN" if args.dry_run else "FILE",
            result.path.replace("\\", "/"),
            result.sha256[:12],
            "（dry-run，未落盘）" if args.dry_run else "",
        )
    )
    print("REPORT_SHEETS " + " | ".join(result.sheet_names))
    print(
        "REPORT_SCOPE 工程 {0} 形态 {1} 轮次 {2}：判定 {judged} 异常 {abnormal} 未闭环 {unclosed} "
        "待定值 {undetermined} 检核 {violations} 追溯 {traces} 过程线图 {chart_count}".format(
            result.project_code,
            result.kind,
            report_builder.scope_label(result.rounds),
            **counters
        )
    )
    print("REPORT_TRACES 每个数字回到台账行，见工作表 {0}".format(report_builder.SHEET_TRACE))
    if bench_report is not None:
        print(
            "REPORT_BENCH 逐起 {0} 起，退出码 {1}（结论用词与 bench 同源，报告未重算）".format(
                len(bench_report.events), bench_report.exit_code
            )
        )
    print("REPORT_BOUNDARY {0}".format(report_builder.DISCLAIMER))
    return result.exit_code


def _num(value) -> str:
    """待定值与缺测一律打印 `-`，不打印 0 —— 展示层也要守住 R2 纪律。"""
    if value is None:
        return "-"
    if isinstance(value, float):
        return "{0:.4g}".format(value)
    return "{0}".format(value)


def _cmd_audit(args: argparse.Namespace) -> int:
    import os
    import sqlite3

    from pmc.compliance import auditor
    from pmc.contract.clauses import load_register
    from pmc.db.schema import missing_tables
    from pmc.errors import InputError
    from pmc.rules.loader import load_rulesets, load_rulesets_from

    if not os.path.isfile(args.db):
        raise InputError("台账 {0} 不存在：先跑 pmc init --db {0}".format(args.db))
    data_dir = find_data_dir(args.data_dir)
    clauses = load_register(data_dir)
    if args.rules_dir:
        if not os.path.isdir(args.rules_dir):
            raise InputError(
                "规则集目录 {0} 不存在：频率分档按工程各指一个目录（plan/08 §三）".format(
                    args.rules_dir
                )
            )
        sets = load_rulesets_from(args.rules_dir)
    else:
        sets = load_rulesets(data_dir)
    rules = [rule for ruleset in sets for rule in ruleset.rules]

    conn = sqlite3.connect(args.db)
    try:
        gaps = missing_tables(conn)
        if gaps:
            raise InputError("台账缺表：{0}（先 pmc init）".format(",".join(gaps)))
        if args.project:
            codes = [args.project]
        else:
            codes = auditor.project_codes_with_rounds(conn)
            if not codes:
                raise InputError(
                    "台账里没有任何带轮次档案的工程：先 pmc synth --db 或 pmc import"
                )
        results = [
            auditor.run_audit(
                conn,
                project_code=code,
                rules=rules,
                clauses=clauses,
                round_from=args.round_from,
                round_to=args.round_to,
            )[0]
            for code in codes
        ]
    finally:
        conn.close()

    for result in results:
        span = result.round_span
        print(
            "AUDIT_SCOPE 工程 {0} 轮次档案 R{1}–R{2}，检核范围 {3} 轮（加密观测轮次 {4}）；"
            "落库 新增 {5} / 清除 {6}".format(
                result.project_code,
                span[0],
                span[1],
                result.rounds_audited,
                _round_labels(result.intensified_rounds),
                result.persisted.get("inserted", 0),
                result.persisted.get("deleted", 0),
            )
        )
        counts = result.counts_by_kind()
        print(
            "AUDIT_SUMMARY "
            + " ".join(
                "{0}={1}".format(kind, counts.get(kind, 0))
                for kind in auditor.VIOLATION_KINDS
            )
            + " 应核实事项 {0} 处".format(len(result.violations))
        )
        grouped = result.grouped_by_clause()
        for clause_id in sorted(grouped):
            print(
                "AUDIT_CLAUSE {0} 应核实 {1} 处".format(
                    clause_id, len(grouped[clause_id])
                )
            )
        print(
            "工程\t类别\t测点\t轮次\t规则\t条款号\t结论\t证据"
        )
        for item in result.violations:
            print(
                "\t".join(
                    (
                        result.project_code,
                        item.kind,
                        item.point_code or "-",
                        "-" if item.round_index is None else "R{0}".format(item.round_index),
                        item.rule_id,
                        ",".join(item.clause_ids),
                        auditor.VERDICT,
                        str(item.evidence.get("detail", _json_evidence(item))),
                    )
                )
            )
        if not result.violations:
            print("  （检核范围内没有应核实事项）")
    blocked = [(r.project_code,) + rest for r in results for rest in r.blocked]
    notes = [(r.project_code,) + rest for r in results for rest in r.notes]
    print(
        "AUDIT_QUEUE 不生效频率规则 {0} 条、检核缺口 {1} 处".format(
            len(blocked), len(notes)
        )
    )
    for project_code, rule_id, reason in sorted(blocked):
        print("  {0} {1} 不生效 reason={2}".format(project_code, rule_id, reason))
    for project_code, code, detail in notes:
        print("  {0} {1}：{2}".format(project_code, code, detail))
    return max(auditor.degraded_exit(result) for result in results)


def _round_labels(indexes) -> str:
    if not indexes:
        return "无"
    return "/".join("R{0}".format(index) for index in indexes)


def _json_evidence(item) -> str:
    import json

    return json.dumps(item.evidence, ensure_ascii=False, sort_keys=True)


def _cmd_gui(args: argparse.Namespace) -> int:
    """桌面入口：转交给 `pmc.gui.app.main`，参数原样拼回一条命令链（数据目录走根级旗标）。"""
    from pmc.gui.app import main as gui_main

    argv: List[str] = []
    if args.data_dir:
        argv.extend(["--data-dir", args.data_dir])
    if args.db:
        argv.extend(["--db", args.db])
    if args.project:
        argv.extend(["--project", args.project])
    if args.smoke:
        argv.append("--smoke")
    return gui_main(argv)


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
        "synth": _cmd_synth,
        "import": _cmd_import,
        "ledger": _cmd_ledger,
        "check": _cmd_check,
        "audit": _cmd_audit,
        "bench": _cmd_bench,
        "report": _cmd_report,
        "gui": _cmd_gui,
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
