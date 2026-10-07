"""三座虚拟基坑的档案模板（plan/07 §一、§六）。

只定义"测什么、多久测一次、工况什么时候变、事件植在哪一点哪一轮"：
  * 单位与判据形态一律取自 data/dict/monitoring_items.json，这里不重复登记；
  * 控制值数值一律不写进档案（合成档位属 pmc/synth/profile.py，档案属档案）；
  * 工程名/单位名/图号全部走 data/README §三 的假数据白名单形式。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

STEP = "step"
RATE_BURST = "rate_burst"
DRIFT = "drift"
MISSING = "missing"
OUTLIER = "outlier"
UNIT_ERROR = "unit_error"
DUPLICATE_REPORT = "duplicate_report"

EVENT_TYPES: Tuple[str, ...] = (
    STEP, RATE_BURST, DRIFT, MISSING, OUTLIER, UNIT_ERROR, DUPLICATE_REPORT,
)

FIRST_OBSERVED_ON = "2026-03-02"


@dataclass(frozen=True)
class ConditionSpec:
    """工况时间线的一行：从 from_round 起生效，直到下一条工况。"""

    code: str
    name: str
    excavation_depth: float
    gain: float
    from_round: int


@dataclass(frozen=True)
class EventSpec:
    """一起植入事件的目标与形态参数。

    主触发轮次上"相对基线多出的量"才是真值里的 injected_magnitude，由生成器算出，不在这里抄。
    """

    event_type: str
    item_code: str
    point_seq: int
    round_index: int
    magnitude: Optional[float] = None
    per_round: Optional[float] = None
    span_rounds: int = 1
    recovery_per_round: Optional[float] = None
    recovery_rounds: int = 0
    first_report_delta: Optional[float] = None
    wrong_unit: Optional[str] = None


@dataclass(frozen=True)
class SiteSpec:
    code: str
    name: str
    builder_unit: str
    monitor_unit: str
    supervisor_unit: str
    scheme_no: str
    interval_days: int
    rounds: int
    window_days: int
    target_ratio: float
    items: Tuple[Tuple[str, int], ...]
    conditions: Tuple[ConditionSpec, ...]
    events: Tuple[EventSpec, ...]
    intensified_rounds: Tuple[int, ...] = ()
    note: str = ""

    def point_count(self) -> int:
        return sum(count for _, count in self.items)

    def rows_per_round(self) -> int:
        return self.point_count()

    def item_codes(self) -> List[str]:
        return [code for code, _ in self.items]

    def condition_for_round(self, round_index: int) -> ConditionSpec:
        chosen = self.conditions[0]
        for cond in self.conditions:
            if cond.from_round <= round_index:
                chosen = cond
        return chosen

    def observed_on(self, round_index: int) -> str:
        from datetime import date, timedelta

        year, month, day = (int(part) for part in FIRST_OBSERVED_ON.split("-"))
        stamp = date(year, month, day) + timedelta(days=self.interval_days * (round_index - 1))
        return "{0:04d}-{1:02d}-{2:02d}".format(stamp.year, stamp.month, stamp.day)


SITE_LJ3 = SiteSpec(
    code="SYN-LJ3",
    name="SYN·临江路地块 3 号基坑",
    builder_unit="某建设集团第 9 项目部",
    monitor_unit="示例岩土监测有限公司",
    supervisor_unit="示例工程监理有限公司",
    scheme_no="SYN-JK-2026-0001",
    interval_days=1,
    rounds=8,
    window_days=3,
    # 0.18 而不是 0.25：日报站跨度只有 7 天，斜率再叠工况增益 1.25 与 ±15% 抖动后
    # 会顶到速率档，"误报率的分母"就自己先报警了。生成器自检会直接拒绝这种档案。
    target_ratio=0.18,
    items=(
        ("top_h_disp", 14), ("top_v_disp", 14), ("deep_h_disp", 14),
        ("column_v_disp", 14), ("pit_bottom_heave", 14), ("prop_force", 14),
        ("anchor_force", 14), ("water_level", 14), ("ground_v_disp", 14),
        ("building_v_disp", 14), ("building_tilt", 14), ("pipeline_v_disp", 14),
        ("soil_h_disp", 14), ("crack_width", 14),
    ),
    conditions=(
        ConditionSpec("C1", "SYN·一层开挖至 -3.5m", 3.5, 1.0, 1),
        ConditionSpec("C2", "SYN·二层开挖至 -7.0m", 7.0, 1.25, 4),
    ),
    events=(),
    note="全部 14 项、8 轮日报、零事件：误报率的分母",
)

SITE_ZHDQ = SiteSpec(
    code="SYN-ZHDQ",
    name="SYN·中环广场东区基坑",
    builder_unit="某建设集团第 3 项目部",
    monitor_unit="示例岩土监测有限公司",
    supervisor_unit="示例工程监理有限公司",
    scheme_no="SYN-JK-2026-0002",
    interval_days=2,
    rounds=20,
    window_days=5,
    # 0.62 而不是 0.72：留出工况增益与 ±15% 抖动的余量，
    # 让基线峰值停在控制值之下（§九 自检 1），植入事件的超标才归因得清楚。
    target_ratio=0.62,
    items=(
        ("top_h_disp", 28), ("top_v_disp", 28), ("deep_h_disp", 28),
        ("column_v_disp", 28), ("pit_bottom_heave", 28), ("prop_force", 28),
        ("water_level", 28),
    ),
    conditions=(
        ConditionSpec("C1", "SYN·一层开挖至 -4.0m", 4.0, 1.0, 1),
        ConditionSpec("C2", "SYN·二层开挖至 -8.0m", 8.0, 1.2, 7),
        ConditionSpec("C3", "SYN·换撑卸荷段", 8.0, 0.7, 15),
    ),
    events=(
        EventSpec(DUPLICATE_REPORT, "top_h_disp", 18, 9,
                  magnitude=18.5, first_report_delta=-17.7),
        EventSpec(OUTLIER, "water_level", 12, 6, magnitude=0.35),
        EventSpec(UNIT_ERROR, "top_h_disp", 15, 12, wrong_unit="cm"),
        EventSpec(DRIFT, "top_v_disp", 7, 8, per_round=1.0),
        EventSpec(RATE_BURST, "top_h_disp", 3, 11, per_round=5.0, span_rounds=2),
        EventSpec(STEP, "deep_h_disp", 5, 14, magnitude=18.0),
        EventSpec(MISSING, "prop_force", 9, 16),
    ),
    intensified_rounds=(10, 11, 12),
    note="7 类事件各 1 起、20 轮隔日：召回率与首超定位误差的主战场",
)

SITE_YYCG = SiteSpec(
    code="SYN-YYCG",
    name="SYN·综合医院地下车库基坑",
    builder_unit="某建设集团第 7 项目部",
    monitor_unit="示例岩土监测有限公司",
    supervisor_unit="示例工程监理有限公司",
    scheme_no="SYN-JK-2026-0003",
    interval_days=7,
    rounds=30,
    window_days=7,
    target_ratio=0.62,
    items=(
        ("top_h_disp", 28), ("top_v_disp", 28), ("deep_h_disp", 28),
        ("pipeline_v_disp", 28), ("building_v_disp", 28), ("ground_v_disp", 28),
        ("crack_width", 28),
    ),
    conditions=(
        ConditionSpec("C1", "SYN·一层开挖至 -5.0m", 5.0, 1.0, 1),
        ConditionSpec("C2", "SYN·二层开挖至 -10.0m", 10.0, 1.2, 9),
        ConditionSpec("C3", "SYN·三层开挖至 -14.5m", 14.5, 1.1, 17),
        ConditionSpec("C4", "SYN·换撑封底段", 14.5, 0.6, 25),
    ),
    events=(
        # 跳增后分三轮回撤：主触发轮次之后数值回落但仍无处置记录，
        # 用来检验"未闭环跨轮次延续"不是靠持续超标蒙出来的。
        EventSpec(STEP, "pipeline_v_disp", 4, 18,
                  magnitude=10.5, recovery_per_round=-4.0, recovery_rounds=3),
        EventSpec(RATE_BURST, "top_h_disp", 11, 22, per_round=11.0, span_rounds=2),
        EventSpec(MISSING, "ground_v_disp", 20, 25),
        EventSpec(MISSING, "building_v_disp", 2, 30),
    ),
    intensified_rounds=(),
    note="报警未处置跨 3 轮 + 工况变更 4 段 + 漏测 2 处，且报警后不加密观测（模块 3 的反例）",
)

SITE_ORDER: Tuple[str, ...] = (SITE_LJ3.code, SITE_ZHDQ.code, SITE_YYCG.code)

_BY_CODE: Dict[str, SiteSpec] = {
    SITE_LJ3.code: SITE_LJ3,
    SITE_ZHDQ.code: SITE_ZHDQ,
    SITE_YYCG.code: SITE_YYCG,
}


def site_codes() -> List[str]:
    return list(SITE_ORDER)


def get_site(code: str) -> SiteSpec:
    if code not in _BY_CODE:
        raise KeyError("虚拟基坑 {0} 未登记档案".format(code))
    return _BY_CODE[code]


def sites(count: int) -> List[SiteSpec]:
    """取前 N 座基坑（--sites 的口径：按登记顺序取前 N，不是任选）。"""
    if not 1 <= count <= len(SITE_ORDER):
        raise KeyError("--sites 取值范围 1–{0}".format(len(SITE_ORDER)))
    return [_BY_CODE[c] for c in SITE_ORDER[:count]]


def event_types_covered(events: Tuple[EventSpec, ...]) -> List[str]:
    seen = set(ev.event_type for ev in events)
    return sorted(seen)
