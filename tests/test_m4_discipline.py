"""M4 纪律：bench 的每个对外用词都必须先在 `plan/10` 登记，装配点例外要收窄而不是放行。

与 M2/M3 的纪律测试同一手法：先扫代码里的常量，再逐条要求文档出现；
每条断言都配**阳性对照**，防止"清单空了所以全过"的假绿。
"""

from __future__ import annotations

import ast
import os
import re
import sys

import pytest
from _helpers import DATA, ROOT

sys.path.insert(0, str(ROOT / "src"))

from pmc.bench import runner
from pmc.errors import InputError

SRC = ROOT / "src"
DOC = (ROOT / "plan" / "10-基准与评测.md").read_text(encoding="utf-8")
RUNNER_SRC = (SRC / "pmc" / "bench" / "runner.py").read_text(encoding="utf-8")
GATE_SRC = (ROOT / "scripts" / "gate.py").read_text(encoding="utf-8")
FREEZE_TEST_SRC = (ROOT / "tests" / "test_synth_freeze.py").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
SCRATCH = os.path.join(str(ROOT), ".tmp_verify")


def _bench_vocabulary():
    """runner 对外的全部用词：改名或加词都必须先动文档。"""
    words = list(runner.PLANES) + list(runner.METRIC_STATES) + list(runner.OUTCOMES)
    words += list(runner.JUDGMENT_CHECKS) + list(runner.IMPORT_CHECKS)
    words += list(runner.NOTE_CODES) + list(runner.METRIC_ORDER)
    words += sorted(runner.METRIC_NAMES.values()) + sorted(runner.GATE_TEXT.values())
    words += [runner.JSON_SCHEMA, runner.GOLDEN_SCHEMA, runner.GOLDEN_REL_PATH,
              runner.PROFILE_ID, runner.GRADE_EVIDENCE]
    words += list(runner.SYNTH_ALLOWED_MODULES)
    return words


def test_bench_vocabulary_is_documented():
    missing = [word for word in _bench_vocabulary() if word not in DOC]
    assert not missing, "plan/10 未登记这些 bench 用词：" + "、".join(missing)
    assert "不可查" not in DOC, "阳性对照：词汇表扩容后这条必须还能发现漏登记"


def test_bench_states_are_exactly_four():
    assert len(set(runner.METRIC_STATES)) == 4
    assert set(runner.METRIC_STATES) == {"达标", "未达标", "不可判", "不可用"}


def test_note_codes_are_the_fourth_axis_and_stay_documented():
    """评测轴原因码与阈值轴 9 码 / 引擎轴 2 码 / 检核轴 5 码分账，不与任何一轴混用。"""
    assert len(runner.NOTE_CODES) == 9
    overlap = set(runner.NOTE_CODES) & {"no_applicable_rule", "unit_inconsistent",
                                       "no_applicable_interval_band", "alarm_state_empty"}
    assert overlap == set(), overlap


# ---- 装配点例外：收窄覆盖面，不放宽 ------------------------------------------


def test_only_assembly_points_import_synth():
    """除 `synth/` 包本身、顶层装配模块与 `bench/` 外，任何模块 import pmc.synth 即红。"""
    offenders = []  # type: list
    allowed_importers = []  # type: list
    for path in sorted((SRC / "pmc").rglob("*.py")):
        rel = path.relative_to(SRC / "pmc")
        parts = rel.parts
        if len(parts) < 2 or parts[0] == "synth":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = set()  # type: set
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module or "")
        hit = sorted(name for name in names if name.startswith("pmc.synth"))
        if not hit:
            continue
        if parts[0] == "bench" or rel.name in ("cli.py", "selfcheck.py"):
            allowed_importers.append((str(rel).replace("\\", "/"), hit))
            continue
        offenders.append("{0}:{1}".format(str(rel).replace("\\", "/), "), ",".join(hit)))
    assert not offenders, "判定/规则/导入/台账层引用了合成档位：" + " | ".join(offenders)
    assert any(name.startswith("bench") for name, _ in allowed_importers), "阳性对照：例外名单要真的被用上"


def test_bench_synth_imports_are_limited_to_the_grades_and_the_ledger_seed():
    """`bench` 只准拿档位（profile）、站档案（sites）与建档入口（freeze），不碰生成器与 RNG。"""
    tree = ast.parse(RUNNER_SRC, filename="runner.py")
    modules = set()  # type: set
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pmc.synth"):
            parts = node.module.split(".")
            if len(parts) > 2:
                modules.add(parts[-1])
            else:
                modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("pmc.synth."):
                    modules.add(alias.name.split(".")[-1])
    assert modules <= set(runner.SYNTH_ALLOWED_MODULES), sorted(modules)
    assert "generator" not in modules and "rng" not in modules
    assert {"profile", "sites", "freeze"} <= modules, sorted(modules)


def test_freeze_guard_skips_bench_only_as_a_named_assembly_point():
    """`test_synth_freeze` 的跳过名单必须显式写着 bench 并指回 plan/10，不能是匿名漏扫。"""
    assert re.search(r'parts\[0\] == "bench"', FREEZE_TEST_SRC), "扫描面被改动，先核对 plan/10 §一"
    assert "SYNTH_ASSEMBLY_POINTS" in FREEZE_TEST_SRC and "plan/10" in FREEZE_TEST_SRC


# ---- 期望值来自真值，不来自代码 --------------------------------------------


def test_runner_never_hardcodes_expected_counts():
    """评测的期望值只能从真值文件读：代码里不许出现 `len(...) == 常数` 这类自证断言。"""
    pattern = r"len\([^)]*\)\s*==\s*[0-9]"
    assert not re.search(pattern, RUNNER_SRC), re.findall(pattern, RUNNER_SRC)
    assert "EXPECTED_ALARM_EVENTS" not in RUNNER_SRC
    assert not re.findall(r"== *11\b|== *6\b", RUNNER_SRC)
    assert re.search(pattern, "assert len(truth) == 11"), "阳性对照失效"


def _write_truth(dir_name: str, body: str) -> str:
    path = os.path.join(SCRATCH, dir_name)
    os.makedirs(os.path.join(path, "truth"), exist_ok=True)
    with open(os.path.join(path, "truth", "SYN-ZHDQ.truth.csv"), "wb") as handle:
        handle.write(body.encode("utf-8"))
    return path


def test_unknown_truth_token_is_refused():
    bad = _write_truth(
        "bad-token-data",
        ",".join(runner.freeze.TRUTH_HEADER) + "\n"
        "SYN-ZHDQ-X01,SYN-TH-03,11,step,1.0,11,alarm_basis=nonsense\n",
    )
    with pytest.raises(InputError) as exc:
        runner.load_truth(bad, ["SYN-ZHDQ"])
    assert "未登记 token" in exc.value.message
    assert "alarm_basis=nonsense" in exc.value.message


def test_truth_header_must_stay_seven_columns():
    bad = _write_truth("short-truth-data", "event_id,point_id,round_index\nA,B,C\n")
    with pytest.raises(InputError) as exc:
        runner.load_truth(bad, ["SYN-ZHDQ"])
    assert "7 列契约" in exc.value.message


def test_missing_truth_file_is_input_error():
    with pytest.raises(InputError) as exc:
        runner.load_truth(str(DATA), ["SYN-NOPE"])
    assert "真值文件缺失" in exc.value.message


# ---- 门禁链：脚本、README 与 CI 必须同一条 ----------------------------------


def test_gate_chain_steps_and_expected_exit_codes():
    tree = ast.parse(GATE_SRC, filename="gate.py")
    steps = None
    for node in ast.walk(tree):
        target = getattr(node, "target", None) or (node.targets[0] if hasattr(node, "targets") else None)
        if getattr(target, "id", "") == "STEPS":
            steps = ast.literal_eval(node.value)
    assert steps, "gate.py 里的 STEPS 读不到"
    commands = [" ".join(args) for _, args, _ in steps]
    assert commands == [
        "-m pmc selfcheck",
        "-m pmc synth --check",
        "-m pytest -rs",
        "-m pmc bench --all --json",
        "-m pmc bench --plane ledger",
    ], commands
    assert [code for _, _, code in steps] == [0, 0, 0, 0, 1], \
        "反证环期望码必须是 1：依据未核对就不能报成达标"


def test_readme_and_ci_point_at_the_same_gate_chain():
    assert "scripts/gate.py" in README and "pmc bench" in README
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "-m pmc bench" in ci, "CI 未跑 bench"


def test_benchmark_metric_names_match_readme_wording():
    for code in runner.METRIC_ORDER:
        assert runner.METRIC_NAMES[code] in README, code


def test_bench_writes_nothing_into_the_data_plane_without_the_flag():
    """`data/` 里出现"档位数值 + 单位"的组合 = 幻觉防线破口：golden 只记结论。"""
    from pmc.catalog.items import load_items

    golden = os.path.join(str(DATA), *runner.GOLDEN_REL_PATH.split("/"))
    with open(golden, "rb") as handle:
        text = handle.read().decode("utf-8")
    items = load_items(str(DATA))
    patterns = []
    for item_code in sorted(runner.SYNTH_PROFILE):
        grade = runner.SYNTH_PROFILE[item_code]
        unit = items[item_code].unit
        for key in ("cum", "rate"):
            value = grade[key]
            if value is None:
                continue
            literal = "{0}".format(value)
            patterns.append("{0} {1}".format(literal, unit))
            patterns.append("{0}{1}".format(literal, unit))
            patterns.append("{0} {1}/d".format(literal, unit))
    assert len(patterns) >= 40, "阳性对照失效：档位形态都没扫到"
    leaks = [pattern for pattern in patterns if pattern in text]
    assert not leaks, "golden 里出现了档位数值：" + ",".join(leaks)
    planted = text + '{"x": "25.0 mm", "y": "25.0mm"}'
    assert set(p for p in patterns if p in planted) == {"25.0 mm", "25.0mm"}, "阳性对照失效"
