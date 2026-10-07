"""建筑基坑工程监测数据判读与预警工具（离线内核）。

内核只用标准库：判定与计算的第三方依赖数为 0（见 pyproject 的 dependencies 说明）。
"""

from __future__ import annotations

__version__ = "0.1.0"

#: SQLite 台账的 schema 版本；任何破坏兼容的表结构变更都要 +1 并在 plan/06 记一条口径变更
SCHEMA_VERSION = 1

#: 台账与报告指纹算法标识（改动会让已发布报告的哈希失效）
FINGERPRINT_ALG = "sha256"
