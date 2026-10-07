"""合成自证档位与量化规则（plan/07 §四、§五）。

这里的双控控制值是**合成数据自己的标尺**，唯一用途是让生成器反算"首个应触发报警的轮次"：
  * 运行时判定不读它们（运行时数值只来自测点档案的设计值，或 data/rulesets 里 verified 的标准回退档）；
  * 它们也不写进 data/ 的任何文件 —— manifest 只登记 profile_id（口径 C7：数据面不得出现未核对阈值数值）。

所以"档位里没有一条来自规范"是刻意的：一旦写成"规范值"，误报率=0 就成了自证循环。
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Optional

PROFILE_ID = "syn-fixture-grade-1"

#: 双控档位（单位一律取监测项目字典，不在这里重复登记）
SYNTH_PROFILE: Dict[str, Dict[str, Optional[float]]] = {
    "top_h_disp": {"cum": 25.0, "rate": 1.5},
    "top_v_disp": {"cum": 20.0, "rate": 1.2},
    "deep_h_disp": {"cum": 30.0, "rate": 1.5},
    "column_v_disp": {"cum": 8.0, "rate": 0.6},
    "pit_bottom_heave": {"cum": 12.0, "rate": 0.8},
    "prop_force": {"cum": 40.0, "rate": 3.0},
    "anchor_force": {"cum": 35.0, "rate": 2.5},
    "water_level": {"cum": 1.0, "rate": 0.30},
    "ground_v_disp": {"cum": 20.0, "rate": 1.2},
    "building_v_disp": {"cum": 20.0, "rate": 1.2},
    "building_tilt": {"cum": 3.0, "rate": None},
    "pipeline_v_disp": {"cum": 15.0, "rate": 1.0},
    "soil_h_disp": {"cum": 25.0, "rate": 1.5},
    "crack_width": {"cum": 4.0, "rate": None},
}

#: 仪器分辨率与落盘小数位（plan/07 §4.2）
UNIT_QUANTIZATION: Dict[str, Dict[str, float]] = {
    "mm": {"resolution": 0.1, "decimals": 1},
    "kN": {"resolution": 1.0, "decimals": 0},
    "m": {"resolution": 0.01, "decimals": 2},
    "‰": {"resolution": 0.1, "decimals": 1},
}

#: 测点编号前缀：一项目一前缀，编号形态 SYN-XX-NN
POINT_PREFIX: Dict[str, str] = {
    "top_h_disp": "TH",
    "top_v_disp": "TV",
    "deep_h_disp": "DH",
    "column_v_disp": "CV",
    "pit_bottom_heave": "PB",
    "prop_force": "PF",
    "anchor_force": "AF",
    "water_level": "WL",
    "ground_v_disp": "GD",
    "building_v_disp": "BV",
    "building_tilt": "BT",
    "pipeline_v_disp": "PL",
    "soil_h_disp": "SH",
    "crack_width": "CW",
}

POINT_CODE_RE = re.compile(r"^SYN-[A-Z]{2,3}-\d{2}$")
PROJECT_CODE_RE = re.compile(r"^SYN-[A-Z0-9]{2,10}$")

#: 形态学方向（plan/07 §4.1）
MORPH_GROWTH = "growth"
MORPH_REBOUND = "rebound"
MORPH_SEASONAL = "seasonal"

ITEM_MORPH: Dict[str, str] = {
    "top_h_disp": MORPH_GROWTH,
    "top_v_disp": MORPH_GROWTH,
    "deep_h_disp": MORPH_GROWTH,
    "column_v_disp": MORPH_REBOUND,
    "pit_bottom_heave": MORPH_REBOUND,
    "prop_force": MORPH_GROWTH,
    "anchor_force": MORPH_GROWTH,
    "water_level": MORPH_SEASONAL,
    "ground_v_disp": MORPH_GROWTH,
    "building_v_disp": MORPH_GROWTH,
    "building_tilt": MORPH_GROWTH,
    "pipeline_v_disp": MORPH_GROWTH,
    "soil_h_disp": MORPH_GROWTH,
    "crack_width": MORPH_GROWTH,
}

#: 流变段衰减常数与末段占比（占本站轮次的最后 25%）
RHEOLOGY_TAU = 4.0
RHEOLOGY_TAIL_RATIO = 0.25
#: 回弹段相对开挖影响段的斜率折减
REBOUND_FACTOR = -0.45
#: 逐轮抖动幅度（±15%）
JITTER = 0.15
#: 季节项幅度，按该项累计控制值的比例取（仅 morph=seasonal，按轮次序取正弦，不读时钟）。
#: 0.12 是上限约束的结果：日报站首两轮的季节跳变会被速率判据看到，幅度再大就自己报警了。
SEASON_AMPLITUDE = 0.12


def resolution(unit: str) -> float:
    key = _quant_key(unit)
    return UNIT_QUANTIZATION[key]["resolution"]


def decimals(unit: str) -> int:
    key = _quant_key(unit)
    return int(UNIT_QUANTIZATION[key]["decimals"])


def _quant_key(unit: str) -> str:
    if unit not in UNIT_QUANTIZATION:
        raise KeyError("单位 {0} 未登记分辨率与小数位".format(unit))
    return unit


def quantize(value: float, unit: str) -> float:
    """按仪器分辨率量化：分辨率与小数位同源（plan/07 §4.2）。

    用 Decimal 半进位而不是 round()：round 的银行家进位跟着二进制表示漂，
    而量化后的值要参与期望触发轮次反算，末位漂一位就可能把首超轮次漂开一轮。
    """
    quantum = Decimal(1).scaleb(-decimals(unit))
    rounded = Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP)
    return float(rounded) + 0.0  # +0.0 归一化 -0.0，落盘文本不得出现 "-0.0"


def format_value(value: float, unit: str) -> str:
    """固定小数位格式化：不依赖平台 repr，跨机器逐字节一致。"""
    return "{0:.{1}f}".format(value, decimals(unit))


def thresholds(item_code: str) -> Dict[str, Optional[float]]:
    if item_code not in SYNTH_PROFILE:
        raise KeyError("监测项目 {0} 未登记合成档位".format(item_code))
    return SYNTH_PROFILE[item_code]


def point_code(item_code: str, seq: int) -> str:
    prefix = POINT_PREFIX[item_code]
    return "SYN-{0}-{1:02d}".format(prefix, seq)


def valid_point_code(code: str) -> bool:
    return bool(POINT_CODE_RE.match(code))


def valid_project_code(code: str) -> bool:
    return bool(PROJECT_CODE_RE.match(code))
