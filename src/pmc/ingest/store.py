"""导入落库：修订链追加、回执写入与台账查询（plan/07 §八）。

重复上报的处置是本题与 Excel 重算的真正差别：
  * 后到的行**只追加**（revision_seq = 链上最大值 +1），前一条的 superseded_by 指向新行；
  * 完全同值的重复不产生空修订（否则链上全是噪声，审计看不清谁改过数）；
  * 回执不平（入库+拒绝 ≠ 总行数）由 DDL 的 CHECK 直接拒写，不是靠代码自觉。
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from pmc.contract.records import ImportReceipt, Observation, Rejection
from pmc.errors import InputError
from pmc.ingest.csvio import MISSING_TOKEN, ParsedFile, check_round_dates

BATCH_IMPORTED_AT = None  # 回执不带时钟：台账可以带时间，但可复现的对账通路不靠它


def project_id_for(conn, project_code: str) -> int:
    row = conn.execute("SELECT id FROM project WHERE code = ?", (project_code,)).fetchone()
    if row is None:
        raise InputError(
            "工程 {0} 不在台账里：先跑 `pmc synth --db <台账>` 建档，或按档案手工登记".format(project_code)
        )
    return int(row[0])


def point_archive(conn, project_id: int) -> Dict[str, str]:
    """测点档案：code → item_code。导入器据此做 unknown_point 与 point_item_mismatch 校验。"""
    rows = conn.execute(
        "SELECT code, item_code FROM point WHERE project_id = ?", (project_id,)
    ).fetchall()
    if not rows:
        raise InputError(
            "工程 id={0} 没有测点档案，无法校验测点编号唯一性".format(project_id)
        )
    return {code: item for code, item in rows}


def round_row(conn, project_id: int, round_index: int) -> Tuple[int, str]:
    row = conn.execute(
        "SELECT id, observed_on FROM obs_round WHERE project_id = ? AND round_index = ?",
        (project_id, round_index),
    ).fetchone()
    if row is None:
        raise InputError(
            "轮次 R{0} 不在工程 id={1} 的台账里：一轮观测必须先有轮次档案".format(
                round_index, project_id
            )
        )
    return int(row[0]), row[1]


def _chain_state(conn, project_id: int, round_id: int) -> Dict[str, Tuple[int, object, int]]:
    """链上现状：point_code → (最大 revision, 链上最新一行的内容, 该行的 row id)。"""
    rows = conn.execute(
        "SELECT id, point_id, revision_seq, value_cum, missing, raw_text FROM observation"
        " WHERE round_id = ? AND superseded_by IS NULL",
        (round_id,),
    ).fetchall()
    point_codes = {
        pid: code
        for pid, code in conn.execute(
            "SELECT id, code FROM point WHERE project_id = ?", (project_id,)
        ).fetchall()
    }
    latest = {}  # type: Dict[str, Tuple[int, object, int]]
    for obs_id, point_id, revision_seq, value_cum, missing, raw_text in rows:
        code = point_codes.get(point_id)
        if code is None:
            continue
        if missing:
            token = ("mark", MISSING_TOKEN)
        elif value_cum is not None:
            token = ("num", float(value_cum))
        else:
            token = ("text", raw_text or "")
        current = latest.get(code)
        if current is None or revision_seq > current[0]:
            latest[code] = (int(revision_seq), token, int(obs_id))
    return latest


def _token(obs: Observation) -> Tuple[str, object]:
    """链上比较用的内容：数值走 float，缺测走标记，纯文本行走文本。

    两侧都归化成同一种形状，`"12"`（CSV 整数写法）与 `12.0`（SQLite REAL）才算同值。
    """
    if obs.missing:
        return ("mark", MISSING_TOKEN)
    if obs.cumulative_value is not None:
        return ("num", float(obs.cumulative_value))
    return ("text", obs.raw_text or "")


def _receipt_json(
    parsed: ParsedFile,
    round_index: int,
    rejections: List[Rejection],
    revisions: List[Dict[str, object]],
) -> str:
    doc = {
        "source_file": parsed.source_file.replace("\\", "/"),
        "file_sha256": parsed.file_sha256,
        "round_index": round_index,
        "rows_total": parsed.rows_total,
        "rows_accepted": len(revisions),
        "rows_rejected": len(rejections),
        "rejections": [
            {
                "source_row": item.source_row,
                "reason_code": item.reason_code,
                "detail": item.detail,
            }
            for item in rejections
        ],
        "revisions": revisions,
    }
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def write_batch(
    conn,
    *,
    project_code: str,
    round_index: int,
    parsed: ParsedFile,
    dry_run: bool = False,
) -> ImportReceipt:
    """把一轮 CSV 落进台账并出回执。`dry_run` 只算不落，用于导入前体检。"""
    project_id = project_id_for(conn, project_code)
    round_id, ledger_date = round_row(conn, project_id, round_index)
    clash = conn.execute(
        "SELECT id FROM import_batch WHERE project_id = ? AND round_id = ? AND file_sha256 = ?",
        (project_id, round_id, parsed.file_sha256),
    ).fetchone()
    if clash is not None:
        raise InputError(
            "{0} 与已导入批次 id={1} 内容同哈希：同一份文件不重复入库，"
            "修订请由新文件上报后进修订链".format(parsed.source_file.replace("\\", "/"), clash[0])
        )
    point_ids = {
        code: pid
        for code, pid in conn.execute(
            "SELECT code, id FROM point WHERE project_id = ?", (project_id,)
        ).fetchall()
    }

    observations = list(parsed.observations)
    rejections = list(parsed.rejections)

    if parsed.observed_on is None and observations:
        raise InputError("{0} 没有任何可解析的日期，无法核对轮次单调性".format(parsed.source_file))
    if parsed.observed_on is not None:
        neighbors = conn.execute(
            "SELECT round_index, observed_on FROM obs_round"
            " WHERE project_id = ? AND round_index <> ? ORDER BY round_index",
            (project_id, round_index),
        ).fetchall()
        detail = "文件观测日期 {0} 与台账该轮 {1} 冲突：一轮只有一个日期".format(
            parsed.observed_on, ledger_date
        )
        if parsed.observed_on != ledger_date or check_round_dates(
            round_index, parsed.observed_on, neighbors
        ):
            rejections.extend(
                Rejection(source_row=obs.source_row or 0, reason_code="round_not_monotonic", detail=detail)
                for obs in observations
            )
            observations = []

    #: chain[point] = (当前最大 revision, 链上最新一行的内容, 取代指针)
    #: 取代指针二选一：("db", 已有 row id) 或 ("plan", 本批次内前一条的下标)
    chain = _chain_state(conn, project_id, round_id)
    for code, (revision, token, obs_id) in chain.items():
        chain[code] = (revision, token, ("db", obs_id))
    plan = []  # type: List[Dict[str, object]]
    for obs in observations:
        token = _token(obs)
        base, head_token, ref = chain.get(obs.point_code, (0, None, None))
        if head_token is not None and head_token == token:
            rejections.append(
                Rejection(
                    source_row=obs.source_row or 0,
                    reason_code="duplicate_identical",
                    detail="与链上最新一行同值：重复上报必须带来新内容，否则不产生空修订",
                )
            )
            continue
        revision = base + 1
        plan.append(
            {
                "obs": obs,
                "revision": revision,
                "ref": ref,
                "point_code": obs.point_code,
                "token": token,
            }
        )
        chain[obs.point_code] = (revision, token, ("plan", len(plan) - 1))

    receipt = ImportReceipt(
        source_file=parsed.source_file,
        file_sha256=parsed.file_sha256,
        rows_total=parsed.rows_total,
        rows_accepted=len(plan),
        rows_rejected=len(rejections),
        rejections=rejections,
    )
    receipt.validate()
    if receipt.rows_total != receipt.rows_accepted + receipt.rows_rejected:
        raise InputError(
            "{0} 回执不平：总 {1} / 入库 {2} / 拒绝 {3}".format(
                parsed.source_file,
                receipt.rows_total,
                receipt.rows_accepted,
                receipt.rows_rejected,
            )
        )

    if dry_run:
        return receipt

    revisions = []
    inserted_ids = []
    for entry in plan:
        obs = entry["obs"]
        ref = entry["ref"]
        supersedes = None
        if ref is not None:
            kind, value = ref
            supersedes = int(value) if kind == "db" else int(inserted_ids[int(value)])
        point_id = point_ids[str(entry["point_code"])]
        cur = conn.execute(
            "INSERT INTO observation(point_id, round_id, revision_seq, value_cum, unit, raw_text,"
            " missing, unit_flag, batch_id, source_row) VALUES(?,?,?,?,?,?,?,?,NULL,?)",
            (
                point_id,
                round_id,
                int(entry["revision"]),
                obs.cumulative_value,
                obs.unit,
                obs.raw_text,
                1 if obs.missing else 0,
                obs.unit_flag,
                obs.source_row,
            ),
        )
        observation_id = int(cur.lastrowid)
        inserted_ids.append(observation_id)
        if supersedes is not None:
            conn.execute(
                "UPDATE observation SET superseded_by = ? WHERE id = ?",
                (observation_id, supersedes),
            )
        revisions.append(
            {
                "source_row": obs.source_row,
                "point_code": entry["point_code"],
                "revision_seq": entry["revision"],
                "supersedes_row_id": supersedes,
                "observation_id": observation_id,
            }
        )

    receipt_json = _receipt_json(parsed, round_index, rejections, revisions)
    cur = conn.execute(
        "INSERT INTO import_batch(project_id, source_file, file_sha256, round_id, rows_total,"
        " rows_accepted, rows_rejected, receipt_json, imported_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            project_id,
            parsed.source_file,
            parsed.file_sha256,
            round_id,
            receipt.rows_total,
            receipt.rows_accepted,
            receipt.rows_rejected,
            receipt_json,
            BATCH_IMPORTED_AT,
        ),
    )
    batch_id = int(cur.lastrowid)
    conn.execute(
        "UPDATE observation SET batch_id = ? WHERE id IN ({0})".format(
            ",".join("?" * len(revisions))
        ),
        [batch_id] + [entry["observation_id"] for entry in revisions],
    )
    conn.commit()
    return receipt


def ledger_query(
    conn,
    *,
    project_code: Optional[str],
    point_code: Optional[str],
    round_from: Optional[int],
    round_to: Optional[int],
) -> List[Dict[str, object]]:
    """台账查询：每行给到修订链上的位置与被谁取代，缺测单独成列。"""
    sql = [
        "SELECT p.code, poi.code, poi.item_code, r.round_index, r.observed_on,",
        "       o.revision_seq, o.superseded_by, o.value_cum, o.unit, o.missing,",
        "       o.raw_text, o.source_row, r.is_intensified, c.code",
        " FROM observation o",
        " JOIN point poi ON poi.id = o.point_id",
        " JOIN obs_round r ON r.id = o.round_id",
        " JOIN project p ON p.id = poi.project_id",
        " LEFT JOIN working_condition c ON c.id = r.condition_id",
        " WHERE 1=1",
    ]
    args: List[object] = []
    if project_code:
        sql.append(" AND p.code = ?")
        args.append(project_code)
    if point_code:
        sql.append(" AND poi.code = ?")
        args.append(point_code)
    if round_from is not None:
        sql.append(" AND r.round_index >= ?")
        args.append(round_from)
    if round_to is not None:
        sql.append(" AND r.round_index <= ?")
        args.append(round_to)
    sql.append(" ORDER BY p.code, poi.code, r.round_index, o.revision_seq")
    rows = conn.execute(" ".join(sql), args).fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "project_code": row[0],
                "point_code": row[1],
                "item_code": row[2],
                "round_index": row[3],
                "observed_on": row[4],
                "revision_seq": row[5],
                "superseded_by": row[6],
                "value_cum": row[7],
                "unit": row[8],
                "missing": row[9],
                "raw_text": row[10],
                "source_row": row[11],
                "is_intensified": row[12],
                "condition_code": row[13],
                "effective": row[6] is None,
            }
        )
    return out
