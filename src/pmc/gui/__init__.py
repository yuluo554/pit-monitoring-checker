"""模块 5 桌面界面（PySide6）。落地于 M5。

界面只消费 CLI/引擎 API，不另起第二套判定行为；Qt 导入必须留在 app.py 的惰性分支里，
使无 PySide6 的环境仍能 import pmc.gui.app 读取入口元数据。
"""

from __future__ import annotations
