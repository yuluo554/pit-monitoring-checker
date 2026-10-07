"""M2 回归：夹具档位下，植入的每一起事件都要被真值的 `expected_first_alarm_round` 命中。

这是 `07 §九` 生成器自检的另一半：生成器保证"数据与期望自洽"，本文件保证"判定与期望对得上"。
`SYN-LJ3` 的 1568 行零事件序列同时是误报率的分母 —— 它一条 alarm 都不许出。
"""

from __future__ import annotations

import csv
import os
import sqlite3
import sys
from typing import Dict, List, Optional, Tuple

import pytest
from _fixtures import apply_point_grades, grades, load_rules, ruleset_dir
from _helpers import DATA, ROOT

sys.path.insert(0, str(ROOT / "src"))

from pmc import cli
from pmc.synth import freeze

SEED = 20260107
SITES = ("SYN-LJ3", "SYN-ZHDQ", "SYN-YYCG")
ROUNDS = {"SYN-LJ3": 8, "SYN-ZHDQ": 20, "SYN-YYCG": 30}
#: 真值面里"应报警"的事件数：改了生成器就必须先在这里归因，别让它悄悄缩水
EXPECTED_ALARM_EVENTS = {"SYN-ZHDQ": 4, "SYN-YYCG": 2}


def _ledger_dir(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("m2reg") / "ledger.sqlite")
    code = cli.main(["init", "--db", path])
    assert code == 0
    return path


@pytest.fixture(scope="session")
def ledger(tmp_path_factory):
    """建库 → 建档 → 导入三座基坑全部轮次 → 挂夹具档位，一次成型给整组回归复用。"""
    db = _ledger_dir(tmp_path_factory)
    conn = sqlite3.connect(db)
    try:
        datas = freeze.build(str(DATA), SEED, 3)[0]
        freeze.seed_ledger(conn, datas)
    finally:
        conn.close()
    for site in SITES:
        for round_index in range(1, ROUNDS[site] + 1):
            path = os.path.join(str(DATA), "raw", site, "round-{0:02d}.csv".format(round_index))
            code = cli.main(
                ["import", path, "--project", site, "--round", str(round_index), "--db", db]
            )
            assert code in (0, 1), "导入 {0} R{1} 退出码 {2}".format(site, round_index, code)
    conn = sqlite3.connect(db)
    try:
        for site in SITES:
            applied = apply_point_grades(conn, site)
            assert applied == 196, "工程 {0} 夹具档位挂到 {1} 个测点".format(site, applied)
    finally:
        conn.close()
    yield db
    os.remove(db)


def truth_rows(site: str) -> List[Dict[str, str]]:
    path = os.path.join(str(DATA), "truth", site + ".truth.csv")
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _state(ledger_path: str, site: str, point_code: str, round_index: int) -> Optional[Dict]:
    conn = sqlite3.connect(ledger_path)
    try:
        row = conn.execute(
            "SELECT a.state, a.trigger_basis, a.unclosed, a.first_alarm_round_id,"
            " a.cum_value, a.cum_threshold, a.rate_value, a.rate_threshold, a.window_days,"
            " a.threshold_source_kind, a.clause_ids, a.disabled_reasons, a.observation_id"
            " FROM alarm_state a"
            " JOIN point p ON p.id = a.point_id AND p.code = ? AND p.project_id ="
            "  (SELECT id FROM project WHERE code = ?)"
            " JOIN obs_round r ON r.id = a.round_id AND r.round_index = ?",
            (point_code, site, round_index),
        ).fetchone()
        if row is None:
            return None
        keys = (
            "state", "trigger_basis", "unclosed", "first_alarm_round_id", "cum_value",
            "cum_threshold", "rate_value", "rate_threshold", "window_days",
            "threshold_source_kind", "clause_ids", "disabled_reasons", "observation_id",
        )
        return dict(zip(keys, row))
    finally:
        conn.close()


def _run_check(site: str, ledger_path: str) -> int:
    return cli.main(
        ["check", "--db", ledger_path, "--project", site, "--rules-dir", ruleset_dir(site)]
    )


def _alarm_rounds(ledger_path: str, site: str, point_code: str) -> List[int]:
    conn = sqlite3.connect(ledger_path)
    try:
        rows = conn.execute(
            "SELECT r.round_index FROM alarm_state a"
            " JOIN point p ON p.id = a.point_id AND p.code = ? AND p.project_id ="
            "  (SELECT id FROM project WHERE code = ?)"
            " JOIN obs_round r ON r.id = a.round_id"
            " WHERE a.state IN ('alarm','alarm_confirmed') ORDER BY r.round_index",
            (point_code, site),
        ).fetchall()
        return [int(r[0]) for r in rows]
    finally:
        conn.close()


@pytest.mark.parametrize("site", SITES)
def test_check_runs_and_writes_every_row(site, ledger):
    code = _run_check(site, ledger)
    conn = sqlite3.connect(ledger)
    try:
        judged = conn.execute(
            "SELECT COUNT(*) FROM alarm_state a JOIN point p ON p.id = a.point_id"
            " JOIN project pr ON pr.id = p.project_id WHERE pr.code = ?",
            (site,),
        ).fetchone()[0]
        effective = conn.execute(
            "SELECT COUNT(*) FROM observation o"
            " JOIN point p ON p.id = o.point_id JOIN project pr ON pr.id = p.project_id"
            " JOIN obs_round r ON r.id = o.round_id"
            " WHERE pr.code = ? AND o.superseded_by IS NULL AND o.missing = 0",
            (site,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert judged == effective, "每个链上有效读数都要出一行判定"
    assert code in (0, 1)


def test_lj3_zero_event_sequence_produces_no_alarm(ledger):
    """误报率的分母：1568 行零事件序列只准出 normal/prewarning，一条 alarm 都不许有。"""
    assert _run_check("SYN-LJ3", ledger) == 0, "LJ3 全序列可判且无未闭环，退出码必须是 0"
    conn = sqlite3.connect(ledger)
    try:
        states = conn.execute(
            "SELECT a.state, COUNT(*) FROM alarm_state a"
            " JOIN point p ON p.id = a.point_id WHERE p.project_id ="
            "  (SELECT id FROM project WHERE code='SYN-LJ3') GROUP BY a.state"
            " ORDER BY a.state",
        ).fetchall()
    finally:
        conn.close()
    counts = dict(states)
    assert sum(counts.values()) == 1568
    assert counts.get("normal", 0) + counts.get("prewarning", 0) == 1568, counts
    for state in ("alarm", "alarm_confirmed", "alarm_handled", "undetermined"):
        assert counts.get(state, 0) == 0, state


@pytest.mark.parametrize("site", ("SYN-ZHDQ", "SYN-YYCG"))
def test_every_planted_alarm_hits_its_expected_round(site, ledger):
    assert _run_check(site, ledger) == 1, "有未闭环报警就是降级，退出码 1"
    rows = truth_rows(site)
    expected_total = sum(1 for row in rows if row["expected_first_alarm_round"])
    assert expected_total == EXPECTED_ALARM_EVENTS[site], "真值面自己变了，先归因再改断言"
    checked = 0
    for row in rows:
        expected = row["expected_first_alarm_round"]
        point = row["point_id"]
        tokens = set(filter(None, row["also_expect"].split(";")))
        if not expected:
            assert "no_alarm" in tokens or "no_row" in tokens, row
            continue
        round_index = int(expected)
        checked += 1
        judged = _state(ledger, site, point, round_index)
        assert judged is not None, "真值 {0} 的期望轮次 R{1} 没有判定行".format(row["event_id"], round_index)
        assert judged["state"] == "alarm", judged
        if "alarm_basis=both" in tokens:
            assert judged["trigger_basis"] == "both", judged
        elif "alarm_basis=rate" in tokens:
            assert judged["trigger_basis"] == "rate", judged
        elif "alarm_basis=cumulative" in tokens:
            assert judged["trigger_basis"] == "cumulative", judged
        assert judged["unclosed"] == 1
        assert judged["first_alarm_round_id"] is not None
        #: 期望轮次之前不得提前报警，否则"首超定位误差"就是假的
        earlier = [r for r in _alarm_rounds(ledger, site, point) if r < round_index]
        assert not earlier, "{0} 在期望轮次之前已报警：{1}".format(row["event_id"], earlier)
        assert judged["threshold_source_kind"] == "user_input"
        assert judged["clause_ids"], "判定必须挂条款号"
    assert checked == expected_total, "应报警事件 {0} 起，实际校验 {1} 起".format(expected_total, checked)


def test_unclosed_carries_while_values_fall_back(ledger):
    """`SYN-PL-04` 第 18 轮报警后，19/20/21 轮数值已回落仍带 unclosed，且起点仍指 18。"""
    assert _run_check("SYN-YYCG", ledger) == 1
    origin = _state(ledger, "SYN-YYCG", "SYN-PL-04", 18)
    assert origin["state"] == "alarm"
    assert origin["first_alarm_round_id"] is not None
    origin_round_id = origin["first_alarm_round_id"]
    conn = sqlite3.connect(ledger)
    try:
        first_index = conn.execute(
            "SELECT round_index FROM obs_round WHERE id = ?", (origin_round_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert int(first_index) == 18
    for round_index in (19, 20, 21):
        row = _state(ledger, "SYN-YYCG", "SYN-PL-04", round_index)
        assert row is not None, "回落轮次 R{0} 也要出判定行".format(round_index)
        assert row["unclosed"] == 1, row
        assert row["first_alarm_round_id"] == origin_round_id, row
        assert abs(row["cum_value"]) < row["cum_threshold"], (
            "R{0} 数值应已回落到累计控制值之下：{1} vs {2}".format(
                round_index, row["cum_value"], row["cum_threshold"]
            )
        )
        assert row["rate_value"] < row["rate_threshold"], row


def test_missing_round_produces_no_row_and_no_interpolation(ledger):
    """缺测轮次不出判定行，也不许被补值 —— 状态机跳过它，速率窗口只认实际存在的读数。"""
    assert _run_check("SYN-ZHDQ", ledger) == 1
    assert _state(ledger, "SYN-ZHDQ", "SYN-PF-09", 16) is None
    conn = sqlite3.connect(ledger)
    try:
        heads = conn.execute(
            "SELECT o.missing, o.value_cum, o.raw_text FROM observation o"
            " JOIN point p ON p.id = o.point_id JOIN obs_round r ON r.id = o.round_id"
            " WHERE p.code='SYN-PF-09' AND p.project_id=(SELECT id FROM project WHERE code='SYN-ZHDQ')"
            " AND r.round_index=16",
        ).fetchone()
    finally:
        conn.close()
    assert heads is not None and heads[0] == 1 and heads[1] is None
    assert heads[2] == "未测"
    #: 缺测轮的下一个读数照常判定，且窗口跨过的就是空洞
    after = _state(ledger, "SYN-ZHDQ", "SYN-PF-09", 17)
    assert after is not None and after["state"] in ("normal", "prewarning")


def test_judgment_reads_only_chain_head(ledger):
    """`SYN-TH-18` 第 9 轮：按修正值 26.0 判超，按首报 8.3 判正常 —— 取错行就会漏报。"""
    assert _run_check("SYN-ZHDQ", ledger) == 1
    row = _state(ledger, "SYN-ZHDQ", "SYN-TH-18", 9)
    assert row["state"] == "alarm"
    assert row["cum_value"] == pytest.approx(26.0)
    conn = sqlite3.connect(ledger)
    try:
        revisions = conn.execute(
            "SELECT o.id, o.revision_seq, o.value_cum, o.superseded_by FROM observation o"
            " JOIN point p ON p.id = o.point_id JOIN obs_round r ON r.id = o.round_id"
            " WHERE p.code='SYN-TH-18' AND r.round_index=9"
            " AND p.project_id=(SELECT id FROM project WHERE code='SYN-ZHDQ')"
            " ORDER BY o.revision_seq",
        ).fetchall()
    finally:
        conn.close()
    assert [r[1] for r in revisions] == [1, 2]
    assert revisions[0][2] == pytest.approx(8.3) and revisions[0][3] is not None
    assert revisions[1][2] == pytest.approx(26.0) and revisions[1][3] is None
    #: 首报值 8.3 低于 25.0 控制值：判定行必须挂在链上最新那一行（26.0），否则这条报警就漏了
    assert row["observation_id"] == revisions[1][0]


def test_unit_error_row_never_reaches_the_judgment(ledger):
    """单位错误的行在入库前就被拒收，判定层看不到它：该轮不出行，下一轮窗口跨过空洞。"""
    assert _run_check("SYN-ZHDQ", ledger) == 1
    assert _state(ledger, "SYN-ZHDQ", "SYN-TH-15", 12) is None
    assert _state(ledger, "SYN-ZHDQ", "SYN-TH-15", 13) is not None


def test_outlier_persists_without_alarm(ledger):
    assert _run_check("SYN-ZHDQ", ledger) == 1
    assert _alarm_rounds(ledger, "SYN-ZHDQ", "SYN-WL-12") == []
    row = _state(ledger, "SYN-ZHDQ", "SYN-WL-12", 6)
    assert row["state"] in ("normal", "prewarning")


def test_check_is_reproducible_row_by_row(ledger):
    """整段重算必须可复现：同库两次 check 的判定面逐列一致。"""
    assert _run_check("SYN-ZHDQ", ledger) == 1
    conn = sqlite3.connect(ledger)
    try:
        first = conn.execute(
            "SELECT p.code, r.round_index, a.item_code, a.state, a.trigger_basis,"
            " a.cum_value, a.cum_threshold, a.rate_value, a.rate_threshold, a.unclosed,"
            " a.first_alarm_round_id, a.disabled_reasons FROM alarm_state a"
            " JOIN point p ON p.id = a.point_id JOIN obs_round r ON r.id = a.round_id"
            " WHERE a.project_id = (SELECT id FROM project WHERE code='SYN-ZHDQ')"
            " ORDER BY p.code, r.round_index, a.item_code",
        ).fetchall()
    finally:
        conn.close()
    assert _run_check("SYN-ZHDQ", ledger) == 1
    conn = sqlite3.connect(ledger)
    try:
        second = conn.execute(
            "SELECT p.code, r.round_index, a.item_code, a.state, a.trigger_basis,"
            " a.cum_value, a.cum_threshold, a.rate_value, a.rate_threshold, a.unclosed,"
            " a.first_alarm_round_id, a.disabled_reasons FROM alarm_state a"
            " JOIN point p ON p.id = a.point_id JOIN obs_round r ON r.id = a.round_id"
            " WHERE a.project_id = (SELECT id FROM project WHERE code='SYN-ZHDQ')"
            " ORDER BY p.code, r.round_index, a.item_code",
        ).fetchall()
    finally:
        conn.close()
    assert first == second and len(first) == 3918


def test_undetermined_rows_blank_their_thresholds(ledger, capsys):
    """摘掉夹具档位（回到生产数据面的"全是待定值"通路）：阈值列全空且带原因码。"""
    db = os.path.join(os.path.dirname(ledger), "undetermined.sqlite")
    conn = sqlite3.connect(db)
    try:
        datas = freeze.build(str(DATA), SEED, 1)[0]
        freeze.seed_ledger(conn, datas)
    finally:
        conn.close()
    path = os.path.join(str(DATA), "raw", "SYN-LJ3", "round-03.csv")
    assert cli.main(["import", path, "--project", "SYN-LJ3", "--round", "3", "--db", db]) == 0
    code = cli.main(["check", "--db", db, "--project", "SYN-LJ3"])
    capsys.readouterr()
    assert code == 1, "全是待定值就是降级，不能报 0"
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT state, trigger_basis, cum_threshold, rate_threshold,"
            " threshold_source_kind, disabled_reasons, clause_ids, cum_value"
            " FROM alarm_state"
        ).fetchall()
    finally:
        conn.close()
    assert rows, "档案与规则都在，但一条阈值都没核对：仍要逐行出待定值"
    for state, basis, cum_thr, rate_thr, kind, reasons, clauses, cum_value in rows:
        assert state == "undetermined"
        assert basis == "none"
        assert cum_thr is None and rate_thr is None
        assert reasons, "待定值必须写明不启用原因码"
        tokens = set(reasons.split(";"))
        assert tokens & {"no_source", "source_not_confirmed", "no_applicable_rule"}, tokens
        assert cum_value is not None, "待定值不掩盖读数本身"
    conn.close()
    os.remove(db)


def test_fixture_grades_cover_every_monitored_item():
    table = grades()
    assert len(table) == 14
    for code, grade in sorted(table.items()):
        assert grade["cum"] is not None, code
        if grade["rate"] is not None:
            assert grade["rate_unit"] == grade["unit"] + "/d", code
        else:
            assert grade["rate_unit"] is None, code
    assert load_rules("SYN-LJ3"), "夹具规则集至少要给出窗口与预警比例"
