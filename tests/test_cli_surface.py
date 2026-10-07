"""CLI 子命令面与退出码语义锁定（定稿后重构不得漂移）。"""

from __future__ import annotations

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


@pytest.mark.parametrize(
    "command,milestone",
    [
        ("import", "M1"),
        ("check", "M2"),
        ("audit", "M3"),
        ("bench", "M4"),
        ("report", "M5"),
    ],
)
def test_unbuilt_commands_return_not_implemented(command, milestone, capsys):
    code = cli.main([command])
    err = capsys.readouterr().err
    assert code == EXIT_NOT_IMPLEMENTED
    assert milestone in err
    assert "NOT_IMPLEMENTED" in err


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
    from pmc.gui import app

    assert callable(app.main)
    assert app.main([]) == EXIT_NOT_IMPLEMENTED
