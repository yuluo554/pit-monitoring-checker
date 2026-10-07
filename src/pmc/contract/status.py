"""报警状态机的状态集合与允许迁移（模块 2 的输出契约）。

状态定义来自题面 01 §模块 2；未闭环延续是本题必须持久化才能得到的一条纪律。
迁移表是"契约"，判定算法在 M2 的 pmc/alarm/engine.py 落地。
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Tuple


class ObsState:
    """测点 × 轮次 × 监测项目 的一条状态。值即落库文本，改名属口径变更。"""

    NORMAL = "normal"
    PREWARNING = "prewarning"
    ALARM = "alarm"
    ALARM_CONFIRMED = "alarm_confirmed"
    ALARM_HANDLED = "alarm_handled"
    #: 待定值：阈值无来源或未挂原文核对 —— 不得进入报警判定，也不得被渲染成"正常"
    UNDETERMINED = "undetermined"


ALL_STATES: Tuple[str, ...] = (
    ObsState.NORMAL,
    ObsState.PREWARNING,
    ObsState.ALARM,
    ObsState.ALARM_CONFIRMED,
    ObsState.ALARM_HANDLED,
    ObsState.UNDETERMINED,
)

#: 未闭环集合：处于这些状态的测点，下一轮即使数值回落也必须带"未闭环"标记
UNCLOSED_STATES: FrozenSet[str] = frozenset(
    {ObsState.ALARM, ObsState.ALARM_CONFIRMED}
)

#: 已闭环集合（报警生命周期结束）
CLOSED_STATES: FrozenSet[str] = frozenset({ObsState.ALARM_HANDLED})

#: 触发依据：与状态正交，说明这条状态是靠哪个判据得出的
TRIGGER_BASES: Tuple[str, ...] = (
    "cumulative",  # 累计量判据
    "rate",  # 速率判据
    "both",  # 双控同时触发
    "none",  # 正常 / 待定值
)

#: 合法迁移（轮次推进时的状态机边）。未列出的迁移视为非法，引擎要报错而不是静默纠正。
ALLOWED_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    ObsState.UNDETERMINED: frozenset(
        {ObsState.UNDETERMINED, ObsState.NORMAL, ObsState.PREWARNING, ObsState.ALARM}
    ),
    ObsState.NORMAL: frozenset(
        {ObsState.NORMAL, ObsState.PREWARNING, ObsState.ALARM, ObsState.UNDETERMINED}
    ),
    ObsState.PREWARNING: frozenset(
        {ObsState.NORMAL, ObsState.PREWARNING, ObsState.ALARM, ObsState.UNDETERMINED}
    ),
    ObsState.ALARM: frozenset(
        {
            ObsState.ALARM,
            ObsState.ALARM_CONFIRMED,
            ObsState.ALARM_HANDLED,
            ObsState.UNDETERMINED,
        }
    ),
    ObsState.ALARM_CONFIRMED: frozenset(
        {ObsState.ALARM_CONFIRMED, ObsState.ALARM_HANDLED, ObsState.UNDETERMINED}
    ),
    ObsState.ALARM_HANDLED: frozenset(
        {ObsState.NORMAL, ObsState.PREWARNING, ObsState.ALARM, ObsState.UNDETERMINED}
    ),
}

#: 报警严重程度序（用于"同一测点同轮多判据取重"与报告排序）
SEVERITY_ORDER: Tuple[str, ...] = (
    ObsState.NORMAL,
    ObsState.UNDETERMINED,
    ObsState.PREWARNING,
    ObsState.ALARM,
    ObsState.ALARM_CONFIRMED,
)


def is_unclosed(state: str) -> bool:
    """报警是否尚未闭环（处置记录缺失即一直为真，跨轮次延续）。"""
    return state in UNCLOSED_STATES


def can_transition(frm: str, to: str) -> bool:
    if frm not in ALL_STATES or to not in ALL_STATES:
        return False
    return to in ALLOWED_TRANSITIONS[frm]


def worse(left: str, right: str) -> str:
    """取更严重的一侧；SEVERITY_ORDER 之外的状态按 left 优先，交调用方报错。"""
    if left not in SEVERITY_ORDER or right not in SEVERITY_ORDER:
        return left
    return left if SEVERITY_ORDER.index(left) >= SEVERITY_ORDER.index(right) else right
