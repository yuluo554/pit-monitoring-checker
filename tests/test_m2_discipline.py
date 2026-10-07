"""M2 的纪律扫描：夹具不许渗进数据面，判定路径不许读时钟，冻结产物不许被动过。

这一组是 `plan/09 §六`、口径 C5/C7/C10 与禁项四的行为化落点。
纪律测试红了，说明数据或代码越界了，而不是测试写错了。
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from _fixtures import FIXTURE_DIR, FIXTURE_MARKERS, grades_doc, ruleset_dir
from _helpers import DATA, ROOT, SRC

from pmc import cli
from pmc.alarm import engine as alarm_engine
from pmc.synth import freeze

#: 判定与输入侧的分层：这些层一旦读时钟或引随机源，报告与基准就无法逐字节复现
DISCIPLINED_LAYERS = ("alarm", "rules", "ingest", "db", "contract", "catalog")
DISCIPLINED_FILES = ("cli.py", "selfcheck.py")
FORBIDDEN_CLOCK_ATTRS = {"now", "today", "utcnow", "localtime", "monotonic", "time"}
FORBIDDEN_MODULES = {"random", "socket", "urllib", "http", "requests"}


def _data_files():
    return sorted(p for p in pathlib.Path(DATA).rglob("*") if p.is_file())


def _fixture_files():
    return sorted(p for p in FIXTURE_DIR.rglob("*") if p.is_file())


# ---- 夹具隔离 ----------------------------------------------------------------


def test_fixture_markers_never_appear_in_the_data_dir():
    """阳性对照先行：正则失效比断言失败更危险。"""
    joined = "\n".join(path.read_text(encoding="utf-8") for path in _fixture_files())
    for marker in FIXTURE_MARKERS:
        assert marker in joined, "夹具自身不含 {0}，这条门是空的".format(marker)
    offenders = []
    for path in _data_files():
        text = path.read_text(encoding="utf-8")
        hits = [marker for marker in FIXTURE_MARKERS if marker in text]
        if hits:
            offenders.append("{0}:{1}".format(path.name, ",".join(hits)))
    assert not offenders, "夹具渗进数据面：" + "; ".join(offenders)


def test_fixture_files_live_only_under_tests():
    for path in _fixture_files():
        assert str(path).startswith(str(ROOT / "tests" / "fixtures"))
    assert list((pathlib.Path(DATA) / "rulesets").glob("fx_*")) == []
    assert sorted(p.name for p in (pathlib.Path(DATA) / "rulesets").glob("*.json")) == [
        "alarm_dual_control.json",
        "frequency_compliance.json",
    ]


def test_production_rulesets_still_supply_no_numbers():
    """`data/rulesets/*` 的阈值数值必须仍是 null：M2 打开的是判定通路，不是数据面。"""
    from pmc.contract.thresholds import STATUS_VERIFIED
    import json

    for path in sorted((pathlib.Path(DATA) / "rulesets").glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for rule in doc["rules"]:
            threshold = rule["threshold"]
            if threshold.get("status") != STATUS_VERIFIED:
                assert threshold.get("value") is None, "{0}#{1}".format(path.name, rule["id"])


def test_fixture_registration_says_it_is_a_fixture():
    doc = grades_doc()
    assert str(doc["fixture_id"]) in doc["registration"]["evidence"]
    assert "非设计文件值" in doc["registration"]["evidence"]
    assert "非规范条文值" in doc["registration"]["evidence"]
    assert doc["registration"]["kind"] == "user_input", "夹具只当现场手填，不当设计值或条文值"


def test_fixture_helper_does_not_read_the_synth_profile():
    """夹具装载器只读 JSON：一旦 import pmc.synth，档位就变成了自证循环。"""
    for name in ("_fixtures.py", "_xlsx.py"):
        path = ROOT / "tests" / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module or "")
        assert not [m for m in modules if m.startswith("pmc.synth")], name


def test_per_project_ruleset_directories_are_disjoint():
    seen = {}
    for site, slug in sorted({"SYN-LJ3": "fx_lj3", "SYN-ZHDQ": "fx_zhdq", "SYN-YYCG": "fx_yycg"}.items()):
        path = pathlib.Path(ruleset_dir(site)) / "alarm_window.json"
        assert path.is_file(), str(path)
        import json

        doc = json.loads(path.read_text(encoding="utf-8"))
        windows = {r["window_days"] for r in doc["rules"] if r["basis"] == "rate"}
        assert len(windows) == 1, "一个工程的速率窗口必须只有一个值"
        seen[site] = windows.pop()
    assert seen == {"SYN-LJ3": 3, "SYN-YYCG": 7, "SYN-ZHDQ": 5}, "窗口是工程配置事实（07 §五）"


# ---- 判定路径的确定性 --------------------------------------------------------


def _layer_files():
    out = []
    for layer in DISCIPLINED_LAYERS:
        root = pathlib.Path(SRC) / "pmc" / layer
        out.extend(sorted(p for p in root.rglob("*.py")))
    for name in DISCIPLINED_FILES:
        single = pathlib.Path(SRC) / "pmc" / name
        assert single.is_file(), name
        out.append(single)
    return out


def _attributes_and_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    attrs = set()
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            attrs.add(node.attr)
        elif isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return attrs, modules


def test_judgment_path_never_reads_the_clock():
    """判定与导入路径不许读当前时间：同一台账两次 check 必须逐列一致（回归里已实测）。"""
    offenders = []
    for path in _layer_files():
        attrs, modules = _attributes_and_imports(path)
        hits = attrs & FORBIDDEN_CLOCK_ATTRS
        if hits:
            offenders.append("{0}:{1}".format(path.name, ",".join(sorted(hits))))
        if "time" in modules:
            offenders.append("{0}:import time".format(path.name))
    assert not offenders, "判定路径读了时钟：" + "; ".join(offenders)


def test_judgment_path_has_no_network_or_random():
    offenders = []
    for path in _layer_files():
        _, modules = _attributes_and_imports(path)
        hits = modules & FORBIDDEN_MODULES
        if hits:
            offenders.append("{0}:{1}".format(path.name, ",".join(sorted(hits))))
    assert not offenders, "离线内核引了网络/随机模块：" + "; ".join(offenders)


def test_engine_basis_vocabulary_is_documented():
    """新增原因码必须先写进 plan/09：口径变更走文档，不走代码注释。"""
    doc = (ROOT / "plan" / "09-报警状态机设计.md").read_text(encoding="utf-8")
    for token in alarm_engine.ENGINE_REASON_CODES:
        assert token in doc, "{0} 未登记".format(token)
    assert set(alarm_engine.ENGINE_REASON_CODES) == {
        "no_applicable_rule",
        "unit_inconsistent",
    }
    assert alarm_engine.RATE_UNIT_SUFFIX == "/d"


def test_state_machine_reuses_the_contract_table_not_a_copy():
    """迁移表只有 `contract/status.py` 一份：引擎复用它，不许自带第二套。"""
    from pmc.contract.status import ALLOWED_TRANSITIONS

    source = (pathlib.Path(SRC) / "pmc" / "alarm" / "engine.py").read_text(encoding="utf-8")
    assert "can_transition" in source and "is_unclosed" in source
    assert "ALLOWED_TRANSITIONS = " not in source, "引擎里不得出现第二张迁移表"
    assert set(ALLOWED_TRANSITIONS[alarm_engine.ObsState.ALARM]) == {
        "alarm",
        "alarm_confirmed",
        "alarm_handled",
        "undetermined",
    }


def test_frozen_data_is_still_byte_identical():
    """M2 只读数据面：跑完判定与 XLSX 通路之后，冻结产物必须仍然逐字节一致。"""
    assert cli.main(["synth", "--check"]) == 0
    blobs = freeze.build_blobs(str(DATA), 20260107, 3)
    ok, problems = freeze.check_blobs(str(DATA), blobs)
    assert ok, problems


@pytest.mark.parametrize("layer", DISCIPLINED_LAYERS)
def test_layer_has_files(layer):
    assert list((pathlib.Path(SRC) / "pmc" / layer).rglob("*.py")), layer
