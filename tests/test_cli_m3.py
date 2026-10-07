"""`pmc audit` 的命令面与端到端结论：四类时序检核在三座虚拟基坑上真的对得上。

生产数据面（`data/clauses` 全 pending）与夹具数据面（`tests/fixtures/data_freq`）各跑一遍：
前者证明"没有已核对依据就一条结论都不出"，后者证明通路本身能出四类结论（plan/08 §五、§八）。
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Dict, List

import pytest
from _fixtures import apply_point_grades, freq_data_dir, ruleset_dir
from _helpers import DATA

from pmc import cli
from pmc.errors import EXIT_DEGRADED, EXIT_INPUT_UNAVAILABLE, EXIT_OK
from pmc.synth import freeze

SEED = 20260107
ROUNDS = {"SYN-LJ3": 8, "SYN-ZHDQ": 20, "SYN-YYCG": 30}
#: 导入过观测数据的工程：`SYN-LJ3` 故意一行都不导入，用来验"整轮没入库"的通路
IMPORTED = ("SYN-ZHDQ", "SYN-YYCG")


@pytest.fixture(scope="session")
def ledger(tmp_path_factory):
    """建库 → 建档 → 导入两座基坑全部轮次 → 挂夹具档位 → 跑判定，一次成型。"""
    db = str(tmp_path_factory.mktemp("m3cli") / "ledger.sqlite")
    assert cli.main(["init", "--db", db]) == EXIT_OK
    conn = sqlite3.connect(db)
    try:
        datas = freeze.build(str(DATA), SEED, 3)[0]
        freeze.seed_ledger(conn, datas)
    finally:
        conn.close()
    for site in IMPORTED:
        for index in range(1, ROUNDS[site] + 1):
            path = os.path.join(str(DATA), "raw", site, "round-{0:02d}.csv".format(index))
            assert cli.main(
                ["import", path, "--project", site, "--round", str(index), "--db", db]
            ) in (EXIT_OK, EXIT_DEGRADED)
    conn = sqlite3.connect(db)
    try:
        for site in IMPORTED:
            assert apply_point_grades(conn, site) == 196
    finally:
        conn.close()
    for site in IMPORTED:
        assert cli.main(
            ["check", "--db", db, "--project", site, "--rules-dir", ruleset_dir(site)]
        ) in (EXIT_OK, EXIT_DEGRADED)
    yield db
    os.remove(db)


def run_audit(db, capsys, *extra, data_dir=None):
    """`--data-dir` 是全局参数，argparse 要求它出现在子命令之前。"""
    argv = (["--data-dir", data_dir] if data_dir else []) + ["audit", "--db", db] + list(extra)
    capsys.readouterr()
    code = cli.main(argv)
    seen = capsys.readouterr()
    return code, seen.out, seen.err


def summary(out: str) -> Dict[str, int]:
    line = next(l for l in out.splitlines() if l.startswith("AUDIT_SUMMARY"))
    cells = line.split("\t") if "\t" in line else line.split()
    counts = {}
    for token in cells:
        if "=" in token:
            key, _, value = token.partition("=")
            if key in ("missed", "over_interval", "stale_frequency", "no_intensified_after_alarm"):
                counts[key] = int(value)
    return counts


def body_rows(out: str, project: str) -> List[List[str]]:
    return [
        line.split("\t")
        for line in out.splitlines()
        if line.startswith(project + "\t")
    ]


def rows_of(out: str, project: str, kind: str) -> List[List[str]]:
    return [row for row in body_rows(out, project) if row[1] == kind]


# ---- 生产数据面：没有已核对依据就不出结论 --------------------------------------


def test_production_audit_reports_nothing_but_the_queue(ledger, capsys):
    code, out, _err = run_audit(ledger, capsys, "--project", "SYN-YYCG")
    assert code == EXIT_DEGRADED, "有规则被门控挡住就是降级"
    assert summary(out) == {
        "missed": 0,
        "over_interval": 0,
        "stale_frequency": 0,
        "no_intensified_after_alarm": 0,
    }
    assert body_rows(out, "SYN-YYCG") == []
    assert "不生效频率规则 6 条" in out
    for rule_id in (
        "FREQ-MAX-INTERVAL-D5",
        "FREQ-MAX-INTERVAL-D10",
        "FREQ-MAX-INTERVAL-DEEP",
        "FREQ-CONDITION-CHANGE",
        "FREQ-INTENSIFY-AFTER-ALARM",
        "FREQ-MISSED-ROUND",
    ):
        assert rule_id in out, "{0} 未进应核实队列".format(rule_id)
    assert "clause_not_verified" in out and "source_not_confirmed" in out


# ---- 夹具数据面：四类都触发 ----------------------------------------------------


def test_weekly_cadence_site_triggers_all_four_kinds(ledger, capsys):
    code, out, _err = run_audit(
        ledger, capsys, "--project", "SYN-YYCG", data_dir=freq_data_dir()
    )
    assert code == EXIT_DEGRADED
    counts = summary(out)
    assert counts == {
        "missed": 2,
        "over_interval": 19,
        "stale_frequency": 3,
        "no_intensified_after_alarm": 2,
    }, "三站档案是按这四类反例设计的，数量对不上就是检核口径漂移"
    assert "检核范围 30 轮（加密观测轮次 无）" in out


def test_condition_change_rounds_are_judged_against_the_new_frequency(ledger, capsys):
    _code, out, _err = run_audit(ledger, capsys, "--project", "SYN-YYCG", data_dir=freq_data_dir())
    stale = rows_of(out, "SYN-YYCG", "stale_frequency")
    assert [row[3] for row in stale] == ["R9", "R17", "R25"], "工况变更当轮即按新频率判"
    for row in stale:
        assert row[4] == "FREQ-CONDITION-CHANGE"
        assert "GB50497-2019:monitoring-frequency" in row[5]
        assert row[6] == "应核实"
    # 第一段（C1，深度 5 m，周报本就合规）没有结论行：结论存在，只是"不超限"
    assert [row[3] for row in rows_of(out, "SYN-YYCG", "over_interval")][0] == "R10"


def test_missed_covers_both_missing_mark_and_rejected_unit_row(ledger, capsys):
    _code, out, _err = run_audit(ledger, capsys, "--project", "SYN-ZHDQ", data_dir=freq_data_dir())
    missed = rows_of(out, "SYN-ZHDQ", "missed")
    marks = {row[2]: row[7] for row in missed}
    assert marks == {
        "SYN-TH-15": "该轮该测点链上无有效读数：无入库行",
        "SYN-PF-09": "该轮该测点链上无有效读数：带缺测标记",
    }, "单位错误行被拒收后该轮就是空、缺测标记不许被插值掩盖"


def test_intensified_observation_conclusions_are_opposite_on_the_two_sites(ledger, capsys):
    """同型场景（报警后的加密观测）在两站结论相反：ZHDQ 有加密轮次并覆盖了前两处报警。"""
    _code, zhdq, _err = run_audit(ledger, capsys, "--project", "SYN-ZHDQ", data_dir=freq_data_dir())
    assert "加密观测轮次 R10/R11/R12" in zhdq
    uncovered_zhdq = {row[2] for row in rows_of(zhdq, "SYN-ZHDQ", "no_intensified_after_alarm")}
    assert uncovered_zhdq == {"SYN-DH-05", "SYN-TV-07"}, "R9/R11 两处报警已被 R10–R12 覆盖"

    _code, yycg, _err = run_audit(ledger, capsys, "--project", "SYN-YYCG", data_dir=freq_data_dir())
    uncovered_yycg = {row[2] for row in rows_of(yycg, "SYN-YYCG", "no_intensified_after_alarm")}
    assert uncovered_yycg == {"SYN-PL-04", "SYN-TH-11"}, "全程零加密轮次：每处报警都要点名"
    for row in rows_of(yycg, "SYN-YYCG", "no_intensified_after_alarm"):
        assert row[4] == "FREQ-INTENSIFY-AFTER-ALARM"
        assert "MOHURD-37" in row[5]


def test_zhdq_weekly_cadence_is_clean_and_still_lists_alarms(ledger, capsys):
    """隔日站合规：零间隔违规，但漏测与未加密照样点名 —— 检核不是报告生成器。"""
    code, out, _err = run_audit(ledger, capsys, "--project", "SYN-ZHDQ", data_dir=freq_data_dir())
    assert code == EXIT_DEGRADED
    counts = summary(out)
    assert counts["over_interval"] == 0 and counts["stale_frequency"] == 0
    assert counts["missed"] == 2 and counts["no_intensified_after_alarm"] == 2


def test_violations_are_grouped_by_clause_in_the_output(ledger, capsys):
    _code, out, _err = run_audit(ledger, capsys, "--project", "SYN-YYCG", data_dir=freq_data_dir())
    groups = {
        line.split()[1]: int(line.split("应核实 ")[1].split(" 处")[0])
        for line in out.splitlines()
        if line.startswith("AUDIT_CLAUSE")
    }
    assert groups == {
        "GB50497-2019:monitoring-frequency": 26,
        "MOHURD-37": 2,
    }, "只有「报警后未加密观测」同时挂管理条款"


def test_audit_output_only_says_should_verify(ledger, capsys):
    """结论列只写"应核实"：不认定违规、不判定基坑安全（题面 §六.4）。"""
    _code, out, _err = run_audit(ledger, capsys, "--project", "SYN-YYCG", data_dir=freq_data_dir())
    assert "应核实" in out
    for phrase in ("已确认违规", "认定违规", "已认定超标", "判定为违规", "确认超标"):
        assert phrase not in out, out


# ---- 落库、范围与重放 ----------------------------------------------------------


def test_persisted_rows_carry_rule_clause_and_recomputable_evidence(ledger, capsys):
    code, _out, _err = run_audit(ledger, capsys, "--project", "SYN-YYCG", data_dir=freq_data_dir())
    assert code == EXIT_DEGRADED
    conn = sqlite3.connect(ledger)
    try:
        rows = conn.execute(
            "SELECT kind, rule_id, clause_ids, evidence_json, round_id, point_id"
            " FROM violation WHERE project_id = (SELECT id FROM project WHERE code='SYN-YYCG')"
            " ORDER BY kind, round_id, point_id"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 26
    for kind, rule_id, clause_ids, evidence, _round_id, _point_id in rows:
        assert kind in (
            "missed", "over_interval", "stale_frequency", "no_intensified_after_alarm",
        )
        assert rule_id and clause_ids, "无依据的检核结论不得进台账"
        doc = json.loads(evidence)
        assert doc.get("detail")
        if kind in ("over_interval", "stale_frequency"):
            assert {"gap_days", "limit_days", "condition_code", "prev_observed_on"} <= set(doc)
        if kind == "no_intensified_after_alarm":
            assert {"first_alarm_round_index", "unclosed_rounds"} <= set(doc)


def test_rerun_replaces_the_list_instead_of_accumulating(ledger, capsys):
    args = ("--project", "SYN-YYCG")
    run_audit(ledger, capsys, *args, data_dir=freq_data_dir())
    _code, out, _err = run_audit(ledger, capsys, *args, data_dir=freq_data_dir())
    assert "落库 新增 26 / 清除 26" in out
    conn = sqlite3.connect(ledger)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM violation"
            " WHERE project_id = (SELECT id FROM project WHERE code='SYN-YYCG')"
        ).fetchone()[0] == 26
    finally:
        conn.close()


def test_round_scope_filters_the_list_but_keeps_the_previous_round(ledger, capsys):
    _code, out, _err = run_audit(
        ledger, capsys, "--project", "SYN-YYCG", "--from", "18", "--to", "22", data_dir=freq_data_dir()
    )
    rows = body_rows(out, "SYN-YYCG")
    assert rows, "范围内应有结论"
    assert all(18 <= int(row[3][1:]) <= 22 for row in rows if row[3] != "-"), rows
    for row in rows_of(out, "SYN-YYCG", "over_interval"):
        assert "间隔 7 天" in row[7], "范围只筛清单，间隔仍按真实前一轮算"


def test_project_without_any_imported_round_says_the_round_was_not_imported(ledger, capsys):
    """`SYN-LJ3` 有轮次档案但一行没导入：出一条工程级应核实事项，而不是 196 条测点级。"""
    _code, out, _err = run_audit(ledger, capsys, "--project", "SYN-LJ3", data_dir=freq_data_dir())
    missed = rows_of(out, "SYN-LJ3", "missed")
    assert len(missed) == 8
    assert all(row[2] == "-" for row in missed)
    assert "台账内一行观测都没有" in missed[0][7]


def test_audit_without_project_covers_every_project_with_rounds(ledger, capsys):
    code, out, _err = run_audit(ledger, capsys, data_dir=freq_data_dir())
    assert code == EXIT_DEGRADED
    scopes = [l for l in out.splitlines() if l.startswith("AUDIT_SCOPE")]
    assert len(scopes) == 3, "三座基坑都建档了轮次，就该各出一条检核范围"


# ---- 失败路径 ------------------------------------------------------------------


def test_unknown_project_is_input_unavailable(ledger, capsys):
    code, _out, err = run_audit(ledger, capsys, "--project", "SYN-NOPE")
    assert code == EXIT_INPUT_UNAVAILABLE
    assert "不在台账里" in err


def test_missing_rules_dir_is_input_unavailable(ledger, capsys):
    code, _out, err = run_audit(
        ledger, capsys, "--project", "SYN-YYCG", "--rules-dir", "nope/not/here"
    )
    assert code == EXIT_INPUT_UNAVAILABLE
    assert "规则集目录" in err


def test_missing_database_is_input_unavailable(tmp_path, capsys):
    code, _out, err = run_audit(str(tmp_path / "absent.sqlite"), capsys, "--project", "SYN-YYCG")
    assert code == EXIT_INPUT_UNAVAILABLE
    assert "不存在" in err
