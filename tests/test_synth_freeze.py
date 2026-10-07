"""字节冻结与生成路径的确定性纪律（plan/07 §九、§十）。

这一条门只在"开发机改生成器却忘了重生成"时暴露，所以必须常驻：
基准要跨机器逐字节复现，产物与代码不一致就等于评测跑的是另一份数据。
"""

from __future__ import annotations

import ast
import hashlib
import os
import shutil

import pytest
from _helpers import DATA, ROOT, SRC

from pmc.synth import freeze
from pmc.synth.profile import PROFILE_ID

SEED = 20260107


@pytest.fixture(scope="module")
def blobs():
    return freeze.build_blobs(str(DATA), SEED, 3)


def test_repo_artifacts_match_regeneration(blobs):
    """仓内冻结产物 = 重生成结果，逐字节一致（DoD：`synth --check` 必须过）。"""
    ok, problems = freeze.check_blobs(str(DATA), blobs)
    assert ok, "位级对账失败：" + " | ".join(problems[:8])


def test_expected_file_set(blobs):
    rounds = {"SYN-LJ3": 8, "SYN-ZHDQ": 20, "SYN-YYCG": 30}
    expect = {"raw/manifest.json"}
    for code, count in rounds.items():
        expect.update("raw/{0}/round-{1:02d}.csv".format(code, r) for r in range(1, count + 1))
        expect.add("truth/{0}.truth.csv".format(code))
    assert set(blobs) == expect


def test_every_round_file_has_one_row_per_point(blobs):
    for path, blob in blobs.items():
        if not path.startswith("raw/") or not path.endswith(".csv"):
            continue
        lines = blob.decode("utf-8").splitlines()
        expected = 196 + (1 if "SYN-ZHDQ/round-09.csv" in path else 0)
        assert len(lines) == expected + 1, "{0} 行数 {1}".format(path, len(lines))
        assert lines[0] == ",".join(freeze.CSV_HEADER)


def test_artifacts_are_lf_utf8_without_bom(blobs):
    offenders = []
    for path, blob in blobs.items():
        if b"\r" in blob:
            offenders.append(path + ":CR")
        if blob.startswith(b"\xef\xbb\xbf"):
            offenders.append(path + ":BOM")
        if not blob.endswith(b"\n") or blob.endswith(b"\n\n"):
            offenders.append(path + ":尾部换行")
        try:
            blob.decode("utf-8")
        except UnicodeDecodeError:
            offenders.append(path + ":编码")
    assert not offenders, "冻结产物格式不合格：" + ",".join(offenders)


def test_building_twice_is_identical(blobs):
    again = freeze.build_blobs(str(DATA), SEED, 3)
    assert again == blobs


def test_different_seed_changes_series_but_keeps_shape(blobs):
    other = freeze.build_blobs(str(DATA), SEED + 1, 3)
    assert set(other) == set(blobs)
    assert other["raw/SYN-ZHDQ/round-03.csv"] != blobs["raw/SYN-ZHDQ/round-03.csv"]
    assert other["raw/manifest.json"].count(b"sha256") == blobs["raw/manifest.json"].count(b"sha256")


def test_manifest_declares_identity_not_thresholds(blobs):
    import json

    doc = json.loads(blobs["raw/manifest.json"].decode("utf-8"))
    assert doc["schema"] == "pmc-synth-manifest/1"
    assert doc["seed"] == SEED
    assert doc["profile_id"] == PROFILE_ID
    assert doc["notice"]
    assert doc["reproduce"].startswith("python -X utf8 -m pmc synth --seed")
    text = blobs["raw/manifest.json"].decode("utf-8")
    for site in doc["sites"]:
        assert set(["code", "rounds", "points", "observations", "events", "files"]) <= set(site)
    # 档位数值（含速率/累计控制值）一律不得进数据面
    for code, thr in _profile_numbers().items():
        for value in thr:
            assert "{0}".format(value) not in text, "{0} 的档位数值 {1} 泄漏进 manifest".format(code, value)


def _profile_numbers():
    from pmc.synth.profile import SYNTH_PROFILE

    return {code: (thr["cum"], thr["rate"]) for code, thr in SYNTH_PROFILE.items()}


def test_truth_files_have_seven_columns(blobs):
    for path, blob in blobs.items():
        if not path.startswith("truth/"):
            continue
        lines = blob.decode("utf-8").splitlines()
        assert lines[0] == ",".join(freeze.TRUTH_HEADER)
        assert len(lines) - 1 == len(blob.decode("utf-8").splitlines()) - 1
        for line in lines[1:]:
            assert len(line.split(",")) == 7


def test_check_detects_hand_edited_artifact(tmp_path):
    target = tmp_path / "data"
    shutil.copytree(str(DATA), str(target))
    path = target / "raw" / "SYN-ZHDQ" / "round-03.csv"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(",3.4,", ",3.5,"), encoding="utf-8")
    fresh = freeze.build_blobs(str(DATA), SEED, 3)
    ok, problems = freeze.check_blobs(str(target), fresh)
    assert not ok
    assert any("round-03.csv" in problem for problem in problems)


def test_check_detects_extra_and_missing_files(tmp_path):
    target = tmp_path / "data"
    shutil.copytree(str(DATA), str(target))
    (target / "raw" / "SYN-LJ3" / "round-09.csv").write_text("x\n", encoding="utf-8")
    os.remove(str(target / "raw" / "SYN-LJ3" / "round-02.csv"))
    ok, problems = freeze.check_blobs(str(target), freeze.build_blobs(str(DATA), SEED, 3))
    assert not ok
    joined = " ".join(problems)
    assert "多出文件" in joined and "缺文件" in joined


def test_write_refuses_without_force(tmp_path):
    from pmc.errors import SynthError

    target = tmp_path / "data"
    shutil.copytree(str(DATA), str(target))
    blobs = freeze.build_blobs(str(DATA), SEED, 1)
    with pytest.raises(SynthError):
        freeze.write_blobs(str(target), blobs, force=False)
    # 原产物不得被部分覆盖：拒绝之后仍然逐字节一致
    assert (target / "raw" / "SYN-LJ3" / "round-01.csv").read_bytes() == blobs[
        "raw/SYN-LJ3/round-01.csv"
    ]


FORBIDDEN_CLOCK_CALLS = {"now", "today", "time", "utcnow", "localtime", "monotonic"}


def _synth_files():
    return sorted(p for p in (SRC / "pmc" / "synth").rglob("*.py"))


def test_generator_never_reads_the_clock():
    """落盘产物带时间戳就不能逐字节复现：这一条禁的是行为，不是注释。"""
    offenders = []
    for path in _synth_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_CLOCK_CALLS:
                offenders.append("{0}:{1}".format(path.name, node.attr))
            if isinstance(node, ast.Name) and node.id in ("time",):
                offenders.append("{0}:time".format(path.name))
    assert not offenders, "生成路径读了时钟：" + ",".join(offenders)


LAYER_NAMES = (
    "contract", "db", "catalog", "rules", "ingest", "alarm",
    "compliance", "report", "bench", "gui",
)

#: 装配点例外：CLI 与 `bench` 都不算阈值，只把合成自证档位挂进内存台账当标尺（plan/10 §一 P08）。
#: 例外面由 `test_m4_discipline.py` 钉死：除这两处外任何模块 import pmc.synth 即红，
#: 且 bench 只准 import profile/sites/freeze 三个模块，档位数值永不进 data/。
SYNTH_ASSEMBLY_POINTS = ("cli.py", "selfcheck.py", "paths.py", "errors.py", "__main__.py",
                         "__init__.py")


def test_synthetic_grades_never_reach_the_judgment_path():
    """合成档位只准生成器与装配点用：判定/规则/导入/台账层 import pmc.synth 就切断"夹具冒充规范值"。"""
    offenders = []
    for path in sorted((SRC / "pmc").rglob("*.py")):
        rel = path.relative_to(SRC / "pmc")
        parts = rel.parts
        if len(parts) < 2 or parts[0] == "synth":
            continue
        if parts[0] == "bench":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                if name.startswith("pmc.synth"):
                    offenders.append(str(rel).replace("\\", "/"))
    assert not offenders, "非生成层引用了合成档位：" + ",".join(sorted(set(offenders)))


def test_assembly_point_list_is_the_only_escape_hatch():
    """例外名单本身也要守：漏进 `SYNTH_ASSEMBLY_POINTS` 的层必须还是被上面那条扫到。"""
    assert set(SYNTH_ASSEMBLY_POINTS) & {"alarm.py", "auditor.py", "loader.py"} == set()
    assert "bench" in LAYER_NAMES, "bench 层不在分层名单里，扫描会静默跳过它"


def test_manifest_sha256_matches_files_on_disk(blobs):
    import json

    doc = json.loads(blobs["raw/manifest.json"].decode("utf-8"))
    for site in doc["sites"]:
        for entry in site["files"]:
            path = os.path.join(str(DATA), *entry["path"].split("/"))
            with open(path, "rb") as handle:
                blob = handle.read()
            assert hashlib.sha256(blob).hexdigest() == entry["sha256"], entry["path"]
            assert entry["bytes"] == len(blob)
