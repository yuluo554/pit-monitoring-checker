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

#: 手机号与身份证字面扫描。两侧的 hex 边界是为了别把 sha256 里的数字串读成手机号：
#: 十六进制串里"…ab1383934581cd…"完全可能出现，把它当 PII 报警会让这条门失去可信度。
#: 真号段/真证件号在文本与 CSV 字段里都是独立成段，前后是标点或汉字，照样命中（见阳性对照）。
MOBILE_RE = re.compile(r"(?<![\da-fA-F])1[3-9]\d{9}(?![\da-fA-F])")
IDCARD_RE = re.compile(r"(?<![\da-fA-F])\d{17}[\dXx](?![\da-fA-F])")


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


def _scan_files():
    """脱敏扫描覆盖面 = data/ 下全部 json 与 csv（M1 起合成时序与真值也入仓，不能只扫契约文件）。"""
    return sorted(p for p in Path(DATA).rglob("*") if p.is_file() and p.suffix in (".json", ".csv"))


def test_scanner_still_catches_real_forms():
    """先跑阳性对照：改了正则却扫不出真号段，等于把这条门悄悄关了。

    阳性样本必须片段拼接 —— 入库文本里不留完整的真实形态号码（M6 脱敏门，见 `plan/RELEASE-M6.md §一`），
    拼完运行时仍是真实形态，检测力一分不减。
    两条阴性样本**不许拆**：它们安全全靠正则的非 hex 相邻守卫，一拆就变成被引号包围的纯数字串，守卫失效。
    """
    real_form_mobile = "13" + "8001" + "123" + "45"
    real_form_idcard = "11" + "0101" + "1990" + "0307" + "12" + "3X"
    assert MOBILE_RE.search('{"tel":"' + real_form_mobile + '"},')
    assert MOBILE_RE.search("监测人 1990000" + "0123 电话")
    assert IDCARD_RE.search("身份证号 " + real_form_idcard + " 登记")
    # 阴性：sha256 里的数字串不是手机号
    assert not MOBILE_RE.search("ab1383934581cd")
    assert not IDCARD_RE.search("dead01234567890123456789012345678901beef")


def test_no_personal_data_in_any_data_file():
    offenders = []
    for path in _scan_files():
        text = path.read_text(encoding="utf-8")
        if MOBILE_RE.search(text):
            offenders.append("{0}:手机号".format(path.relative_to(DATA)))
        if IDCARD_RE.search(text):
            offenders.append("{0}:证件号".format(path.relative_to(DATA)))
    assert not offenders, "数据面出现疑似真实身份信息：" + "; ".join(offenders)


def test_synthetic_forms_stay_on_the_whitelist():
    """合成时序只准出现白名单形式：测点编号 SYN-XX-NN、工程目录 SYN-*、真值事件 SYN-*-ENN。"""
    point_re = re.compile(r"^SYN-[A-Z]{2,3}-[0-9]{2}$")
    offenders = []
    for path in _scan_files():
        if path.suffix != ".csv":
            continue
        rows = path.read_text(encoding="utf-8").splitlines()
        header = rows[0].split(",")
        for line in rows[1:]:
            cells = dict(zip(header, line.split(",")))
            code = cells.get("point_code") or cells.get("point_id") or ""
            if not point_re.match(code):
                offenders.append("{0}:{1}".format(path.name, code))
    assert not offenders, "非白名单测点编号入仓：" + ",".join(sorted(set(offenders))[:6])
    for directory in sorted(p.name for p in Path(DATA).glob("raw/*") if p.is_dir()):
        assert directory.startswith("SYN-"), "工程目录名必须是 SYN 前缀：{0}".format(directory)
