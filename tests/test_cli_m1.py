"""M1 命令面端到端：synth → import → ledger（plan/07 §十一）。

这一条链是"每周可演示物"的 W2 项，也是 M2 判定内核的输入面。
退出码按 0/1/2 走：全接受 0、有拒收 1（降级）、输入不可用或契约失败 2。
"""

from __future__ import annotations

import pytest
from _helpers import DATA

from pmc import cli
from pmc.errors import EXIT_DEGRADED, EXIT_INPUT_UNAVAILABLE, EXIT_OK

SEED = 20260107


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    """把 data/ 复制到 scratch 里跑，命令面绝不往仓内冻结产物写东西。"""
    import shutil

    root = tmp_path_factory.mktemp("m1cli")
    data = root / "data"
    shutil.copytree(str(DATA), str(data))
    return str(data), str(root / "ledger.sqlite")


@pytest.fixture(scope="module")
def seeded(sandbox):
    """模块级跑一次 init + synth --force + 建档；capsys 是 function 作用域，这里自己吞输出。"""
    import io
    import contextlib

    data, db = sandbox
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        assert cli.main(["--data-dir", data, "init", "--db", db]) == EXIT_OK
        code = cli.main(
            ["--data-dir", data, "synth", "--seed", str(SEED), "--sites", "3", "--force", "--db", db]
        )
    assert code == EXIT_OK, sink.getvalue()
    return data, db


def _import(data, db, name, project, round_index, extra=()):
    return cli.main(
        [
            "--data-dir", data, "import",
            "data/raw/{0}/{1}".format(project, name),
            "--project", project, "--round", str(round_index), "--db", db,
        ] + list(extra)
    )


def test_synth_check_passes_on_fresh_generation(sandbox, capsys):
    data, _ = sandbox
    capsys.readouterr()
    code = cli.main(["--data-dir", data, "synth", "--seed", str(SEED), "--sites", "3", "--check"])
    assert code == EXIT_OK
    assert "SYNTH_CHECK_OK" in capsys.readouterr().out


def test_synth_refuses_without_force(sandbox, capsys):
    data, _ = sandbox
    capsys.readouterr()
    code = cli.main(["--data-dir", data, "synth", "--seed", str(SEED), "--sites", "1"])
    assert code == EXIT_INPUT_UNAVAILABLE
    assert "--force" in capsys.readouterr().err


def test_synth_check_detects_hand_edit(sandbox, capsys):
    """篡改方式刻意选"CRLF 重写"：Excel/记事本在 Windows 上另存一次就是这样，冻结产物必须发现。"""
    import pathlib

    data, _ = sandbox
    path = pathlib.Path(data) / "raw" / "SYN-YYCG" / "round-05.csv"
    original = path.read_bytes()
    path.write_bytes(original.replace(b"\n", b"\r\n"))
    capsys.readouterr()
    code = cli.main(["--data-dir", data, "synth", "--seed", str(SEED), "--sites", "3", "--check"])
    assert code == EXIT_INPUT_UNAVAILABLE
    err = capsys.readouterr().err
    assert "round-05.csv" in err and "字节不一致" in err
    path.write_bytes(original)


def test_synth_seeded_the_ledger_archive(seeded):
    import sqlite3

    data, db = seeded
    conn = sqlite3.connect(db)
    try:
        counts = {
            "project": conn.execute("SELECT COUNT(*) FROM project").fetchone()[0],
            "point": conn.execute("SELECT COUNT(*) FROM point").fetchone()[0],
            "round": conn.execute("SELECT COUNT(*) FROM obs_round").fetchone()[0],
            "condition": conn.execute("SELECT COUNT(*) FROM working_condition").fetchone()[0],
            "thresholds": conn.execute(
                "SELECT COUNT(*) FROM point WHERE design_cum_value IS NOT NULL"
            ).fetchone()[0],
        }
    finally:
        conn.close()
    assert counts == {
        "project": 3, "point": 588, "round": 58, "condition": 9, "thresholds": 0
    }


def test_import_clean_round_exits_zero(seeded, capsys):
    data, db = seeded
    capsys.readouterr()
    code = _import(data, db, "round-01.csv", "SYN-ZHDQ", 1)
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "IMPORT_RECEIPT" in out and "拒收 0" in out


def test_import_planted_unit_error_degrades(seeded, capsys):
    data, db = seeded
    capsys.readouterr()
    code = _import(data, db, "round-12.csv", "SYN-ZHDQ", 12)
    assert code == EXIT_DEGRADED
    out = capsys.readouterr().out
    assert "REJECT row=128 reason=unit_mismatch" in out


def test_import_dry_run_writes_nothing(seeded, capsys):
    import sqlite3

    data, db = seeded
    capsys.readouterr()
    code = _import(data, db, "round-13.csv", "SYN-ZHDQ", 13, extra=["--dry-run"])
    assert code == EXIT_OK
    assert "dry-run" in capsys.readouterr().out
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT COUNT(*) FROM observation o JOIN obs_round r ON r.id=o.round_id"
            " WHERE r.round_index=13"
        ).fetchone()[0]
        batches = conn.execute("SELECT COUNT(*) FROM import_batch").fetchone()[0]
    finally:
        conn.close()
    assert rows == 0
    assert batches == 2, "dry-run 不该落库，前面两轮真导入的批次要在"


def test_ledger_shows_the_revision_chain(seeded, capsys):
    data, db = seeded
    _import(data, db, "round-09.csv", "SYN-ZHDQ", 9)
    capsys.readouterr()
    code = cli.main(
        ["--data-dir", data, "ledger", "--db", db, "--project", "SYN-ZHDQ",
         "--point", "SYN-TH-18", "--from", "9", "--to", "9"]
    )
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "已被取代" in out and "现行" in out
    assert "rev" in out.splitlines()[0]


def test_ledger_reports_missing_round(seeded, capsys):
    data, db = seeded
    _import(data, db, "round-16.csv", "SYN-ZHDQ", 16)
    capsys.readouterr()
    code = cli.main(
        ["--data-dir", data, "ledger", "--db", db, "--project", "SYN-ZHDQ",
         "--point", "SYN-PF-09", "--from", "16", "--to", "16"]
    )
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "缺测" in out and "未测" not in out, "原文列不进默认视图，避免与数值混读"


def test_ledger_of_rejected_row_is_empty(seeded, capsys):
    data, db = seeded
    _import(data, db, "round-12.csv", "SYN-ZHDQ", 12)
    capsys.readouterr()
    code = cli.main(
        ["--data-dir", data, "ledger", "--db", db, "--project", "SYN-ZHDQ",
         "--point", "SYN-TH-15", "--from", "12", "--to", "12"]
    )
    assert code == EXIT_INPUT_UNAVAILABLE
    assert "LEDGER_EMPTY" in capsys.readouterr().err


def test_missing_ledger_is_input_unavailable(sandbox, capsys):
    data, _ = sandbox
    capsys.readouterr()
    assert _import(data, str(data) + "/nope.sqlite", "round-01.csv", "SYN-ZHDQ", 1) == (
        EXIT_INPUT_UNAVAILABLE
    )
    assert cli.main(["--data-dir", data, "ledger", "--db", str(data) + "/nope.sqlite"]) == (
        EXIT_INPUT_UNAVAILABLE
    )
    capsys.readouterr()


def test_import_unknown_project_is_reported(seeded, capsys):
    data, db = seeded
    capsys.readouterr()
    code = cli.main(
        ["--data-dir", data, "import", "data/raw/SYN-ZHDQ/round-02.csv",
         "--project", "SYN-NOPE", "--round", "2", "--db", db]
    )
    assert code == EXIT_INPUT_UNAVAILABLE
    err = capsys.readouterr().err
    assert "SYN-NOPE" in err and "不在台账里" in err
    assert "UNEXPECTED" not in err, "缺档案要走 InputError，不该落到兜底分支"


def test_import_missing_file_is_reported_not_tracebacked(seeded, capsys):
    data, db = seeded
    capsys.readouterr()
    code = cli.main(
        ["--data-dir", data, "import", "data/raw/SYN-ZHDQ/round-77.csv",
         "--project", "SYN-ZHDQ", "--round", "77", "--db", db]
    )
    assert code == EXIT_INPUT_UNAVAILABLE
    err = capsys.readouterr().err
    assert "不存在" in err and "UNEXPECTED" not in err
