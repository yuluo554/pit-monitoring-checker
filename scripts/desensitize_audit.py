r"""入库脱敏审计（plan/HANDOFF-M6 §二.2、§六）。

三种模式：

* `tracked`  —— 全部 git 跟踪文件的当前内容（zip 容器逐条目展开）：硬门，必须 0 命中；
* `messages` —— 提交与标签的作者/邮箱/标题/正文，外加 `.git/config` 里带凭据的远端地址：硬门，必须 0 命中；
* `history`  —— 全部历史 blob：号段类样本按类别分硬门与复核，复核项必须逐条登记在 `KNOWN_HISTORY_REVIEW`，
  没登记过的一律算新泄露 —— 否则"历史模式永远是红的"会退化成没人看。

用法::

    py -3.12 -X utf8 scripts/desensitize_audit.py                    # 三模式全跑
    py -3.12 -X utf8 scripts/desensitize_audit.py --mode tracked
    py -3.12 -X utf8 scripts/desensitize_audit.py --include reports/out
    py -3.12 -X utf8 scripts/desensitize_audit.py --selftest

设计取舍：

* **模式一律片段拼接**：源码里不留任何能被自家模式命中的完整字面值，扫描器要能过自己的扫描；
* **个人身份标记取自运行环境的 home 路径族**（含 `USERPROFILE`/`HOMEDRIVE+HOMEPATH` 与两种分隔符变体），
  不写死模式表，换台机器照样有效；**只按路径形状判**，裸用户名不参与硬门（CI runner 的账号名就是 `runner`）；
* **输出只给「位置 [类别] x次数」，绝不回显命中原文** —— 否则审计报告本身成了第二个泄露源；
* sha256 之类十六进制串与号段类模式天然打架，数字类模式一律加非 hex 相邻守卫。
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
import zipfile
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MARKER_OK = "DESENSITIZE_OK"
MARKER_FAIL = "DESENSITIZE_FAIL"
MARKER_REVIEW = "DESENSITIZE_REVIEW"
MARKER_SUMMARY = "SCAN_SUMMARY"
MARKER_SELFTEST_OK = "DESENSITIZE_SELFTEST_OK"
MARKER_SELFTEST_FAIL = "DESENSITIZE_SELFTEST_FAIL"

#: 树扫描跳过的目录：生成物、缓存与工具产物都不算入库内容
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tmp_verify",
    ".tmp_parse",
    ".qoder-credits",
    "dist",
    "build",
}

_HEX_IN = r"(?<![\da-fA-F])"
_HEX_OUT = r"(?![\da-fA-F])"

HARD_CATEGORIES = (
    "credential",
    "private_key_block",
    "drive_path",
    "home_path",
    "email",
    "internal_ip",
    "internal_domain",
    "personal_identity",
)

#: 号段类：检测器自己的阳性对照需要"真实形态"的假样本，逐条登记后放行
REVIEW_CATEGORIES = ("phone_number", "id_number")

#: `data/README.md §三` 白名单号段：199 保留号段 19900000000–19900009999
_PHONE_ALLOWED = re.compile("^1990000" + r"\d{4}$")

#: RFC 2606/6761 保留域 + GitHub noreply 别名（别名指向账号而非真实信箱，发布同账号时不构成泄露）
_EMAIL_ALLOWED_DOMAINS = (
    "example.com",
    "example.org",
    "example.net",
    "example.edu",
    "example.invalid",
    "example",
    "test",
    "localhost",
    "localhost." + "localdomain",
    "users.noreply.github.com",
    "noreply.github.com",
)

#: 通用示例占位路径放行：命中的盘符片段天然止于分隔符（字符类不含分隔符），所以按整段比对
_DRIVE_ALLOWED = re.compile("^[XxDd]:[\\\\/](?:[Xx]|path|dir|TEMP|TMP)$")

RULES: List[Tuple[str, "re.Pattern[str]"]] = [
    ("phone_number", re.compile(_HEX_IN + "1" + "[3-9]" + r"\d{9}" + _HEX_OUT)),
    (
        # 18 位号码要按"6 位区划 + 8 位出生日期 + 3 位顺序 + 校验位"的真实结构判：
        # 只按"连续 18 位数字"判会把台账里的长数字串（时间戳、指纹片段）一并误伤，
        # 而误伤一旦被登记放行，真号就跟着一起被放行了。
        "id_number",
        re.compile(
            _HEX_IN
            + r"\d{6}"
            + r"(?:19|20)" + r"\d{2}"
            + r"(?:0[1-9]|1[0-2])"
            + r"(?:[0-2]\d|3[01])"
            + r"\d{3}"
            + r"[\dXx]"
            + _HEX_OUT
        ),
    ),
    (
        "email",
        re.compile(
            r"[A-Za-z0-9][A-Za-z0-9._%+\-]*" + "@" + r"[A-Za-z0-9][A-Za-z0-9.\-]*" + r"\.[A-Za-z]{2,}"
        ),
    ),
    ("credential", re.compile("AKIA" + r"[A-Z0-9]{16}")),
    (
        "credential",
        re.compile(
            r"(?:ghp|gho|ghu|ghs|ghr)" + "_" + r"[A-Za-z0-9]{30,}"
            + "|github_" + "pat_" + r"[A-Za-z0-9_]{36,}"
        ),
    ),
    ("credential", re.compile("xox" + r"[baprse]" + "-" + r"[0-9A-Za-z\-]{10,}")),
    (
        "credential",
        re.compile(
            r"(?i)(?:api[_-]?key"
            + r"|apikey|secret[_-]?key|client[_-]?secret|access[_-]?token|auth[_-]?token"
            + r"|passphrase|password|passwd)"
            + r'''["]?[\s]*[:=][\s]*["']?[^\s"';<>,]{8,}'''
        ),
    ),
    ("credential", re.compile(r"://" + r"[^/@\n\s:]+:" + r"[^/@\s]+@")),
    ("private_key_block", re.compile("-----BEGIN " + "[A-Z ]*PRIVATE KEY" + "-----")),
    ("drive_path", re.compile(r"(?<![A-Za-z0-9/\\])[A-Za-z]:[\\\\/][A-Za-z0-9_.\-]{2,}")),
    ("home_path", re.compile("(?:/Users/|/home/" + "|\\\\Users\\\\)" + r"[A-Za-z0-9._\-]{3,}")),
    (
        "internal_ip",
        re.compile(
            r"(?<![A-Za-z0-9/\\.])(?:10"
            + r"\.\d{1,3}\.\d{1,3}\.\d{1,3}"
            + r"|172" + r"\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
            + r"|192\.168\.\d{1,3}\.\d{1,3}"
            + r"|169\.254\.\d{1,3}\.\d{1,3}"
            + r"|127" + r"\.\d{1,3}\.\d{1,3}\.\d{1,3}"
            + r")(?![A-Za-z0-9./\\])"
        ),
    ),
    (
        "internal_domain",
        re.compile(
            r"\.(?:local|localhost|localdomain|lan|corp|internal|intra" + "|intranet)[^A-Za-z0-9]"
            + r"|\b(?:gitlab|jira|confluence|nextcloud|nexus|sonar|vpn|nas)" + r"\.[A-Za-z]"
        ),
    ),
]

#: 历史里已定性放行的号段类样本，元素为 `(blob 标识, 类别)`；标识可写 `路径@sha10`（精确版本）
#: 或 `路径@`（该路径的全部历史版本）。新增条目必须同时写进 plan/RELEASE-M6.md。
#:
#: 已登记：脱敏门建立前，检测器自己的阳性对照用的是"真实形态"字面值（M6 改为片段拼接后旧 blob 留在历史里）。
#: 那两条不是任何真人手机号/证件号，而是 `data/README.md §三` 白名单纪律下的正则样本；
#: 改历史会抹掉 M0–M5 的验收轨迹，代价远大于收益，故登记放行而不是重写。
KNOWN_HISTORY_REVIEW: Tuple[Tuple[str, str], ...] = (
    ("tests/test_data_discipline.py@2be0d9694a", "phone_number"),
    ("tests/test_data_discipline.py@2be0d9694a", "id_number"),
)


class Hit(object):
    __slots__ = ("label", "line", "category")

    def __init__(self, label: str, line: int, category: str) -> None:
        self.label = label
        self.line = line
        self.category = category

    def key(self) -> Tuple[str, int, str]:
        return (self.label, self.line, self.category)


def identity_markers() -> List[str]:
    """本机个人路径字面值：取自运行环境，换台机器照样有效。

    **只保留路径形状**（含分隔符）的标记：裸用户名不参与硬门。
    在 GitHub 的 runner 机器上账号名就是 `runner`/`runneradmin`，裸名匹配会把满仓的
    `bench/runner.py` 全判成个人标记 —— 那是把门做成噪音，噪音一大就等于没有门。
    裸名能泄漏的东西（本机绝对路径）已经由 `drive_path` 与 `home_path` 两条通用规则接住。
    """
    paths: List[str] = []
    home = os.path.expanduser("~")
    drive = os.environ.get("HOMEDRIVE")
    homepage = os.environ.get("HOMEPATH")
    for value in (home, os.environ.get("USERPROFILE"), drive + homepage if drive and homepage else None):
        if not value:
            continue
        for variant in (value, value.replace("\\", "/"), value.replace("/", "\\")):
            paths.append(variant)
    markers: List[str] = []
    for value in paths:
        value = value.strip().strip("\\").strip("/")
        if len(value) >= 6 and any(sep in value for sep in ("/", "\\")):
            if value.lower() not in [item.lower() for item in markers]:
                markers.append(value)
    return sorted(markers, key=len, reverse=True)


def _classify(category: str, matched: str) -> bool:
    """True = 确属泄露；False = 落白名单放行。"""
    if category == "phone_number":
        return not _PHONE_ALLOWED.match(matched)
    if category == "email":
        domain = matched.rsplit("@", 1)[-1].lower().strip(")]},;'\").")
        return not any(
            domain == allowed or domain.endswith("." + allowed) for allowed in _EMAIL_ALLOWED_DOMAINS
        )
    if category == "drive_path":
        return not _DRIVE_ALLOWED.match(matched)
    return True


def scan_text(label: str, text: str, markers: Sequence[str]) -> List[Hit]:
    hits: List[Hit] = []
    lowered_markers = [item.lower() for item in markers]
    for number, line in enumerate(text.splitlines(), start=1):
        if not line:
            continue
        lowered = line.lower()
        for marker in lowered_markers:
            if marker in lowered:
                hits.append(Hit(label, number, "personal_identity"))
                break
        for category, pattern in RULES:
            for match in pattern.finditer(line):
                if _classify(category, match.group(0)):
                    hits.append(Hit(label, number, category))
    return hits


def scan_blob(label: str, blob: bytes, markers: Sequence[str]) -> List[Hit]:
    """二进制单独扫；zip 容器必须逐条目展开 —— docProps/core.xml 与 .rels 最爱藏创建者姓名与外部目标。"""
    if blob[:2] != b"PK":
        return scan_text(label, blob.decode("utf-8", "replace"), markers)
    hits: List[Hit] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return scan_text(label + "!(bad-zip)", blob.decode("utf-8", "replace"), markers)
    with archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            try:
                payload = archive.read(entry.filename)
            except Exception:
                continue
            hits.extend(
                scan_text(
                    "{0}!{1}".format(label, entry.filename),
                    payload.decode("utf-8", "replace"),
                    markers,
                )
            )
    return hits


# --------------------------------------------------------------------------- 数据源


def _git(*args: str) -> bytes:
    proc = subprocess.Popen(
        ["git", "-c", "core.quotepath=off"] + list(args),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, err = proc.communicate()
    if proc.returncode != 0:
        raise SystemExit(
            "git {0} 失败：{1}".format(" ".join(args), err.decode("utf-8", "replace").strip())
        )
    return out


def units_tracked() -> List[Tuple[str, bytes]]:
    out: List[Tuple[str, bytes]] = []
    for rel in _git("ls-files", "-z").split(b"\0"):
        if not rel:
            continue
        name = rel.decode("utf-8")
        path = os.path.join(ROOT, *name.split("/"))
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                out.append((name, handle.read()))
    return out


def units_history() -> List[Tuple[str, bytes]]:
    ids: List[str] = []
    path_of: Dict[str, str] = {}
    for line in _git("rev-list", "--objects", "--all").decode("utf-8", "replace").splitlines():
        parts = line.split(" ", 1)
        if not parts[0] or not re.match(r"^[0-9a-f]{7,40}$", parts[0]):
            continue
        ids.append(parts[0])
        if len(parts) == 2:
            path_of.setdefault(parts[0], parts[1])
    if not ids:
        return []
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, _err = proc.communicate(("\n".join(ids) + "\n").encode("ascii"))
    units: List[Tuple[str, bytes]] = []
    pos = 0
    while pos < len(out):
        newline = out.find(b"\n", pos)
        if newline < 0:
            break
        parts = out[pos:newline].decode("ascii", "replace").split(" ")
        if len(parts) == 3 and parts[1] == "blob":
            size = int(parts[2])
            body = out[newline + 1 : newline + 1 + size]
            pos = newline + 1 + size + 1
            units.append((path_of.get(parts[0], parts[0]) + "@" + parts[0][:10], body))
        else:
            pos = newline + 1  # tree / commit / tag / missing：非 blob 一律跳过
    return units


def units_messages() -> List[Tuple[str, bytes]]:
    sep = "\x1f"
    units: List[Tuple[str, bytes]] = []
    fmt = sep.join(["%H", "%an", "%ae", "%cn", "%ce", "%s", "%b"]) + sep
    raw = _git("log", "--all", "--format=" + fmt).decode("utf-8", "replace")
    for chunk in raw.split("\x1f\n"):
        parts = chunk.split(sep)
        if len(parts) < 7 or not parts[0]:
            continue
        text = "author={0} <{1}> committer={2} <{3}>\n{4}\n{5}".format(
            parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]
        )
        units.append(("commit:" + parts[0][:10], text.encode("utf-8")))
    tagfmt = sep.join(["%(refname:short)", "%(subject)", "%(body)"]) + sep
    raw = _git("for-each-ref", "refs/tags", "--format=" + tagfmt).decode("utf-8", "replace")
    for chunk in raw.split("\x1f\n"):
        parts = chunk.split(sep)
        if len(parts) < 2 or not parts[0]:
            continue
        units.append(("tag:" + parts[0], " ".join(parts[1:]).encode("utf-8")))
    config = os.path.join(ROOT, ".git", "config")
    if os.path.isfile(config):
        with open(config, "rb") as handle:
            text = handle.read().decode("utf-8", "replace")
        # .git/config 不入库：只判有没有把凭据写进远端地址，本机路径属环境事实不算泄露
        for number, line in enumerate(text.splitlines(), start=1):
            for category, pattern in RULES:
                if category in ("credential", "private_key_block") and pattern.search(line):
                    units.append(("config-line-{0}".format(number), line.encode("utf-8")))
    return units


def _label(path: str, relative_to: Optional[str] = None) -> str:
    """输出里的位置标识一律相对化：仓库外的目标只保留 `external:名字`，
    否则一次 `--include` 就把本机绝对路径（含用户名）印进报告 —— 审计器自己就成了泄露源。"""
    base = relative_to or path
    try:
        rel = os.path.relpath(base, ROOT).replace("\\", "/")
    except ValueError:  # 跨盘符（Windows 临时目录常见）没有相对路径可言
        rel = "external:" + os.path.basename(os.path.abspath(base))
    return rel


def units_tree(base: str) -> List[Tuple[str, bytes]]:
    prefix = _label(base)
    out: List[Tuple[str, bytes]] = []
    if os.path.isfile(base):
        with open(base, "rb") as handle:
            out.append((prefix, handle.read()))
        return out
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                with open(full, "rb") as handle:
                    blob = handle.read()
            except OSError:
                continue
            inner = os.path.relpath(full, base).replace("\\", "/")
            out.append(("{0}/{1}".format(prefix, inner), blob))
    return out


# --------------------------------------------------------------------------- 汇总


def summarize(hits: Iterable[Hit]) -> List[str]:
    """同位置同类折叠成一行；只给位置与计数，不给原文。"""
    counts: Dict[Tuple[str, int, str], int] = {}
    for hit in hits:
        counts[hit.key()] = counts.get(hit.key(), 0) + 1
    return [
        "{0}:{1} [{2}] x{3}".format(label, line, category, count)
        for (label, line, category), count in sorted(counts.items())
    ]


def _registered(label: str, category: str) -> bool:
    for known, known_category in KNOWN_HISTORY_REVIEW:
        if known_category != category:
            continue
        if known == label or (known.endswith("@") and label.startswith(known)):
            return True
    return False


def run(mode: str, includes: Sequence[str]) -> Tuple[List[str], List[str], Dict[str, int]]:
    """返回 (硬门失败行, 已登记的复核行, 类别计数)。"""
    markers = identity_markers()
    units: List[Tuple[str, bytes]] = []
    if mode == "tracked":
        units = units_tracked()
    elif mode == "history":
        units = units_history()
    elif mode == "messages":
        units = units_messages()
    elif mode == "all":
        units = units_tracked() + units_history() + units_messages()
    for path in includes:
        units.extend(units_tree(path))

    hard: List[Hit] = []
    review: List[Hit] = []
    by_category: Dict[str, int] = {}
    for label, blob in units:
        for hit in scan_blob(label, blob, markers):
            bucket = hard if hit.category in HARD_CATEGORIES else review
            bucket.append(hit)
            by_category[hit.category] = by_category.get(hit.category, 0) + 1

    # 号段类样本只有在 KNOWN_HISTORY_REVIEW 里登记过原因才放行 —— 所有模式一视同仁：
    # 若只在历史模式设门，生成物（报告 xlsx）里的真实号码就变成"打印出来但照样绿"。
    unfiled = [hit for hit in review if not _registered(hit.label, hit.category)]
    hard.extend(unfiled)
    review = [hit for hit in review if _registered(hit.label, hit.category)]
    return summarize(hard), summarize(review), by_category


# --------------------------------------------------------------------------- 反证


def selftest() -> int:
    """阳性对照逐类必抓 + 白名单形式不误报 + zip 展开 + 输出纪律，缺一不可。"""
    # 片段拼接：源码里不留完整字面值，运行时才拼成真实形态
    phone = "1" + "38" + "0011" + "22" + "33"
    idcard = "11" + "0101" + "1990" + "0307" + "12" + "3X"
    mail = "zhang" + "." + "san" + "@" + "abc-corp" + "." + "com"
    home = "/ho" + "me/" + "zhang" + "san"
    drive = "C" + ":" + "\\" + "Users" + "\\" + "zhan" + "gsan"
    ip = "192" + "." + "168" + "." + "3" + "." + "21"
    host = "gitlab" + "." + "abc" + "." + "corp"
    keyblock = "-" * 5 + "BEGIN RSA PRI" + "VATE KEY" + "-" * 5
    token = "ghp_" + "A" * 40
    apikey = "api" + "_key = " + "SuperSecr" + "etValue123"
    urlcred = "https" + "://" + "user1" + ":" + "hunter2secret" + "@git" + ".example.org"

    positives = [
        ("personal_identity", "输出目录 " + home),
        ("phone_number", "监测人 " + phone + " 电话"),
        ("id_number", "登记 " + idcard),
        ("email", "联系人 " + mail),
        ("home_path", "工作目录 " + home),
        ("drive_path", "输出 " + drive),
        ("internal_ip", "内网 " + ip),
        ("internal_domain", "服务 " + host),
        ("private_key_block", keyblock),
        ("credential", token),
        ("credential", apikey),
        ("credential", urlcred),
    ]
    negatives = [
        ("phone_number", "白名单号段 1990000" + "0123"),
        ("id_number", "sha256 ab" + "1383934581" + "cd"),
        ("id_number", "修订时间戳 202601091200000000"),
        ("email", "mailto:" + "docs" + "@" + "example.com"),
        ("drive_path", "通用示例 X:" + "\\" + "path" + "\\" + "file"),
        ("internal_ip", "DOI 10.9999/SYN" + ".0001"),
        ("credential", 'obs["password_hint"] = None'),
        ("internal_domain", "内嵌目录 _internal/" + "data"),
        ("home_path", "注释里的 用户名/home/盘符"),
        # 裸账号名当文件名不许判成个人标记：CI runner 的账号名就叫 runner，
        # 一旦按裸名匹配，满仓的 `bench/runner.py` 全变红线，噪音会把真门淹掉。
        ("personal_identity", "模块路径 src/pmc/" + "zhang" + "san" + ".py"),
    ]

    checks: List[Tuple[str, bool, str]] = []
    markers = [home]

    for category, text in positives:
        found = {hit.category for hit in scan_text("case", text, markers)}
        ok = category in found
        checks.append(("阳性 {0}".format(category), ok, "" if ok else "未命中"))

    for category, text in negatives:
        offenders = [hit for hit in scan_text("case", text, markers) if hit.category == category]
        checks.append(("阴性 {0}".format(category), not offenders, summarize(offenders)[:1] and " ".join(summarize(offenders))))

    blob = io.BytesIO()
    archive = zipfile.ZipFile(blob, "w", zipfile.ZIP_DEFLATED)
    archive.writestr("docProps/core.xml", "<dc:creator>" + mail + "</dc:creator>")
    archive.writestr("xl/_rels/workbook.xml.rels", '<Relationship Target="' + home + '"/>')
    archive.writestr("xl/worksheets/sheet1.xml", "<row>" + phone + "</row>")
    archive.close()
    zip_hits = scan_blob("report.xlsx", blob.getvalue(), markers)
    found = {hit.category for hit in zip_hits}
    checks.append(
        (
            "zip 条目逐个展开",
            {"email", "home_path", "phone_number"} <= found,
            "" if {"email", "home_path", "phone_number"} <= found else str(sorted(found)),
        )
    )
    lines = summarize(zip_hits)
    echoed = [item for item in lines if any(secret in item for secret in (phone, idcard, mail, token))]
    checks.append(("输出不回显命中原文", not echoed, " ".join(echoed)))

    with open(os.path.abspath(__file__), "rb") as handle:
        own = scan_blob("scripts/desensitize_audit.py", handle.read(), markers)
    checks.append(("扫描器源码过自家扫描", not own, " ".join(summarize(own)[:3])))

    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, detail in checks:
        print("{0} {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else " <- {0}".format(detail)))
    if failed:
        print("{0}：{1}".format(MARKER_SELFTEST_FAIL, ", ".join(failed)), file=sys.stderr)
        return 1
    print("{0} {1} 项反证全部命中".format(MARKER_SELFTEST_OK, len(checks)))
    return 0


# --------------------------------------------------------------------------- 入口


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="desensitize_audit", description="入库脱敏审计（plan/HANDOFF-M6 §二.2）"
    )
    parser.add_argument("--mode", default="all", choices=("tracked", "history", "messages", "all"))
    parser.add_argument("--include", action="append", default=[], help="附带扫描目录或文件（生成物/产物）")
    parser.add_argument("--selftest", action="store_true", help="阳性对照 + 白名单阴性 + 输出纪律反证")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    failures, review, by_category = run(args.mode, args.include)
    for line in review:
        print("{0} {1}".format(MARKER_REVIEW, line))
    for line in failures:
        print("FAIL {0}".format(line), file=sys.stderr)
    print(
        "{0} mode={1} 硬门={2} 复核={3} 类别={4}".format(
            MARKER_SUMMARY,
            args.mode,
            len(failures),
            len(review),
            ",".join("{0}:{1}".format(key, value) for key, value in sorted(by_category.items())) or "无",
        )
    )
    if failures:
        print("{0} mode={1} 命中 {2} 处".format(MARKER_FAIL, args.mode, len(failures)), file=sys.stderr)
        return 1
    print("{0} mode={1} 无泄露命中".format(MARKER_OK, args.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
