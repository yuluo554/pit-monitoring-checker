"""确定性随机数：splitmix64。

为什么不用 stdlib random：random 模块不承诺跨 Python 版本的序列一致性，
基准要求"干净环境一键复现 + 两次运行逐字节一致"，因此生成/落库路径禁用 stdlib random。
本函数输出 64 位无符号整数，与解释器版本无关。
"""

from __future__ import annotations

from typing import Iterator

MASK64 = 0xFFFFFFFFFFFFFFFF


def splitmix64_next(state: int) -> int:
    state = (state + 0x9E3779B97F4A7C15) & MASK64
    z = state
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
    return (z ^ (z >> 31)) & MASK64


class DetRng:
    """固定 seed 的可复现随机源；stream 名参与派生，避免多个流互相干扰。"""

    def __init__(self, seed: int, stream: str = "default") -> None:
        self.seed = seed & MASK64
        self.stream = stream
        self.state = self._derive_state()

    def _derive_state(self) -> int:
        import hashlib

        digest = hashlib.sha256(
            "{0}:{1}".format(self.seed, self.stream).encode("utf-8")
        ).digest()
        return int.from_bytes(digest[:8], "big") & MASK64

    def next_u64(self) -> int:
        self.state = splitmix64_next(self.state)
        return self.state

    def uniform(self, lo: float, hi: float) -> float:
        return lo + (hi - lo) * (self.next_u64() / float(MASK64 + 1))

    def randint(self, lo: int, hi: int) -> int:
        if hi < lo:
            raise ValueError("randint 需要 hi >= lo")
        span = hi - lo + 1
        return lo + int(self.next_u64() % span)

    def sequence(self, count: int) -> Iterator[int]:
        for _ in range(count):
            yield self.next_u64()


def reseed(rng: DetRng, stream: str) -> DetRng:
    return DetRng(rng.seed, stream)
