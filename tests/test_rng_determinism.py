"""确定性生成纪律：生成/落盘路径禁 stdlib random、禁 set 迭代序依赖、禁系统时钟。

基准要跨机器逐字节复现，stdlib random 不承诺跨 Python 版本序列一致。
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from _helpers import ROOT, SRC

from pmc.synth.rng import MASK64, DetRng, reseed, splitmix64_next


def test_same_seed_same_stream_same_sequence():
    a = list(DetRng(2026, "rounds").sequence(50))
    b = list(DetRng(2026, "rounds").sequence(50))
    assert a == b


def test_different_stream_isolates():
    assert list(DetRng(2026, "a").sequence(10)) != list(DetRng(2026, "b").sequence(10))


def test_reseed_keeps_seed_and_swaps_stream():
    rng = DetRng(7, "orig")
    other = reseed(rng, "other")
    assert other.seed == rng.seed and other.stream == "other"


def test_uniform_within_bounds():
    rng = DetRng(11, "u")
    for _ in range(200):
        assert -5.0 <= rng.uniform(-5.0, 3.0) <= 3.0


def test_randint_bounds_and_determinism():
    values = [DetRng(13, "i").randint(1, 6) for _ in range(1)]
    assert 1 <= values[0] <= 6
    rng_a, rng_b = DetRng(13, "i"), DetRng(13, "i")
    seq_a = [rng_a.randint(1, 6) for _ in range(100)]
    seq_b = [rng_b.randint(1, 6) for _ in range(100)]
    assert seq_a == seq_b
    assert all(1 <= v <= 6 for v in seq_a)


def test_next_u64_stays_in_64_bits():
    state = 0
    for _ in range(1000):
        state = splitmix64_next(state)
        assert 0 <= state <= MASK64


def test_randint_rejects_inverted_range():
    with pytest.raises(ValueError):
        DetRng(1, "x").randint(5, 4)


FORBIDDEN_MODULES = {"random", "requests", "socket", "http", "urllib"}


def _imports_of(path: pathlib.Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def _src_files():
    return sorted(p for p in pathlib.Path(SRC).rglob("*.py"))


def test_no_stdlib_random_in_kernel():
    offenders = []
    for path in _src_files():
        if "random" in _imports_of(path):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, "生成/落盘路径禁用 stdlib random：" + ",".join(offenders)


def test_no_network_imports_anywhere_in_kernel():
    offenders = []
    for path in _src_files():
        hits = _imports_of(path) & FORBIDDEN_MODULES
        if hits:
            offenders.append("{0}:{1}".format(path.relative_to(ROOT), ",".join(sorted(hits))))
    assert not offenders, "全离线内核不得引用网络模块：" + "; ".join(offenders)
