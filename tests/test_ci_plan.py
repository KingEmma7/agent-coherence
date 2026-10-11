# Copyright (c) 2026 agent-coherence contributors.
# The Coherence Protocol for AI Agents

"""The "Plan checks" job's decisions, run the way CI runs them.

Every other job reads the plan job's outputs, and every skip they allow needs
an explicit value. So the script must write every output key on every run and
never exit non-zero: a missing or empty value makes consumers run everything,
which is the safe direction, while a plan job that failed would leave its
consumers to GitHub's cascade rules instead.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "ci_plan.py"
OUTPUT_KEYS = frozenset({"tested-tree", "tla-needed"})
BASE_FILES = (
    "formal/tla/OCC.tla",
    "formal/tla/OCC_CI.cfg",
    "Makefile",
    ".github/workflows/ci.yml",
    "src/ccs/x.py",
    "tests/test_x.py",
    "docs/guide.md",
)


def _run_plan(
    tmp_path: Path, env_overrides: dict[str, str] | None = None, cwd: Path = REPO_ROOT
) -> tuple[int, dict[str, str]]:
    output = tmp_path / "github_output"
    output.write_text("")
    env = {**os.environ, "GITHUB_OUTPUT": str(output), **(env_overrides or {})}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=cwd, env=env, capture_output=True, text=True, timeout=60, check=False
    )
    values = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
    return proc.returncode, values


def test_the_tested_tree_is_the_checked_out_tree(tmp_path: Path) -> None:
    """The tree id names exactly what this run tests, whatever commit or merge
    carried it; a later push can recognise an identical tree by it."""
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    exit_code, values = _run_plan(tmp_path)
    assert exit_code == 0
    assert set(values) == OUTPUT_KEYS
    assert values["tested-tree"] == expected


def test_an_error_still_writes_every_key_and_exits_zero(tmp_path: Path) -> None:
    """With ``git`` unreachable the plan cannot decide anything, so it writes
    every key with its run-everything value instead of failing: no tested
    tree (nothing can match it) and TLA+ needed."""
    exit_code, values = _run_plan(
        tmp_path, {"PATH": str(tmp_path / "no-git-here"), "GITHUB_EVENT_NAME": "pull_request"}
    )
    assert exit_code == 0
    assert values == {"tested-tree": "", "tla-needed": "true"}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _repo_with_change(tmp_path: Path, changed: tuple[str, ...]) -> Path:
    """A repo whose HEAD changes ``changed`` on top of a base commit, as the
    first parent of a pull request's merge commit is the base."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    for name in BASE_FILES:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    for name in changed:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("changed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change", "--allow-empty")
    return repo


@pytest.mark.parametrize(
    ("changed", "needed"),
    [
        pytest.param(("src/ccs/x.py", "tests/test_x.py"), "false", id="code-and-tests-only"),
        pytest.param(("docs/guide.md",), "false", id="docs-only"),
        pytest.param(("formal/tla/OCC_CI.cfg",), "true", id="a-model-config"),
        pytest.param(("formal/tla/NewSpec.tla",), "true", id="a-new-spec"),
        pytest.param(("Makefile",), "true", id="the-makefile"),
        pytest.param((".github/workflows/ci.yml",), "true", id="the-workflow"),
        pytest.param((".github/actions/setup/action.yml",), "true", id="a-composite-action"),
        pytest.param((), "true", id="an-empty-diff"),
    ],
)
def test_a_pull_request_needs_tla_only_when_a_tla_input_changed(tmp_path: Path, changed, needed) -> None:
    """The bins may skip only on a pull request that provably left every TLA+
    input alone: the specs and configs (specs extend each other, so the whole
    directory), the Makefile that runs them, and anything under .github. An
    empty diff proves nothing, so it runs them."""
    repo = _repo_with_change(tmp_path, changed)
    exit_code, values = _run_plan(tmp_path, {"GITHUB_EVENT_NAME": "pull_request"}, cwd=repo)
    assert exit_code == 0
    assert values["tla-needed"] == needed


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", "schedule", ""])
def test_every_other_event_needs_tla_whatever_the_diff(tmp_path: Path, event) -> None:
    """Only a pull request can skip the model check; a push to main or dev, a
    tag, a dispatch and the weekly run always run it."""
    repo = _repo_with_change(tmp_path, ("src/ccs/x.py",))
    exit_code, values = _run_plan(tmp_path, {"GITHUB_EVENT_NAME": event}, cwd=repo)
    assert exit_code == 0
    assert values["tla-needed"] == "true"
