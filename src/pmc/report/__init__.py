"""模块 4 成果报告与交付物。落地于 M5。

xlsx 由标准库 zipfile 直写 OOXML（含过程线图），固定 ZipInfo 保证两次导出逐字节一致、
且不写创建时间与用户名；不引入 python-docx / openpyxl（决策见 plan/06 D10）。
"""

from __future__ import annotations
