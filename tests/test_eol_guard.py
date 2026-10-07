"""EOL 守门：Windows 开发机 core.autocrlf=true 会把全新 clone 重写成 CRLF，
而本项目的基准要求逐字节可复现 —— 这道门只在"开发机本机的全新 clone"才暴露，故入仓常驻。
"""

from __future__ import annotations

from _helpers import ROOT

SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".tmp_verify",
    ".tmp_parse",
    ".venv",
    "venv",
    "build",
    "dist",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}

TEXT_SUFFIX = {
    ".py", ".md", ".toml", ".yml", ".yaml", ".json", ".txt", ".csv",
    ".gitignore", ".gitattributes", ".cfg", ".ini", ".sql", ".html", ".js",
}


def _text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIX or not path.suffix:
            yield path


def test_no_cr_in_tracked_text():
    offenders = []
    for path in _text_files():
        with open(path, "rb") as handle:
            blob = handle.read()
        if b"\r" in blob:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, "出现 CR，冻结基准会因 EOL 重写全挂：{0}".format(offenders)


def test_gitattributes_declares_lf():
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "text=auto eol=lf" in text
