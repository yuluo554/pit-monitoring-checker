"""数据版本指纹（题面 01 §模块 4"生成时间 + 台账哈希"，plan/03 §6）。

指纹 = 台账**内容**的 sha256，与"什么时候导入"无关：

* 逐表按主键排序，逐列按 `PRAGMA table_info` 顺序取值；
* **剔除三个时间列**（`import_batch.imported_at` / `disposition.acted_at` /
  `ruleset_applied.applied_at`）—— 同一批数据重复导入只该得到同一个指纹，
  否则页眉指纹每天变，签字栏对不上号就成了装饰；
* 只算本工程可见的行（`ruleset_applied` 是全局表，一并计入）。

指纹本身进报告页眉，不进任何入仓冻结产物。
"""

from __future__ import annotations

import hashlib
from typing import Dict, List, Optional, Tuple

from pmc.contract.records import LedgerFingerprint
from pmc.db.schema import TABLE_NAMES

#: 不参与指纹的列：这些列记录"何时"，不记录"是什么"
UNTIMED_EXCLUDED_COLUMNS = ("imported_at", "acted_at", "applied_at")

#: 表 → 主键排序列（`ruleset_applied` 是复合主键，其余表都是 INTEGER 主键 id）
PRIMARY_KEYS: Dict[str, Tuple[str, ...]] = {
    "meta": ("key",),
    "project": ("id",),
    "working_condition": ("id",),
    "point": ("id",),
    "obs_round": ("id",),
    "import_batch": ("id",),
    "observation": ("id",),
    "alarm_state": ("id",),
    "disposition": ("id",),
    "violation": ("id",),
    "ruleset_applied": ("code", "version"),
}

_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"
_TABLE_SEP = "\x0e"


def _table_columns(conn, table: str) -> List[str]:
    return [row[1] for row in conn.execute("PRAGMA table_info({0})".format(table))]


def _project_scope(table: str, columns: List[str], project_id: Optional[int]) -> Tuple[str, List[object]]:
    """把"本工程"的谓词落到每张表上：有 project_id 的直接筛，没有的按外键上溯。"""
    if project_id is None:
        return "", []
    if table == "project":
        return " WHERE id = ?", [project_id]
    if "project_id" in columns:
        return " WHERE project_id = ?", [project_id]
    if table == "observation":
        return " WHERE point_id IN (SELECT id FROM point WHERE project_id = ?)", [project_id]
    if table == "disposition":
        return " WHERE alarm_id IN (SELECT id FROM alarm_state WHERE project_id = ?)", [project_id]
    return "", []


def serialize_ledger(conn, project_id: Optional[int] = None) -> str:
    """台账内容的确定性文本形式（指纹的输入，也是测试可直接断言的对象）。"""
    chunks: List[str] = []
    for table in TABLE_NAMES:
        if table == "meta":
            continue
        columns = _table_columns(conn, table)
        if not columns:
            continue
        kept = [name for name in columns if name not in UNTIMED_EXCLUDED_COLUMNS]
        pk = PRIMARY_KEYS.get(table, ("id",))
        order = " ORDER BY {0}".format(",".join(pk))
        where, args = _project_scope(table, columns, project_id)
        selected = ",".join(kept)
        sql = "SELECT {0} FROM {1}{2}{3}".format(selected, table, where, order)
        rows = conn.execute(sql, args).fetchall()
        rendered = [
            table + _FIELD_SEP + _RECORD_SEP.join(
                "|".join("~" if value is None else "{0}".format(value) for value in row)
                for row in rows
            )
        ]
        chunks.append(_TABLE_SEP.join(rendered))
    return _TABLE_SEP.join(chunks)


def ledger_fingerprint(conn, project_code: Optional[str] = None) -> LedgerFingerprint:
    """报告页眉用的指纹；`project_code` 为空时算整库。"""
    project_id = None
    if project_code is not None:
        row = conn.execute("SELECT id FROM project WHERE code = ?", (project_code,)).fetchone()
        if row is None:
            project_id = -1  # 让下游 InputError 处理，这里不重复报错
        else:
            project_id = int(row[0])
    blob = serialize_ledger(conn, project_id)
    return LedgerFingerprint(
        algorithm="sha256", sha256=hashlib.sha256(blob.encode("utf-8")).hexdigest()
    )
