"""M5 打包门：spec 白名单、冻结数据定位、审计器反证、文档覆盖。

审计器的反证（`--selftest`）随全量测试常驻：
"构建后遍历断言"若没人测过审计器本身，`DIST_AUDIT_OK` 就只是一句打印。
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from _helpers import ROOT, SRC
from pmc import paths as pmc_paths

SPEC = ROOT / "packaging" / "pmc.spec"
CLI_MAIN = ROOT / "packaging" / "pmc_cli_main.py"
GUI_MAIN = ROOT / "packaging" / "pmc_gui_main.py"
AUDIT = ROOT / "scripts" / "dist_audit.py"


def spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


def test_spec_and_entry_shims_exist():
    for path in (SPEC, CLI_MAIN, GUI_MAIN):
        assert path.is_file(), "缺打包文件：{0}".format(path)


def test_spec_datas_is_a_whitelist_without_tests_or_plan():
    """白名单按 AST 取，不按全文子串判 —— spec 的注释本来就要写"什么不许入包"。"""
    import ast

    tree = ast.parse(spec_text(), filename="pmc.spec")
    folders = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", "") == "DATA_FOLDERS":
                    folders = tuple(element.value for element in node.value.elts)
    assert folders == ("dict", "rulesets", "clauses", "raw", "truth", "golden")
    present = {path.name for path in (ROOT / "data").iterdir() if path.is_dir()}
    assert set(folders) <= present, "白名单里有没有落地的目录：{0}".format(set(folders) - present)
    for banned in ("tests", "fixtures", "plan", ".tmp_verify", ".github"):
        assert banned not in folders
    assert "collect_submodules(\"pmc\")" in spec_text().replace("'", "'"), "hiddenimports 必须覆盖惰性导入"


def test_spec_root_is_parent_of_specpath():
    """PyInstaller 的 SPECPATH 是 spec 所在目录；按 CWD 或按 spec 目录当仓库根都会指错。"""
    assert "pathlib.Path(SPECPATH).resolve().parent" in spec_text()


def test_entry_shims_carry_no_behavior():
    for path in (CLI_MAIN, GUI_MAIN):
        source = path.read_text(encoding="utf-8")
        assert "sys.exit(" in source
        for banned in ("random", "socket", "urllib", "http", "open(", "sqlite3"):
            assert banned not in source, "{0} 的入口薄壳里不该有：{1}".format(path.name, banned)


def test_gitignore_does_not_swallow_spec():
    lines = [line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert "*.spec" not in lines, "*.spec 通配会把打包脚本一起吞掉"
    assert "dist/" in lines


@pytest.mark.parametrize("branch", ["explicit", "env", "meipass", "stale_meipass", "no_meipass"])
def test_find_data_dir_priority(tmp_path, monkeypatch, branch):
    """冻结通路实测：优先级顺序是 exe 内嵌数据验证的前提，改顺序就是口径变更。

    PyInstaller 6 的 onedir 把 `sys._MEIPASS` 指向 `_internal`，所以内嵌数据落在
    `_internal/data`；第 2、3 顺位分别是 `exe/_internal/data` 与 `exe/data`（5.x 兼容与裸拷形态）。
    """
    def marker_dir(base: pathlib.Path) -> str:
        for name in pmc_paths._DATA_MARKER:
            (base / name).mkdir(parents=True, exist_ok=True)
        return str(base)

    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    (exe_dir / "pmc.exe").write_bytes(b"MZ")
    internal = exe_dir / "_internal"
    meipass = internal  # PyInstaller 6：_MEIPASS 就是 _internal
    marker_dir(internal / "data")
    marker_dir(exe_dir / "data")
    env_dir = marker_dir(tmp_path / "envdata")
    explicit = marker_dir(tmp_path / "explicit")
    stale = str(tmp_path / "gone")  # 故意不存在：验证顺位下移而不是命中空目录

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "pmc.exe"), raising=False)
    monkeypatch.delenv(pmc_paths.ENV_VAR, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.chdir(tmp_path)

    if branch == "explicit":
        found = pmc_paths.find_data_dir(explicit)
        want = explicit
    elif branch == "env":
        monkeypatch.setenv(pmc_paths.ENV_VAR, env_dir)
        monkeypatch.setattr(sys, "_MEIPASS", stale, raising=False)
        found = pmc_paths.find_data_dir(None)
        want = env_dir
    elif branch == "meipass":
        monkeypatch.setattr(sys, "_MEIPASS", meipass, raising=False)
        found = pmc_paths.find_data_dir(None)
        want = str(internal / "data")
    elif branch == "stale_meipass":
        # _MEIPASS 指向不存在的位置：顺位下移，但不能落到 exe 同级 data（那是更低的顺位）
        monkeypatch.setattr(sys, "_MEIPASS", stale, raising=False)
        found = pmc_paths.find_data_dir(None)
        want = str(internal / "data")
    else:
        # 没有 _MEIPASS（PyInstaller 5 或裸拷）：第 2/3 顺位 exe 同级与 _internal
        found = pmc_paths.find_data_dir(None)
        want = str(exe_dir / "data")
    assert os.path.normcase(found) == os.path.normcase(want)
    if branch in ("explicit", "env"):
        return
    # 内嵌优先级永远高于"CWD 上溯找仓库"，否则在仓库树里跑 exe 会验了个寂寞
    marker_dir(tmp_path / "data")
    monkeypatch.delenv(pmc_paths.ENV_VAR, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", meipass, raising=False)
    assert os.path.normcase(pmc_paths.find_data_dir(None)) == os.path.normcase(str(internal / "data"))


def test_env_var_beats_frozen(tmp_path, monkeypatch):
    env_dir = tmp_path / "env"
    for name in pmc_paths._DATA_MARKER:
        (env_dir / name).mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pmc.exe"), raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)
    for base in (tmp_path / "meipass", tmp_path / "_internal"):
        for name in pmc_paths._DATA_MARKER:
            (base / "data" / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(pmc_paths.ENV_VAR, str(env_dir))
    assert os.path.normcase(pmc_paths.find_data_dir(None)) == os.path.normcase(str(env_dir))


def test_dist_auditor_selftest_passes():
    """审计器必须能被伪造产物打挂：这条断言在 3.8/3.12 两端都跑。"""
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(AUDIT), "--selftest"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DIST_AUDIT_SELFTEST_OK" in result.stdout
    assert "PASS" in result.stdout


def test_dist_auditor_fails_without_build(tmp_path):
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(AUDIT), "--dist", str(tmp_path / "nope")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "DIST_AUDIT_FAIL" in result.stderr
    assert "DIST_AUDIT_OK" not in result.stdout


def test_readme_documents_report_gui_and_exe():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for token in (
        "pmc report --kind daily",
        "pmc gui",
        "PySide6",
        "dist/pmc/pmc-gui.exe",
        "追溯清单",
        "不判定基坑是否安全",
    ):
        assert token in text, "README 未覆盖 M5 交付面：{0}".format(token)


def test_plan11_exists_and_is_referenced():
    plan = ROOT / "plan" / "11-报告与打包.md"
    assert plan.is_file()
    text = (ROOT / "plan" / "00-README总览.md").read_text(encoding="utf-8")
    assert "11" in text, "plan/00 索引未登记 11 号文档"
