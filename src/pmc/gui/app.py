"""桌面界面（M5）：台账 / 导入 / 判定 / 检核 / 报告 五页签。

架构纪律（plan/02 §四、plan/03 §3、HANDOFF-M5 §二.5）：

* **GUI 不另起第二套行为**：每个页签的"执行"都走 `pmc.cli.main(argv)` 同一条命令链，
  界面只拼参数、只展示文本；判定/检核/报告的算法在这里一份都没有复制；
* **PySide6 是可选依赖**：本模块顶层不 import Qt，缺依赖时 `main()` 打印安装提示并退回码 2，
  源码仍可被 pytest 收集（`extras=gui`）；
* **不用模态框**：反馈一律进页签内的日志区（`notify`），测试永不触模态；
  文件对话框只在按钮槽里出现，测试不点那条分支；
* **offscreen**：`QT_QPA_PLATFORM` 在首次 import PySide6 之前由 `main(--smoke)` 设定。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import sys
from typing import List, Optional, Tuple

from pmc.errors import EXIT_INPUT_UNAVAILABLE, PmcError

#: 五页签的固定顺序与标题（题面 01 §模块 5、plan/05 M5 行）
TAB_TITLES: Tuple[str, ...] = ("台账", "导入", "判定", "检核", "报告")

INSTALL_HINT = '缺少 PySide6：pip install -e ".[dev,gui,pkg]"（打包与界面口径见 plan/11）'

#: 每个页签的参数面：与 `pmc <命令>` 的旗标一一对应，不新增"只有界面才有"的行为
PAGE_COMMANDS: Tuple[str, ...] = ("ledger", "import", "check", "audit", "report")

PAGE_FIELDS = {
    "台账": ["--point", "--from", "--to"],
    "导入": ["--file", "--round", "--dry-run"],
    "判定": ["--round", "--rules-dir", "--dry-run"],
    "检核": ["--from", "--to", "--rules-dir"],
    "报告": ["--kind", "--round", "--from", "--to", "--out", "--bench-plane"],
}


def cli_available() -> bool:
    """PySide6 是否可用：只做 import 探测，不在模块顶层引入 Qt。"""
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return False
    return True


def run_cli(argv: List[str]) -> Tuple[int, str]:
    """把一条 pmc 命令跑在进程内并抓回输出——界面看到的与终端看到的必须是同一份文本。"""
    from pmc import cli

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit:
            code = EXIT_INPUT_UNAVAILABLE
        except PmcError as exc:
            code = exc.exit_code
            err.write("{0}: {1}\n".format(type(exc).__name__, exc.message))
        except Exception as exc:  # noqa: BLE001 - 界面兜底，内核异常原样回显不吞栈
            code = EXIT_INPUT_UNAVAILABLE
            err.write("UNEXPECTED {0}: {1}\n".format(type(exc).__name__, exc))
    return int(code), out.getvalue() + err.getvalue()


def build_window(db: str = "", data_dir: str = "", project: str = ""):
    """构造五页签主窗口，返回 (QApplication, QMainWindow, pages)。缺 PySide6 抛 ImportError。"""
    try:
        from pmc.gui.pages import make_window
    except ImportError as exc:  # pragma: no cover - 依赖缺失在 main() 里降级
        raise ImportError(INSTALL_HINT) from exc

    return make_window(db=db, data_dir=data_dir, project=project)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="pmc gui", description="桌面界面（只消费 pmc 命令链）")
    parser.add_argument("--db", default="", help="台账 .sqlite 路径")
    parser.add_argument("--data-dir", dest="data_dir", default="", help="数据目录（缺省自动定位）")
    parser.add_argument("--project", default="", help="默示工程编码")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="只构造窗口不进入事件循环（offscreen 冒烟与打包自检用）",
    )
    args = parser.parse_args(argv)

    if not cli_available():
        print(INSTALL_HINT, file=sys.stderr)
        return EXIT_INPUT_UNAVAILABLE
    if not args.db:
        print("GUI_INPUT 需要 --db（台账路径）；--smoke 也必须给路径以便页签预填", file=sys.stderr)
        return EXIT_INPUT_UNAVAILABLE
    if not os.path.isfile(args.db):
        print("GUI_INPUT 台账不存在：{0}".format(args.db.replace("\\", "/")), file=sys.stderr)
        return EXIT_INPUT_UNAVAILABLE

    if args.smoke:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    try:
        _app, window, pages = build_window(args.db, args.data_dir, args.project)
    except ImportError:
        print(INSTALL_HINT, file=sys.stderr)
        return EXIT_INPUT_UNAVAILABLE

    if args.smoke:
        titles = "、".join(page.title for page in pages)
        print("GUI_SMOKE_OK 页签 {0} 个：{1}".format(len(pages), titles))
        window.close()
        return 0
    window.show()
    from PySide6.QtWidgets import QApplication

    return int(QApplication.instance().exec())
