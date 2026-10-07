"""数据目录定位：源码树、pytest、PyInstaller onedir 三种形态共用一条通路。

优先级（改动属口径变更，会打挂 exe 内嵌数据验证，见 plan/HANDOFF 既定口径）：
显式参数 > 环境变量 PMC_DATA_DIR > 冻结内嵌 > exe 同级 _internal > 仓库根 > 当前目录上溯。
"""

from __future__ import annotations

import os
import sys
from typing import List, Optional

ENV_VAR = "PMC_DATA_DIR"
_DATA_MARKER = ("dict", "rulesets", "clauses")


def _has_data_marker(path: str) -> bool:
    return all(os.path.isdir(os.path.join(path, m)) for m in _DATA_MARKER)


def _candidates(explicit: Optional[str], start: Optional[str] = None) -> List[str]:
    out: List[str] = []
    if explicit:
        out.append(explicit)
    env = os.environ.get(ENV_VAR)
    if env:
        out.append(env)

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        out.append(os.path.join(getattr(sys, "_MEIPASS", exe_dir), "data"))
        out.append(os.path.join(exe_dir, "_internal", "data"))
        out.append(os.path.join(exe_dir, "data"))

    base = os.path.abspath(start or os.path.dirname(os.path.abspath(__file__)))
    out.append(os.path.join(_repo_root(base), "data"))

    cwd = os.path.abspath(start or os.getcwd())
    for _ in range(6):
        out.append(os.path.join(cwd, "data"))
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    return out


def _repo_root(start: str) -> str:
    """从 src/pmc/paths.py 上溯到仓库根（src 的上一级）。"""
    path = os.path.abspath(start)
    for _ in range(8):
        if os.path.isdir(os.path.join(path, "plan")) and os.path.isdir(
            os.path.join(path, "data")
        ):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def find_data_dir(explicit: Optional[str] = None) -> str:
    for cand in _candidates(explicit):
        if cand and _has_data_marker(os.path.abspath(cand)):
            return os.path.abspath(cand)
    raise FileNotFoundError(
        "找不到数据目录（需同时包含 {0}）。可用 --data-dir 或环境变量 {1} 指定。".format(
            "/".join(_DATA_MARKER), ENV_VAR
        )
    )


def data_path(*parts: str, explicit: Optional[str] = None) -> str:
    return os.path.join(find_data_dir(explicit), *parts)
