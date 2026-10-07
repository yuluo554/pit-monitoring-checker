"""CSV 观测文件 → `Observation` + 导入回执（plan/07 §三、§八）。

三条设计取向：
  * 结构坏（编码/表头/列数）= 文件级硬失败，退出码 2 且不写批次；一行填错 = 行级拒收并写回执。
    把两种失败混在一起，回执就失去了"哪一行要回去改"的指引。
  * 拒收就是拒收：单位错的行不进台账，缺测的行不补值，被取代的行不覆盖。
  * 数值只接受 ASCII 十进制：`float()` 会认全角数字与 nan/inf，那是幻觉的通路口子。
"""

from __future__ import annotations

import codecs
import csv
import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from pmc.catalog.items import MonitoringItem
from pmc.contract.records import Observation, Rejection
from pmc.errors import InputError

REASON_CODES: Tuple[str, ...] = (
    "name_rule_violation",
    "unknown_item",
    "unknown_point",
    "point_item_mismatch",
    "unit_mismatch",
    "bad_number",
    "bad_date",
    "missing_flag_conflict",
    "round_mismatch",
    "round_not_monotonic",
    "duplicate_identical",
)

HEADER = (
    "round_index",
    "observed_on",
    "point_code",
    "item_code",
    "cumulative_value",
    "unit",
    "missing",
    "raw_text",
)

#: 测点编号与日期的字面校验（plan/07 §三）
#: 一律用 [0-9] 而不是 \\d：Python 的 \\d 认全角数字与阿拉伯-印度数字，
#: `float("３.４")` 也照样给 3.4 —— 那等于把"抄错的单位制读数"静默洗成合法值。
POINT_CODE_RE = re.compile(r"^SYN-[A-Z]{2,3}-[0-9]{2}$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
NUMBER_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")

UNIT_FLAG_DICT = "dict"

#: 缺测行在修订链里的占位原文：数值列只接受 ASCII 十进制，所以它与任何合法数值都不相撞
MISSING_TOKEN = "未测"


@dataclass
class ParsedFile:
    """一个 CSV 的解析结果：接受的行为修订链顺序，拒绝的行带原因码与物理行号。

    `rows_total` 在解析时就冻结为数据行数：后面无论谁被改判成拒收，回执的分母都不许跟着变。
    """

    source_file: str
    file_sha256: str
    rows_total: int = 0
    observations: List[Observation] = field(default_factory=list)
    rejections: List[Rejection] = field(default_factory=list)
    observed_on: Optional[str] = None

    @property
    def rows_accepted(self) -> int:
        return len(self.observations)

    @property
    def rows_rejected(self) -> int:
        return len(self.rejections)


def load_file(path: str) -> Tuple[str, str]:
    """读文件并出 (文本, sha256)；BOM 与非 UTF-8 都是文件级硬失败。"""
    with open(path, "rb") as handle:
        blob = handle.read()
    sha256 = hashlib.sha256(blob).hexdigest()
    if blob.startswith(codecs.BOM_UTF8):
        raise InputError(
            "{0} 带 BOM：表头列名会被读成 \\ufeffround_index，请另存为 UTF-8 无 BOM".format(
                path.replace("\\", "/")
            )
        )
    try:
        return blob.decode("utf-8"), sha256
    except UnicodeDecodeError as exc:
        raise InputError(
            "{0} 不是 UTF-8 编码（字节 {1}）：先用编辑器另存为 UTF-8 再导入".format(
                path.replace("\\", "/"), exc.start
            )
        ) from exc


def _split_rows(text: str) -> List[List[str]]:
    return [row for row in csv.reader(text.splitlines())]


def check_round_dates(
    round_index: int, observed_on: str, neighbors: Sequence[Tuple[int, str]]
) -> Optional[str]:
    """轮次日期单调性：比 (round_index, observed_on) 相邻的两侧都要求日期同向（plan/07 §八）。"""
    for other_index, other_date in neighbors:
        if other_index == round_index:
            continue
        if not DATE_RE.match(other_date):
            continue
        forward = other_index < round_index
        earlier_ok = other_date < observed_on
        later_ok = other_date > observed_on
        if (forward and not earlier_ok) or ((not forward) and not later_ok):
            return "round_not_monotonic"
    return None


def parse_csv(
    text: str,
    *,
    source_file: str,
    file_sha256: str,
    round_index: int,
    items: Dict[str, MonitoringItem],
    points: Dict[str, str],
) -> ParsedFile:
    """按行校验并编修订链；结构坏到无法逐行判断的一律文件级失败。"""
    rows = _split_rows(text)
    if not rows:
        raise InputError("{0} 是空文件，没有任何观测行".format(source_file))
    header = tuple(cell.strip() for cell in rows[0])
    if header != HEADER:
        missing = [c for c in HEADER if c not in header]
        extra = [c for c in header if c not in HEADER]
        raise InputError(
            "{0} 表头与契约不符：缺列 [{1}]，多列 [{2}]".format(
                source_file, ",".join(missing) or "-", ",".join(extra) or "-"
            )
        )

    parsed = ParsedFile(
        source_file=source_file,
        file_sha256=file_sha256,
        rows_total=len(rows) - 1,
    )
    #: 文件内的修订链：测点编号 → 历次上报的数值原文（缺测用 MISSING_TOKEN 占位）
    chain: Dict[str, List[str]] = {}
    dates = set()

    for offset, cells in enumerate(rows[1:], start=2):
        if len(cells) != len(HEADER):
            raise InputError(
                "{0} 第 {1} 行有 {2} 列，与表头 {3} 列不符：整个文件的结构不可信".format(
                    source_file, offset, len(cells), len(HEADER)
                )
            )
        row = dict(zip(HEADER, (cell.strip() for cell in cells)))
        reason, detail = _check_row(row, round_index, items, points)
        if reason is not None:
            parsed.rejections.append(Rejection(source_row=offset, reason_code=reason, detail=detail))
            continue
        if row["missing"] == "1":
            token = MISSING_TOKEN
        elif row["cumulative_value"]:
            token = row["cumulative_value"]
        else:
            # 纯文本行（仪器故障等）：拿原文比，否则两条不同的原文会被当成同值重复
            token = "text:{0}".format(row["raw_text"])
        history = chain.setdefault(row["point_code"], [])
        if history and history[-1] == token:
            parsed.rejections.append(
                Rejection(
                    source_row=offset,
                    reason_code="duplicate_identical",
                    detail="与链上最新一行的内容逐位相同，不产生空修订",
                )
            )
            continue
        history.append(token)
        dates.add(row["observed_on"])
        parsed.observations.append(
            Observation(
                point_code=row["point_code"],
                item_code=row["item_code"],
                round_index=round_index,
                cumulative_value=None if row["missing"] == "1" or not row["cumulative_value"] else float(row["cumulative_value"]),
                unit=None if row["missing"] == "1" else row["unit"],
                raw_text=(row["raw_text"] or "未测") if row["missing"] == "1" else (row["raw_text"] or None),
                missing=row["missing"] == "1",
                revision_seq=len(history),
                source_file=source_file,
                source_row=offset,
                unit_flag=None if row["missing"] == "1" else UNIT_FLAG_DICT,
            )
        )

    if len(dates) > 1:
        raise InputError(
            "{0} 出现多个观测日期 {1}：一个文件只准承载一轮观测".format(source_file, sorted(dates))
        )
    parsed.observed_on = next(iter(dates)) if dates else None
    return parsed


def _check_row(
    row: Dict[str, str],
    round_index: int,
    items: Dict[str, MonitoringItem],
    points: Dict[str, str],
) -> Tuple[Optional[str], str]:
    """行级校验，返回 (原因码, 说明)；None 表示接受。顺序即优先级（plan/07 §八）。"""
    code = row["point_code"]
    if not POINT_CODE_RE.match(code):
        return "name_rule_violation", "测点编号须形如 SYN-XX-NN：{0}".format(code)
    item_code = row["item_code"]
    if item_code not in items:
        return "unknown_item", "监测项目 {0} 不在字典里".format(item_code)
    if code not in points:
        return "unknown_point", "测点 {0} 不在本工程档案里".format(code)
    if points[code] != item_code:
        return "point_item_mismatch", "测点 {0} 档案项目为 {1}，行为 {2}".format(
            code, points[code], item_code
        )
    item = items[item_code]
    unit = row["unit"]
    if unit != item.unit:
        return "unit_mismatch", "{0} 的单位应为 {1}，实为 {2}".format(code, item.unit, unit or "空")
    if row["round_index"] != str(round_index):
        return "round_mismatch", "行内轮次 {0} 与 --round {1} 不一致".format(
            row["round_index"], round_index
        )
    if not DATE_RE.match(row["observed_on"]) or not _valid_date(row["observed_on"]):
        return "bad_date", "观测日期非 YYYY-MM-DD 或不存在：{0}".format(row["observed_on"])
    if row["missing"] not in ("0", "1"):
        return "missing_flag_conflict", "缺测标记只能是 0 或 1：{0}".format(row["missing"])
    if row["missing"] == "1":
        if row["cumulative_value"]:
            return "missing_flag_conflict", "标了缺测却带数值 {0}，不许两样都要".format(
                row["cumulative_value"]
            )
        return None, ""
    if not row["cumulative_value"]:
        if not row["raw_text"]:
            return "missing_flag_conflict", "既不标缺测，又无数值与原文"
        return None, ""
    if not NUMBER_RE.match(row["cumulative_value"]):
        return "bad_number", "数值须为 ASCII 十进制：{0}".format(row["cumulative_value"])
    number = float(row["cumulative_value"])
    if number != number or number in (float("inf"), float("-inf")):
        return "bad_number", "数值非有限：{0}".format(row["cumulative_value"])
    return None, ""


def _valid_date(text: str) -> bool:
    from datetime import date

    year, month, day = (int(part) for part in text.split("-"))
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True
