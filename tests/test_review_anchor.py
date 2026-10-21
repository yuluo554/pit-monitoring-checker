"""复核锚守门：5 条条款登记与 17 条规则 threshold 都能回答「何时看过 / 何时该再看」。

这条门只锁**元数据是否齐备且合法**，不碰结论：`status` 仍是 pending、`value` 仍是 null、
`verified_on` 仍是 null —— 加锚不等于核对过，逾期只打印警告不改退出码（否则"该复核了"
会变成"门红了"，而红了又没人能靠改代码变绿，最后只能被绕过去）。

日期一律用 `datetime.date.fromisoformat` 判：`2027-13-45` 这种字符串长得像日期但 parse 会炸。
"""

from __future__ import annotations

import datetime
import json
import warnings

import pytest
from _helpers import DATA, ROOT

from pmc.contract.clauses import load_register
from pmc.rules.gate import split_rulesets
from pmc.rules.loader import load_rulesets

REGISTER = DATA / "clauses" / "register.json"
RULESETS = sorted((DATA / "rulesets").glob("*.json"))

#: 本轮定的复核周期：一年一核（与 plan/05 的台账口径一致）
INTERVALS = {"annual": 365, "semiannual": 182, "quarterly": 91, "monthly": 30}


def _register_doc():
    return json.loads(REGISTER.read_text(encoding="utf-8"))


def _rule_docs():
    return [(path, json.loads(path.read_text(encoding="utf-8"))) for path in RULESETS]


def _parse(value, label):
    """合法 ISO 日期；不是字符串或 parse 失败都算缺陷。"""
    assert isinstance(value, str) and value, "{0} 必须是非空字符串，实为 {1!r}".format(label, value)
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        pytest.fail("{0} 不是合法 ISO 日期：{1!r}".format(label, value))


# ---- 条款登记（5 条） -------------------------------------------------------


def test_register_entries_are_the_five_known_ones():
    entries = _register_doc()["entries"]
    assert len(entries) == 5, [e["id"] for e in entries]


def test_every_register_entry_carries_the_three_anchors():
    for entry in _register_doc()["entries"]:
        for key in ("reviewed_at", "next_review", "review_interval"):
            assert entry.get(key), "{0} 缺复核锚 {1}".format(entry["id"], key)
        assert entry["review_interval"] in INTERVALS, (
            "{0} 的 review_interval 非法：{1!r}".format(entry["id"], entry["review_interval"])
        )


def test_register_anchor_dates_are_iso_and_ordered():
    for entry in _register_doc()["entries"]:
        reviewed = _parse(entry["reviewed_at"], entry["id"] + ".reviewed_at")
        due = _parse(entry["next_review"], entry["id"] + ".next_review")
        assert reviewed < due, "{0} 的下次复核日必须晚于本次核对日".format(entry["id"])


def test_register_anchor_matches_retrieved_on():
    """`reviewed_at` 记的是"这一版依据是什么时候看过的"，必须与 `retrieved_on` 同日。

    允许不等就等于允许两个时间口径漂移 —— 而"哪天查的"只该有一个答案。
    """
    for entry in _register_doc()["entries"]:
        assert entry["reviewed_at"] == entry["retrieved_on"], entry["id"]


def test_register_status_is_untouched_by_the_anchors():
    """加锚不是核对：状态分布必须还是 located 1 / pending 4，且没有任何 verified。"""
    statuses = [entry["status"] for entry in _register_doc()["entries"]]
    assert statuses.count("verified") == 0, statuses
    assert statuses.count("located") == 1 and statuses.count("pending") == 4, statuses


# ---- 规则 threshold（17 条） ------------------------------------------------


def test_all_seventeen_thresholds_carry_review_due():
    seen = 0
    for path, doc in _rule_docs():
        for rule in doc["rules"]:
            threshold = rule["threshold"]
            assert threshold.get("review_due"), "{0}#{1} 缺 review_due".format(
                path.name, rule["id"]
            )
            seen += 1
    assert seen == 17, "规则条数变了：{0}".format(seen)


def test_threshold_review_due_is_iso_and_in_the_future():
    today = datetime.date.today()
    for path, doc in _rule_docs():
        for rule in doc["rules"]:
            due = _parse(rule["threshold"]["review_due"], rule["id"] + ".review_due")
            assert due > today, "{0} 的 review_due 已过期：{1}".format(rule["id"], due)


def test_review_due_does_not_touch_verification_state():
    """红线：加锚不许顺手把 pending 变 verified，也不许给 value 填数。"""
    for path, doc in _rule_docs():
        for rule in doc["rules"]:
            threshold = rule["threshold"]
            assert threshold.get("status") == "pending", (path.name, rule["id"])
            assert threshold.get("value") is None, (path.name, rule["id"])
            assert threshold.get("verified_on") is None, (path.name, rule["id"])


def test_no_verified_status_literal_in_any_data_file():
    """字面扫描：`"status": "verified"` 在 data/ 里必须 0 次（与批次门禁同一条）。"""
    hits = 0
    for path in [REGISTER] + RULESETS:
        hits += path.read_text(encoding="utf-8").count('"status": "verified"')
    assert hits == 0, "data/ 里出现 verified 状态：{0} 次".format(hits)


# ---- 装载层：锚必须能被读出来（不是只躺在 JSON 里） -------------------------


def test_loader_exposes_the_register_anchors():
    entries = load_register(str(DATA))
    assert len(entries) == 5
    for entry in entries.values():
        assert entry.reviewed_at and entry.next_review and entry.review_interval, entry.id


def test_loader_accepts_review_due_without_making_rules_usable():
    """`review_due` 进了 THRESHOLD_KEYS 白名单，但**不得**让任何规则变成可判定。"""
    sets = load_rulesets(str(DATA))
    assert sum(len(rs.rules) for rs in sets) == 17
    enabled, blocked = split_rulesets(sets, load_register(str(DATA)))
    assert enabled == [], "加复核锚后仍必须 0 条可判定"
    assert len(blocked) == 17


# ---- 逾期只提示，不改退出码 -------------------------------------------------


def _overdue(entry_review, interval):
    days = INTERVALS[interval]
    return entry_review + datetime.timedelta(days=days) < datetime.date.today()


def test_overdue_only_warns():
    """把 next_review 人为推到过去：只该打印警告，断言层面不得 raise、不得改任何退出码。

    这是这条门的设计要点 —— 「该复核了」是运维信号，不是产品失败。
    """
    today = datetime.date.today()
    entries = load_register(str(DATA))
    stale = []
    for entry in entries.values():
        reviewed = datetime.date.fromisoformat(entry.reviewed_at)
        if _overdue(reviewed, entry.review_interval):
            stale.append(entry.id)
    if stale:
        warnings.warn("依据登记逾期未复核：{0}".format(",".join(stale)), stacklevel=1)
    # 逾期也不改结论：装载结果与门控结果必须与不逾期时一致
    enabled, blocked = split_rulesets(load_rulesets(str(DATA)), entries)
    assert enabled == [] and len(blocked) == 17
    assert today  # 明确本测试只做观察，不做断言翻转


def test_review_anchor_guard_is_wired_into_the_suite():
    """自指守门：这条文件必须在 tests/ 里被收集（删掉它不该让套件"变绿"）。"""
    assert (ROOT / "tests" / "test_review_anchor.py").is_file()
