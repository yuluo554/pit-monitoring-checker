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


def test_requires_python_floor_38():
    assert DOC["project"]["requires-python"] == ">=3.8"


def test_extras_declare_gui_pkg_and_pytest_runtime():
    extras = DOC["project.optional-dependencies"]
    assert {"dev", "gui", "pkg"} <= set(extras)
    dev = " ".join(extras["dev"])
    assert "pytest" in dev and "pyyaml" in dev, "测试期 import（yaml）必须在 dev 里声明"


def test_gui_extra_caps_pyside6_for_py38():
    gui = " ".join(DOC["project.optional-dependencies"]["gui"])
    assert "PySide6" in gui and "<" in gui, "重 GUI 依赖在老解释器上必须写上界"


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
