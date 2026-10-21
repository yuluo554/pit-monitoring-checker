"""自指守门：README / plan 里写的测试数必须与真实收集数一致。

为什么需要它：本仓历史上（M6 的 §四 对账、以及本轮）都出现过「文档自述 452，实测 480」
这类漂移 —— 数字写进 README、又随 tag 进了 Release 树，读者按文档对账就会以为漏跑了。
把自述数字变成断言，下一次加/删测试而忘了改文档时会当场红。

**两个口径必须都能过**（本仓的核心设计就是"全绿但少跑要逐项归因"）：

* 装齐 `.[dev,gui]` 时：实测收集数 == README 的「装齐 extras」数；
* 只装 `.[dev]` 时：实测收集数 == README 的「只装 dev」数。

两者之间恒成立的关系是 `全量 − GUI 用例数 == dev 口径`。GUI 用例数**不能靠 pytest 收集**去问 ——
缺 PySide6 时 `test_m5_gui.py` 整模块跳过、收集数是 0，那样这条恒等式在任何只装 dev 的环境里
都会退化成 `全量 == dev`。所以这里改用 AST 数模块里的 `test_*` 函数，与解释器环境无关。
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys

from _helpers import ROOT

README = (ROOT / "README.md").read_text(encoding="utf-8")
PLAN00 = (ROOT / "plan" / "00-README总览.md").read_text(encoding="utf-8")

#: GUI 用例数唯一的来源：`test_m5_gui.py`（缺 PySide6 时整模块声明式跳过）
GUI_MODULE = ROOT / "tests" / "test_m5_gui.py"


def _collect_count(path: str = "") -> int:
    """在**当前解释器环境**下数收集数。path 为空表示数全仓。"""
    args = [sys.executable, "-X", "utf8", "-m", "pytest", "--collect-only", "-q"]
    if path:
        args.append(path)
    result = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True)
    match = re.search(r"(\d+) tests? collected", result.stdout)
    assert match, "收集数解析失败：{0}".format(result.stdout[-400:])
    return int(match.group(1))


def _declared(pattern: str, text: str, label: str) -> int:
    match = re.search(pattern, text)
    assert match, "文档里找不到自述数字（{0}）：{1}".format(label, pattern)
    return int(match.group(1))


def _declared_full():
    return _declared(r"装齐 extras：(\d+) passed", README, "README 全量测试行")


def _declared_dev():
    return _declared(r"只装 dev：(\d+) \+ 1 条 GUI 模块级跳过", README, "README dev 口径")


def _gui_test_count():
    """数 `test_m5_gui.py` 里的 `test_*` 函数。AST 口径与解释器环境无关。"""
    tree = ast.parse(GUI_MODULE.read_text(encoding="utf-8"))
    return sum(
        1
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    )


def _gui_extra_installed() -> bool:
    """本环境是否装了 `gui` extras（PySide6 可导入即算）。

    判"装了没"必须问 import，不能靠 `_gui_test_count()`（那是 AST 口径，恒为 11）
    也不能靠 GUI 模块的收集数（缺依赖时它是 0，与"这个文件里没有用例"长得一样）。
    """
    try:
        import PySide6  # noqa: F401
    except Exception:
        return False
    return True


def test_readme_two_configurations_are_consistent_with_each_other():
    """恒等关系：全量 − GUI 用例数 == dev 口径。与"当前环境"无关，任何环境都该成立。"""
    full, dev, gui = _declared_full(), _declared_dev(), _gui_test_count()
    assert full - gui == dev, (
        "README 自述全量 {0} / dev {1}，但 GUI 用例实测 {2} 项 —— 缺口对不上".format(
            full, dev, gui
        )
    )


def test_readme_matches_what_this_environment_collects():
    """本环境的实测收集数必须命中 README 对应那一口径（装了 PySide6 命中全量，否则命中 dev）。"""
    collected = _collect_count()
    if _gui_extra_installed():
        expected = _declared_full()
        which = "装齐 extras"
    else:
        expected = _declared_dev()
        which = "只装 dev"
    assert collected == expected, (
        "README 自述 {0} 为 {1} 项，本环境实测 {2} 项 —— 自述测试数漂移".format(
            which, expected, collected
        )
    )


def test_readme_collect_accounting_line_matches_too():
    """「收集数与跳过项的对账口径」那段里的两个数必须与上面同一套。"""
    full = _declared(r"装齐 extras 是 \*\*(\d+) 项", README, "对账段全量")
    dev = _declared(r"只装 `dev` 是 \*\*(\d+) 项", README, "对账段 dev")
    assert (full, dev) == (_declared_full(), _declared_dev()), (
        "README 两处自述的全量/dev 数不一致：{0}/{1} vs {2}/{3}".format(
            full, dev, _declared_full(), _declared_dev()
        )
    )


def test_plan00_quickstart_count_matches_readme():
    """plan/00 的快速开始块报的是同一个总数：两处不一致就是文档内部打架。"""
    declared = _declared(r"# (\d+) 项守门测试", PLAN00, "plan/00 快速开始")
    assert declared == _declared_full(), (
        "plan/00 说 {0} 项，README 说 {1} 项".format(declared, _declared_full())
    )


def test_release_ledger_count_section_is_marked_historical():
    """plan/RELEASE-M6 §四 的 452/441 是 v0.1.0 当时的历史实测，必须带"追加说明"。

    本轮已在该节下追加了「二次迭代后：收集数口径不变」那段；只要它还在，
    历史数字就不算陈旧声明（口径不变 = 452/441 的关系仍成立，只是绝对值变了）。
    """
    ledger = (ROOT / "plan" / "RELEASE-M6.md").read_text(encoding="utf-8")
    assert "2026-10-21 二次迭代后" in ledger, "§四 的追加说明被删了，历史数字会读成当前值"
    assert "收集数口径不变" in ledger, "追加说明缺「口径不变」这句，读者会以为关系变了"
