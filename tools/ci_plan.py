# Copyright (c) 2026 agent-coherence contributors.
# The Coherence Protocol for AI Agents

"""The "Plan checks" job: decide what this CI run needs, and never fail.

Every other job in ci.yml lists this job in ``needs`` and reads its outputs.
The rule for those outputs is fail-open: a consumer skips work only when a
job condition sees both the right event and an explicit value, so an empty or
missing output means "run". This script therefore writes every key on every
run, writes it empty when it cannot decide, and always exits 0 — a plan job
that failed would hand its consumers to GitHub's cascade-skip rules.

Outputs, appended to ``$GITHUB_OUTPUT``:
    tested-tree   the tree id of the checked-out commit (what this run tests)

Runs on the runner's system Python with no setup step, from the repository
root: ``python3 tools/ci_plan.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys

OUTPUT_KEYS = ("tested-tree",)


def tested_tree() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], capture_output=True, text=True, timeout=30, check=True
    )
    return result.stdout.strip()


def plan() -> dict[str, str]:
    return {"tested-tree": tested_tree()}


def write_outputs(outputs: dict[str, str]) -> None:
    lines = "".join(f"{key}={outputs.get(key, '')}\n" for key in OUTPUT_KEYS)
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as out:
            out.write(lines)
    print(lines, end="")


def main() -> int:
    outputs = {key: "" for key in OUTPUT_KEYS}
    try:
        outputs.update(plan())
    # Fail open by design: any error leaves every output empty, which every
    # consumer reads as "run everything".
    except Exception as exc:  # noqa: BLE001
        print(f"plan: could not decide ({type(exc).__name__}: {exc}); every check runs", file=sys.stderr)
    write_outputs(outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
