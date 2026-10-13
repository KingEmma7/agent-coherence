# Copyright (c) 2026 agent-coherence contributors.
# The Coherence Protocol for AI Agents

"""Unit 10 — supply-chain hardening artifact tests.

Validates the presence and structural correctness of:

* ``.github/workflows/release.yml`` — Trusted Publishers OIDC + PEP 740 attestations
* ``requirements-diagnose.txt`` — hash-pinned reproducible install path
* ``docs/security.md`` — end-user trust contract (env-var kill switches,
  hash-pinned install, attestation verification, threat model, canonical
  install command, security-issue reporting)
* ``uv.lock`` — committed developer lockfile
* ``README.md`` — security & supply chain pointer subsection

The maintainer-side pre-release verification gate (``ccs-check-release`` and
its manual checklist) is documented in a local-only operations file at the
repo root and is not asserted here, because it intentionally never lands in
the public tree.

These are file-existence + content-grep + YAML-validity checks. We do not try to
validate full GitHub Actions workflow semantics (that's GitHub's job) — we only
assert the load-bearing properties this unit shipped.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
REQUIREMENTS_DIAGNOSE = REPO_ROOT / "requirements-diagnose.txt"
SECURITY_MD = REPO_ROOT / "docs" / "security.md"
UV_LOCK = REPO_ROOT / "uv.lock"
README = REPO_ROOT / "README.md"
DEPENDABOT_CONFIG = REPO_ROOT / ".github" / "dependabot.yml"

# (ecosystem, directory, the file Dependabot reads in that directory)
DEPENDABOT_MANIFESTS = frozenset(
    {
        ("uv", "/", "uv.lock"),
        ("npm", "/examples/refactor_demo/fixture_repo_ts", "package-lock.json"),
        ("github-actions", "/", ".github/workflows"),
    }
)

# The command recorded in requirements-diagnose.txt's own header.
REGENERATE_DIAGNOSE = (
    "uv export --format requirements-txt --frozen --extra diagnose "
    "--no-emit-project --no-dev > requirements-diagnose.txt"
)
_PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")
_HASH = re.compile(r"--hash=(sha256:[0-9a-f]{64})")


# ---------------------------------------------------------------------------
# release.yml
# ---------------------------------------------------------------------------


def test_release_workflow_exists() -> None:
    assert RELEASE_WORKFLOW.is_file(), "release.yml must exist"


def test_release_workflow_is_valid_yaml() -> None:
    parsed = yaml.safe_load(RELEASE_WORKFLOW.read_text())
    assert isinstance(parsed, dict)
    assert "jobs" in parsed


def test_release_workflow_triggers_on_v_tags() -> None:
    parsed = yaml.safe_load(RELEASE_WORKFLOW.read_text())
    # PyYAML parses bare "on:" as boolean True under YAML 1.1 — handle either form.
    on_block = parsed.get("on") if "on" in parsed else parsed.get(True)
    assert on_block is not None, "release.yml must declare a trigger block"
    push = on_block.get("push", {})
    tags = push.get("tags", [])
    assert any("v*" in t for t in tags), "release.yml must trigger on v* tags"


def test_release_workflow_declares_id_token_write() -> None:
    parsed = yaml.safe_load(RELEASE_WORKFLOW.read_text())
    jobs = parsed["jobs"]
    found_id_token_write = False
    for _job_name, job in jobs.items():
        permissions = job.get("permissions", {}) or {}
        if permissions.get("id-token") == "write":
            found_id_token_write = True
            break
    assert found_id_token_write, (
        "At least one job must declare permissions.id-token: write for OIDC"
    )


def test_release_workflow_uses_pypa_publish_with_attestations() -> None:
    text = RELEASE_WORKFLOW.read_text()
    # The workflow must reference pypa/gh-action-pypi-publish — either by
    # the floating ``release/v1`` tag or pinned to a commit SHA with the
    # tag preserved as a trailing comment (the supply-chain hardened form).
    floating = "pypa/gh-action-pypi-publish@release/v1"
    sha_pinned = "pypa/gh-action-pypi-publish@" in text and (
        "# release/v1" in text or "#release/v1" in text
    )
    assert floating in text or sha_pinned, (
        "release.yml must use pypa/gh-action-pypi-publish "
        "(release/v1 or SHA-pinned with a release/v1 comment)"
    )
    assert "attestations: true" in text, (
        "release.yml must enable PEP 740 attestations: attestations: true"
    )


def test_release_workflow_has_no_static_pypi_token() -> None:
    text = RELEASE_WORKFLOW.read_text()
    forbidden = ("PYPI_TOKEN", "PYPI_API_TOKEN", "password:")
    for needle in forbidden:
        assert needle not in text, (
            f"release.yml must not reference {needle!r} — Trusted Publishers OIDC only"
        )


def test_release_workflow_generates_sbom() -> None:
    text = RELEASE_WORKFLOW.read_text()
    assert "cyclonedx" in text.lower(), (
        "release.yml must generate a CycloneDX SBOM"
    )


# ---------------------------------------------------------------------------
# requirements-diagnose.txt
# ---------------------------------------------------------------------------


def test_requirements_diagnose_exists_and_nonempty() -> None:
    assert REQUIREMENTS_DIAGNOSE.is_file(), "requirements-diagnose.txt must exist"
    assert REQUIREMENTS_DIAGNOSE.stat().st_size > 0


def test_requirements_diagnose_contains_hash_pins() -> None:
    text = REQUIREMENTS_DIAGNOSE.read_text()
    assert "--hash=sha256:" in text, (
        "requirements-diagnose.txt must contain --hash=sha256: pins"
    )
    # Every non-comment, non-blank line group should have at least one hash;
    # cheap sanity check: many hash lines.
    hash_count = text.count("--hash=sha256:")
    assert hash_count >= 50, (
        f"Expected many hash pins (>=50), saw {hash_count}"
    )


@pytest.mark.parametrize("dep", ["jinja2", "langgraph", "langchain-core"])
def test_requirements_diagnose_pins_load_bearing_deps(dep: str) -> None:
    text = REQUIREMENTS_DIAGNOSE.read_text()
    # Each dep should appear with == version pin somewhere.
    assert f"\n{dep}==" in text or text.startswith(f"{dep}=="), (
        f"requirements-diagnose.txt must pin {dep}=="
    )


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _diagnose_pins() -> list[tuple[str, str, frozenset[str]]]:
    """(name, version, hashes) for each requirement in requirements-diagnose.txt."""
    joined = REQUIREMENTS_DIAGNOSE.read_text().replace("\\\n", " ")
    requirements = [
        line
        for line in joined.splitlines()
        if line.strip() and not line[0].isspace() and not line.startswith("#")
    ]
    pins = []
    for line in requirements:
        match = _PIN.match(line)
        assert match, f"requirements-diagnose.txt has an unpinned line: {line[:80]!r}"
        hashes = frozenset(_HASH.findall(line))
        pins.append((_normalized(match[1]), match[2], hashes))
    return pins


def _lock_distributions() -> dict[tuple[str, str], frozenset[str]]:
    """Each (name, version) uv.lock resolves, with every file hash it records."""
    lock = tomllib.loads(UV_LOCK.read_text())
    return {
        (_normalized(package["name"]), package["version"]): frozenset(
            dist["hash"]
            for dist in [package.get("sdist"), *package.get("wheels", [])]
            if dist and "hash" in dist
        )
        for package in lock["package"]
        if "version" in package
    }


def _diagnose_closure() -> set[str]:
    """Every package uv.lock resolves for this project's base and ``diagnose``
    dependencies, extras followed: the set ``uv export --extra diagnose
    --no-emit-project`` writes out."""
    by_name: dict[str, list[dict]] = {}
    for package in tomllib.loads(UV_LOCK.read_text())["package"]:
        by_name.setdefault(_normalized(package["name"]), []).append(package)
    project = _normalized("agent-coherence")
    pending, visited, names = [(project, ("diagnose",))], set(), set()
    while pending:
        name, extras = pending.pop()
        for extra in (None, *extras):
            if (name, extra) in visited:
                continue
            visited.add((name, extra))
            for package in by_name.get(name, []):
                if extra is None:
                    deps = package.get("dependencies", [])
                else:
                    deps = package.get("optional-dependencies", {}).get(extra, [])
                for dep in deps:
                    names.add(_normalized(dep["name"]))
                    pending.append((_normalized(dep["name"]), tuple(dep.get("extra", ()))))
    return names - {project}


def test_requirements_diagnose_matches_uv_lock() -> None:
    """The file is ``uv export`` of uv.lock, so each pin must carry the version
    and the hashes uv.lock records. A dependency update that rewrites the pins
    in place without resolving them leaves a file ``pip install
    --require-hashes`` cannot install, while CI, which installs from
    pyproject.toml, stays green."""
    pins = _diagnose_pins()
    assert pins, "requirements-diagnose.txt has no pinned requirements"
    lock = _lock_distributions()
    drifted = sorted(
        f"{name}=={version}"
        for name, version, hashes in pins
        if lock.get((name, version)) != hashes
    )
    assert not drifted, (
        f"requirements-diagnose.txt disagrees with uv.lock on {drifted}; "
        f"regenerate it from uv.lock with: {REGENERATE_DIAGNOSE}"
    )


def test_requirements_diagnose_lists_every_locked_dependency() -> None:
    """A pin can match uv.lock while the file still misses a package that the
    new version needs: ``pip install --require-hashes`` then refuses the
    unpinned dependency. The file must list exactly what uv.lock resolves."""
    pinned = {name for name, _version, _hashes in _diagnose_pins()}
    locked = _diagnose_closure()
    assert pinned == locked, (
        f"requirements-diagnose.txt is missing {sorted(locked - pinned)} and "
        f"has extra {sorted(pinned - locked)}; regenerate it from uv.lock with: "
        f"{REGENERATE_DIAGNOSE}"
    )


def test_requirements_diagnose_excludes_self() -> None:
    """The file is generated with --no-emit-project; agent-coherence itself
    should not appear as a pinned line."""
    text = REQUIREMENTS_DIAGNOSE.read_text()
    assert "agent-coherence==" not in text, (
        "requirements-diagnose.txt must not include the project itself "
        "(generated with --no-emit-project)"
    )


# ---------------------------------------------------------------------------
# .github/dependabot.yml
# ---------------------------------------------------------------------------


def _dependabot_updates() -> list[dict]:
    return yaml.safe_load(DEPENDABOT_CONFIG.read_text())["updates"]


def test_dependabot_watches_each_manifest() -> None:
    """A manifest that moves leaves Dependabot watching a directory with
    nothing in it, which it reports as nothing to update."""
    watched = {(u["package-ecosystem"], u["directory"]) for u in _dependabot_updates()}
    assert watched == {(eco, directory) for eco, directory, _ in DEPENDABOT_MANIFESTS}
    for _eco, directory, manifest in DEPENDABOT_MANIFESTS:
        path = REPO_ROOT / directory.lstrip("/") / manifest
        assert path.exists(), f"dependabot.yml watches {directory}, which has no {manifest}"


def test_dependabot_never_rewrites_pyproject_bounds() -> None:
    """The bounds in pyproject.toml are what users of this library may
    install. Python updates move the pins in uv.lock only."""
    (uv_update,) = [u for u in _dependabot_updates() if u["package-ecosystem"] == "uv"]
    assert uv_update.get("versioning-strategy") == "lockfile-only"


# ---------------------------------------------------------------------------
# SECURITY.md
# ---------------------------------------------------------------------------


def test_security_md_exists() -> None:
    assert SECURITY_MD.is_file(), "docs/security.md must exist"


@pytest.mark.parametrize(
    "kill_switch",
    ["DO_NOT_TRACK", "DISABLE_TELEMETRY", "CCS_DIAGNOSE_NO_TELEMETRY"],
)
def test_security_md_documents_kill_switches(kill_switch: str) -> None:
    text = SECURITY_MD.read_text()
    assert kill_switch in text, (
        f"docs/security.md must document {kill_switch} env-var kill switch"
    )


# The pre-release verification gate (``ccs-check-release`` and its manual
# checklist) is intentionally maintainer-only and lives in a local-only file
# at the repo root. It is not asserted here.


def test_security_md_documents_canonical_install() -> None:
    text = SECURITY_MD.read_text()
    assert "https://pypi.org/simple/" in text, (
        "docs/security.md must document the canonical pypi.org install command"
    )
    assert "agent-coherence[diagnose]" in text


def test_security_md_documents_attestation_verification() -> None:
    text = SECURITY_MD.read_text()
    assert "PEP 740" in text
    assert "pypi-attestations" in text or "gh attestation" in text


def test_security_md_documents_hash_pinned_install() -> None:
    text = SECURITY_MD.read_text()
    assert "--require-hashes" in text
    assert "requirements-diagnose.txt" in text


def test_security_md_threat_model_present() -> None:
    text = SECURITY_MD.read_text()
    assert "threat model" in text.lower()
    # Spec-required threat-model rows.
    for needle in (
        "Trusted Publishers",
        "Typosquat",
        "Dependency-confusion",
        "deepeval",
    ):
        assert needle in text, (
            f"docs/security.md threat model must mention {needle!r}"
        )


# ---------------------------------------------------------------------------
# uv.lock
# ---------------------------------------------------------------------------


def test_uv_lock_committed() -> None:
    assert UV_LOCK.is_file(), "uv.lock must be committed at repo root"
    # uv.lock for this project is multi-thousand lines.
    line_count = sum(1 for _ in UV_LOCK.open())
    assert line_count > 100, f"uv.lock looks truncated ({line_count} lines)"


# ---------------------------------------------------------------------------
# README.md security pointer
# ---------------------------------------------------------------------------


def test_readme_links_to_security_doc() -> None:
    """The README is the first artifact a visitor reads. It must surface a
    discoverable pointer to docs/security.md so the trust contract,
    kill switches, and threat model are one click away. We do not
    require a specific heading shape — the pointer may live inline in
    a TOC, a dedicated subsection, or anywhere else in the README.
    """
    text = README.read_text()
    assert "docs/security.md" in text, (
        "README.md must link to docs/security.md so end users can find "
        "the public trust contract"
    )
