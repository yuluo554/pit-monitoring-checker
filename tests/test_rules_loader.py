"""规则集装载与门控：文件里不得自带"是否启用"，启用与否由 gate 单点决定。"""

from __future__ import annotations

import json
import os

import pytest
from _helpers import DATA, ROOT

from pmc.contract.clauses import load_register
from pmc.contract.thresholds import (
    REASON_NOT_VERIFIED,
    REASON_WINDOW_UNCONFIGURED,
    SOURCE_DESIGN,
    SOURCE_STANDARD,
    STATUS_PENDING,
    STATUS_VERIFIED,
    Threshold,
)
from pmc.errors import ContractError
from pmc.rules.gate import reason_counts, rule_enabled, split_rulesets
from pmc.rules.loader import SCHEMA_TAG, Rule, RuleSet, load_rulesets, parse_ruleset

RULESET_PATH = os.path.join(str(DATA), "rulesets", "alarm_dual_control.json")


def test_shipped_rulesets_load():
    sets = load_rulesets(str(DATA))
    assert len(sets) == 2
    codes = {rs.code for rs in sets}
    assert codes == {"alarm_dual_control", "frequency_compliance"}
    for rs in sets:
        assert rs.schema == SCHEMA_TAG
        assert rs.version >= 1
        assert len(rs.sha256) == 64


def test_nothing_is_enabled_before_clause_verification():
    """M0 的既成事实：0 条规则可判定，因此任何测点都只能输出待定值。

    17 = 11 条报警判据 + 6 条频率检核（M3 把间隔上限按开挖深度拆成三档，plan/08 §三）。
    """
    clauses = load_register(str(DATA))
    sets = load_rulesets(str(DATA))
    enabled, blocked = split_rulesets(sets, clauses)
    assert enabled == []
    assert len(blocked) == 17
    counts = reason_counts(blocked)
    assert sum(counts.values()) == len(blocked)
    assert REASON_NOT_VERIFIED in counts


def test_unknown_top_level_key_rejected(tmp_path):
    bad = json.load(open(RULESET_PATH, encoding="utf-8"))
    bad["extra_field"] = 1
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ContractError):
        parse_ruleset(str(path))


def test_rule_level_unknown_key_rejected(tmp_path):
    bad = json.load(open(RULESET_PATH, encoding="utf-8"))
    bad["rules"][0]["enabled"] = True  # 数据侧自称启用
    path = tmp_path / "bad2.json"
    path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ContractError):
        parse_ruleset(str(path))


def test_threshold_note_key_is_allowed():
    sets = load_rulesets(str(DATA))
    assert any(r.threshold.note for rs in sets for r in rs.rules)


def test_design_value_cannot_live_in_ruleset(tmp_path):
    doc = json.load(open(RULESET_PATH, encoding="utf-8"))
    doc["rules"][0]["threshold"] = {
        "kind": SOURCE_DESIGN,
        "status": STATUS_VERIFIED,
        "value": 30.0,
        "unit": "mm",
        "evidence": "SYN-JK-2026-0001",
    }
    path = tmp_path / "design.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ContractError):
        parse_ruleset(str(path))


def test_window_days_requires_source(tmp_path):
    doc = json.load(open(RULESET_PATH, encoding="utf-8"))
    doc["rules"][1]["window_days"] = 3.0
    doc["rules"][1].pop("window_source", None)
    path = tmp_path / "window.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ContractError):
        parse_ruleset(str(path))


def test_window_without_config_blocks_rate_rule():
    clauses = _verified_clauses()
    ok, reason = rule_enabled(_rate_rule("RATE-X"), clauses)
    assert not ok
    assert reason == REASON_WINDOW_UNCONFIGURED


def _verified_clauses():
    """夹具档：只允许出现在 tests/ 里，用来跑门控通路，不代表任何已核对事实。"""
    from pmc.contract.clauses import ClauseEntry

    entry = ClauseEntry(
        id="GB50497-2019",
        standard_code="GB 50497-2019",
        standard_title="建筑基坑工程监测技术标准（夹具，非已核对）",
        topic="报警值",
        status=STATUS_VERIFIED,
        channels=[{"channel": "官方原文页", "locator": "https://example.com/gb50497"}],
        retrieved_on="2026-01-01",
    )
    return {"GB50497-2019": entry}


def _rate_rule(rule_id):
    return Rule(
        id=rule_id,
        name="速率报警夹具规则",
        item_code="top_h_disp",
        judgement="alarm",
        basis="rate",
        threshold=Threshold(
            kind=SOURCE_STANDARD,
            status=STATUS_VERIFIED,
            value=20.0,
            unit="mm/d",
            clause_id="GB50497-2019",
            evidence="夹具",
            channel="c",
            url="https://example.com/x",
            verified_on="2026-01-01",
        ),
        clause_ids=["GB50497-2019"],
    )


def test_rate_rule_with_verified_clause_and_window_is_enabled():
    clauses = _verified_clauses()
    rule = _rate_rule("RATE-Y")
    ok, reason = rule_enabled(rule, clauses)
    assert reason == REASON_WINDOW_UNCONFIGURED

    rule = Rule(
        id=rule.id,
        name=rule.name,
        item_code=rule.item_code,
        judgement=rule.judgement,
        basis=rule.basis,
        threshold=rule.threshold,
        clause_ids=rule.clause_ids,
        window_days=3.0,
        window_source={
            "kind": "project_config",
            "evidence": "监测方案 3.2 节，SYN-JK-2026-0001",
        },
    )
    ok, reason = rule_enabled(rule, clauses)
    assert ok and reason == ""
