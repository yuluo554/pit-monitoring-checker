# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir 双 exe（plan/11 §九）。

一个 COLLECT 里两个入口，共用同一份 `_internal`：

* `pmc.exe`（console=True）—— 与 `pmc` 脚本同一个 main，是没有 Python 的机器上的
  **脚本化验证通路**：`selfcheck` / `rulesets` / `synth --check` / `bench` / `report` 全可跑；
* `pmc-gui.exe`（console=False）—— 桌面界面。

三条实测坑（写在这里免得下轮再踩）：

1. spec 里的相对路径解析到 `SPECPATH` 而不是 CWD，所以一律走 `ROOT` 绝对路径；
2. `Analysis` 的入口脚本必须真的存在且不在 `src/` 包内（用 `packaging/*_main.py` 两个薄壳），
   否则冻结后模块身份与 `pmc.__main__` 抢 `__main__`；
3. `datas` 是**白名单**：只收运行必需数据，测试夹具与 plan 文档一律不入包
   （构建后由 `scripts/dist_audit.py` 逐份 sha256 对账，多一分少一分都是失败）。
"""

import pathlib

from PyInstaller.utils.hooks import collect_submodules

ROOT = pathlib.Path(SPECPATH).resolve().parent  # noqa: F821 - PyInstaller 注入的是 spec 所在目录
SRC = ROOT / "src"
DATA = ROOT / "data"

#: 运行必需数据白名单（相对 data/ 的目录名）
DATA_FOLDERS = ("dict", "rulesets", "clauses", "raw", "truth", "golden")

datas = []
for folder in DATA_FOLDERS:
    source = DATA / folder
    if source.is_dir():
        datas.append((str(source), "data/" + folder))

hiddenimports = collect_submodules("pmc") + ["sqlite3", "zipfile", "hashlib", "json"]

common = dict(
    pathex=[str(SRC)],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    noarchive=False,
)

cli_analysis = Analysis(
    [str(ROOT / "packaging" / "pmc_cli_main.py")],
    datas=list(datas),
    **common,
)
gui_analysis = Analysis(
    [str(ROOT / "packaging" / "pmc_gui_main.py")],
    datas=list(datas),
    **common,
)

cli_pyz = PYZ(cli_analysis.pure)
gui_pyz = PYZ(gui_analysis.pure)

cli_exe = EXE(
    cli_pyz,
    cli_analysis.scripts,
    [],
    [],
    exclude_binaries=True,
    name="pmc",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    icon=None,
)
gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    [],
    exclude_binaries=True,
    name="pmc-gui",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,
)

coll = COLLECT(
    cli_exe,
    gui_exe,
    cli_analysis.binaries,
    cli_analysis.zipfiles,
    cli_analysis.datas,
    gui_analysis.binaries,
    gui_analysis.zipfiles,
    gui_analysis.datas,
    strip=False,
    upx=False,
    name="pmc",
)
