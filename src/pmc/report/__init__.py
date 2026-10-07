"""模块 4：成果报告（xlsx）。标准库直写 OOXML，零第三方依赖（plan/06 D03 / D34）。

对外只有一个入口：`builder.build_report(conn, project, kind, ...)`。
"""

from __future__ import annotations

from pmc.report.builder import (
    ABNORMAL_STATES,
    DISCLAIMER,
    KIND_LABELS,
    REPORT_KINDS,
    SIGN_BOUNDARY,
    STATE_LABELS,
    ReportError,
    ReportResult,
    assert_vocabulary,
    build_report,
    locator_sql,
    report_exit_code,
    report_filename,
)
from pmc.report.fingerprint import ledger_fingerprint, serialize_ledger

__all__ = [
    "ABNORMAL_STATES",
    "DISCLAIMER",
    "KIND_LABELS",
    "REPORT_KINDS",
    "SIGN_BOUNDARY",
    "STATE_LABELS",
    "ReportError",
    "ReportResult",
    "assert_vocabulary",
    "build_report",
    "ledger_fingerprint",
    "locator_sql",
    "report_exit_code",
    "report_filename",
    "serialize_ledger",
]
