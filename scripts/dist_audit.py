"""构建产物红线审计（plan/11 §十）。

用法：

    py -3.12 -X utf8 scripts/dist_audit.py                 # 审计 dist/pmc
    py -3.12 -X utf8 scripts/dist_audit.py --selftest      # 用伪造产物反证审计器不空转
    py -3.12 -X utf8 scripts/dist_audit.py --report a.xlsx # 附带审计一份报告产物

设计取舍：

* **内嵌数据整目录逐份 sha256 对账**，只比顶层文件不算对账 —— 子目录里的语料会根本没参与比对，
  而审计照样打印通过；
* **禁区成分**按路径段判（`plan/`、`tests/`、`fixtures`、临时目录），不按文件名猜；
* **身份红线拿本机用户名与 home 路径当诱饵样本**，比写死模式表可靠；
* 冻结载荷里的绝对盘符路径属 PyInstaller 记录 `co_filename` 的固有行为，列成**复核项**如实输出，
  不静默放行也不伪装成硬门。
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import io
import os
import re
import sys
import tempfile
import zipfile
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DATA = os.path.join(ROOT, "data")
DEFAULT_DIST = os.path.join(ROOT, "dist", "pmc")

#: 与 packaging/pmc.spec 的白名单一致（改动必须同步，否则对账必然漂）
DATA_FOLDERS = ("dict", "rulesets", "clauses", "raw", "truth", "golden")

ALLOWED_TOPLEVEL = {"pmc.exe", "pmc-gui.exe", "_internal"}

FORBIDDEN_SEGMENTS = ("plan", "tests", "fixtures", ".tmp_verify", ".tmp_parse", ".github")

MARKER = "DIST_AUDIT_OK"


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree(base: str, folders: Tuple[str, ...]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for folder in folders:
        root = os.path.join(base, folder)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, base).replace("\\", "/")
                out[rel] = sha256_file(full)
    return out


def identity_markers() -> List[bytes]:
    home = os.path.expanduser("~")
    user = getpass.getuser()
    markers = [home.encode("utf-8", "replace")]
    if user:
        markers.append(user.encode("utf-8", "replace"))
    return [item for item in markers if len(item) > 3]


def embedded_data_dir(dist: str) -> Optional[str]:
    for candidate in (
        os.path.join(dist, "_internal", "data"),
        os.path.join(dist, "data"),
        os.path.join(dist, "_MEIPASS", "data"),
    ):
        if os.path.isdir(candidate):
            return candidate
    return None


def audit(dist: str, report: Optional[str] = None) -> Tuple[List[str], List[str]]:
    """返回 (硬门失败项, 复核项)。"""
    problems: List[str] = []
    review: List[str] = []
    if not os.path.isdir(dist):
        return ["dist 目录不存在：{0}".format(dist.replace("\\", "/"))], review

    entries = set(os.listdir(dist))
    extra = sorted(entries - ALLOWED_TOPLEVEL)
    missing = sorted(ALLOWED_TOPLEVEL - entries)
    if extra:
        problems.append("dist 顶层多出的成分：{0}".format(", ".join(extra)))
    for name in ("pmc.exe", "pmc-gui.exe"):
        if name in missing:
            problems.append("dist 缺入口：{0}".format(name))

    # 禁区成分：按路径段判
    for dirpath, dirnames, filenames in os.walk(dist):
        rel = os.path.relpath(dirpath, dist).replace("\\", "/")
        segments = {part for part in rel.split("/") if part}
        hit = segments & set(FORBIDDEN_SEGMENTS)
        if hit:
            problems.append("产物树里有禁区成分：{0} ← {1}".format(rel, ",".join(sorted(hit))))
        for name in filenames:
            if name.endswith((".sqlite", ".db", ".env")):
                problems.append("产物树里带了运行数据/机密：{0}".format(name))
        dirnames.sort()

    # 内嵌数据逐份对账
    embedded = embedded_data_dir(dist)
    if embedded is None:
        problems.append("产物里找不到内嵌 data/（_internal/data 或 data/）")
    else:
        want = tree(REPO_DATA, DATA_FOLDERS)
        got = tree(embedded, DATA_FOLDERS)
        only_repo = sorted(set(want) - set(got))
        only_dist = sorted(set(got) - set(want))
        differ = sorted(name for name in set(want) & set(got) if want[name] != got[name])
        if only_repo:
            problems.append("内嵌缺 {0} 份：{1}".format(len(only_repo), ", ".join(only_repo[:5])))
        if only_dist:
            problems.append("内嵌多出 {0} 份：{1}".format(len(only_dist), ", ".join(only_dist[:5])))
        if differ:
            problems.append(
                "内嵌与仓库字节不一致 {0} 份：{1}".format(len(differ), ", ".join(differ[:5]))
            )
        if not (only_repo or only_dist or differ):
            review.append("内嵌数据 {0} 份与仓库逐字节一致".format(len(got)))

        # 载荷身份红线：内嵌件不得含本机用户名/home/盘符绝对路径
        # 盘符模式要带字母环视：`https://` 里的 `s:/` 不是盘符（误报校准四项之一）
        markers = identity_markers()
        drive = re.compile(rb"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
        for rel in sorted(got):
            blob = open(os.path.join(embedded, *rel.split("/")), "rb").read()
            for marker in markers:
                if marker in blob:
                    problems.append("内嵌件含个人标记：{0}".format(rel))
            if drive.search(blob):
                review.append("内嵌件含盘符路径：{0}".format(rel))

    # 报告产物：0 外链 + 固定 ZIP 时间
    if report:
        problems.extend(audit_report(report))
    return problems, review


def audit_report(path: str) -> List[str]:
    problems: List[str] = []
    if not os.path.isfile(path):
        return ["报告产物不存在：{0}".format(path.replace("\\", "/"))]
    archive = zipfile.ZipFile(path)
    stamps = {info.date_time for info in archive.infolist()}
    if stamps != {(1980, 1, 1, 0, 0, 0)}:
        problems.append("报告 ZIP 时间戳不是固定 1980-01-01：{0}".format(sorted(stamps)[:3]))
    blob = open(path, "rb").read()
    for marker in identity_markers():
        if marker in blob:
            problems.append("报告产物含个人标记")
    if b"<hyperlink" in blob or b'drawingml/hyperlink' in blob:
        problems.append("报告产物含超链接部件")
    for name in archive.namelist():
        if not name.startswith("xl/worksheets/") or not name.endswith(".xml"):
            continue
        text = archive.read(name).decode("utf-8", "replace")
        #: 剥掉标签只看正文：xmlns 里的 http URI 是 OOXML 规范命名空间，不是外链
        body = re.sub(r"<[^>]+>", "", text)
        if re.search(r"https?://|www\.", body):
            problems.append("报告正文含外链：{0}".format(name))
    for name in archive.namelist():
        if name.endswith(".rels"):
            text = archive.read(name).decode("utf-8", "replace")
            targets = re.findall(r'Target="([^"]+)"', text)
            if 'TargetMode="External"' in text or any(
                target.startswith(("http://", "https://", "file:")) for target in targets
            ):
                problems.append("报告关系表含外部目标：{0}".format(name))
    archive.close()
    return problems


# --------------------------------------------------------------------------- 反证


def _fake_dist(base: str) -> str:
    """按当前仓库真实数据搭一个"合格"dist 骨架，供逐项破坏。"""
    dist = os.path.join(base, "pmc")
    internal = os.path.join(dist, "_internal")
    os.makedirs(os.path.join(internal, "data"), exist_ok=True)
    for name in ("pmc.exe", "pmc-gui.exe"):
        with open(os.path.join(dist, name), "wb") as handle:
            handle.write(b"MZ\x90\x00")
    for folder in DATA_FOLDERS:
        src = os.path.join(REPO_DATA, folder)
        if not os.path.isdir(src):
            continue
        target = os.path.join(internal, "data", folder)
        os.makedirs(target, exist_ok=True)
        for dirpath, _names, filenames in os.walk(src):
            rel = os.path.relpath(dirpath, src)
            os.makedirs(os.path.join(target, rel), exist_ok=True)
            for name in sorted(filenames):
                with open(os.path.join(dirpath, name), "rb") as read_handle:
                    blob = read_handle.read()
                with open(os.path.join(target, rel, name), "wb") as write_handle:
                    write_handle.write(blob)
    return dist


def _fake_report(base: str, kind: str = "clean") -> str:
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, "report-{0}.xlsx".format(kind))
    archive = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)
    body = (
        '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
        if kind == "external"
        else '<?xml version="1.0"?><Types xmlns="pkg"/>'
    )
    info = zipfile.ZipInfo("[Content_Types].xml", date_time=(1980, 1, 1, 0, 0, 0))
    archive.writestr(info, body)
    rels = zipfile.ZipInfo("xl/_rels/workbook.xml.rels", date_time=(1980, 1, 1, 0, 0, 0))
    target = "http://example.invalid/x" if kind == "external" else "worksheets/sheet1.xml"
    archive.writestr(
        rels,
        '<Relationships xmlns="r"><Relationship Id="rId1" Type="t" Target="{0}"/></Relationships>'.format(
            target
        ),
    )
    archive.writestr(zipfile.ZipInfo("xl/payload.bin", date_time=(1980, 1, 1, 0, 0, 0)), b"clean")
    #: 命名空间 URI 里的 http 出现在标签上，正文干净 —— 这一条专门反证"剥标签"的实现不误报
    cell = "http://example.invalid" if kind == "link" else "累计值"
    archive.writestr(
        zipfile.ZipInfo("xl/worksheets/sheet1.xml", date_time=(1980, 1, 1, 0, 0, 0)),
        (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<row r="1"><c r="A1" t="inlineStr"><is><t>{0}</t></is></c></row></worksheet>'
        ).format(cell),
    )
    if kind == "home":
        archive.writestr(
            zipfile.ZipInfo("xl/home.bin", date_time=(1980, 1, 1, 0, 0, 0)),
            os.path.expanduser("~").encode("utf-8", "replace"),
        )
    archive.close()
    return path


def selftest() -> int:
    """伪造五种违反，逐个确认审计器点名；漏掉任何一种就说明审计器在空转。"""
    checks = []
    base = tempfile.mkdtemp(prefix="pmc-dist-selftest-")

    good = _fake_dist(base)
    problems, _review = audit(good)
    checks.append(("合格产物不误报", not problems, problems))

    os.remove(os.path.join(good, "_internal", "data", "dict", os.listdir(os.path.join(good, "_internal", "data", "dict"))[0]))
    problems, _review = audit(good)
    checks.append(("少一份内嵌语料被抓", any("缺" in item for item in problems), problems))

    _rebuild = _fake_dist(base)

    bad = os.path.join(base, "withplan", "pmc")
    os.makedirs(os.path.join(bad, "plan"), exist_ok=True)
    with open(os.path.join(bad, "plan", "note.md"), "w", encoding="utf-8") as handle:
        handle.write("x")
    _fake_dist(bad)
    problems, _review = audit(bad)
    checks.append(("多一个 plan 目录被抓", any("禁区成分" in item for item in problems), problems))

    target = os.path.join(good, "_internal", "data", "dict")
    victim = os.path.join(target, sorted(os.listdir(target))[0])
    with open(victim, "ab") as handle:
        handle.write(b" ")
    problems, _review = audit(good)
    checks.append(("改一个字节被抓", any("字节不一致" in item for item in problems), problems))

    leaked = os.path.join(good, "_internal", "data", "dict", "leak.json")
    with open(leaked, "w", encoding="utf-8") as handle:
        handle.write("x")
    problems, _review = audit(good)
    checks.append(("多出文件被抓", any("多出" in item for item in problems), problems))
    if os.path.exists(leaked):
        os.remove(leaked)

    report_clean = _fake_report(os.path.join(base, "rep-clean"), kind="clean")
    checks.append(("合格报告不误报", not audit_report(report_clean), audit_report(report_clean)))
    report_home = _fake_report(os.path.join(base, "rep-home"), kind="home")
    checks.append(("报告含 home 路径被抓", bool(audit_report(report_home)), audit_report(report_home)))
    report_ext = _fake_report(os.path.join(base, "rep-ext"), kind="external")
    checks.append(("报告含外链被抓", bool(audit_report(report_ext)), audit_report(report_ext)))
    report_link = _fake_report(os.path.join(base, "rep-link"), kind="link")
    checks.append(("报告正文含 URL 被抓", bool(audit_report(report_link)), audit_report(report_link)))

    failed = [name for name, ok, detail in checks if not ok]
    for name, ok, detail in checks:
        print("{0} {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else " ← {0}".format(detail)))
    if failed:
        print("DIST_AUDIT_SELFTEST_FAIL：{0}".format(", ".join(failed)), file=sys.stderr)
        return 1
    print("DIST_AUDIT_SELFTEST_OK {0} 项反证全部命中".format(len(checks)))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="dist_audit", description="构建产物红线审计（plan/11 §十）")
    parser.add_argument("--dist", default=DEFAULT_DIST, help="onedir 产物目录")
    parser.add_argument("--report", default=None, help="附带审计一份 xlsx 报告产物")
    parser.add_argument("--selftest", action="store_true", help="用伪造产物反证审计器")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest()

    problems, review = audit(args.dist, args.report)
    for item in review:
        print("REVIEW {0}".format(item))
    for item in problems:
        print("DIST_AUDIT_FAIL {0}".format(item), file=sys.stderr)
    if problems:
        return 1
    print("{0} 产物 {1} 通过红线审计".format(MARKER, args.dist.replace("\\", "/")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
