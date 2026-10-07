"""分层禁令：用行为断言而不是"看起来像"的静态断言。

规则：
  * contract 层是最底层，不得反向依赖任何业务层；
  * 判定/规则/台账层不得依赖界面与报告；
  * 只有 gui 层允许 import PySide6；
  * CLI 是单一事实源的入口，GUI 只消费 CLI/引擎 API（M5 起继续收紧）。
"""

from __future__ import annotations

import ast
import pathlib

from _helpers import ROOT, SRC

LAYERS = (
    "contract",
    "db",
    "catalog",
    "rules",
    "ingest",
    "alarm",
    "compliance",
    "report",
    "synth",
    "bench",
    "gui",
)

#: 层 → 禁止依赖的层
FORBIDDEN_EDGES = {
    "contract": {"db", "catalog", "rules", "ingest", "alarm", "compliance", "report", "synth", "bench", "gui"},
    "db": {"alarm", "compliance", "report", "bench", "gui", "synth"},
    "catalog": {"rules", "alarm", "compliance", "report", "bench", "gui"},
    "rules": {"alarm", "compliance", "report", "bench", "gui", "synth"},
    "ingest": {"alarm", "compliance", "report", "bench", "gui"},
    "alarm": {"compliance", "report", "bench", "gui"},
    "compliance": {"report", "bench", "gui"},
    "report": {"bench", "gui"},
    "synth": {"alarm", "compliance", "report", "bench", "gui"},
    "bench": {"report", "gui"},
}


def _layer_of(rel: pathlib.PurePath):
    parts = rel.parts
    if len(parts) < 2:
        return None
    return parts[1] if parts[1] in LAYERS else None


def _pmc_imports(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            if name.startswith("pmc."):
                tail = name.split(".", 2)
                if len(tail) >= 2 and tail[1] in LAYERS:
                    found.add(tail[1])
    return found


def _all_module_files():
    out = []
    for path in sorted(pathlib.Path(SRC).rglob("*.py")):
        rel = path.relative_to(SRC / "pmc")
        layer = _layer_of(rel)
        if layer:
            out.append((layer, path, rel))
    return out


def test_no_forbidden_layer_edges():
    violations = []
    for layer, path, rel in _all_module_files():
        allowed_blacklist = FORBIDDEN_EDGES.get(layer, set())
        hits = _pmc_imports(path) & allowed_blacklist
        if hits:
            violations.append(
                "{0} -> {1}".format(str(rel).replace("\\", "/"), ",".join(sorted(hits)))
            )
    assert not violations, "跨层依赖违规：" + "; ".join(violations)


def test_only_gui_layer_may_import_pyside():
    offenders = []
    for layer, path, rel in _all_module_files():
        if layer == "gui":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.startswith("PySide6"):
                    offenders.append(str(rel).replace("\\", "/"))
    assert not offenders, "非界面层不得导入 Qt：" + ",".join(offenders)


def test_layer_registry_matches_disk():
    present = {p.name for p in (SRC / "pmc").iterdir() if (p / "__init__.py").exists()}
    assert set(LAYERS) <= present, "缺层：" + ",".join(sorted(set(LAYERS) - present))
    assert (ROOT / "plan").is_dir()
