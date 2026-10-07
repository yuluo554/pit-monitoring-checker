"""合成产物的渲染、字节冻结对账与台账建档（plan/07 §十）。

冻结口径三条：
  * 全部 UTF-8 无 BOM、LF、结尾单个换行 —— Windows 的 `core.autocrlf=true` 会重写工作树，
    所以写入一律走二进制通路，不交给平台默认换行；
  * 落盘文本 = 量化值的固定小数位格式化，两次生成必须逐字节一致；
  * manifest 只记生成身份（seed / profile_id / 计数 / sha256），**不记任何阈值数值**：
    合成自证档位属 `pmc/synth/profile.py`，写进数据面就等于冒充规范值（口径 C7）。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

from pmc.catalog.items import load_items
from pmc.db.schema import apply_schema
from pmc.errors import InputError, SynthError
from pmc.synth import profile
from pmc.synth.generator import (
    ObservationRow,
    SiteData,
    TruthRow,
    generate_site,
)
from pmc.synth.sites import get_site, site_codes

CSV_HEADER = (
    "round_index",
    "observed_on",
    "point_code",
    "item_code",
    "cumulative_value",
    "unit",
    "missing",
    "raw_text",
)

TRUTH_HEADER = (
    "event_id",
    "point_id",
    "round_index",
    "event_type",
    "injected_magnitude",
    "expected_first_alarm_round",
    "also_expect",
)

SCHEMA_TAG = "pmc-synth-manifest/1"
MANIFEST_PATH = "raw/manifest.json"
MANIFEST_NOTICE = (
    "本目录全部为程序生成的合成时序（虚构工程，带 SYNTHETIC 标记），禁止手改。"
    "改生成器必须用固定 seed 整目录重新生成并一并提交：`python -X utf8 -m pmc synth --check` "
    "逐字节对账。manifest 只登记生成身份（seed / profile_id / 计数 / sha256），不含任何阈值数值。"
)


def _render(rows: Sequence[Sequence[str]], header: Sequence[str]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(list(header))
    for row in rows:
        writer.writerow(list(row))
    return buffer.getvalue().encode("utf-8")


def render_round_csv(rows: Sequence[ObservationRow]) -> bytes:
    return _render([row.as_cells() for row in rows], CSV_HEADER)


def render_truth_csv(truth: Sequence[TruthRow]) -> bytes:
    return _render([row.as_cells() for row in truth], TRUTH_HEADER)


def generate(
    data_dir: str, seed: int, sites: int = 3, rounds: Optional[int] = None
) -> List[SiteData]:
    """按档案生成前 N 座基坑（--sites 的口径：按登记顺序取前 N，不是任选）。"""
    codes = site_codes()
    if not 1 <= sites <= len(codes):
        raise SynthError("--sites 取值范围 1–{0}".format(len(codes)))
    items = load_items(data_dir)
    return [generate_site(get_site(code), items, seed, rounds) for code in codes[:sites]]


def _csv_blobs(datas: Sequence[SiteData]) -> Dict[str, bytes]:
    blobs: Dict[str, bytes] = {}
    for data in datas:
        for r in sorted(data.rows):
            path = "raw/{0}/round-{1:02d}.csv".format(data.site.code, r)
            blobs[path] = render_round_csv(data.rows[r])
        blobs["truth/{0}.truth.csv".format(data.site.code)] = render_truth_csv(data.truth)
    return blobs


def render_manifest(datas: Sequence[SiteData], blobs: Dict[str, bytes]) -> bytes:
    entries = []
    for data in datas:
        site = data.site
        prefix = "raw/{0}/".format(site.code)
        files = [
            {
                "path": path,
                "sha256": hashlib.sha256(blobs[path]).hexdigest(),
                "bytes": len(blobs[path]),
                "rows": blobs[path].count(b"\n") - 1,
            }
            for path in sorted(blobs)
            if path.startswith(prefix)
        ]
        truth_path = "truth/{0}.truth.csv".format(site.code)
        truth_blob = blobs[truth_path]
        entries.append(
            {
                "code": site.code,
                "name": site.name,
                "scheme_no": site.scheme_no,
                "rounds": len(data.rounds),
                "interval_days": site.interval_days,
                "points": site.point_count(),
                "observations": data.total_rows(),
                "events": len(data.truth),
                "first_observed_on": data.rounds[0].observed_on,
                "last_observed_on": data.rounds[-1].observed_on,
                "files": files,
                "truth_file": {
                    "path": truth_path,
                    "sha256": hashlib.sha256(truth_blob).hexdigest(),
                    "bytes": len(truth_blob),
                    "rows": len(data.truth),
                },
            }
        )
    doc = {
        "schema": SCHEMA_TAG,
        "notice": MANIFEST_NOTICE,
        "generator": "pmc.synth.generator",
        "seed": _common_seed(datas),
        "profile_id": profile.PROFILE_ID,
        "reproduce": "python -X utf8 -m pmc synth --seed {0} --sites {1} --force".format(
            _common_seed(datas), len(datas)
        ),
        "sites": entries,
    }
    return (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _common_seed(datas: Sequence[SiteData]) -> int:
    seeds = {data.seed for data in datas}
    if len(seeds) != 1:
        raise SynthError("同一份 manifest 里的基坑必须同 seed")
    return seeds.pop()


def build(
    data_dir: str, seed: int, sites: int = 3, rounds: Optional[int] = None
) -> Tuple[List[SiteData], Dict[str, bytes]]:
    """生成 + 渲染，返回 (各站数据, 相对路径→字节)。--db 建档要复用前者，不重跑一遍。"""
    datas = generate(data_dir, seed, sites, rounds)
    blobs = _csv_blobs(datas)
    blobs[MANIFEST_PATH] = render_manifest(datas, blobs)
    return datas, blobs


def build_blobs(
    data_dir: str, seed: int, sites: int = 3, rounds: Optional[int] = None
) -> Dict[str, bytes]:
    return build(data_dir, seed, sites, rounds)[1]


def write_blobs(data_dir: str, blobs: Dict[str, bytes], force: bool) -> List[str]:
    """整目录写盘：先检查再写，避免覆盖失败留下半套冻结产物。"""
    if not force:
        clashes = [
            rel
            for rel in blobs
            if os.path.isfile(os.path.join(data_dir, *rel.split("/")))
        ]
        if clashes:
            raise SynthError(
                "{0} 等 {1} 个产物已存在，重新生成必须显式 --force".format(clashes[0], len(clashes))
            )
    written = []
    for rel in sorted(blobs):
        path = os.path.join(data_dir, *rel.split("/"))
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "wb") as handle:
            handle.write(blobs[rel])
        written.append(rel)
    return written


def _on_disk_files(data_dir: str) -> List[str]:
    out = []
    for sub in ("raw", "truth"):
        root = os.path.join(data_dir, sub)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, names in os.walk(root):
            dirnames.sort()
            for name in sorted(names):
                rel = os.path.relpath(os.path.join(dirpath, name), data_dir)
                out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def check_blobs(data_dir: str, blobs: Dict[str, bytes]) -> Tuple[bool, List[str]]:
    """与仓内冻结产物逐字节对账：缺文件、多文件、内容不一致都算不过。"""
    problems: List[str] = []
    for rel in sorted(blobs):
        path = os.path.join(data_dir, *rel.split("/"))
        if not os.path.isfile(path):
            problems.append("缺文件 {0}".format(rel))
            continue
        with open(path, "rb") as handle:
            stored = handle.read()
        if stored != blobs[rel]:
            problems.append(
                "字节不一致 {0}（仓内 {1} 字节 / 重生成 {2} 字节）".format(
                    rel, len(stored), len(blobs[rel])
                )
            )
    for rel in _on_disk_files(data_dir):
        if rel not in blobs:
            problems.append("多出文件 {0}".format(rel))
    return (not problems), problems


def seed_ledger(conn, datas: Sequence[SiteData]) -> Dict[str, int]:
    """把工程/工况/测点/轮次档案写进台账：只写"测什么、多久测、工况何时变"，不写控制值。"""
    apply_schema(conn)
    counts = {"project": 0, "condition": 0, "point": 0, "round": 0}
    for data in datas:
        site = data.site
        cur = conn.execute(
            "INSERT OR IGNORE INTO project(code, name, builder_unit, monitor_unit,"
            " supervisor_unit, scheme_no, start_date, end_date, note, synthetic)"
            " VALUES(?,?,?,?,?,?,?,?,?,1)",
            (
                site.code,
                site.name,
                site.builder_unit,
                site.monitor_unit,
                site.supervisor_unit,
                site.scheme_no,
                data.rounds[0].observed_on,
                data.rounds[-1].observed_on,
                site.note,
            ),
        )
        counts["project"] += cur.rowcount
        row = conn.execute("SELECT id FROM project WHERE code = ?", (site.code,)).fetchone()
        if row is None:
            raise InputError("工程 {0} 建档失败".format(site.code))
        project_id = row[0]
        for cond in site.conditions:
            cur = conn.execute(
                "INSERT OR IGNORE INTO working_condition(project_id, code, name,"
                " excavation_depth, effective_from) VALUES(?,?,?,?,?)",
                (
                    project_id,
                    cond.code,
                    cond.name,
                    cond.excavation_depth,
                    site.observed_on(cond.from_round),
                ),
            )
            counts["condition"] += cur.rowcount
        condition_ids = {
            code: cid
            for code, cid in conn.execute(
                "SELECT code, id FROM working_condition WHERE project_id = ?", (project_id,)
            ).fetchall()
        }
        for rec in data.rounds:
            cur = conn.execute(
                "INSERT OR IGNORE INTO obs_round(project_id, round_index, observed_on,"
                " condition_id, is_intensified, note) VALUES(?,?,?,?,?,?)",
                (
                    project_id,
                    rec.round_index,
                    rec.observed_on,
                    condition_ids[rec.condition_code],
                    rec.is_intensified,
                    rec.note(),
                ),
            )
            counts["round"] += cur.rowcount
        for point in data.points:
            cur = conn.execute(
                "INSERT OR IGNORE INTO point(project_id, code, item_code, location, install_date)"
                " VALUES(?,?,?,?,?)",
                (project_id, point.code, point.item_code, point.location, point.install_date),
            )
            counts["point"] += cur.rowcount
    conn.commit()
    return counts
