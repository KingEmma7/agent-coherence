# Copyright (c) 2026 agent-coherence contributors.
# The Coherence Protocol for AI Agents

"""The shape of ``.github/workflows/ci.yml``, read rather than trusted.

A workflow mistake does not fail a test: it fails silently on GitHub. An
invalid workflow reports no checks at all, so every required check on ``dev``
waits forever; a job with no timeout holds one of the org's shared job slots
for six hours when a worker hangs; an unpinned runner image changes the
toolchain between runs of the same commit. These tests load the workflow with ``yaml.safe_load`` and assert the
properties the CI layout depends on.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

RUNNER_IMAGE = "ubuntu-24.04"
MAX_JOB_TIMEOUT_MINUTES = 60
RESOLVE_STEP = "Resolve plugin ref"
PLUGIN_REPOSITORY = "Cohexa-ai/agent-coherence-plugin"
PLUGIN_LOCKFILE = "_plugin_checkout/package-lock.json"
PINNED_ACTION = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
NEEDS_REFERENCE = re.compile(r"\bneeds\.(?P<job>[A-Za-z0-9_-]+)\.(?:outputs\.(?P<output>[A-Za-z0-9_-]+)|result)\b")


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(CI_WORKFLOW.read_text())


def _jobs() -> dict[str, dict[str, Any]]:
    return _workflow()["jobs"]


def _strings(node: Any) -> Iterator[str]:
    """Every string anywhere under ``node``: conditions, env values, run bodies."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def _all_steps() -> Iterator[tuple[str, dict[str, Any]]]:
    for job_name, job in _jobs().items():
        for step in job.get("steps") or []:
            yield job_name, step


def _corpus_steps() -> list[dict[str, Any]]:
    return _jobs()["protocol-corpus"]["steps"]


def _index_of(steps: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> int:
    matches = [i for i, step in enumerate(steps) if predicate(step)]
    assert len(matches) == 1, f"expected exactly one matching step, found {len(matches)}"
    return matches[0]


def _is_plugin_checkout(step: dict[str, Any]) -> bool:
    return (
        str(step.get("uses", "")).startswith("actions/checkout@")
        and (step.get("with") or {}).get("repository") == PLUGIN_REPOSITORY
    )


def _is_setup_node(step: dict[str, Any]) -> bool:
    return str(step.get("uses", "")).startswith("actions/setup-node@")


def test_every_job_pins_the_runner_image_and_bounds_its_time() -> None:
    """A moving ``ubuntu-latest`` or a job with the default 6-hour timeout fails here."""
    for name, job in _jobs().items():
        assert job.get("runs-on") == RUNNER_IMAGE, f"{name} runs on {job.get('runs-on')!r}"
        timeout = job.get("timeout-minutes")
        assert isinstance(timeout, int) and 0 < timeout <= MAX_JOB_TIMEOUT_MINUTES, (
            f"{name} timeout-minutes is {timeout!r}"
        )


def test_only_pull_request_runs_cancel_each_other() -> None:
    """Runs group per PR and cancel there; every other event keys on its own run id.

    Pinned as literals: a group keyed on the commit would let a ``main`` push,
    a tag push and a dispatch on one commit cancel each other's pending runs.
    """
    concurrency = _workflow()["concurrency"]
    assert concurrency == {
        "group": (
            "${{ github.workflow }}-${{ github.event_name == 'pull_request'"
            " && format('pr-{0}', github.event.pull_request.number)"
            " || format('run-{0}', github.run_id) }}"
        ),
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }


def test_corpus_sets_up_node_22_after_the_plugin_checkout_with_an_npm_cache() -> None:
    """The npm cache keys on the plugin's lockfile, which exists only after its checkout."""
    steps = _corpus_steps()
    checkout = _index_of(steps, _is_plugin_checkout)
    setup_node = _index_of(steps, _is_setup_node)
    build = _index_of(steps, lambda step: step.get("name") == "Build Node coordinator")
    assert checkout < setup_node < build
    assert steps[setup_node]["with"] == {
        "node-version": "22",
        "cache": "npm",
        "cache-dependency-path": PLUGIN_LOCKFILE,
    }


def test_resolve_plugin_ref_keeps_its_contract() -> None:
    """The step that ``tests/test_parity_gate_ref_resolution.py`` executes stays where it is.

    Its name, id and environment are how that test finds and runs it, and the
    plugin checkout reads its output.
    """
    jobs_with_step = [job_name for job_name, step in _all_steps() if step.get("name") == RESOLVE_STEP]
    assert jobs_with_step == ["protocol-corpus"]

    steps = _corpus_steps()
    resolve = _index_of(steps, lambda candidate: candidate.get("name") == RESOLVE_STEP)
    step = steps[resolve]
    assert step["id"] == "plugin_ref"
    assert step["env"] == {
        "SIBLING_REF": "${{ github.head_ref || github.ref_name }}",
        "BASE_REF": "${{ github.base_ref }}",
        "EVENT_NAME": "${{ github.event_name }}",
        "REF_NAME": "${{ github.ref_name }}",
    }

    checkout = _index_of(steps, _is_plugin_checkout)
    assert resolve < checkout
    assert steps[checkout]["with"]["ref"] == "${{ steps.plugin_ref.outputs.ref }}"
    corpus_run = _index_of(steps, lambda candidate: candidate.get("name") == "Run protocol corpus")
    assert steps[corpus_run]["env"] == {
        "AGENT_COHERENCE_PLUGIN_DIST_PATH": ("${{ github.workspace }}/_plugin_checkout/dist/coordinator.js"),
    }


def test_every_action_is_pinned_to_a_commit_sha() -> None:
    """A tag can be moved to other code; a full commit SHA cannot."""
    for job_name, step in _all_steps():
        if "uses" in step:
            assert PINNED_ACTION.match(step["uses"]), f"{job_name}: {step['uses']!r}"


def test_every_needs_entry_names_an_existing_job() -> None:
    """A ``needs`` on a missing job makes the whole workflow invalid: no checks report."""
    jobs = _jobs()
    for name, job in jobs.items():
        for needed in _needs(job):
            assert needed in jobs, f"{name} needs unknown job {needed!r}"


def test_every_needs_reference_is_declared() -> None:
    """``needs.<job>.outputs.<key>`` resolves only for a listed job and a declared output.

    GitHub exposes a job's outputs only to jobs that list it in ``needs``; an
    unlisted job reads as empty, which silently flips any condition built on it.
    """
    jobs = _jobs()
    for name, job in jobs.items():
        for text in _strings(job):
            for match in NEEDS_REFERENCE.finditer(text):
                needed, output = match["job"], match["output"]
                assert needed in _needs(job), f"{name} reads needs.{needed} without needing it"
                if output is not None:
                    declared = jobs[needed].get("outputs") or {}
                    assert output in declared, f"{name} reads undeclared {needed}.outputs.{output}"


# The names `dev`'s branch protection requires. Frozen on purpose (tests/CLAUDE.md):
# a job renamed in ci.yml leaves its required check pending forever, so the
# rename has to be a deliberate edit here too.
REQUIRED_CHECKS = frozenset(
    {
        "Lint",
        "Architecture Checks",
        "Tests (Python 3.11)",
        "Tests (Python 3.12)",
        "Benchmark Smoke",
        "Benchmark Regression",
        "TLA+ Model Check",
        "Protocol Corpus (Python ↔ Node parity)",
        "Build Package",
    }
)
REQUIRED_CHECK_COUNT = 9
TEST_GATES = {"tests-py311": "test-shards-py311", "tests-py312": "test-shards-py312"}
OTHER_WORKFLOWS = ("publish-mcp.yml", "release.yml")


def _job_names() -> dict[str, str]:
    return {job_id: job.get("name", job_id) for job_id, job in _jobs().items()}


def test_every_required_check_is_one_non_matrix_job() -> None:
    """A required name on a matrix job that GitHub skips as a whole renders
    unexpanded and never reports; a required name on two jobs is ambiguous."""
    assert len(REQUIRED_CHECKS) == REQUIRED_CHECK_COUNT
    names = list(_job_names().values())
    for check in REQUIRED_CHECKS:
        assert names.count(check) == 1, f"{check!r} names {names.count(check)} jobs"
    jobs = _jobs()
    for job_id, name in _job_names().items():
        if name in REQUIRED_CHECKS:
            assert "strategy" not in jobs[job_id], f"required check {name!r} is a matrix job"
    for workflow in OTHER_WORKFLOWS:
        other = yaml.safe_load((CI_WORKFLOW.parent / workflow).read_text())
        other_names = {job.get("name", job_id) for job_id, job in other["jobs"].items()}
        assert not other_names & REQUIRED_CHECKS, f"{workflow} reuses a required name"


def test_the_corpus_needs_nothing_and_build_and_smoke_need_only_the_plan() -> None:
    """A needed job that is skipped or failed cascades; the corpus must run
    whatever else happens, and Build Package no longer waits on the long jobs."""
    jobs = _jobs()
    assert "needs" not in jobs["protocol-corpus"]
    assert _needs(jobs["build"]) == ["plan"]
    assert _needs(jobs["benchmark-smoke"]) == ["plan"]


def test_plan_consumers_run_unless_cancelled() -> None:
    """A plan job that died must not cascade-skip its consumers: a skipped
    required check would read as passing."""
    for job_id, job in _jobs().items():
        if "plan" in _needs(job):
            assert job.get("if") == "${{ !cancelled() }}", f"{job_id} would cascade-skip on a dead plan job"


def test_the_test_gates_always_run_and_judge_their_shards() -> None:
    """A gate skipped or cancelled with its run would leave the required check
    in a state GitHub might count as passing; under always() it reports, and
    its verdict reads its own shards' result."""
    jobs = _jobs()
    for gate, shards in TEST_GATES.items():
        job = jobs[gate]
        assert job["if"] == "${{ always() }}"
        assert _needs(job) == [shards]
        run_text = "\n".join(_strings(job))
        assert f"needs.{shards}.result" in run_text
        assert '--shards "$TEST_SHARD_COUNT"' in run_text


def _shard_steps(job_id: str) -> list[dict[str, Any]]:
    return _jobs()[job_id]["steps"]


def test_both_python_legs_run_identical_shards() -> None:
    """The legs must differ only in the Python version; a step that exists in
    one leg would silently check less in the other."""
    py311 = yaml.safe_dump(_jobs()["test-shards-py311"]).replace("3.11", "X")
    py312 = yaml.safe_dump(_jobs()["test-shards-py312"]).replace("3.12", "X")
    assert py311 == py312


def test_the_shard_matrix_matches_the_shard_count() -> None:
    """pytest-split's --splits, the matrix and the gate's --shards must agree,
    or a group would never run and the coverage check would see too few shards."""
    count = int(_workflow()["env"]["TEST_SHARD_COUNT"])
    for job_id in TEST_GATES.values():
        assert _jobs()[job_id]["strategy"]["matrix"]["shard"] == list(range(1, count + 1))


def _step(job_id: str, name: str) -> dict[str, Any]:
    steps = [step for step in _shard_steps(job_id) if step.get("name") == name]
    assert len(steps) == 1, f"{job_id} has {len(steps)} {name!r} steps"
    return steps[0]


def test_the_lanes_run_as_tests_conftest_expects() -> None:
    """The lane is a command-line option, never `-m` (which would replace the
    addopts exclusions); the serial lane runs without workers and still runs
    after a parallel-lane failure; the node-id directory is one argument, or
    pytest takes the existing directory for a test path; the upload survives
    a failed or cancelled shard so the gate can see what happened."""
    for job_id in TEST_GATES.values():
        parallel = _step(job_id, "Run the parallel lane")
        serial = _step(job_id, "Run the serial lane")
        upload = _step(job_id, "Upload node-id lists and test reports")
        assert "-n 4 --dist worksteal --test-lane parallel" in parallel["run"]
        assert "-n 0 --test-lane serial" in serial["run"]
        assert serial["if"] == "${{ !cancelled() }}"
        assert upload["if"] == "${{ always() }}"
        for step in (parallel, serial):
            assert " -m " not in f" {step['run']} "
            assert '"--node-id-dir=lists/shard-$SHARD"' in step["run"]
            assert "--durations-path tests/.test_durations" in step["run"]
