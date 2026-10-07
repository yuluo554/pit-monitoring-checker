"""M5 测试用的最小台账装配（文件名前缀下划线：pytest 不当作测试用例收集）。

不用整站合成数据：报告层的被测对象是"落库行 → DTO → xlsx"这条通路，
一张 3 测点 × 3 轮次的小台账足够覆盖，且让测试保持在毫秒级。
数值一律由夹具档位反推，源码里不写死阈值数字。
"""

from __future__ import annotations

import pathlib
import sqlite3
from typing import Dict, List, Tuple

from _fixtures import apply_point_grades, grades, ruleset_file, ruleset_dir

SITE = "SYN-ZHDQ"


def chosen_items(limit: int = 3) -> List[str]:
    picked = []
    for code, grade in sorted(grades().items()):
        if grade.get("cum") is not None:
            picked.append(code)
        if len(picked) >= limit:
            break
    if not picked:
        raise AssertionError("夹具档位里没有带累计控制值的项目")
    return picked


def seed_db(tmp_path: pathlib.Path, with_ruleset: bool = True) -> Dict[str, object]:
    """建一张带判定行的小台账；返回路径、工程编码与规则集目录。"""
    db = tmp_path / "ledger.sqlite"
    conn = sqlite3.connect(str(db))
    from pmc.db.schema import apply_schema

    apply_schema(conn)
    cur = conn.execute(
        "INSERT INTO project(code, name, builder_unit, monitor_unit, supervisor_unit,"
        " scheme_no, start_date, end_date, synthetic)"
        " VALUES(?,?,?,?,?,?,?,?,1)",
        (
            SITE,
            "SYN·测试基坑 3 号",
            "SYN·测试城建集团",
            "SYN·测试监测中心",
            "SYN·测试监理公司",
            "SYN-JK-2026-0003",
            "2026-01-06",
            "2026-01-14",
        ),
    )
    project_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO working_condition(project_id, code, name, excavation_depth, effective_from)"
        " VALUES(?,?,?,?,?)",
        (project_id, "C1", "SYN·一层开挖工况", 6.0, "2026-01-06"),
    )
    condition_id = cur.lastrowid

    items = chosen_items()
    points = []
    for index, item_code in enumerate(items, start=1):
        code = "SYN-M5-{0:02d}".format(index)
        cur = conn.execute(
            "INSERT INTO point(project_id, code, item_code, design_cum_unit)"
            " VALUES(?,?,?,?)",
            (project_id, code, item_code, grades()[item_code]["unit"]),
        )
        points.append((cur.lastrowid, code, item_code))

    rounds = []
    for index, observed in enumerate(("2026-01-06", "2026-01-08", "2026-01-10"), start=1):
        cur = conn.execute(
            "INSERT INTO obs_round(project_id, round_index, observed_on, condition_id)"
            " VALUES(?,?,?,?)",
            (project_id, index, observed, condition_id),
        )
        rounds.append((cur.lastrowid, index))

    apply_point_grades(conn, SITE, only_items=items)

    factors = (0.5, 0.7, 1.2)
    obs_ids: List[int] = []
    for point_id, point_code, item_code in points:
        threshold = float(grades()[item_code]["cum"])
        for round_id, round_index in rounds:
            value = round(threshold * factors[round_index - 1], 3)
            cur = conn.execute(
                "INSERT INTO observation(point_id, round_id, revision_seq, value_cum, unit,"
                " missing, source_row) VALUES(?,?,?, ?,?,0,?)",
                (point_id, round_id, 1, value, grades()[item_code]["unit"], round_index + 1),
            )
            obs_ids.append(cur.lastrowid)
    conn.commit()
    conn.close()

    from pmc import cli

    code = cli.main(
        ["check", "--db", str(db), "--project", SITE, "--rules-dir", ruleset_dir(SITE)]
    )
    assert code in (0, 1), "判定通路未跑通：rc={0}".format(code)

    if with_ruleset:
        conn = sqlite3.connect(str(db))
        import hashlib

        blob = pathlib.Path(ruleset_file(SITE)).read_bytes()
        conn.execute(
            "INSERT OR REPLACE INTO ruleset_applied(code, version, sha256) VALUES(?,?,?)",
            ("FX-M5", 1, hashlib.sha256(blob).hexdigest()),
        )
        conn.commit()
        conn.close()

    return {
        "db": str(db),
        "project": SITE,
        "rules_dir": ruleset_dir(SITE),
        "rounds": [index for _rid, index in rounds],
        "points": [code for _pid, code, _item in points],
        "items": items,
        "tmp": tmp_path,
    }


def insert_violation(db: str, point_id_hint: str, kind: str = "missed", round_index: int = 3) -> int:
    """直接落一条应核实事项：与 `pmc audit` 写的是同一张表，报告只读不判。"""
    conn = sqlite3.connect(db)
    from _fixtures import RULESET_ROOT  # noqa: F401  (确保夹具根可用，路径不对时先炸在这里)

    project_id = conn.execute("SELECT id FROM project WHERE code=?", (SITE,)).fetchone()[0]
    point_id = conn.execute(
        "SELECT id FROM point WHERE project_id=? AND code=?", (project_id, point_id_hint)
    ).fetchone()[0]
    round_id = conn.execute(
        "SELECT id FROM obs_round WHERE project_id=? AND round_index=?", (project_id, round_index)
    ).fetchone()[0]
    import json

    cur = conn.execute(
        "INSERT INTO violation(project_id, point_id, round_id, kind, rule_id, clause_ids,"
        " evidence_json) VALUES(?,?,?,?,?,?,?)",
        (
            project_id,
            point_id,
            round_id,
            kind,
            "FREQ-MISSED-ROUND",
            "GB50497-2019:monitor-frequency",
            json.dumps({"detail": "上一轮间隔 4 天，超过配置的 3 天窗口", "gap_days": 4}, ensure_ascii=False),
        ),
    )
    conn.commit()
    violation_id = cur.lastrowid
    conn.close()
    return int(violation_id)


def open_zip(path: str):
    import zipfile

    return zipfile.ZipFile(path)


def sheet_part_names(path: str) -> Tuple[List[str], Dict[str, str]]:
    """(工作表名顺序, 部件名集合)。"""
    import zipfile
    from xml.etree import ElementTree

    from pmc.report.ooxml import MAIN_NS, OFFICE_R_NS

    archive = zipfile.ZipFile(path)
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {rel.get("Id"): rel.get("Target") for rel in rels}
    names = []
    for sheet in workbook.iter("{{{0}}}sheet".format(MAIN_NS)):
        target = targets.get(sheet.get("{{{0}}}id".format(OFFICE_R_NS)), "")
        names.append((sheet.get("name"), "xl/" + target.lstrip("./")))
    return [entry[0] for entry in names], set(archive.namelist())
