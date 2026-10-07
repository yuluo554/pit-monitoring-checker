"""CLI 退出码与异常族。

退出码语义在 M0 定稿，之后重构不得漂移（由 tests/test_cli_surface.py 锁定）：

0  OK                  命令完成，且结果可直接使用
1  DEGRADED            完成但有降级：存在未闭环报警、存在未核对依据、或部分规则不启用
2  INPUT_UNAVAILABLE    输入不可用：文件缺失/格式非法/契约校验失败/台账与阈值对不上
3  NOT_IMPLEMENTED      该命令所属里程碑尚未开工（骨架期占位，M5 后可删除本档）
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_DEGRADED = 1
EXIT_INPUT_UNAVAILABLE = 2
EXIT_NOT_IMPLEMENTED = 3

EXIT_MEANING = {
    EXIT_OK: "ok",
    EXIT_DEGRADED: "degraded",
    EXIT_INPUT_UNAVAILABLE: "input_unavailable",
    EXIT_NOT_IMPLEMENTED: "not_implemented",
}


class PmcError(Exception):
    """本工具所有可预期错误的基类：携带退出码与面向用户的说明。"""

    exit_code = EXIT_INPUT_UNAVAILABLE

    def __init__(self, message: str, exit_code: int = None):
        super().__init__(message)
        self.message = message
        if exit_code is not None:
            self.exit_code = exit_code


class ContractError(PmcError):
    """契约（表结构/规则集/条款登记）不合法：一律拒绝进入判定路径。"""

    exit_code = EXIT_INPUT_UNAVAILABLE


class InputError(PmcError):
    """外部输入不可用：缺文件、编码错、单位不一致等。"""

    exit_code = EXIT_INPUT_UNAVAILABLE


class NotImpl(PmcError):
    """占位命令：指向里程碑，避免把未实现伪装成成功。"""

    exit_code = EXIT_NOT_IMPLEMENTED

    def __init__(self, command: str, milestone: str):
        super().__init__("{0} 属 {1}，尚未开工（本轮只交付契约级骨架）".format(command, milestone))
