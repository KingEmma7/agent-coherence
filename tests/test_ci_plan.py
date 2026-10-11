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

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "ci_plan.py"
OUTPUT_KEYS = frozenset({"tested-tree"})


def _run_plan(tmp_path: Path, env_overrides: dict[str, str] | None = None) -> tuple[int, dict[str, str]]:
    output = tmp_path / "github_output"
    output.touch()
    env = {**os.environ, "GITHUB_OUTPUT": str(output), **(env_overrides or {})}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60, check=False
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
    every key empty (consumers then run everything) instead of failing."""
    exit_code, values = _run_plan(tmp_path, {"PATH": str(tmp_path / "no-git-here")})
    assert exit_code == 0
    assert set(values) == OUTPUT_KEYS
    assert values == {key: "" for key in OUTPUT_KEYS}
