"""M4 基准评测的行为面：逐起对账、指标四态、golden 位级一致、退出码、与 README 的逐行对账。

期望值全部来自 `data/truth/*.truth.csv`：本文件不硬编事件条数，
真值面缩水由 golden 对账与"应报警事件为 0 起"两条断言当场抓住（M2 的 `EXPECTED_ALARM_EVENTS` 同一思路）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from typing import Dict, List

import pytest
from _helpers import DATA, ROOT

sys.path.insert(0, str(ROOT / "src"))

from pmc import cli
from pmc.bench import runner
from pmc.errors import EXIT_DEGRADED, EXIT_OK

README = (ROOT / "README.md").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def synth():
    return runner.run_bench(str(DATA))


@pytest.fixture(scope="session")
def ledger():
    return runner.run_bench(str(DATA), plane=runner.PLANE_LEDGER)


def truth_ids() -> Dict[str, str]:
    """直接从真值文件读 event_id → event_type：测试的期望也不经代码中转。"""
    out = {}  # type: Dict[str, str]
    for site in runner.site_codes():
        path = os.path.join(str(DATA), "truth", site + ".truth.csv")
        with open(path, newline="", encoding="utf-8") as handle:
            for row in csv_rows(handle):
                out[row["event_id"]] = row["event_type"]
    return out


def csv_rows(handle) -> List[Dict[str, str]]:
    import csv

    return list(csv.DictReader(handle))


def _metric(report, code):
    return report.metric(code)


# ---- 逐起对账 ----------------------------------------------------------------


def test_every_alarm_expectant_event_hits_its_expected_round(synth):
    expectant = [e for e in synth.events if e.expected_round is not None]
    assert expectant, "真值面里应报警事件 0 起：基准失去意义（先查真值文件是否被改坏）"
    for event in expectant:
        assert event.outcome == runner.OUTCOME_HIT, event.as_dict()
        assert event.error_rounds == 0, event.as_dict()
        assert event.detected_round == event.expected_round


def test_bench_agrees_with_the_m2_per_event_regression(synth):
    """M2 逐起对账与 M4 逐起表必须互相印证：同一批事件、同一批期望轮次。"""
    ids = truth_ids()
    for event in synth.events:
        assert ids[event.event_id] == event.event_type
    planted = sorted(e.event_id for e in synth.events if e.expected_round is not None)
    expected = sorted(
        [
            "SYN-YYCG-E01", "SYN-YYCG-E02",
            "SYN-ZHDQ-E02", "SYN-ZHDQ-E03", "SYN-ZHDQ-E04", "SYN-ZHDQ-E06",
        ]
    )
    assert planted == expected, "应报警事件集合变了：先归因再改断言"


def test_token_checks_all_pass_on_the_synth_plane(synth):
    weak = [
        "{0}/{1}".format(event.event_id, check.name)
        for event in synth.events
        for check in event.checks
        if check.ok is False
    ]
    assert not weak, "对账项失败：" + ",".join(weak)


def test_import_guard_events_never_appear_in_the_miss_list(synth):
    """`unit_error` 与 `duplicate_report` 属导入器与修订链的考题，不许被读成引擎漏报（04 §2.3）。"""
    ids = truth_ids()
    miss_ids = [event.event_id for event in synth.misses]
    guard_types = ("unit_error", "duplicate_report", "missing", "outlier")
    for event in synth.events:
        if ids[event.event_id] in guard_types:
            assert event.event_id not in miss_ids, event.event_id
    assert [e.event_id for e in synth.guards] == []


# ---- 指标四态 ----------------------------------------------------------------


def test_synth_plane_meets_every_gate(synth):
    assert _metric(synth, runner.METRIC_RECALL).value >= runner.RECALL_GATE
    assert _metric(synth, runner.METRIC_FALSE_ALARM).numerator == 0
    location = _metric(synth, runner.METRIC_LOCATION)
    assert location.value == 0.0 and "P95 0" in location.detail
    assert _metric(synth, runner.METRIC_MISSED).numerator == 0
    for metric in synth.metrics:
        assert metric.state == runner.STATE_MET, metric.as_dict()
    assert synth.exit_code == EXIT_OK


def test_metrics_use_only_the_four_documented_states(synth, ledger):
    for report in (synth, ledger):
        for metric in report.metrics:
            assert metric.state in runner.METRIC_STATES, metric.as_dict()
        assert [m.code for m in report.metrics] == list(runner.METRIC_ORDER)


def test_ledger_plane_is_unavailable_not_unmet(ledger):
    """通路未通记「不可用」而不是 0 分召回：量不了与量出来差是两件事。"""
    for code in runner.BENCHMARK_METRICS:
        metric = _metric(ledger, code)
        assert metric.state == runner.STATE_UNAVAILABLE, metric.as_dict()
        assert metric.value is None
    assert ledger.counts["judgeable_rows"] == 0
    assert ledger.counts["undetermined_rows"] == ledger.counts["judged_rows"]
    assert not any(m.state == runner.STATE_UNMET for m in ledger.metrics)
    assert ledger.exit_code == EXIT_DEGRADED


def test_ledger_plane_still_ships_no_conclusions(ledger):
    assert _metric(ledger, runner.METRIC_BLANK).state == runner.STATE_MET
    assert _metric(ledger, runner.METRIC_RECEIPT).state == runner.STATE_MET
    assert ledger.golden_state == "skipped"


def test_judged_row_count_matches_the_m2_frozen_plane(synth):
    """基准吃的判定面必须与 M2 落库面同源：待定值 0 行、行数与三座站档案一致。"""
    assert synth.counts["batches"] == 58
    assert synth.counts["judged_rows"] == synth.counts["judgeable_rows"]
    assert synth.counts["judged_rows"] > 0


# ---- golden 位级一致 ---------------------------------------------------------


def test_golden_on_disk_matches_this_run_byte_for_byte(synth):
    path = runner.golden_path(str(DATA))
    with open(path, "rb") as handle:
        stored = handle.read()
    assert stored == runner.canonical_bytes(runner.golden_doc(synth))
    assert b"\r" not in stored
    assert synth.golden_state == "ok" and synth.golden_problems == []


def test_golden_drift_is_caught(synth):
    mutated = copy.deepcopy(synth)
    mutated.events[0].outcome = runner.OUTCOME_MISS
    mutated.events[0].detected_round = None
    state, problems = runner.check_golden(mutated, str(DATA))
    assert state == "drift"
    assert any(mutated.events[0].event_id in text for text in problems), problems


def test_truth_shrink_shows_up_as_drift(synth):
    trimmed = copy.deepcopy(synth)
    dropped = trimmed.events[0].event_id
    trimmed.events = [e for e in trimmed.events if e.event_id != dropped]
    state, problems = runner.check_golden(trimmed, str(DATA))
    assert state == "drift"
    assert any("缩水" in text and dropped in text for text in problems), problems


def test_golden_carries_no_yardstick_numbers():
    """golden 记结论不记标尺：档位数值一个字都不许进 `data/`（C7）。"""
    path = runner.golden_path(str(DATA))
    with open(path, "rb") as handle:
        doc = json.loads(handle.read().decode("utf-8"))
    text = json.dumps(doc, ensure_ascii=False, sort_keys=True)
    keys = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                keys.add(key)
                walk(value)
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if isinstance(node, float):
            leaves.append(node)

    leaves = []  # type: List[float]
    walk(doc)
    assert doc["schema"] == runner.GOLDEN_SCHEMA and doc["notice"]
    forbidden = {
        "threshold", "cum_threshold", "rate_threshold", "design_cum_value",
        "design_rate_value", "unit", "grades", "grade_values", "window_days", "depth_max",
    }
    assert not (keys & forbidden), sorted(keys & forbidden)
    assert "SYNTH-GRADE" not in text and "FIXTURE" not in text
    assert "syn-fixture-grade-1" in text, "golden 必须登记用的是哪一版档位"
    assert [v for v in leaves if not (0.0 <= v <= 1.0)] == [], leaves
    assert any(value > 1.0 for value in runner.SYNTH_PROFILE["top_h_disp"].values()), "阳性对照失效"


def test_write_golden_refuses_subsets_and_the_ledger_plane():
    with pytest.raises(Exception) as exc:
        runner.run_bench(str(DATA), plane=runner.PLANE_LEDGER, write=True)
    assert "重基线只在合成面全三座基坑时允许" in str(exc.value.message)
    with pytest.raises(Exception) as exc2:
        runner.run_bench(str(DATA), sites="SYN-LJ3", write=True)
    assert "重基线只在合成面全三座基坑时允许" in str(exc2.value.message)


# ---- 复现与不留痕 ------------------------------------------------------------


def _tree_digest(root: str) -> str:
    digest = hashlib.sha256()
    for dirpath, dirnames, names in os.walk(root):
        dirnames.sort()
        for name in sorted(names):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel.startswith(".git/"):
                continue
            with open(path, "rb") as handle:
                blob = handle.read()
            digest.update(rel.encode("utf-8"))
            digest.update(blob)
    return digest.hexdigest()


def test_plain_run_is_reproducible_and_leaves_no_trace():
    """同一次评测两次跑出同一份期望结果，且不往工作树与 `data/` 写任何东西。"""
    before_data = _tree_digest(os.path.join(ROOT, "data"))
    before_root = {name for name in os.listdir(ROOT) if name.endswith((".sqlite", ".db"))}
    first = runner.run_bench(str(DATA))
    bytes_first = runner.canonical_bytes(runner.golden_doc(first))
    second = runner.run_bench(str(DATA))
    bytes_second = runner.canonical_bytes(runner.golden_doc(second))
    assert bytes_first == bytes_second and first.exit_code == EXIT_OK
    assert _tree_digest(os.path.join(ROOT, "data")) == before_data
    after_root = {name for name in os.listdir(ROOT) if name.endswith((".sqlite", ".db"))}
    assert after_root == before_root, "评测把台账建在 :memory: 里，工作树不该多出库文件：{0}".format(
        sorted(after_root - before_root)
    )


# ---- 输出契约与 README 对账 --------------------------------------------------


def test_json_report_carries_the_documented_contract(synth):
    doc = synth.as_dict()
    for key in ("schema", "plane", "grade_profile", "seed", "sites", "truth", "counts",
                "metrics", "events", "misses", "guards", "notes", "golden", "exit_code"):
        assert key in doc, key
    assert doc["schema"] == runner.JSON_SCHEMA
    assert doc["grade_profile"] == runner.PROFILE_ID
    assert {note["code"] for note in doc["notes"]} <= set(runner.NOTE_CODES)
    assert roundtrip_is_stable(doc)


def roundtrip_is_stable(doc) -> bool:
    blob = json.dumps(doc, ensure_ascii=False, sort_keys=True)
    return json.loads(blob) == doc


def test_markdown_table_is_four_columns_and_seven_rows(synth, ledger):
    for report in (synth, ledger):
        lines = report.markdown()
        assert lines[0] == "| 指标 | 状态 | 说明 | 复现命令 |"
        assert lines[1] == "|---|---|---|---|"
        assert len(lines) == 2 + len(runner.METRIC_ORDER)
        for line in lines[2:]:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            assert len(cells) == 4 and cells[1] in runner.METRIC_STATES


def _readme_rows(heading: str) -> List[List[str]]:
    section = README.split(heading, 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        if not line.strip().startswith("|") or set(line.strip()) <= {"|", "-", " ", ":"}:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] in ("指标",):
            continue
        rows.append(cells)
    return rows


def test_readme_benchmark_rows_are_the_ledger_plane_output(ledger):
    """README 指标节的基准四行 = `bench --plane ledger --markdown` 的逐行输出（不是手抄）。"""
    rows = {cells[0]: cells for cells in _readme_rows("## 指标")}
    for line in ledger.markdown()[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] not in rows:
            continue
        assert rows[cells[0]] == cells, (rows[cells[0]], cells)


def test_readme_benchmark_section_is_the_synth_plane_output(synth):
    rows = {cells[0]: cells for cells in _readme_rows("## 基准评测")}
    for line in synth.markdown()[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert cells[0] in rows, cells
        assert rows[cells[0]] == cells, (rows[cells[0]], cells)


def test_cli_wires_bench_end_to_end(capsys):
    assert cli.main(["bench"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "BENCH_PLANE synth" in out and "BENCH_GOLDEN OK" in out
    assert "BENCH_EXIT 0" in out
    assert cli.main(["bench", "--plane", "ledger", "--markdown"]) == EXIT_DEGRADED
    table = capsys.readouterr().out
    assert "| 报警召回率 | 不可用 |" in table


def test_unknown_plane_and_missing_truth_are_input_errors():
    with pytest.raises(Exception) as exc:
        runner.run_bench(str(DATA), plane="spec_verified")
    assert "评测平面" in exc.value.message
    with pytest.raises(Exception) as missing:
        runner.load_truth(str(DATA), ["SYN-NOPE"])
    assert "真值文件缺失" in missing.value.message
