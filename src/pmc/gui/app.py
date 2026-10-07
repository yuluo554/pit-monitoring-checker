"""桌面入口占位（M5 落地）。

模块级不导入 PySide6：没有装 gui extras 的环境也能读取入口元数据、也不打挂 `pmc --help`。
Qt 相关导入一律留在 main() 的惰性分支里（缺依赖时抛带安装提示的异常，CLI 捕获后降级）。
"""

from __future__ import annotations

import sys

from pmc.errors import EXIT_NOT_IMPLEMENTED

MILESTONE = "M5"


def main(argv=None) -> int:
    sys.stderr.write(
        "pmc-gui: 桌面界面属 {0}，尚未开工。当前请用命令行内核（python -m pmc --help）。".format(
            MILESTONE
        )
    )
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":
    sys.exit(main())
