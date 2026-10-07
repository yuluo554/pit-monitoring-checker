"""M2 判定夹具的装载入口（`tests/fixtures/` → 台账档位 + 规则集）。

夹具数值的来源登记一律写成"测试夹具录入"（`kind=user_input`），不当设计文件号、不当规范条文：
它只证明"阈值挂上之后判定通路走得通"，不构成任何规范依据（plan/09 §六）。
本模块只读 JSON，不 import `pmc.synth`。
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from typing import Dict, List, Optional

from _helpers import ROOT

from pmc.rules.loader import Rule, parse_ruleset

FIXTURE_DIR = ROOT / "tests" / "fixtures"
GRADES_FILE = FIXTURE_DIR / "point_grades.json"
RULESET_ROOT = FIXTURE_DIR / "rulesets"
#: M3 频率检核夹具：一整套"条款已核对 + 深度分档"的数据面替身，只用于证通路
FREQ_DATA_DIR = FIXTURE_DIR / "data_freq"

#: 工程 → 夹具规则集目录：速率窗口是工程配置事实（日报 3 天 / 隔日 5 天 / 周报 7 天）
SITE_RULESET_DIR = {
    "SYN-LJ3": "fx_lj3",
    "SYN-ZHDQ": "fx_zhdq",
    "SYN-YYCG": "fx_yycg",
}

FIXTURE_MARKERS = (
    "FIXTURE-GRADE",
    "FIXTURE-FREQ",
    "syn-m3-fixture-1",
    "syn-m2-fixture-1",
    "fx-alarm-cum-",
    "fx-alarm-window-",
    "fx-warn-ratio-",
    "fx_alarm_",
)


def grades_doc() -> Dict[str, object]:
    return json.loads(GRADES_FILE.read_text(encoding="utf-8"))


def grades() -> Dict[str, Dict[str, Optional[float]]]:
    return grades_doc()["grades"]


def fixture_id() -> str:
    return grades_doc()["fixture_id"]


def marker_forms() -> List[str]:
    return list(FIXTURE_MARKERS)


def ruleset_dir(project_code: str) -> str:
    slug = SITE_RULESET_DIR.get(project_code)
    if slug is None:
        raise KeyError("工程 {0} 没有对应的夹具规则集目录".format(project_code))
    return str(RULESET_ROOT / slug)


def freq_data_dir() -> str:
    """`--data-dir` 指到这里，模块 3 的检核才拿得到"已核对"的频率条款。"""
    return str(FREQ_DATA_DIR)


def freq_ruleset_dir() -> str:
    return str(FREQ_DATA_DIR / "rulesets")


def load_rules(project_code: str) -> List[Rule]:
    return list(parse_ruleset(ruleset_file(project_code)).rules)


def ruleset_file(project_code: str) -> str:
    return str(pathlib.Path(ruleset_dir(project_code)) / "alarm_window.json")


def apply_point_grades(
    conn: sqlite3.Connection, project_code: str, only_items: Optional[List[str]] = None
) -> int:
    """把夹具档位写进测点档案，模拟"用户在档案里录入了控制值"。"""
    doc = grades_doc()
    registration = doc["registration"]
    row = conn.execute("SELECT id FROM project WHERE code = ?", (project_code,)).fetchone()
    if row is None:
        raise KeyError("工程 {0} 未建档".format(project_code))
    project_id = int(row[0])
    updated = 0
    for item_code, grade in sorted(doc["grades"].items()):
        if only_items is not None and item_code not in only_items:
            continue
        cur = conn.execute(
            "UPDATE point SET design_cum_value=?, design_cum_unit=?, design_rate_value=?,"
            " design_rate_unit=?, threshold_source_kind=?, threshold_status=?,"
            " threshold_evidence=? WHERE project_id=? AND item_code=?",
            (
                grade["cum"],
                grade["unit"],
                grade["rate"],
                grade["rate_unit"],
                registration["kind"],
                registration["status"],
                registration["evidence"],
                project_id,
                item_code,
            ),
        )
        updated += cur.rowcount
    conn.commit()
    return updated
