"""EOL 守门：Windows 开发机 core.autocrlf=true 会把全新 clone 重写成 CRLF，
而本项目的基准要求逐字节可复现 —— 这道门只在"开发机本机的全新 clone"才暴露，故入仓常驻。

扫描面取 **git 自己的口径**（跟踪 + 未跟踪但未被忽略），不再手维护一份 SKIP 名单：
M6 的干净环境验证把 clone 放在 `.clean-store/`（已在 .gitignore），手维护名单没有它，
于是这道门被 pip 生成的 `*.egg-info/PKG-INFO` 里的 CRLF 打挂 —— 名单与 .gitignore 一漂移，
门就开始误伤，而误伤的门很快就会被当成噪音绕过去。
"""

from __future__ import annotations

import subprocess

from _helpers import ROOT

TEXT_SUFFIX = {
    ".py", ".md", ".toml", ".yml", ".yaml", ".json", ".txt", ".csv",
    ".gitignore", ".gitattributes", ".cfg", ".ini", ".sql", ".html", ".js",
}


def _git_ls_files(*args: str):
    result = subprocess.run(
        ["git", "ls-files", "-z"] + list(args),
        cwd=str(ROOT),
        capture_output=True,
    )
    return [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]


def _candidate_files():
    """该入库的东西 = 已跟踪 + 未跟踪且没被 .gitignore 排除。"""
    names = set(_git_ls_files()) | set(_git_ls_files("--others", "--exclude-standard"))
    for name in sorted(names):
        path = ROOT / name
        if path.suffix.lower() in TEXT_SUFFIX or not path.suffix:
            if path.is_file():
                yield path


def _offenders():
    return [str(path.relative_to(ROOT)) for path in _candidate_files() if b"\r" in path.read_bytes()]


def test_no_cr_in_tracked_text():
    offenders = _offenders()
    assert not offenders, "出现 CR，冻结基准会因 EOL 重写全挂：{0}".format(offenders)


def test_gitattributes_declares_lf():
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "text=auto eol=lf" in text


def test_ignored_scratch_dirs_are_out_of_the_scan():
    """`.tmp_verify/` 里放一个 CRLF 文件：它永远不该被提交，也就不该进这道门的视野。"""
    probe = ROOT / ".tmp_verify" / "crlf_probe.txt"
    probe.parent.mkdir(parents=True, exist_ok=True)  # 全新 clone 里这个目录还不存在
    probe.write_bytes(b"a\r\nb\r\n")
    try:
        names = [str(path.relative_to(ROOT)) for path in _candidate_files()]
        assert not any("crlf_probe" in name for name in names), names[:5]
        assert not _offenders()
    finally:
        probe.unlink()


def test_scan_still_catches_a_committable_crlf_file():
    """阳性对照：一个"会被提交"的 CRLF 文件必须被点名，否则上面那条豁免就是自废武功。"""
    probe = ROOT / "crlf_positive_control.txt"
    probe.write_bytes(b"a\r\nb\r\n")
    try:
        assert "crlf_positive_control.txt" in _offenders()
    finally:
        probe.unlink()
