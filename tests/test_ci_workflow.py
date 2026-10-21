"""CI workflow 守门：非法 YAML 会让 GitHub 不建任何 job（状态仍显示 active，字节也洁净），
只有真解析才能暴露。同时把"取退出码不吃管道"的纪律固化成一条断言。
"""

from __future__ import annotations

import re

import yaml
from _helpers import ROOT

WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _doc():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_workflow_parses_and_has_jobs():
    doc = _doc()
    assert isinstance(doc, dict) and doc.get("jobs"), "workflow 解析失败或没有 job"


def test_every_job_declares_runs_on_and_steps():
    for job_id, job in _doc()["jobs"].items():
        assert job.get("runs-on"), "{0} 缺 runs-on".format(job_id)
        assert isinstance(job.get("steps"), list) and job["steps"], "{0} 缺 steps".format(job_id)


def test_step_names_with_colon_are_quoted():
    offenders = []
    for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\s*-\s+name:\s+[^'\"].*:\s", line):
            offenders.append(line.strip())
    assert not offenders, "含冒号空格的 step 名必须加引号，否则整份 workflow 非法：" + str(offenders)


def test_matrix_matches_the_declared_python_floor_and_covers_gui():
    """矩阵 = 声明面：floor 抬到 3.12 后，矩阵里不许再有 3.8 腿（本轮 DEC-1/DEC-2=A）。

    断言从「3.8 与 3.12 都在」翻成「只剩 3.12」—— 同一个意图（矩阵与声明同一集合），
    只是集合随 DEC-1 换了；gui 与双 OS 的覆盖要求原样保留。
    """
    combos = {
        (str(entry["os"]), str(entry["python-version"]), str(entry["extras"]))
        for entry in _doc()["jobs"]["test"]["strategy"]["matrix"]["include"]
    }
    versions = {c[1] for c in combos}
    assert versions == {"3.12"}, "矩阵版本集合必须与 requires-python 的 floor 一致：{0}".format(versions)
    assert any("gui" in c[2] for c in combos), "gui extras 未进任何矩阵作业，PySide6 分支测不到"
    assert any(c[0].startswith("windows") for c in combos)
    assert any(c[0].startswith("ubuntu") for c in combos)


def test_matrix_does_not_pin_an_eol_runner():
    """`ubuntu-22.04` 是随 3.8 一起进来的钉版本；3.8 腿删掉后它没有存在理由。"""
    for entry in _doc()["jobs"]["test"]["strategy"]["matrix"]["include"]:
        assert str(entry["os"]) in ("ubuntu-latest", "windows-latest"), entry


def test_actions_are_on_current_majors():
    """Node20 弃用注解的根因是 action 大版本过旧；钉住下限，别让它们漂回去。"""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/checkout@v4" not in text
    assert "actions/setup-python@v5" not in text
    assert "actions/checkout@v7" in text
    assert "actions/setup-python@v7" in text


def test_history_desensitization_keeps_full_history():
    """`--mode history` 需要全历史 blob；删掉 fetch-depth: 0 会让它静默 0 命中（空验）。"""
    steps = _doc()["jobs"]["test"]["steps"]
    checkout = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")]
    assert checkout and checkout[0]["with"]["fetch-depth"] == 0


def test_run_commands_do_not_swallow_exit_codes():
    offenders = []
    for job in _doc()["jobs"].values():
        for step in job["steps"]:
            run = step.get("run")
            if not run:
                continue
            if "| tail" in run or "| head" in run or "PIPESTATUS" in run:
                offenders.append((step.get("name", "?"), run.strip()[:60]))
    assert not offenders, "管道会吃掉退出码，关键命令必须单独跑：" + str(offenders)


def test_installs_extras_and_upgrades_pip_first():
    runs = " || ".join(
        step.get("run", "")
        for job in _doc()["jobs"].values()
        for step in job["steps"]
    )
    assert "pip install -U pip" in runs, "老 pip 无法可编辑安装 pyproject-only 项目"
    assert 'pip install -e ".[' in runs
    assert "-m pytest -rs" in runs, "跳过项必须可见，否则静默少跑无从发现"
