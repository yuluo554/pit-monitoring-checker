"""模块 1 的输入解析与校验层（CSV/XLSX → Observation）。落地于 M1。

纪律：确定性解析，缺测不插值、单位不一致即拒绝、重复上报进修订链。
"""

from __future__ import annotations
