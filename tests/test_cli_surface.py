"""CLI 子命令面与退出码语义锁定（定稿后重构不得漂移）。"""

from __future__ import annotations

import importlib.util

import pytest

from pmc import cli
from pmc.errors import (
    EXIT_INPUT_UNAVAILABLE,
    EXIT_NOT_IMPLEMENTED,
    EXIT_OK,
)


def test_commands_all_registered():
    parser = cli.build_parser()
    choices = set()
    for action in parser._actions:  # noqa: SLF001 - argparse 子命令面自省
        if isinstance(action, argparse_subparsers_type()):
            choices = set(action.choices)
    for command in cli.COMMAND_MILESTONE:
        assert command in choices, "{0} 未在 CLI 注册".format(command)
    for implemented in ("selfcheck", "init", "dict", "rulesets"):
        assert implemented in choices


def argparse_subparsers_type():
    import argparse

    return argparse._SubParsersAction


def test_selfcheck_exits_zero(capsys):
    code = cli.main(["selfcheck"])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "SELF_CHECK_OK" in out
    assert "规则" in out


def test_selfcheck_json_carries_exit_code():
    code = cli.main(["selfcheck", "--json"])
    assert code == EXIT_OK


def test_no_command_is_left_as_placeholder():
    """M0 留的占位分支只剩 `NOT_IMPLEMENTED` 兜底：命令面上每条都要有 handler。"""
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse_subparsers_type()))
    registered = set(sub.choices)
    assert set(cli.COMMAND_MILESTONE) <= registered
    for name in cli.COMMAND_MILESTONE:
        handler = "_cmd_{0}".format(name)
        assert hasattr(cli, handler), "{0} 仍无实现（占位分支不得留在命令面上）".format(name)


def test_m5_report_is_live_not_placeholder(tmp_path, capsys):
    """`report` 必须有真实参数面并真的出 xlsx：退回占位文本或缺 --kind/--db 就是假交付。"""
    assert hasattr(cli, "_cmd_report")
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse_subparsers_type()))
    flags = {a.dest for a in sub.choices["report"]._actions}  # noqa: SLF001
    assert {"kind", "db", "project", "round_index", "round_from", "round_to", "out", "bench_plane"} <= flags
    db = str(tmp_path / "empty.sqlite")
    assert cli.main(["init", "--db", db]) == EXIT_OK
    capsys.readouterr()
    code = cli.main(["report", "--kind", "daily", "--db", db, "--project", "SYN-NOPE"])
    err = capsys.readouterr().err
    assert code == EXIT_INPUT_UNAVAILABLE
    assert "NOT_IMPLEMENTED" not in err and "不在台账里" in err


def test_m5_gui_is_live_and_degrades_without_db(capsys):
    assert hasattr(cli, "_cmd_gui")
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse_subparsers_type()))
    flags = {a.dest for a in sub.choices["gui"]._actions}  # noqa: SLF001
    assert {"db", "project", "smoke"} <= flags
    capsys.readouterr()
    assert cli.main(["gui"]) == EXIT_INPUT_UNAVAILABLE
    err = capsys.readouterr().err
    if importlib.util.find_spec("PySide6") is not None:
        assert "GUI_INPUT" in err
    else:
        # C28 定的降级顺序：PySide6 不在场时先报安装提示，`gui` 的缺输入分支根本走不到。
        # 只断言"没装就红"会把 CI 的三个非 gui 矩阵全打挂 —— 那是断言写漏了前置条件，不是产品退化。
        assert "PySide6" in err


def test_m4_bench_is_live_not_placeholder(capsys):
    """`bench` 必须有真实参数面并真的跑评测：退回占位文本或空表就是假交付。"""
    assert hasattr(cli, "_cmd_bench")
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse_subparsers_type()))
    flags = {a.dest for a in sub.choices["bench"]._actions}  # noqa: SLF001
    assert {"plane", "sites", "all", "seed", "json", "markdown", "write_golden"} <= flags
    code = cli.main(["bench", "--sites", "SYN-NOPE"])
    err = capsys.readouterr().err
    assert code == EXIT_INPUT_UNAVAILABLE
    assert "NOT_IMPLEMENTED" not in err and "不在虚拟基坑登记顺序里" in err


def test_m2_check_is_live_not_placeholder(tmp_path, capsys):
    """`check` 必须有真实参数面并真的跑判定：退回占位文本或缺 --db/--project 就是假交付。"""
    assert hasattr(cli, "_cmd_check")
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse_subparsers_type()))
    check_parser = sub.choices["check"]
    flags = {a.dest for a in check_parser._actions}
    assert {"db", "project", "round_index", "rules_dir", "dry_run"} <= flags
    db = str(tmp_path / "empty.sqlite")
    assert cli.main(["init", "--db", db]) == EXIT_OK
    capsys.readouterr()
    assert cli.main(["check", "--db", db, "--project", "SYN-NOPE"]) == EXIT_INPUT_UNAVAILABLE
    err = capsys.readouterr().err
    assert "NOT_IMPLEMENTED" not in err and "不在台账里" in err


def test_m3_audit_is_live_not_placeholder(tmp_path, capsys):
    """`audit` 必须有真实参数面并真的检核：退回占位文本或缺 --db 就是假交付。"""
    assert hasattr(cli, "_cmd_audit")
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse_subparsers_type()))
    flags = {a.dest for a in sub.choices["audit"]._actions}  # noqa: SLF001
    assert {"db", "project", "round_from", "round_to", "rules_dir"} <= flags
    db = str(tmp_path / "empty.sqlite")
    assert cli.main(["init", "--db", db]) == EXIT_OK
    capsys.readouterr()
    assert cli.main(["audit", "--db", db]) == EXIT_INPUT_UNAVAILABLE
    err = capsys.readouterr().err
    assert "NOT_IMPLEMENTED" not in err and "轮次档案" in err


def test_m1_commands_are_live_not_placeholder(capsys):
    """M1 的三条命令必须真的可执行：`synth --check` 出 3 或退回占位文本就是假交付。"""
    code = cli.main(["synth", "--check"])
    out = capsys.readouterr().out
    assert code == EXIT_OK, "synth --check 退出码 {0}".format(code)
    assert "SYNTH_CHECK_OK" in out
    for command in ("import", "ledger"):
        assert command in cli.COMMAND_MILESTONE
        assert hasattr(cli, "_cmd_{0}".format(command))


def test_bad_argument_returns_two():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--nope"])
    assert exc.value.code == EXIT_INPUT_UNAVAILABLE


def test_init_builds_full_ledger(tmp_path):
    db = tmp_path / "ledger.sqlite"
    code = cli.main(["init", "--db", str(db)])
    assert code == EXIT_OK

    import sqlite3

    from pmc.db.schema import TABLE_NAMES

    conn = sqlite3.connect(str(db))
    try:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()
    assert set(TABLE_NAMES) <= names


def test_gui_entry_importable_without_pyside6():
    """界面入口不得在模块顶层依赖 Qt：缺 PySide6 时 `pmc gui` 也要能给出带安装提示的降级。"""
    from pmc.gui import app

    assert callable(app.main)
    # 没给 --db 一律拒绝启动（既不是"未实现"，也不是静默起窗口）
    assert app.main([]) == EXIT_INPUT_UNAVAILABLE
    assert app.main(["--db", "no-such-ledger.sqlite"]) == EXIT_INPUT_UNAVAILABLE
