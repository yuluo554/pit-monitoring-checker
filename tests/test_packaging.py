"""打包与依赖分层守门：核心零依赖、extras 覆盖测试期 import、入口点真实存在。

pyproject 用行解析而不是 tomllib：3.8 没有 tomllib，两端必须走同一套断言，
否则"3.8 跳过、3.12 才测"就是静默少跑。
"""

from __future__ import annotations

import ast
import importlib
import re

from _helpers import ROOT

TOML = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _parse(text: str):
    """把 INI 风格的 pyproject 解析成 {section: {key: value}}；数组值用 ast.literal_eval。"""
    out = {}
    section = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            out.setdefault(section, {})
            continue
        if "=" not in line or section is None:
            continue
        key, _, raw = line.partition("=")
        raw = raw.strip()
        if raw.startswith("["):
            while "]" not in raw and i < len(lines):
                raw += " " + lines[i].strip()
                i += 1
            raw = re.sub(r"#.*", "", raw).strip()
            out[section][key.strip()] = _literal(raw)
        else:
            raw = re.sub(r"#.*", "", raw).strip()
            out[section][key.strip()] = _literal(raw)
    return out


def _literal(raw: str):
    """内联表（{ name = "..." }）与裸字符串不是 Python 字面量：解析不了就原样留着。"""
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return raw


DOC = _parse(TOML)


def test_identity_fields_parse():
    assert DOC["project"]["name"] == "pit-monitoring-checker"
    assert "README.md" in DOC["project"]["readme"]
    assert isinstance(DOC["project"]["dependencies"], list)


def test_core_dependencies_are_empty():
    assert DOC["project"]["dependencies"] == [], "内核必须零第三方依赖"


def test_no_report_extra_xlsx_goes_stdlib():
    extras = DOC["project.optional-dependencies"]
    assert "report" not in extras, "xlsx 走标准库直写 OOXML，不该出现 report extras（plan/06 D10）"


def test_requires_python_floor_matches_the_declared_surface():
    """floor 是声明面的一部分：3.8 已于 2024-10 EOL，声明里不许再留它。

    这条断言从 `>=3.8` 重指到 `>=3.12`（本轮 DEC-1=A）：它本来就是「声明回声」，
    跟着声明走，而不是被删掉 —— 同时把 classifiers 里也不许出现 3.8 一并锁住。
    """
    assert DOC["project"]["requires-python"] == ">=3.12"
    classifiers = " ".join(DOC["project"]["classifiers"])
    assert "Python :: 3.8" not in classifiers
    assert "Python :: 3.12" in classifiers


def test_extras_declare_gui_pkg_and_pytest_runtime():
    extras = DOC["project.optional-dependencies"]
    assert {"dev", "gui", "pkg"} <= set(extras)
    dev = " ".join(extras["dev"])
    assert "pytest" in dev and "pyyaml" in dev, "测试期 import（yaml）必须在 dev 里声明"


def test_gui_extra_has_floor_and_no_stale_cp38_cap():
    """`<6.7` 是专为 3.8 的 cp38 wheel 设的上界（plan/RELEASE-M6.md §四）。

    floor 抬到 3.12 后这个上界失去存在理由：留着它，GUI 永远停在 2024 年的 Qt 6.6.3.1。
    断言方向从「必须有上界」翻成「必须没有陈旧上界」—— 依据同一段历史，不是放宽。
    """
    gui = " ".join(DOC["project.optional-dependencies"]["gui"])
    assert "PySide6>=6.6" in gui, "gui extra 必须有下界"
    assert "<6.7" not in gui, "cp38 时代的 PySide6 上界必须随 floor 抬升一起撤掉"


def test_console_script_target_exists():
    _assert_entrypoint(DOC["project.scripts"]["pmc"])


def test_gui_script_target_exists():
    _assert_entrypoint(DOC["project.gui-scripts"]["pmc-gui"])


def _assert_entrypoint(spec: str) -> None:
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    assert callable(getattr(module, attr)), "{0} 不可调用".format(spec)


def test_package_find_is_src_layout():
    assert DOC["tool.setuptools.packages.find"]["where"] == ["src"]
    assert (ROOT / "src" / "pmc" / "__init__.py").is_file()


def test_pytest_addopts_has_no_quiet_flag():
    addopts = DOC["tool.pytest.ini_options"]["addopts"]
    assert "-q" not in addopts.split(), "addopts 里的 -q 与命令行 -q 叠加成 -qq，汇总行会整行消失"
    assert DOC["tool.pytest.ini_options"]["pythonpath"] == ["src"]


def test_version_matches_package():
    import pmc

    assert DOC["project"]["version"] == pmc.__version__
