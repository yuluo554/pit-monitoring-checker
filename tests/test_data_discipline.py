"""数据契约文件的纪律扫描：数值与来源状态必须互相自洽。

这一组断言是"防幻觉"的落地点 —— 任何悄悄把未核对的数字写进 data/ 的改动都会在这里红。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from _helpers import DATA

from pmc.contract.thresholds import STATUS_VERIFIED

MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
IDCARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")


def _json_files():
    return sorted(p for p in Path(DATA).rglob("*.json"))


def test_data_dir_has_the_three_contract_files():
    names = {p.name for p in _json_files()}
    assert {"register.json", "monitoring_items.json"} <= names
    assert {"alarm_dual_control.json", "frequency_compliance.json"} <= names


def test_every_json_parses_and_declares_schema():
    for path in _json_files():
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(doc, dict), "{0} 顶层必须是对象".format(path.name)
        assert str(doc.get("schema", "")).startswith("pmc-"), "{0} 缺 schema 标识".format(path.name)
        assert doc.get("notice") or doc.get("entries") is not None, "{0} 缺纪律说明".format(path.name)


def test_unverified_thresholds_carry_no_numbers():
    """未核对 = 数值必须为 null：这条断言直接守住"待定值不出货"在数据层。"""
    offenders = []
    for path in sorted((DATA / "rulesets").glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for rule in doc["rules"]:
            thr = rule["threshold"]
            if thr.get("status") != STATUS_VERIFIED and thr.get("value") is not None:
                offenders.append("{0}#{1}".format(path.name, rule["id"]))
    assert not offenders, "未核对却带数值：" + ",".join(offenders)


def test_monitoring_dictionary_has_no_threshold_values():
    doc = json.loads((DATA / "dict" / "monitoring_items.json").read_text(encoding="utf-8"))
    allowed = {
        "code", "name", "unit", "control_kind", "window_basis",
        "list_source", "list_status", "scope_note", "clause_ids",
    }
    for item in doc["items"]:
        extra = sorted(set(item) - allowed)
        assert not extra, "字典条目 {0} 混入阈值类字段：{1}".format(item["code"], extra)


def test_clause_entries_are_https_and_channelled():
    doc = json.loads((DATA / "clauses" / "register.json").read_text(encoding="utf-8"))
    for entry in doc["entries"]:
        assert entry["status"] in ("pending", "located", "verified")
        assert entry["channels"], "{0} 未挂渠道".format(entry["id"])
        for ch in entry["channels"]:
            assert ch.get("channel") and ch.get("locator")
            if ch["locator"].startswith("http"):
                assert ch["locator"].startswith("https://"), "查证渠道一律 https"


@pytest.mark.parametrize("path", _json_files(), ids=lambda p: p.name)
def test_no_personal_data_in_contract_files(path):
    text = path.read_text(encoding="utf-8")
    assert not MOBILE_RE.search(text), "{0} 含疑似真实手机号".format(path.name)
    assert not IDCARD_RE.search(text), "{0} 含疑似身份证号".format(path.name)
