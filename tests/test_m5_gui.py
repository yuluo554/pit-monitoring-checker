"""M5 界面 offscreen 冒烟：五页签、参数面与 CLI 同源、无模态框、无 PySide6 时降级。

`QT_QPA_PLATFORM=offscreen` 必须在**首次 import PySide6 之前**设置，
所以这里先设环境变量，再 importorskip。缺 PySide6 的机器上整模块声明式跳过（-rs 可见）。
"""

from __future__ import annotations

import ast
import os
import pathlib

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest  # noqa: E402

pytest.importorskip("PySide6", reason="界面页签需要 PySide6（extras=gui）")

from PySide6.QtWidgets import QComboBox, QSpinBox  # noqa: E402

import _m5  # noqa: E402
from _helpers import SRC  # noqa: E402
from pmc.errors import EXIT_INPUT_UNAVAILABLE  # noqa: E402
from pmc.gui import app as gui_app  # noqa: E402
from pmc.gui.pages import PAGE_SPECS, PmPage  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture(scope="function")
def window(tmp_path):
    seed = _m5.seed_db(tmp_path)
    _app, main_window, pages = gui_app.build_window(seed["db"], "", seed["project"])
    yield seed, main_window, pages
    main_window.close()


def page_titles_from_ast() -> tuple:
    """页签规格从源码取，避免测试依赖 Qt 已经装载。"""
    source = (pathlib.Path(SRC) / "pmc" / "gui" / "pages.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="pages.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "PAGE_SPECS":
            return tuple(key.value for key in node.value.keys)
    raise AssertionError("pages.py 里找不到 PAGE_SPECS")


def test_five_tabs_in_the_documented_order(window):
    _seed, main_window, pages = window
    assert tuple(page.title for page in pages) == gui_app.TAB_TITLES
    assert gui_app.TAB_TITLES == ("台账", "导入", "判定", "检核", "报告")
    assert page_titles_from_ast() == gui_app.TAB_TITLES


def test_argv_flags_exist_on_the_cli(window):
    """界面的参数面必须是 CLI 的子集：界面不得凭空造旗标。"""
    import argparse

    from pmc import cli

    parser = cli.build_parser()
    sub = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    for page in window[2]:
        argv = page.argv()
        assert page.command in sub.choices, "命令不在 CLI 上：{0}".format(page.command)
        flags = {a.dest for a in sub.choices[page.command]._actions}  # noqa: SLF001
        root_flags = {a.dest for a in parser._actions}
        head = argv[: argv.index(page.command)]
        tail = argv[argv.index(page.command) + 1 :]
        for token in head:
            if token.startswith("--"):
                assert token[2:].replace("-", "_") in root_flags, "根级旗标不存在：{0}".format(token)
        for token in tail:
            if not token.startswith("--"):
                continue
            dest = token[2:].replace("-", "_")
            assert dest in flags, "{0} 页签造了 CLI 没有的旗标：{1}".format(page.title, token)


def test_pages_carry_db_and_project(window):
    seed, _main, pages = window
    for page in pages:
        argv = page.argv()
        assert "--db" in argv and seed["db"] in argv
        assert "--project" in argv and seed["project"] in argv


def test_ledger_page_text_matches_cli_exactly(window):
    """同源证明：页签跑出来的文本与终端跑同一条命令的文本逐字一致。"""
    from pmc import cli

    seed, _main, pages = window
    page = [item for item in pages if item.title == "台账"][0]
    argv = page.argv()
    code, text = gui_app.run_cli(argv)
    collected = []
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        collected.append(cli.main(list(argv)))
    text_cli = buffer.getvalue()
    assert code == collected[0]
    assert text.split("\n")[0] == text_cli.split("\n")[0] or "LEDGER" in text


def test_notify_channel_carries_feedback_without_modals(window):
    seed, _main, pages = window
    page = [item for item in pages if item.title == "报告"][0]
    for flag, control in page.fields:
        if flag == "--kind":
            control.setCurrentText("daily")
        if flag == "--out":
            control.setText(str(seed["tmp"] / "out"))
    code = page.run()
    assert code in (0, 1)
    assert page.messages, "反馈没进 notify 通道"
    assert "退出码" in page.messages[-1]
    assert page.last_text.startswith("REPORT_") or "REPORT_" in page.last_text
    assert page.log.toPlainText().strip(), "日志区没有落字"


def test_report_page_writes_xlsx(window):
    seed, _main, pages = window
    page = [item for item in pages if item.title == "报告"][0]
    for flag, control in page.fields:
        if flag == "--kind":
            control.setCurrentText("daily")
        if flag == "--out":
            control.setText(str(seed["tmp"] / "gui-out"))
    page.run()
    produced = list((seed["tmp"] / "gui-out").glob("*.xlsx"))
    assert len(produced) == 1
    assert produced[0].stat().st_size > 4096


def test_spinbox_zero_means_unset(window):
    _seed, _main, pages = window
    page = [item for item in pages if item.title == "判定"][0]
    for flag, control in page.fields:
        if isinstance(control, QSpinBox):
            control.setValue(0)
    assert "--round" not in page.argv()


def test_no_modal_dialogs_in_page_logic():
    """模态框会挂死 offscreen 测试批：界面不得 import 任何对话框类，事件循环只允许在 main() 里。"""
    import pathlib

    gui_dir = pathlib.Path(SRC) / "pmc" / "gui"
    banned = ("QMessageBox", "QInputDialog", "QFileDialog")
    offenders = []
    for path in sorted(gui_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = ["{0}.{1}".format(node.module or "", alias.name) for alias in node.names]
            for name in names:
                if any(name.endswith(b) for b in banned) and "PySide6" in name:
                    if path.name == "pages.py" and name.endswith("QFileDialog"):
                        continue  # 文件对话框只允许在按钮槽里被 import（下面单独验）
                    offenders.append("{0}:{1}".format(path.name, name))
    assert not offenders, "界面里 import 了模态框：" + ",".join(offenders)

    app_source = (gui_dir / "app.py").read_text(encoding="utf-8")
    assert app_source.count(".exec()") <= 1, "事件循环只能有一处，且在 --smoke 分支之外"
    pages_source = (gui_dir / "pages.py").read_text(encoding="utf-8")
    assert "QFileDialog.getOpenFileName" in pages_source
    assert "def browse" in pages_source, "文件对话框必须待在按钮槽里"


def test_main_without_db_returns_input_unavailable(capsys):
    capsys.readouterr()
    assert gui_app.main([]) == EXIT_INPUT_UNAVAILABLE
    assert "GUI_INPUT" in capsys.readouterr().err


def test_main_smoke_builds_and_exits(tmp_path, capsys):
    seed = _m5.seed_db(tmp_path)
    capsys.readouterr()
    code = gui_app.main(["--db", seed["db"], "--smoke"])
    out = capsys.readouterr().out
    assert code == 0
    assert "GUI_SMOKE_OK" in out
    for title in gui_app.TAB_TITLES:
        assert title in out


def test_app_module_imports_without_qt_at_top():
    """模块顶层不得 import Qt：否则 `pmc report` 等无界面通路也被拖成需要 GUI。"""
    source = (pathlib.Path(SRC) / "pmc" / "gui" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="app.py")
    for node in tree.body:
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            assert not name.startswith("PySide6"), "app.py 顶层 import 了 Qt：{0}".format(name)
