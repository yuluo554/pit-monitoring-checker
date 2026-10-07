"""`pmc check` 的命令面：输出契约、--round 口径、--dry-run、退出码 0/1/2。

判定内核的行为在 `test_alarm_engine.py` / `test_alarm_state_machine.py`，
这里只管"一条命令能不能被脚本化验证"—— 这是 onedir exe 之外 CI 三连的前提。
"""

from __future__ import annotations

import os
import sqlite3

from _fixtures import apply_point_grades, ruleset_dir
from _helpers import DATA

from pmc import cli
from pmc.errors import EXIT_DEGRADED, EXIT_INPUT_UNAVAILABLE, EXIT_OK
from pmc.synth import freeze

CSV_BY_ROUND = {1: "round-01.csv", 2: "round-02.csv", 3: "round-03.csv"}
SITE = "SYN-LJ3"


def ledger(tmp_path, rounds=(1, 2, 3), graded=True):
    db = str(tmp_path / "cli.sqlite")
    assert cli.main(["init", "--db", db]) == EXIT_OK
    conn = sqlite3.connect(db)
    try:
        datas = freeze.build(str(DATA), 20260107, 1)[0]
        freeze.seed_ledger(conn, datas)
    finally:
        conn.close()
    for index in rounds:
        path = os.path.join(str(DATA), "raw", SITE, CSV_BY_ROUND[index])
        assert cli.main(
            ["import", path, "--project", SITE, "--round", str(index), "--db", db]
        ) == EXIT_OK
    if graded:
        conn = sqlite3.connect(db)
        try:
            assert apply_point_grades(conn, SITE) == 196
        finally:
            conn.close()
    return db


def lower_cum(db, value, where="item_code = 'top_h_disp'"):
    """把某项的累计控制值压到基线之下，制造"第 2 轮起报警并延续"的场景。"""
    conn = sqlite3.connect(db)
    try:
        apply_point_grades(conn, SITE)
        conn.execute("UPDATE point SET design_cum_value = ? WHERE " + where, (value,))
        conn.commit()
    finally:
        conn.close()


class Captured:
    def __init__(self, out, err):
        self.out = out
        self.err = err


def run_check(db, capsys, *extra):
    capsys.readouterr()
    code = cli.main(["check", "--db", db, "--project", SITE] + list(extra))
    seen = capsys.readouterr()
    return code, Captured(seen.out, seen.err)


def test_check_prints_header_summary_and_one_line_per_point(tmp_path, capsys):
    db = ledger(tmp_path)
    code, out = run_check(
        db, capsys, "--rules-dir", ruleset_dir(SITE), "--round", "3"
    )
    assert code == EXIT_OK
    lines = out.out.splitlines()
    assert lines[0].startswith("CHECK_SCOPE")
    assert lines[1].startswith("CHECK_SUMMARY normal=196"), lines[1]
    assert "undetermined=0" in lines[1] and "alarm=0" in lines[1]
    assert lines[2].startswith("工程\t测点\t项目\t轮次")
    body = [line for line in lines[3:] if line.startswith("SYN-LJ3\t")]
    assert len(body) == 196, "打印范围就是第 3 轮，每测点一行"
    assert all("\tR3\t" in line for line in body)


def test_check_without_rules_dir_is_all_undetermined_and_degraded(tmp_path, capsys):
    """生产数据面一条阈值都没核对：全待定值，退出码 1，阈值列打印 `-` 而不是 0。"""
    db = ledger(tmp_path, graded=False)
    code, seen = run_check(db, capsys, "--round", "2")
    assert code == EXIT_DEGRADED
    text = seen.out
    assert "undetermined=196" in text
    assert "alarm=0" in text
    assert "/-\t-/-" in text, "待定值的两列阈值必须打 `-`"
    assert "/0.0" not in text, "待定值不得被打印成 0"
    assert "source_not_confirmed" in text or "no_source" in text


def test_round_flag_still_judges_from_round_one(tmp_path, capsys):
    """`--round N` 只筛打印：判据必须从第 1 轮算起，否则未闭环无从延续。"""
    db = ledger(tmp_path)
    lower_cum(db, 0.3)
    code, seen = run_check(db, capsys, "--round", "3", "--rules-dir", ruleset_dir(SITE))
    assert code == EXIT_DEGRADED, "有未闭环报警就是降级"
    scope = seen.out.splitlines()[0]
    assert "整段重算 588 行" in scope, "重算行数覆盖 196 测点 × 3 轮"
    assert "打印 196 行" in scope
    body = [line for line in seen.out.splitlines() if line.startswith("SYN-LJ3\t")]
    assert all("\tR3\t" in line for line in body)
    assert "首个报警轮次=R2" in seen.out


def test_unclosed_list_names_the_origin_and_says_whether_it_fell_back(tmp_path, capsys):
    db = ledger(tmp_path)
    lower_cum(db, 0.3, where="code = 'SYN-TH-01'")
    code, out = run_check(db, capsys, "--rules-dir", ruleset_dir(SITE))
    assert code == EXIT_DEGRADED
    assert "UNCLOSED_LIST 未闭环报警 1 处" in out.out
    assert "SYN-TH-01" in out.out and "已延续 2 轮" in out.out
    assert "本轮仍超标" in out.out


def test_dry_run_leaves_alarm_state_empty(tmp_path, capsys):
    db = ledger(tmp_path)
    code, out = run_check(
        db, capsys, "--rules-dir", ruleset_dir(SITE), "--dry-run", "--round", "3"
    )
    assert code == EXIT_OK
    assert "dry-run，未写 alarm_state" in out.out
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM alarm_state").fetchone()[0] == 0
    finally:
        conn.close()


def test_missing_rules_dir_is_input_unavailable(tmp_path, capsys):
    db = ledger(tmp_path, rounds=(1,))
    code, seen = run_check(db, capsys, "--rules-dir", str(tmp_path / "nope"))
    assert code == EXIT_INPUT_UNAVAILABLE
    assert "规则集目录" in seen.err


def test_project_without_observations_is_input_unavailable(tmp_path, capsys):
    db = str(tmp_path / "empty.sqlite")
    assert cli.main(["init", "--db", db]) == EXIT_OK
    conn = sqlite3.connect(db)
    try:
        datas = freeze.build(str(DATA), 20260107, 1)[0]
        freeze.seed_ledger(conn, datas)
    finally:
        conn.close()
    code, seen = run_check(db, capsys)
    assert code == EXIT_INPUT_UNAVAILABLE
    assert "没有任何链上有效读数" in seen.err


def test_check_persists_rows_that_ledger_and_m3_can_read(tmp_path, capsys):
    """判定结果必须落在台账里：`observation_id` 与 `first_alarm_round_id` 都是外键，
    M3 的"报警后未加密观测"与 M5 的追溯清单都读它们。"""
    db = ledger(tmp_path)
    code, _ = run_check(db, capsys, "--rules-dir", ruleset_dir(SITE))
    assert code == EXIT_OK
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT a.state, a.observation_id, a.window_days, a.item_code, a.clause_ids"
            " FROM alarm_state a ORDER BY a.point_id, a.round_id LIMIT 1"
        ).fetchone()
        stored = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(unclosed), 0) FROM alarm_state"
        ).fetchone()
        orphans = conn.execute(
            "SELECT COUNT(*) FROM alarm_state a LEFT JOIN observation o ON o.id = a.observation_id"
            " WHERE o.id IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert row[0] in ("normal", "prewarning")
    assert row[1] is not None and orphans == 0
    assert row[2] in (None, 3.0)
    assert stored == (588, 0), "3 轮 × 196 测点全部落库且零未闭环"
