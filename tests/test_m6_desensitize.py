"""M6 脱敏门：扫描器可用、入库面干净、放行有据可查、留档不缺项。

扫描器的含金量取决于它自己被测过：`--selftest` 的逐类阳性对照随全量测试常驻，
再加"往临时目录撒一个真泄露必须被打挂"的反证 —— 否则 `DESENSITIZE_OK` 只是一句打印。
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

from _helpers import ROOT

AUDIT = ROOT / "scripts" / "desensitize_audit.py"
RELEASE = ROOT / "plan" / "RELEASE-M6.md"

REVIEW_PREFIX = "DESENSITIZE_REVIEW "


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(AUDIT)] + list(args),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )


def _registry():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import desensitize_audit as module

        return tuple(module.KNOWN_HISTORY_REVIEW)
    finally:
        sys.path.pop(0)


def _review_findings(stdout: str):
    """把 `位置 [类别] xN` 解析回 (标识, 类别)，用于和登记表对账。"""
    found = set()
    for line in stdout.splitlines():
        if not line.startswith(REVIEW_PREFIX):
            continue
        pieces = line[len(REVIEW_PREFIX) :].split(" ")
        if len(pieces) < 2:
            continue
        location = pieces[0].rsplit(":", 1)[0]  # 去掉行号，留 blob 标识
        found.add((location, pieces[1].strip("[]")))
    return found


def test_scanner_selftest_passes():
    result = _run("--selftest")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DESENSITIZE_SELFTEST_OK" in result.stdout
    assert "FAIL" not in result.stdout


def test_tracked_tree_has_no_leak():
    """入库面必须真的 0 命中，而不是"有豁免所以看起来绿"。"""
    result = _run("--mode", "tracked")
    assert result.returncode == 0, result.stderr
    assert "硬门=0 复核=0" in result.stdout, result.stdout


def test_commit_messages_have_no_leak():
    result = _run("--mode", "messages")
    assert result.returncode == 0, result.stderr
    assert "硬门=0 复核=0" in result.stdout, result.stdout


def test_history_findings_are_subset_of_registry():
    """历史命中只准来自已登记的 blob（CI 浅克隆命中更少，属正常形态，不判失败）。"""
    result = _run("--mode", "history")
    assert result.returncode == 0, result.stderr
    allowed = {(label, category) for label, category in _registry()}
    assert _review_findings(result.stdout) <= allowed, result.stdout


def test_registry_entries_are_documented():
    text = RELEASE.read_text(encoding="utf-8")
    for label, category in _registry():
        assert label in text, "登记放行 {0} 却没写进 RELEASE-M6".format(label)
        assert category in text


def test_offender_in_a_real_tree_is_caught(tmp_path):
    # 片段拼接：守门测试自己也不许留完整字面值，否则它就是下一个泄露源
    mobile = "1" + "38" + "0011" + "22" + "33"
    victim = tmp_path / "leak.csv"
    victim.write_text("监测人," + mobile + "\n", encoding="utf-8")
    result = _run("--mode", "tracked", "--include", str(victim))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "DESENSITIZE_FAIL" in result.stderr
    assert "phone_number" in result.stderr
    assert mobile not in result.stdout + result.stderr, "输出回显了命中的原文"


def test_zip_payload_entry_is_caught(tmp_path):
    """xlsx 之类容器要能抓到藏在 docProps 里的创建者 —— 整包扫等于没扫。"""
    mail = "li" + "si" + "@" + "proj-company" + "." + "com"
    path = tmp_path / "report.xlsx"
    archive = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)
    archive.writestr("[Content_Types].xml", "<Types/>")
    archive.writestr("docProps/core.xml", "<dc:creator>" + mail + "</dc:creator>")
    archive.writestr("xl/_rels/workbook.xml.rels", "<Relationships/>")
    archive.close()
    result = _run("--mode", "tracked", "--include", str(path))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "docProps/core.xml" in result.stderr
    assert "email" in result.stderr
    assert mail not in result.stdout + result.stderr


def test_scanner_and_guard_test_pass_their_own_scan():
    for path in (AUDIT, Path(__file__).resolve()):
        result = _run("--mode", "tracked", "--include", str(path))
        assert result.returncode == 0, "{0} -> {1}".format(path.name, result.stderr)


def test_release_doc_records_every_step_and_mode():
    text = RELEASE.read_text(encoding="utf-8")
    for token in (
        "git ls-files",
        "desensitize_audit.py --mode tracked",
        "--mode history",
        "--mode messages",
        "%ae %ce",
        "--include reports/out",
        "scripts/dist_audit.py",
        "片段拼接",
        "不回显",
        "个人路径",
        "手机号",
    ):
        assert token in text, "RELEASE-M6 缺留档项：{0}".format(token)
