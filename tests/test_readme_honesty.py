"""README 诚实门：指标表只允许四态，基准指标在基准跑出来之前不得"达标"。

界面与文档最容易把"不可判"渲染成绿勾，这条断言就是防这一手的。
"""

from __future__ import annotations

import re

from _helpers import ROOT

README = (ROOT / "README.md").read_text(encoding="utf-8")
METRIC_STATES = ("达标", "未达标", "不可判", "不可用")
BENCHMARK_ROWS = ("召回率", "误报率", "定位误差", "漏报清单")


def _metric_table_rows():
    section = README.split("## 指标", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= {"-", " ", ":"} or cells[0] in ("指标",):
            continue
        rows.append(cells)
    return rows


def test_metrics_table_exists_and_uses_four_states():
    rows = _metric_table_rows()
    assert len(rows) >= 4, "指标表行数异常，README 指标节可能被删"
    for cells in rows:
        assert cells[1] in METRIC_STATES, "非法状态词：{0}".format(cells[:2])


def test_benchmark_metrics_are_not_claimed_as_met():
    for cells in _metric_table_rows():
        if any(word in cells[0] for word in BENCHMARK_ROWS):
            assert cells[1] != "达标", "基准未跑完之前不得写达标：{0}".format(cells[0])


def test_no_benchmark_numbers_are_invented():
    section = README.split("## 指标", 1)[1].split("\n## ", 1)[0]
    assert not re.search(r"\b(100\.0%|9[5-9]\.\d%|0\.9[5-9])", section), "出现凭空的指标数值"


def test_status_line_declares_m0_skeleton():
    assert "M0" in README and "骨架" in README


def test_disclaimer_and_scope_limits_present():
    assert "免责" in README
    assert "不判基坑是否安全" in README or "不判基坑是否安全" in README.replace("：", "")
    assert "不做验算" in README


def test_quickstart_covers_the_declared_commands():
    for cmd in ("pip install -U pip", 'pip install -e ".[dev]"', "-m pmc selfcheck", "-m pytest"):
        assert cmd in README, "快速开始缺步骤：{0}".format(cmd)
