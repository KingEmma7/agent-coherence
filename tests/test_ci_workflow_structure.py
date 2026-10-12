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
