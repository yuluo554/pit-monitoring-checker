"""门禁四连（plan/10 §九）：selfcheck → synth --check → pytest → bench，外加台账面反证环。

每一环都断言**期望退出码**：反证环（`bench --plane ledger`）期望 1 —— 依据一条都没核对时
生产数据面必须出不了数值结论。任何一环与期望不符就非 0 退出，README 与 CI 跑的是同一条命令。

零第三方依赖：只用 stdlib 的 subprocess + sys.executable。
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: (环名, 参数, 期望退出码)
STEPS: Sequence[Tuple[str, Sequence[str], int]] = (
    ("契约自检", ["-m", "pmc", "selfcheck"], 0),
    ("合成产物位级对账", ["-m", "pmc", "synth", "--check"], 0),
    ("守门测试", ["-m", "pytest", "-rs"], 0),
    ("基准评测（合成自证档位面）", ["-m", "pmc", "bench", "--all", "--json"], 0),
    ("反证：依据未核对就不出货（台账面）", ["-m", "pmc", "bench", "--plane", "ledger"], 1),
)


def _command(args: List[str]) -> List[str]:
    return [sys.executable, "-X", "utf8"] + list(args)


def main(argv: Sequence[str] = None) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(ROOT, "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    failed: List[str] = []
    for name, args, expected in STEPS:
        command = _command(args)
        print("GATE_STEP {0} :: {1}".format(name, " ".join(command)), flush=True)
        code = subprocess.call(command, cwd=ROOT, env=env)
        if code != expected:
            print("GATE_FAIL {0} 退出码 {1}，期望 {2}".format(name, code, expected), flush=True)
            failed.append(name)
        else:
            print("GATE_OK {0}（退出码 {1}）".format(name, code), flush=True)
    if failed:
        print("GATE_RESULT 失败 {0} 环：{1}".format(len(failed), "、".join(failed)))
        return 1
    print("GATE_RESULT {0} 环全通过".format(len(STEPS)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
