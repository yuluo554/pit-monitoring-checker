"""三级台账的表结构契约（工程 → 测点 → 轮次），SQLite DDL 单一事实源。

设计约束（对应题面 01 §模块 1/2/3）：
  * observation 用 UNIQUE(point_id, round_id, revision_seq) 承载重复上报的修订链，
    后到记录只追加、并让前一条 superseded_by 指向新行 —— 静默覆盖在结构层就不可能；
  * missing 与 value_cum 的互斥写进 CHECK，缺测不许被"顺手插值"补上；
  * alarm_state 的 CHECK 把"待定值不出货"落到存储层：undetermined 行的阈值列必须为空，
    非 undetermined 行必须挂来源；按标准条文回退的判定必须带条款号；
  * 库是运行时产物、可以带时间；任何入仓的冻结产物不许带时间（见 plan/04 §确定性纪律）。
"""

from __future__ import annotations

import sqlite3
from typing import List, Sequence

from pmc import SCHEMA_VERSION
from pmc.contract.status import ALL_STATES, TRIGGER_BASES
from pmc.contract.thresholds import SOURCE_KINDS, VERIFY_STATUSES
from pmc.errors import ContractError

DDL_STATEMENTS: Sequence[str] = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS project (
        id              INTEGER PRIMARY KEY,
        code            TEXT NOT NULL UNIQUE,
        name            TEXT NOT NULL,
        builder_unit    TEXT,
        monitor_unit    TEXT,
        supervisor_unit TEXT,
        scheme_no       TEXT,
        start_date      TEXT,
        end_date        TEXT,
        note            TEXT,
        synthetic       INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS working_condition (
        id               INTEGER PRIMARY KEY,
        project_id       INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
        code             TEXT NOT NULL,
        name             TEXT NOT NULL,
        excavation_depth REAL,
        effective_from   TEXT NOT NULL,
        clause_ids       TEXT,
        UNIQUE (project_id, code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS point (
        id                   INTEGER PRIMARY KEY,
        project_id           INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
        code                 TEXT NOT NULL,
        item_code            TEXT NOT NULL,
        location             TEXT,
        initial_value        REAL,
        design_cum_value     REAL,
        design_cum_unit      TEXT,
        design_rate_value    REAL,
        design_rate_unit     TEXT,
        threshold_source_kind TEXT NOT NULL DEFAULT 'none'
            CHECK (threshold_source_kind IN ({source_kinds})),
        threshold_status     TEXT NOT NULL DEFAULT 'pending'
            CHECK (threshold_status IN ({verify_statuses})),
        threshold_evidence   TEXT,
        clause_ids           TEXT,
        install_date         TEXT,
        abandon_date         TEXT,
        UNIQUE (project_id, code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS obs_round (
        id             INTEGER PRIMARY KEY,
        project_id     INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
        round_index    INTEGER NOT NULL,
        observed_on    TEXT NOT NULL,
        condition_id   INTEGER REFERENCES working_condition(id),
        is_intensified INTEGER NOT NULL DEFAULT 0,
        note           TEXT,
        UNIQUE (project_id, round_index),
        CHECK (round_index >= 1)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS import_batch (
        id            INTEGER PRIMARY KEY,
        project_id    INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
        source_file   TEXT NOT NULL,
        file_sha256   TEXT NOT NULL,
        round_id      INTEGER REFERENCES obs_round(id),
        rows_total    INTEGER NOT NULL,
        rows_accepted INTEGER NOT NULL,
        rows_rejected INTEGER NOT NULL,
        receipt_json  TEXT NOT NULL,
        imported_at   TEXT,
        CHECK (rows_accepted + rows_rejected = rows_total)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS observation (
        id            INTEGER PRIMARY KEY,
        point_id      INTEGER NOT NULL REFERENCES point(id) ON DELETE CASCADE,
        round_id      INTEGER NOT NULL REFERENCES obs_round(id) ON DELETE CASCADE,
        revision_seq  INTEGER NOT NULL DEFAULT 1,
        superseded_by INTEGER REFERENCES observation(id),
        value_cum     REAL,
        unit          TEXT,
        raw_text      TEXT,
        missing       INTEGER NOT NULL DEFAULT 0,
        unit_flag     TEXT,
        batch_id      INTEGER REFERENCES import_batch(id),
        source_row    INTEGER,
        CHECK (revision_seq >= 1),
        CHECK (missing = 0 OR value_cum IS NULL),
        CHECK (missing = 1 OR value_cum IS NOT NULL OR raw_text IS NOT NULL),
        UNIQUE (point_id, round_id, revision_seq)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alarm_state (
        id                  INTEGER PRIMARY KEY,
        project_id          INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
        point_id            INTEGER NOT NULL REFERENCES point(id) ON DELETE CASCADE,
        round_id            INTEGER NOT NULL REFERENCES obs_round(id) ON DELETE CASCADE,
        observation_id      INTEGER REFERENCES observation(id),
        item_code           TEXT NOT NULL,
        state               TEXT NOT NULL
            CHECK (state IN ({states})),
        trigger_basis       TEXT NOT NULL DEFAULT 'none'
            CHECK (trigger_basis IN ({bases})),
        cum_value           REAL,
        cum_threshold       REAL,
        rate_value          REAL,
        rate_threshold      REAL,
        window_days         REAL,
        threshold_source_kind TEXT NOT NULL
            CHECK (threshold_source_kind IN ({source_kinds})),
        threshold_status    TEXT NOT NULL
            CHECK (threshold_status IN ({verify_statuses})),
        clause_ids          TEXT,
        disabled_reasons    TEXT,
        unclosed            INTEGER NOT NULL DEFAULT 0,
        first_alarm_round_id INTEGER REFERENCES obs_round(id),
        CHECK ((state <> 'undetermined') OR (trigger_basis = 'none'
            AND cum_threshold IS NULL AND rate_threshold IS NULL)),
        CHECK ((state = 'undetermined') OR (threshold_source_kind <> 'none'
            AND threshold_source_kind <> '')),
        CHECK ((threshold_source_kind <> 'standard_value')
            OR (clause_ids IS NOT NULL AND clause_ids <> '')),
        UNIQUE (point_id, round_id, item_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS disposition (
        id         INTEGER PRIMARY KEY,
        alarm_id   INTEGER NOT NULL REFERENCES alarm_state(id) ON DELETE CASCADE,
        kind       TEXT NOT NULL CHECK (kind IN ('confirm', 'handle', 'reobserve')),
        note       TEXT,
        actor_role TEXT,
        acted_at   TEXT,
        CHECK (kind <> 'handle' OR (actor_role IS NOT NULL AND actor_role <> ''))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS violation (
        id            INTEGER PRIMARY KEY,
        project_id    INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
        point_id      INTEGER REFERENCES point(id) ON DELETE CASCADE,
        round_id      INTEGER REFERENCES obs_round(id),
        kind          TEXT NOT NULL CHECK (kind IN
            ('missed', 'over_interval', 'stale_frequency', 'no_intensified_after_alarm')),
        rule_id       TEXT NOT NULL,
        clause_ids    TEXT NOT NULL,
        evidence_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ruleset_applied (
        code        TEXT NOT NULL,
        version     INTEGER NOT NULL,
        sha256      TEXT NOT NULL,
        applied_at  TEXT,
        PRIMARY KEY (code, version)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_observation_point_round ON observation(point_id, round_id)",
    "CREATE INDEX IF NOT EXISTS idx_alarm_state_point ON alarm_state(point_id, round_id)",
    "CREATE INDEX IF NOT EXISTS idx_violation_project ON violation(project_id, kind)",
)

TABLE_NAMES: Sequence[str] = (
    "meta",
    "project",
    "working_condition",
    "point",
    "obs_round",
    "import_batch",
    "observation",
    "alarm_state",
    "disposition",
    "violation",
    "ruleset_applied",
)


def _quote_list(values: Sequence[str]) -> str:
    return ",".join("'" + str(v).replace("'", "''") + "'" for v in values)


def _render(statement: str) -> str:
    return statement.format(
        states=_quote_list(ALL_STATES),
        bases=_quote_list(TRIGGER_BASES),
        source_kinds=_quote_list(SOURCE_KINDS),
        verify_statuses=_quote_list(VERIFY_STATUSES),
    )


def ddl_statements() -> List[str]:
    """枚举注入 CHECK 约束：契约层改名而 DDL 未跟随时，旧库不会静默接受脏值。"""
    return [_render(s) if "{" in s else s for s in DDL_STATEMENTS]


def apply_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    for stmt in ddl_statements():
        conn.execute(stmt)
    _ensure_meta(conn)
    conn.commit()


def _ensure_meta(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    want = str(SCHEMA_VERSION)
    if row is None:
        conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)", (want,))
    elif row[0] != want:
        raise ContractError(
            "台账 schema 版本不符：库内 {0}，代码 {1}".format(row[0], want)
        )


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_names_in_db(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return sorted(r[0] for r in rows)


def missing_tables(conn: sqlite3.Connection) -> List[str]:
    present = set(table_names_in_db(conn))
    return [t for t in TABLE_NAMES if t not in present]
