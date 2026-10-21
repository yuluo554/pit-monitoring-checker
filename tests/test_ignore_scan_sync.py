"""两份名单的一致性守门：`.gitignore` 的"一次性产物目录"与脱敏扫描器的 `EXCLUDE_DIRS`。

为什么需要这道门：M6 之前 `.gitignore` 的注释写着"EOL 与脱敏扫描器 SKIP 名单同步"，
但两边实际靠人肉对齐 —— 一边加了目录另一边忘了，扫描器就会开始扫不该扫的产物（噪音），
或者更糟：漏扫了该扫的东西。这里把"同一件事的两份名单"变成可执行的断言。

**反向豁免同样重要**：`reports/` 被 `.gitignore` 忽略，却**故意不在** `EXCLUDE_DIRS` 里 ——
报告产物必须能被 `--include reports/out` 扫到（`dist_audit --selftest` 有一条
「报告含 home 路径被抓」的反证）。所以本门不是"两边完全相等"，而是"差集必须逐条登记理由"。
"""

from __future__ import annotations

import re

from _helpers import ROOT

GITIGNORE = (ROOT / ".gitignore").read_text(encoding="utf-8")
AUDIT_SRC = (ROOT / "scripts" / "desensitize_audit.py").read_text(encoding="utf-8")

#: `.gitignore` 里标为「一次性验证产物」的那一段（注释 + 连续目录条目）
SCRATCH_SECTION_MARK = "一次性验证产物只允许落在这些目录"

#: 故意不在 EXCLUDE_DIRS 的忽略目录 → 理由。新增差异必须在这里登记，否则本门红。
INTENTIONALLY_SCANNED = {
    "reports/": (
        "报告产物必须可被脱敏扫描（--include reports/out）；"
        "dist_audit --selftest 有「报告含 home 路径被抓」的反证"
    ),
}


def _audit_exclude_dirs() -> set:
    """从 desensitize_audit.py 里取 EXCLUDE_DIRS 的字面集合。"""
    match = re.search(r"^EXCLUDE_DIRS\s*=\s*\{(.*?)^\}", AUDIT_SRC, re.S | re.M)
    assert match, "scripts/desensitize_audit.py 里找不到 EXCLUDE_DIRS 字面量"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _scratch_dirs() -> list:
    """`.gitignore` 里「一次性验证产物」那一段的目录名（去尾斜杠）。

    该段以一条注释开头（可能跨行续注），后接连续目录条目；遇到第一条非注释、非目录条目即止。
    """
    lines = GITIGNORE.splitlines()
    start = next(i for i, line in enumerate(lines) if SCRATCH_SECTION_MARK in line)
    out = []
    for line in lines[start + 1:]:
        entry = line.strip()
        if entry.startswith("#"):
            continue
        if not entry:
            break
        if not entry.endswith("/"):
            break
        out.append(entry.rstrip("/"))
    return out


def _ignored_dirs_that_matter() -> list:
    """`.gitignore` 里所有"整目录忽略"的条目，去尾斜杠、去掉通配与注释。"""
    out = []
    for line in GITIGNORE.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or not entry.endswith("/"):
            continue
        if any(ch in entry for ch in "*?["):
            continue
        out.append(entry.rstrip("/"))
    return out


def test_scratch_section_is_non_empty():
    """自指守门：解析出 0 条就等于这道门在空转。"""
    scratch = _scratch_dirs()
    assert scratch, "`.gitignore` 的「一次性验证产物」段解析为空"
    assert ".tmp_verify" in scratch and ".tmp_parse" in scratch, scratch


def test_scratch_dirs_are_all_excluded_by_the_scanner():
    """一次性产物目录必须全在 EXCLUDE_DIRS 里 —— 否则扫描器会去扫 14 MB 的验证副本。"""
    excludes = _audit_exclude_dirs()
    missing = sorted(d for d in _scratch_dirs() if d not in excludes)
    assert not missing, "这些一次性产物目录没进脱敏扫描器的 EXCLUDE_DIRS：{0}".format(missing)


def test_every_present_ignored_dir_is_either_scanned_or_registered():
    """工作区里**真实存在**的忽略目录，要么被扫描器跳过，要么在 INTENTIONALLY_SCANNED 里登记理由。

    只对存在的目录断言是有意的：扫描器走的是真实文件树，`.gitignore` 里那条 `htmlcov/`
    在当前工作区没有实体，管它没有意义（也不能要求每个可能的产物目录都进 EXCLUDE_DIRS）。
    `reports/` 的差异是这条门的重点 —— 它存在、被忽略、却故意可扫，理由写在登记表里。
    """
    excludes = _audit_exclude_dirs()
    unregistered = []
    for name in _ignored_dirs_that_matter():
        if name in excludes or (name + "/") in INTENTIONALLY_SCANNED:
            continue
        if not (ROOT / name).is_dir():
            continue
        unregistered.append(name)
    assert not unregistered, (
        "这些忽略目录既不在 EXCLUDE_DIRS 也没登记为「故意可扫」：{0}".format(unregistered)
    )


def test_update_dir_is_out_of_the_scan():
    """`update/` 是迭代交接物（含本机实测日志与绝对路径），必须进 EXCLUDE_DIRS。

    它在 `.gitignore` 里只挡住提交；不挡住扫描器，一次 `--include .` 就会把
    诊断报告里的本机路径当泄露报出来 —— 那会把真泄露埋进噪音里。
    """
    assert "update" in _audit_exclude_dirs()


def test_reports_is_deliberately_scannable():
    """反向锁：`reports/` 不许被"顺手"加进 EXCLUDE_DIRS。

    报告产物里的 home 路径是真实泄露面，扫描器跳过它等于给自己开一扇后门。
    """
    assert "reports" not in _audit_exclude_dirs(), (
        "reports 必须保持可扫：报告里可能带本机路径，跳过它就是漏扫"
    )
    assert "reports/" in INTENTIONALLY_SCANNED, "reports 的豁免理由必须留在登记表里"


def test_registered_exemptions_are_still_needed():
    """登记表不许腐烂：登记了的差异，实际必须仍然是差异（否则删掉这条登记）。"""
    excludes = _audit_exclude_dirs()
    for name in INTENTIONALLY_SCANNED:
        assert name.rstrip("/") not in excludes, (
            "{0} 已进 EXCLUDE_DIRS，登记表里的豁免理由过期了".format(name)
        )


def test_include_flag_is_documented_for_reports():
    """`--include reports/out` 是上面那条豁免的唯一用途，文档里必须有它。"""
    assert "--include reports/out" in AUDIT_SRC
