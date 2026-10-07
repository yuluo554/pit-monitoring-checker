"""M3 的纪律扫描：检核结论必须挂依据，词汇必须先登记，夹具不许渗进数据面。

对应 `plan/08 §五/§六/§七` 与口径 C2b/C5/C7/C21。纪律测试红了，说明数据或代码越界了。
"""

from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest
from _fixtures import FIXTURE_MARKERS
from _helpers import DATA, ROOT, SRC

from pmc.compliance import auditor
from pmc.db.schema import DDL_STATEMENTS
from pmc.rules.loader import parse_ruleset

DOC = ROOT / "plan" / "08-规则集与条款核对.md"
FREQUENCY_RULESET = pathlib.Path(DATA) / "rulesets" / "frequency_compliance.json"
ALARM_RULESET = pathlib.Path(DATA) / "rulesets" / "alarm_dual_control.json"
#: 结论用词红线（题面 §六.4）：检核只提"应核实"，不替监理与设计下结论
FORBIDDEN_VERDICT_PHRASES = ("已确认违规", "认定违规", "已认定超标", "判定为违规", "确认超标")


def _imports_and_attrs(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


# ---- 词汇登记 ---------------------------------------------------------------


def test_compliance_vocabulary_is_documented():
    """新增检核轴原因码与违规类别必须先写进 plan/08：口径变更走文档，不走代码注释。"""
    doc = DOC.read_text(encoding="utf-8")
    for token in auditor.COMPLIANCE_REASON_CODES:
        assert token in doc, "{0} 未登记".format(token)
    for kind in auditor.VIOLATION_KINDS:
        assert kind in doc, "{0} 类违规未登记".format(kind)
    for rule_id in auditor.SEQUENCE_RULE_IDS:
        assert rule_id in doc, "{0} 未登记".format(rule_id)
    assert auditor.VERDICT in doc


def test_positive_control_for_the_documentation_gate(tmp_path):
    """阳性对照：把断言改成永远为真的空门，比红更糟。"""
    doc = DOC.read_text(encoding="utf-8")
    fake = "reason_code_that_does_not_exist"
    assert fake not in doc
    assert auditor.COMPLIANCE_REASON_CODES, "原因码集合空了，这条门就是空的"


def test_violation_kinds_match_the_ddl_check():
    """`auditor.VIOLATION_KINDS` 与 DDL 的 CHECK 必须同集合：不同步就是文档与存储两套口径。"""
    statement = next(s for s in DDL_STATEMENTS if "CREATE TABLE IF NOT EXISTS violation" in s)
    in_ddl = set(re.findall(r"'([a-z_]+)'", statement.split("kind IN")[1].split(")")[0]))
    assert in_ddl == set(auditor.VIOLATION_KINDS)


def test_sequence_rule_ids_exist_in_the_production_ruleset():
    doc = json.loads(FREQUENCY_RULESET.read_text(encoding="utf-8"))
    by_id = {rule["id"]: rule for rule in doc["rules"]}
    for rule_id in auditor.SEQUENCE_RULE_IDS:
        assert rule_id in by_id, "{0} 在生产频率规则集里缺失".format(rule_id)
        assert by_id[rule_id]["basis"] == "sequence"
    intervals = [r for r in doc["rules"] if r["basis"] == "interval"]
    assert len(intervals) >= 1
    assert sum(1 for r in intervals if r.get("depth_max") is None) == 1, "只能有一条兜底档"


def test_ruleset_version_bumped_and_recorded():
    """口径变更留痕：频率规则集 v2（深度分档）必须在 plan/06 §五 占一行。"""
    assert json.loads(FREQUENCY_RULESET.read_text(encoding="utf-8"))["version"] == 2
    log = (ROOT / "plan" / "06-交付对标与决策记录.md").read_text(encoding="utf-8")
    assert "frequency_compliance" in log and "depth_max" in log


# ---- 分层与依赖 -------------------------------------------------------------


def test_auditor_does_not_recompute_alarms():
    """模块 3 只读模块 2 的落库结果：不 import alarm 层，也不读判定所需的规则与档案。"""
    path = pathlib.Path(SRC) / "pmc" / "compliance" / "auditor.py"
    modules = _imports_and_attrs(path)
    assert not [m for m in modules if m.startswith("pmc.alarm")], "检核不得回头重算报警"
    source = path.read_text(encoding="utf-8")
    assert "unclosed" in source, "未闭环标记必须被消费"
    assert "alarm_state" in source
    assert "design_cum_value" not in source, "读阈值是模块 2 的事"


def test_compliance_layer_imports_only_allowed_layers():
    path = pathlib.Path(SRC) / "pmc" / "compliance" / "auditor.py"
    layers = {"contract", "db", "catalog", "rules", "ingest", "alarm", "report", "synth", "bench", "gui"}
    hit = {m.split(".")[1] for m in _imports_and_attrs(path) if m.startswith("pmc.")} & layers
    assert hit <= {"contract", "rules"}, "检核层依赖了不该依赖的层：" + str(hit)


def test_verdict_vocabulary_has_no_room_for_overreach():
    """结论列的取值集合只有"应核实"一项：越界用词在结构上就写不出来。"""
    assert auditor.VERDICT == "应核实"
    assert auditor.CONCLUSION_CHOICES == ("应核实",)
    for phrase in FORBIDDEN_VERDICT_PHRASES:
        assert phrase not in auditor.CONCLUSION_CHOICES


def test_audit_command_is_wired_not_a_placeholder():
    from pmc import cli

    parser = cli.build_parser()
    sub = next(a for a in parser._actions if type(a).__name__ == "_SubParsersAction")
    assert "audit" in sub.choices
    flags = {a.dest for a in sub.choices["audit"]._actions}
    assert {"db", "project", "round_from", "round_to", "rules_dir"} <= flags
    assert hasattr(cli, "_cmd_audit")


# ---- 夹具隔离 ---------------------------------------------------------------


def test_frequency_fixture_markers_are_self_describing():
    """夹具必须自述"非已核对条文"，并且标记同时出现在夹具与规则集两处。"""
    root = ROOT / "tests" / "fixtures" / "data_freq"
    texts = [p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.json"))]
    joined = "\n".join(texts)
    assert "FIXTURE-FREQ" in joined and "syn-m3-fixture-1" in joined
    assert "非已核对" in joined
    register = json.loads((root / "clauses" / "register.json").read_text(encoding="utf-8"))
    assert all(e["status"] == "verified" for e in register["entries"])


def test_frequency_fixture_never_touches_the_data_dir():
    offenders = []
    for path in sorted(p for p in pathlib.Path(DATA).rglob("*") if p.is_file()):
        text = path.read_text(encoding="utf-8")
        hits = [marker for marker in FIXTURE_MARKERS if marker in text]
        if hits:
            offenders.append("{0}:{1}".format(path.name, ",".join(hits)))
    assert not offenders, "M3 夹具渗进数据面：" + "; ".join(offenders)


def test_production_frequency_rules_still_supply_no_numbers():
    """M3 没拿到官方条文原文：生产频率规则的上限天数必须仍然是 null。"""
    doc = json.loads(FREQUENCY_RULESET.read_text(encoding="utf-8"))
    for rule in doc["rules"]:
        threshold = rule["threshold"]
        assert threshold["status"] != "verified", "{0} 未经核对不得转 verified".format(rule["id"])
        if rule["basis"] == "interval":
            assert threshold["value"] is None, "{0} 供货了未核对的天数".format(rule["id"])
    assert json.loads(ALARM_RULESET.read_text(encoding="utf-8"))["version"] == 1, \
        "M3 没核对到报警值原文，报警规则集不该被顺手改版本"


def test_frequency_ruleset_loads_through_the_real_parser(tmp_path):
    sets = parse_ruleset(str(FREQUENCY_RULESET))
    assert sets.version == 2
    assert {r.basis for r in sets.rules} == {"interval", "sequence"}

    # 阳性对照：装载器真的在逐字段校验，白名单不是装饰（临时文件只落 tmp_path，不进 data/）
    doc = json.loads(FREQUENCY_RULESET.read_text(encoding="utf-8"))
    doc["rules"][0]["unknown_key"] = 1
    path = tmp_path / "bad_ruleset.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(Exception):
        parse_ruleset(str(path))
