"""测试公共工具（文件名前缀下划线：pytest 不当作测试用例收集）。"""

from __future__ import annotations

import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DATA = ROOT / "data"


def mem_db():
    from pmc.db.schema import apply_schema

    conn = sqlite3.connect(":memory:")
    apply_schema(conn)
    return conn


def seed_project_chain(conn):
    """建一条 工程→工况→测点→轮次 的最小链，返回各主键。"""
    cur = conn.execute(
        "INSERT INTO project(code, name, synthetic) VALUES('SYN-P1','SYN·示例基坑 1 号',1)"
    )
    project_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO working_condition(project_id, code, name, excavation_depth, effective_from)"
        " VALUES(?,?,?,?,?)",
        (project_id, "C1", "SYN·一层开挖工况", 5.0, "2026-01-05"),
    )
    condition_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO point(project_id, code, item_code, design_cum_unit)"
        " VALUES(?,?,?,?)",
        (project_id, "SYN-DH-01", "top_h_disp", "mm"),
    )
    point_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO obs_round(project_id, round_index, observed_on, condition_id)"
        " VALUES(?,?,?,?)",
        (project_id, 1, "2026-01-06", condition_id),
    )
    round_id = cur.lastrowid
    return {
        "project_id": project_id,
        "condition_id": condition_id,
        "point_id": point_id,
        "round_id": round_id,
    }
