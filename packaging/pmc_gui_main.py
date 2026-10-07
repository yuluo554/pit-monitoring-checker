"""`pmc-gui.exe` 的冻结入口薄壳：双击即起界面，不含任何业务行为。

没有参数时，把台账落在 exe 同级的 `pmc-ledger.sqlite`，台账不存在就先跑
`pmc init`（走的是同一条 CLI 通路，界面层没有第二套建库代码）。
"""

import os
import sys

from pmc import cli
from pmc.gui.app import main as gui_main


def default_db_path() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.getcwd()
    return os.path.join(base, "pmc-ledger.sqlite")


def build_argv(argv):
    if any(arg == "--db" or arg.startswith("--db=") for arg in argv):
        return list(argv)
    db = default_db_path()
    if not os.path.isfile(db):
        cli.main(["init", "--db", db])
    return ["--db", db] + list(argv)


if __name__ == "__main__":
    sys.exit(gui_main(build_argv(sys.argv[1:])))
