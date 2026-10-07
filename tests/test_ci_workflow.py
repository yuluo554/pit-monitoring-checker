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


def test_matrix_spans_both_python_versions_and_covers_gui():
    combos = {
        (str(entry["os"]), str(entry["python-version"]), str(entry["extras"]))
        for entry in _doc()["jobs"]["test"]["strategy"]["matrix"]["include"]
    }
    versions = {c[1] for c in combos}
    assert {"3.8", "3.12"} <= versions
    assert any("gui" in c[2] for c in combos), "gui extras 未进任何矩阵作业，PySide6 分支测不到"
    assert any(c[0].startswith("windows") for c in combos)
    assert any(c[0].startswith("ubuntu") for c in combos)


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
