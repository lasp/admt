# admt CI Plan

This document specifies how admt's continuous-integration pipeline implements the testing strategy from [TEST_PLAN.md](TEST_PLAN.md), the architectural guarantees from [ARCHITECTURE.md](ARCHITECTURE.md), and the authoring rules from [CODING_RULES.md](CODING_RULES.md). It is a planning spec for the workflow files and their supporting test scaffolding, not a runbook.

admt has no CI today. The local four-command gate ([TEST_PLAN.md §Quality Gate](TEST_PLAN.md#quality-gate)) is the only gate, and it has held: full line + branch coverage, ruff/mypy clean. CI exists to close the gaps the local gate cannot solve: spec-vs-implementation drift, drift between code and CI itself, lockfile churn, and the merge-as-test problem where independently-green PRs are not validated as a coherent whole until after they land.

This plan is the spec for admt's CI -- the workflow files under `.github/workflows/` and their supporting test scaffolding. Like every admt spec doc, code that does not trace to it is rejected; behavior that diverges is a defect to fix in the workflow or amend here first -- not both, not silently.

---

## Table of Contents

- [Why CI Now](#why-ci-now)
- [Scope and Non-Goals](#scope-and-non-goals)
- [Requirements](#requirements)
- [Workflow Architecture](#workflow-architecture)
- [Workflow: gate.yml](#workflow-gateyml)
- [Workflow: container.yml](#workflow-containeryml)
- [Workflow: release.yml (Roadmap)](#workflow-releaseyml-roadmap)
- [Workflow: upstream.yml (Roadmap)](#workflow-upstreamyml-roadmap)
- [Tier 3 Fixture Strategy](#tier-3-fixture-strategy)
- [Per-Command Coverage Matrix](#per-command-coverage-matrix)
- [Architectural Enforcement](#architectural-enforcement)
- [Artifacts and Provenance](#artifacts-and-provenance)
- [Toolchain Pinning](#toolchain-pinning)
- [Implementation Order](#implementation-order)
- [Test Plan for the Workflows Themselves](#test-plan-for-the-workflows-themselves)
- [Constraints and Assumptions](#constraints-and-assumptions)
- [Roadmap](#roadmap)
- [Appendix A: Trigger and Permission Matrix](#appendix-a-trigger-and-permission-matrix)
- [Appendix B: Job Reference](#appendix-b-job-reference)

---

## Why CI Now

The project retrospectives named three problems the local gate is structurally unable to solve. Each one is a specific failure mode CI is designed to catch:

1. **Spec-vs-implementation drift is invisible until tier 3 runs.** Tier 1+2 tests pin on exception classes; the user-facing exit code is observable only by running the real binary. Several exit-code mismatches stayed latent for the whole pre-CI period because no tier-1 test pinned on the user-facing exit code, and no tier-3 suite existed to catch the discrepancy at the binary boundary. CI is where tier 3 finally runs continuously.

2. **Single-day batch-merge made the merge itself the test.** Multiple PRs landed in the same window, each independently green, but the post-merge state on `main` was not validated as a single coherent run before the merges. CI on every push to `main` provides exactly that validation -- a clean checkout of the merged tip, the four-command gate, and the tier-3 sweep, with no developer-machine state in the picture.

3. **`uv.lock` churn slips into unrelated PRs.** Mechanical lockfile rewrites from uv-version drift have polluted the history more than once. The `uv.lock` policy paragraph is in [CODING_RULES.md §uv.lock policy](CODING_RULES.md#uvlock-policy); CI is where the policy gets *enforced*, by pinning the `uv` version that runs the gate so the reference rewrite is deterministic.

This list is also the test plan for whether CI is doing its job. If a future drift slips through CI without being caught here, that is a CI gap, not a development gap.

---

## Scope and Non-Goals

### In Scope

- **The four-command quality gate**, run on every push and every pull request, on Linux and macOS. The gate is the same gate developers run locally; CI is the second eye, not a different bar.
- **Tier 3 container tests**, run on every non-draft pull request and every push to `main`. Tier 3 is the spec-conformance backstop named in [TEST_PLAN.md §Tier 3](TEST_PLAN.md#tier-3-container-tests).
- **Per-command, per-flag, per-alias coverage** -- every concrete `Command` subclass, every short alias (`e`, `b`, `t`, `s`, `an`, `cl`, `p`, `cov`, `pub`, `w`, `tmpl`), and every global flag and env variable in [TEST_PLAN.md §What to Test](TEST_PLAN.md#what-to-test) is exercised at the appropriate tier.
- **Architectural enforcement** -- `tests/unit/test_architecture.py` (per [TEST_PLAN.md §Architectural Enforcement Tests](TEST_PLAN.md#architectural-enforcement-tests)) enforces the layering, `Command` metadata, and CLI↔Command parity rules.
- **Local rehearsal via a host script** -- tier-3 workflow logic is exposed as `tests/container/run.sh`, the single entry point CI invokes and developers run locally, so the test logic is rehearsable on any host with Docker without a separate local Actions runner.
- **Toolchain pinning** for `uv`, the Python interpreter, the Adamant container image, and any GitHub Actions third-party action versions.
- **Failure forensics** -- every failure produces an artifact bundle with provenance metadata (commit SHA, run ID, branch, OS) and human-navigable HTML reports.
- **Wheel build on every run.** `uv build` runs in the gate and uploads the wheel as an artifact on every push and PR, so packaging and entry-point breakage (`[project.scripts]` wiring, missing package data) surfaces immediately instead of first failing at release. Publishing that wheel to PyPI is deferred ([release.yml](#workflow-releaseyml-roadmap) is Roadmap); the gate's wheel artifact is the packaging signal until then.

### Roadmap (in scope to *describe* here, not to land in the initial CI surface)

- **PyPI publishing on release** -- `uv publish` + attestations. The wheel itself is already built and artifact-checked on every run (see In Scope above); release only *publishes* the built artifact.
- **ARM64 verification on release** -- echo the Adamant ecosystem pattern (`test_all_arm64.yml`).
- **Upstream contract tests** -- a weekly schedule that re-runs tier 3 against the latest `ghcr.io/lasp/adamant:*` image so we notice when an Adamant change breaks our integration.
- **Plugin-author CI template** -- the gate definition packaged for plugin authors to reuse.

### Non-Goals

- **No relaxation of the local gate.** CI does not enforce a *different* bar than the one developers see locally; it enforces the *same* bar from a clean machine. A change that passes CI but fails the local gate is broken.
- **No hidden CI-only commands.** Anything CI runs is either (a) one of the four gate commands, (b) `pytest -m container` for tier 3, (c) a documented packaging command. No bespoke "CI thinks the gate is X" pseudo-checks.
- **No new top-level directory.** Workflow files live in `.github/workflows/`. CI helper assets live in `tests/ci_assets/`. There is no `ci/` or `scripts/` top-level directory.
- **No Windows runners.** Per [CODING_RULES.md §Language and Runtime](CODING_RULES.md#language-and-runtime), Windows is not yet a target. CI matches the spec.
- **No `act` dependency.** admt does not require, ship, or document [`act`](https://github.com/nektos/act) recipes. Tier-3 workflow logic is exposed through `tests/container/run.sh`, which CI and developers invoke identically, and the gate's four commands run locally on their own -- local rehearsal needs a Docker daemon and the host script, not a workflow-runner emulator.

---

## Requirements

These are invariants. Every workflow change traces to one or more of these, exactly the way every code change traces to ARCHITECTURE.md's R1-R14.

### CI1. Same Gate, From a Clean Machine

CI runs the same four commands listed in [TEST_PLAN.md §Quality Gate](TEST_PLAN.md#quality-gate):

```
ruff format --check src/ tests/
ruff check src/ tests/
mypy src/
pytest --cov --cov-branch --cov-fail-under=100 -m "not container"
```

The `-m "not container"` clause is the only deviation: tier 3 tests are marked `@pytest.mark.container` and run in a separate workflow ([CI3](#ci3-tier-3-runs-on-every-non-draft-pr)).

### CI2. The Coverage Threshold Is Hard

100% line + branch on every gate run. If a future change drops coverage to 99.9%, the gate fails. Enforced by `pytest --cov-fail-under=100` and by `[tool.coverage.report] fail_under = 100` in `pyproject.toml`. CI does not loosen the threshold; CI does not split the threshold across "important" and "less important" modules; CI does not exclude lines via `pragma: no cover` without the rare-and-justified rationale documented in TEST_PLAN.md.

### CI3. Tier 3 Runs on Every Non-Draft PR

> **Deferred** -- Tier 3 is not part of the initial CI implementation (Tier 1+2; see [Implementation Order](#implementation-order)). This requirement and its draft-handling are preserved for the dedicated Tier 3 planning and re-review.

Tier 3 catches the spec-vs-implementation drift the local gate cannot. It must run before merge, not as a post-merge afterthought. The `pull_request` trigger filters with `types: [opened, synchronize, reopened, ready_for_review]` and a job-level `if: ${{ github.event.pull_request.draft == false }}` so draft PRs are exempt. It also runs on every push to `main` so the post-merge state is validated coherently.

### CI4. Every Command, Every Flag, Every Alias

Tier 3 exercises every concrete `Command` subclass, every short alias, and every global flag listed in [TEST_PLAN.md §What to Test](TEST_PLAN.md#what-to-test). Covering that surface is the tier-3 suite's own responsibility, backstopped by the 100% coverage gate and `test_architecture.py`'s command/CLI-parity test.

### CI5. Local-Rehearsable via a Host Script

Tier-3 workflow logic lives in a host script (`tests/container/run.sh`), not inlined in the workflow YAML. CI invokes the script and developers run the same script locally, so the test logic is rehearsable on any host with Docker and CI and local share one entry point. admt does not depend on a local GitHub-Actions runner (see [Non-Goals](#scope-and-non-goals)).

### CI6. Toolchain Pinning

Every external version that affects the gate is pinned:

- `uv` -- pinned in `.uv-version` and read by both the workflow and contributors.
- Python -- pinned in `.python-version` (existing).
- Adamant container image -- pinned by tag (the `ADAMANT_TAG` value from `tests/container/_pins.env`), not `:latest`.
- Third-party GitHub Actions -- pinned to a full SHA with a comment (`org/action@<sha>  # vX.Y.Z`).

Renovate or Dependabot handles version bumps via PR; CI itself does nothing dynamic.

### CI7. No Bypass

There is no `[skip ci]` short-circuit. There is no "trivial change" branch protection that lets the gate be optional. Documentation-only PRs run the gate; whitespace PRs run the gate. The single exception: pull-request *draft* status pauses tier 3 (per [CI3](#ci3-tier-3-runs-on-every-non-draft-pr)). Drafts still run the gate.

### CI8. Failure Is Self-Diagnostic

Every failed run uploads enough artifact for forensic diagnosis without a re-run:

- Coverage HTML (themed; see [Artifacts and Provenance](#artifacts-and-provenance)) and XML.
- JUnit XML from pytest, with a rendered summary on the PR's checks tab.
- The `docker compose logs` and `docker inspect` of the test container on tier-3 failures.
- Provenance: the container job ships a `versions.txt` (commit SHA, pins, image digest, OS, timestamp); the gate's provenance is the workflow run's own metadata.

Retention 14 days. Artifact names are stable so links from PR comments do not rot.

### CI9. Spec Traceability

Every step in every workflow file ties back to a section of ARCHITECTURE / CODING_RULES / TEST_PLAN / this document. Steps with no spec home are rejected at review (per [CODING_RULES.md §What to Review Per PR](CODING_RULES.md#what-to-review-per-pr)). The workflow YAML carries a top-of-file comment naming the spec sections it implements.

---

## Workflow Architecture

Four workflow files, each with one purpose. A change to one workflow does not require changes to the others; a failure in one does not gate the others. The split mirrors the project's design culture: each file does one thing, the file-size guideline (~300 lines) applies per-workflow.

```
.github/
  workflows/
    gate.yml          # Tier 1+2: ruff format, ruff check, mypy, pytest -m "not container"
    container.yml     # Tier 3: pytest -m container, against the pinned Adamant image
    release.yml       # Roadmap (deferred): PyPI publish (wheel already ships as a gate artifact)
    upstream.yml      # Roadmap (deferred): weekly tier 3 vs :latest
```

### Concurrency

Every workflow uses the same concurrency group:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

A force-push to a PR cancels the in-flight CI run for that ref. Pushes to different refs (different PRs, `main`, release tags) do not interfere.

### Default Shell

Every workflow sets a predictable shell:

```yaml
defaults:
  run:
    shell: bash --noprofile --norc -euo pipefail {0}
```

No surprises from a runner's shell init files; failures propagate immediately.

---

## Workflow: gate.yml

The four-command quality gate. The same gate, on a clean machine, on every push and PR.

### Triggers

```yaml
on:
  push:
    branches: [main]
  pull_request:
    types: [opened, synchronize, reopened]
  workflow_dispatch:
```

The gate runs on drafts too (a draft with broken style/types/coverage should still be visible).

### Matrix

```yaml
strategy:
  fail-fast: false
  matrix:
    os: [ubuntu-24.04, macos-14]
```

`fail-fast: false` so a Linux failure doesn't hide a separate macOS failure. Both must pass. [CODING_RULES.md §Language and Runtime](CODING_RULES.md#language-and-runtime) names Linux + macOS as the target platforms; CI matches the spec.

### Steps (high level)

1. **Checkout** -- `actions/checkout@v6` with `persist-credentials: false`.
2. **Set up uv** -- `astral-sh/setup-uv@v6` with `version:` read from `.uv-version` and `enable-cache: true`. The action also reads `.python-version` natively.
3. **`uv sync --dev --frozen`** -- `--frozen` so CI cannot rewrite the lockfile. If the lockfile is stale relative to `pyproject.toml`, this step fails and the developer must rerun `uv sync` locally and commit the lockfile bump.
4. **Run gate command 1**: `uv run ruff format --check src/ tests/`.
5. **Run gate command 2**: `uv run ruff check src/ tests/`.
6. **Run gate command 3**: `uv run mypy src/`.
7. **Run gate command 4**: `uv run pytest --cov --cov-branch --cov-fail-under=100 --junitxml=gate-junit.xml -m "not container"`.
8. **Generate themed coverage HTML** (always) -- `uv run coverage html --extra-css tests/ci_assets/admt-dark.css --title "admt coverage @ ${SHORT_SHA}"`.
9. **Surface per-test results** (always) -- `mikepenz/action-junit-report` posts the JUnit XML as a check-run (see [Artifacts and Provenance](#artifacts-and-provenance)).
10. **Upload artifact bundle** (always) -- `actions/upload-artifact@v4` with everything in `_artifacts/` (coverage HTML, coverage XML, JUnit XML, log tail). Name: `gate-${{ matrix.os }}-${{ github.sha }}`.
11. **Post step summary** (always) -- a tabular summary on `$GITHUB_STEP_SUMMARY` with gate command results, coverage %, and a link to the artifact.

### Wheel build (every run)

The ubuntu leg also runs `uv build` and uploads `dist/*.whl` + `dist/*.tar.gz` as an artifact. This runs on every push and PR, so packaging and entry-point breakage (`[project.scripts]` wiring, missing package data) surfaces in the gate -- not first at release time. Publishing the wheel to PyPI is the only release-exclusive packaging step ([release.yml](#workflow-releaseyml-roadmap)).

### Job-Level Configuration

```yaml
jobs:
  gate:
    name: gate (${{ matrix.os }})
    runs-on: ${{ matrix.os }}
    timeout-minutes: 15
    permissions:
      contents: read
```

### Docker-free gate

The gate needs no Docker daemon -- this is load-bearing and deliberate: tier 1+2 inject a fake compose resolver, so the only places that shell `docker compose config` are `env init`/`env refresh` at runtime (and therefore tier 3). A unit or integration test that invokes the real resolver would silently make the gate require a docker CLI -- treat that as a defect, not a dependency to install on the runner.

---

## Workflow: container.yml

> **Deferred.** Tier 3 -- the container suite and this workflow -- is not part of the initial CI implementation (Tier 1+2; see [Implementation Order](#implementation-order)). This specification is preserved for the separate, dedicated Tier 3 planning and re-review; it is left as-is.

Tier 3 container tests, against a real Adamant Docker container, named in [TEST_PLAN.md §Tier 3](TEST_PLAN.md#tier-3-container-tests).

### Purpose

Tier 3 is the ground-truth tier. It exercises every implemented `admt` command end-to-end against a real Adamant container and a real `redo`-driven build system. It catches the bug class that tier 1+2 mock around -- exit-code mismatches, output-format drift, sibling-mount path resolution, signal propagation through `docker exec`.

### Triggers

```yaml
on:
  push:
    branches: [main]
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
  workflow_dispatch:
```

### Job: container

The job logic is *not* inlined into the workflow YAML; it is implemented as a host script (`tests/container/run.sh`) that the workflow invokes. This is deliberate (per [CI5](#ci5-local-rehearsable-via-a-host-script)): CI and developers run the same script, so CI and local share one entry point.

The workflow's job is therefore short:

1. Set up `uv` and the Python interpreter (same step as gate.yml).
2. `uv tool install --python "$(cat .python-version)" --reinstall --editable .` -- install admt onto PATH (the interpreter version reads the pin file, per [Toolchain Pinning](#toolchain-pinning)).
3. `bash tests/container/run.sh` -- the host script does everything else:
   - Pulls `ghcr.io/lasp/adamant:${ADAMANT_TAG}` (idempotent; `actions/cache@v5` keyed on the image digest amortizes pulls).
   - Bootstraps `tests/container/_workspace/adamant/` by cloning `https://github.com/lasp/adamant.git` at a pinned ref (or symlinking a local checkout if `ADMT_LOCAL_ADAMANT=<path>` is set; see [Tier 3 Fixture Strategy](#tier-3-fixture-strategy)).
   - Runs `pytest tests/container/ -m container --junitxml=container-junit.xml`.
   - On failure, dumps `docker compose ... logs` and `docker inspect` for every container the suite touched into `_artifacts/container-logs/`.
4. Surface per-test results (always) -- same JUnit check-run step as gate.yml.
5. Upload artifact bundle (`container-${{ matrix.project }}-${{ github.sha }}`).

### Configuration Matrix

The container job ships single-config (`project: standalone`) and grows along orthogonal axes that don't pay the per-leg activate cost twice. The matrix dimension lives on the host script via an env var, not in the workflow YAML, so local rehearsal can pick a config too.

**Acceptable matrix axes:**

- `project: [standalone, multi-repo]` -- exercises path-mapping against single-mount and multi-mount Adamant project shapes. Follow-up after standalone is stable.
- `os: [ubuntu-24.04, macos-14]` -- when admt grows tier-3-relevant macOS coverage. Currently macOS doesn't ship a usable Adamant container; this axis stays single-OS until upstream changes.
Version-drift across Adamant releases is deliberately **not** a PR/push matrix axis: PR and push CI pin a single image for determinism, and [`upstream.yml`](#workflow-upstreamyml-roadmap) (weekly, against `:latest`) is the early-warning signal for upstream Adamant drift.

**Antipattern: per-command matrix.** Decomposing tier 3 into one matrix leg per admt command (`{command: [build, test, style, ...]}`) is the wrong shape: each leg is a fresh runner with a fresh container, paying the full pull + activate cost (5-10 minutes) for ~30 seconds of test work. Per-test rendering already comes from the JUnit + `mikepenz/action-junit-report` path -- splitting commands into matrix legs adds runtime without buying isolation we need.

**Open question -- amortizing activate across legs.** If a future workflow design successfully amortizes container pull / startup / activate across multiple matrix legs (e.g., a self-hosted runner with persistent state, or an outer "setup" job whose container is reused by inner matrix legs), the per-command matrix antipattern relaxes. Implementation will surface clear benefit or detractor; the rule above stands until that exploration lands evidence.

### Job-Level Configuration

```yaml
jobs:
  container:
    name: container (${{ matrix.project }})
    runs-on: ubuntu-24.04
    if: ${{ github.event_name != 'pull_request' || github.event.pull_request.draft == false }}
    timeout-minutes: 60   # tier 3 dominated by Adamant first-run env/activate (~5-10min) per fresh runner; see Risks
    permissions:
      contents: read
      packages: read
    strategy:
      fail-fast: false
      matrix:
        project: [standalone]
```

---

## Workflow: release.yml (Roadmap)

**Deferred -- not part of the initial CI implementation.** Publish the admt wheel to PyPI on release (and verify ARM64). The gate already builds and artifact-checks the wheel every run, so publishing is the only release-exclusive step; it is specced here for the later planned effort and left as-is for re-review then.

### Triggers

```yaml
on:
  release:
    types: [published]
  workflow_dispatch:
```

### Jobs

1. **gate-release** -- re-runs the four-command gate against the release tag's tip on Linux + macOS. Blocks publish on failure.
2. **container-release** -- re-runs tier 3 against the release tag's tip. Blocks publish on failure.
3. **build-wheel** -- rebuild the wheel at the release tag's tip (`uv build` on `ubuntu-24.04`) so the published bytes match the tag exactly, producing `dist/admt-X.Y.Z-py3-none-any.whl` and `dist/admt-X.Y.Z.tar.gz`. The build is not release-exclusive -- the gate already builds and artifact-checks the wheel on every run (see [gate.yml](#workflow-gateyml)); only the publish step (below) is release-only.
4. **publish-pypi** -- `pypa/gh-action-pypi-publish@release/v1` with trusted publishing (no API token in secrets). `needs: [gate-release, container-release, build-wheel]`.
5. **arm64-verification** -- `docker/setup-qemu-action@v4` + `linux/arm64` execution of the wheel against `ghcr.io/lasp/adamant:${ADAMANT_TAG}-arm64`. Echoes `adamant/.github/workflows/test_all_arm64.yml`. Advisory-only for the first published release; required-blocking once the first arm64 admt user emerges.

---

## Workflow: upstream.yml (Roadmap)

**Deferred -- not part of the initial CI implementation.** Weekly verification that admt still works against the latest Adamant container; specced here for the later planned effort and left as-is for re-review then.

### Purpose

A scheduled job that re-runs tier 3 against the explicitly-mutable `ghcr.io/lasp/adamant:latest` tag. Detects upstream-contract drift (e.g., a `redo what` reformat, a new field in `docker compose ps --format json`) before a downstream user does.

### Triggers

```yaml
on:
  schedule:
    - cron: "0 6 * * 1"   # Monday 06:00 UTC
  workflow_dispatch:
```

### Jobs

1. **upstream-tier3** -- run `pytest -m container` against `ghcr.io/lasp/adamant:latest`. On failure, opens an issue titled `Upstream contract drift: <commit-sha>` with the failure log and a diff of the relevant outputs (`redo what`, `docker compose ps --format json`, etc.) between `:0.2` and `:latest`. Issue label: `upstream-drift`.
2. **pin-parity-remote** -- pulls `ghcr.io/lasp/adamant:${ADAMANT_TAG}` (the *pinned* tag, not `:latest`), reads the OCI image label `org.opencontainers.image.revision`, and asserts it matches `ADAMANT_REF` from `tests/container/_pins.env`. It is a remote pin-parity check: only a network round-trip to GHCR answers "does the upstream container at this tag actually correspond to this source ref?". Drift opens an issue titled `Adamant pin drift: <tag> ≠ <ref>` with the OCI label and the local `_pins.env` snapshot; the fix is to bump `_pins.env` (or, rarely, to re-tag upstream).

The first job uses `:latest` deliberately to detect format-level contract drift. The rest of CI uses pinned tags ([CI6](#ci6-toolchain-pinning)); this workflow's job is to *break* when upstream moves.

---

## Tier 3 Fixture Strategy

> **Deferred** -- part of the Tier 3 specification (see [Implementation Order](#implementation-order)); preserved for re-review, left as-is.

Tier 3 needs a real Adamant project to exercise admt against. Two constraints shape the fixture:

1. **The runner bind-mounts the workspace at a stable path; directories outside it are not reliably visible.** On cloud GHA the checkout *is* the workspace. So the Adamant fixture must live *inside* the admt working tree (`tests/container/_workspace/adamant/`).
2. **The Adamant `docker-compose.yml` uses relative paths** (e.g., `source: ../../adamant`). For docker-compose to resolve the right host paths whether running on cloud GHA or locally via `tests/container/run.sh`, the fixture layout must match the relative-path convention.

### Fixture Layout

```
tests/
  container/
    _workspace/                 # gitignored -- not committed
      adamant/                  # cloned from lasp/adamant at a pinned ref
        docker/docker-compose.yml
        env/activate
        default.do
        ...
    conftest.py                 # session-scoped fixture: pull, register, expose
    test_*.py                   # tier-3 tests
    run.sh                      # the host script the workflow invokes
```

`tests/container/_workspace/` is in `.gitignore`. The host script `tests/container/run.sh` populates it on first run.

### Three Fixture Modes

The fixture supports three modes, picked at invocation by environment variables. Each answers a different development question.

| Mode | When to use | Picked by | Adamant source state |
|---|---|---|---|
| **Pinned** (default) | Routine testing, CI, day-to-day verification that admt still works | (no overrides) | clone at `ADAMANT_REF` from `_pins.env` |
| **Live local** | Developer is co-iterating on admt and on a local Adamant checkout (often a feature branch) | `ADMT_LOCAL_ADAMANT=<path>` | symlink to the developer's working tree (live) |
| **Pin-overridden** | Spot-check admt against current upstream `main` before bumping `_pins.env` | `ADAMANT_REF=main` (or any ref) | fresh clone at the override ref |

`ADMT_LOCAL_ADAMANT` and `ADAMANT_REF` are mutually exclusive: if both are set, `ADMT_LOCAL_ADAMANT` wins. Mode selection happens once per `run.sh` invocation, in the bootstrap phase.

CI always uses pinned mode (no env overrides). Both override modes are developer-facing.

### Bootstrap Logic

The host script:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="$SCRIPT_DIR/_workspace"
ADAMANT_DIR="$WORKSPACE/adamant"

# Source the pin pair. _pins.env uses `:=` so env vars explicitly set by the
# developer (e.g., ADAMANT_REF=main) override the file's defaults.
source "$SCRIPT_DIR/_pins.env"

mkdir -p "$WORKSPACE"

# A previous run may have left a symlink (live mode). If this run is not
# live mode, drop the symlink so the clone branch below operates on a real
# directory rather than recursing into the developer's local tree.
if [[ -L "$ADAMANT_DIR" && -z "${ADMT_LOCAL_ADAMANT:-}" ]]; then
  rm -f "$ADAMANT_DIR"
fi

if [[ -n "${ADMT_LOCAL_ADAMANT:-}" ]]; then
  # Mode: live local. Symlink the developer's working tree.
  echo "tier 3: live mode -- symlinking $ADMT_LOCAL_ADAMANT"
  rm -rf "$ADAMANT_DIR"
  ln -s "$ADMT_LOCAL_ADAMANT" "$ADAMANT_DIR"
elif [[ ! -d "$ADAMANT_DIR/.git" ]]; then
  # Mode: pinned (or pin-overridden). Fresh shallow clone.
  echo "tier 3: cloning Adamant at $ADAMANT_REF"
  rm -rf "$ADAMANT_DIR"
  git clone --depth 1 --branch "$ADAMANT_REF" \
    https://github.com/lasp/adamant.git "$ADAMANT_DIR"
else
  # Existing clone -- update to the requested ref.
  echo "tier 3: updating Adamant to $ADAMANT_REF"
  git -C "$ADAMANT_DIR" fetch --depth 1 origin "$ADAMANT_REF"
  git -C "$ADAMANT_DIR" checkout FETCH_HEAD
fi

# Pull the Adamant container (cached on cloud via actions/cache)
docker pull "ghcr.io/lasp/adamant:${ADAMANT_TAG}"

# Run the tier-3 suite
exec uv run pytest tests/container/ -m container \
  --junitxml=_artifacts/container-junit.xml
```

The script reads `_pins.env` for both the tag and the default ref; per-invocation overrides win. The flow is the same on cloud CI (pinned mode, no env overrides) and locally (any of the three modes), so the same script answers every test question.

#### Live-Mode Caveats

In live mode, the test suite mutates the symlinked Adamant tree (build artifacts under `build/`, generated files under templates' output directories). This is intentional: it reproduces what the developer would see if they ran `admt build` directly. But:

- The developer should `git status` their Adamant tree before and after to confirm nothing unwanted got written.
- A test that explicitly *removes* something (e.g., `admt clean -a`) will erase build artifacts in the developer's live tree -- that is, it does what `admt clean -a` is supposed to do. Tests that call cleanup commands are scoped to fresh pinned-mode runs by default; the conftest fixture warns and skips them under `ADMT_LOCAL_ADAMANT` unless `ADMT_ALLOW_LIVE_MUTATION=1` is set.

#### Pin-Override-Mode Caveats

Pin-override mode (e.g., `ADAMANT_REF=main`) clones a *different* Adamant source than the container at `:${ADAMANT_TAG}` was built from. A test that depends on source-vs-binary parity (e.g., expects a specific `redo what` output that matches a specific source revision) may behave inconsistently. The expected behavior for this mode is the same as for the upstream contract test job ([upstream.yml](#workflow-upstreamyml-roadmap)): tier 3 is allowed to fail, and the failure is the signal that upstream has moved.

### conftest.py Responsibilities

`tests/container/conftest.py` exposes session-scoped fixtures that:

- Point admt at a scratch config home for the whole session (a temp `HOME`), so the suite's registrations and per-terminal session state never touch the developer's real `~/.admt` during local rehearsal -- `config.yml` and `sessions.yml` are both live state now, and polluting them from a test run is not acceptable.
- Locate the Adamant clone (see [Project Resolution Order](#project-resolution-order) below for the four-mode lookup).
- Register the project: `subprocess.run(["admt", "env", "init", <path>])`. Registration shells `docker compose config`, so the docker CLI must be present -- a given on the tier-3 runner.
- Start the container: `admt env start`.
- Yield component-scoped fixtures (see [Component Coverage Requirements](#component-coverage-requirements) below).
- Teardown: `admt env stop`; the scratch config home is discarded with the session.

The fixture is **session-scoped** so the container starts once per pytest run, not once per test. Tests that mutate state restore it (e.g., a test that changes the active project switches it back).

### Project Resolution Order

The conftest looks for the Adamant project in this order (first match wins):

1. **`ADMT_LOCAL_ADAMANT`** -- developer override pointing at a live local checkout (anywhere on the filesystem). Symlinked into `_workspace/adamant`.
2. **`ADMT_TIER3_PROJECT`** -- explicit absolute path to a registered admt project root. CI sets this to the freshly cloned, pinned Adamant.
3. **Pinned clone in `_workspace/adamant/`** -- if `tests/container/_workspace/adamant/` exists with a `.git/` directory, use it. Bootstrapped by `tests/container/run.sh`.
4. **Active project's Adamant volume mount** -- when none of the above resolve, read the active project's *resolved* volume mounts (admt's cached config, derived at registration via `docker compose config`) for a mount whose target is `/home/user/adamant`, and use its host source path. This is the "adjacent siblings" model: a developer with admt and Adamant checked out under a common parent dir, plus an admt-registered downstream project (e.g., a mission FSW repo) gets tier 3 working with no env var setup.

If none resolve to a valid directory, every tier 3 test skips with a clear message rather than fails. The fallback is path-discovered, never hardcoded -- the conftest reads the project's resolved volume mounts to find Adamant, so any layout the user adopts (sibling dirs, a workspace dir, an entirely different filesystem location) works as long as the compose configuration is consistent. Because `docker compose config` emits absolute volume sources (after loading any colocated `.env`), the discovered path needs no relative-path resolution of its own.

The path-discovery code uses `pathlib.Path`, never strings; system-specific path conventions (XDG, macOS Library paths, Windows drive letters) are absorbed at the OS layer, not hardcoded.

### Component Coverage Requirements

Tier 3 exercises admt against real Adamant components. The component fixtures must collectively satisfy a coverage matrix -- not specific component names, since upstream Adamant renames and reorganizes components. The conftest publishes named fixtures, each backed by a component that satisfies a feature requirement; when upstream renames or removes the chosen component, the fixture skips with a clear message and a contributor bumps the constant to a still-existing component matching the requirement.

The required coverage:

| Feature | Why tier 3 needs it | Fixture name |
|---|---|---|
| Passive component | Verifies admt handles the most common component shape end-to-end | `passive_component` |
| Active component | Active-execution semantics differ; admt's path-mapping must work for both | `active_component` |
| Component with `test/` directory | `admt test`, `admt coverage`, `admt analyze` need a real test target | `testing_component` |
| Connectors (multiple) | Component code generation depends on connector resolution | (covered by `passive_component` or `active_component`) |
| Parameters | `admt build` against a component with parameters exercises parameter-table generation | `parameterized_component` |
| Commands | Components emitting commands stress the command-router code path | `commanding_component` |
| Self-contained types (records, arrays, enums) | YAML type-system handling | (covered by any of the above) |
| Complex handcoded specs | Generation interacts with hand-written `.ads`/`.adb` -- coverage matters when admt's templates command runs | `handcoded_component` |
| YAML preambles (component metadata) | Preamble parsing is part of the model the templates command must respect | (covered by any of the above) |

A single component may satisfy multiple feature requirements, and a fixture may compose more than one component when needed. The conftest is permitted to back several fixtures with the same underlying component if it satisfies all the relevant features; the goal is feature coverage, not fixture count.

When a component-fixture lookup fails (renamed, removed, or doesn't satisfy the feature), tier 3 tests using that fixture skip with a message naming the missing feature and the previously-chosen component. The fix is to bump the constant, not to make the test resilient -- tier 3 is supposed to fail loudly when the upstream surface drifts.

The actual component-name selection is an implementation detail of `tests/container/conftest.py`. CI_PLAN.md specifies the coverage requirement; the implementation chooses the components.

### Why Not a Pre-Built Test Project?

A pre-built minimal Adamant project committed to admt (instead of cloning) is tempting but rejected:

- It would drift from real Adamant immediately.
- It would not exercise the path-mapping code against a layout developers actually use.
- It would have to be re-tested every time Adamant's compose-file format moved.

Cloning standalone Adamant at a pinned ref keeps tier 3 honest about what it is: a contract test against the real upstream.

### Multi-Repo Configuration (Follow-Up)

Standalone Adamant is the default tier-3 fixture configuration. A `multi-repo` configuration is the planned second matrix leg: it exercises path-mapping against a layout whose compose mounts more than one repository. Rather than fabricate a stub component repo, the leg uses a public reference Adamant project whose compose already mounts multiple repositories -- e.g. `lasp/adamant_example` -- cloned at a pinned ref into `_workspace/`, so the fixture mirrors a layout developers actually use. Path-mapping bugs that only manifest with multiple bind mounts are caught here.

### Worktree Configuration Coverage

admt's worktree support changed how project metadata is derived and selected: compose metadata is resolved via `docker compose config` (which loads a colocated `.env` and expands `${VAR:-default}` interpolation -- so a parameterized `container_name` resolves to its real per-worktree value), the `.env` mtime participates in config staleness alongside the compose file's, and the active project is per-terminal (TTY-keyed session store, `ADMT_ENV` outranking it). Each of those behaviors has a binary-boundary failure mode tier 1+2 cannot see, so tier 3 covers them explicitly (`test_env_worktrees.py`):

- **Parameterized registration.** A fixture compose using `${COMPOSE_PROJECT_NAME:-...}` for project and container name plus parameterized host ports, with a colocated `.env`: `admt env init` must register the *resolved* values (the exec hot path targets the resolved `container_name`), and the no-`.env` default must reproduce the unparameterized behavior.
- **`.env` staleness.** Editing the `.env` (project rename, port change) with the compose file untouched must trigger a re-derive on the next command; adding or removing the `.env` outright must read as a change too (the absent-file sentinel).
- **Side-by-side selection.** Two registered projects (a second copy of the fixture with a distinct `.env`): `ADMT_ENV` must target each correctly, and `env list`'s `*` must mark the resolution in effect.
- **No-TTY resolution.** CI runners have no controlling terminal, so the per-terminal session layer is disabled by design and every resolution follows the global default -- tier 3 in CI exercises that path by construction, and one test asserts `env use` followed by a passthrough command behaves correctly without a TTY. The TTY-present behaviors (session pinning, stale-sid fallthrough, pruning) are tier-1/2 territory, where the terminal is faked.

The worktree fixture is cheap: it reuses the standalone clone and varies only the compose file and `.env`, so it ships as a test file rather than a matrix leg. If the configuration count grows, `project: worktree` becomes an acceptable matrix axis under the same rules as `multi-repo`.

### Eventual: `admt create test-project`

Once `admt create project` ([ROADMAP.md](ROADMAP.md) Tier 4) lands, the bootstrap can shift to `admt create project tests/container/_workspace/test-project` and the standalone-Adamant clone becomes one of two configs rather than the default. This is forward-looking; it does not block the initial CI surface.

---

## Per-Command Coverage Matrix

> **Partly deferred** -- the Tier 3 rows below belong to the deferred Tier 3 specification (see [Implementation Order](#implementation-order)) and are preserved for re-review. The Tier 1+2 rows are exercised by the initial CI gate.

[TEST_PLAN.md §What to Test](TEST_PLAN.md#what-to-test) lists the scenarios every command must cover. The CI plan's job is to enforce that the full matrix is exercised at the right tier.

### Scenarios (per [TEST_PLAN.md](TEST_PLAN.md))

| Scenario | Tier | Why |
|---|---|---|
| Happy path | 1 + 2 + 3 | Tier 1 verifies command construction; tier 2 verifies CLI dispatch; tier 3 verifies real exit + stdout |
| Missing config | 2 + 3 | Exit-code is the user-visible signal -- tier 2 catches via CliRunner, tier 3 catches at the binary boundary |
| Container not running | 2 + 3 | Same logic; tier 3 confirms the prompt + ADMT_NONINTERACTIVE error renders correctly |
| Invalid arguments | 2 + 3 | Same |
| Path not mapped (CLI tier) | 2 + 3 | Same |
| `--verbose` | 2 + 3 | Tier 3 confirms the underlying command is actually printed |
| `--quiet` | 2 + 3 | Tier 3 confirms output is genuinely suppressed (TTY-aware behavior) |
| `--debug` | 2 + 3 | Tier 3 confirms `DEBUG=1` actually reaches redo |
| `--yes` | 2 + 3 | Same |
| `--force` | 2 + 3 | Same |
| `ADMT_NONINTERACTIVE` | 2 + 3 | Tier 3 confirms the error message text |
| `ADMT_NONINTERACTIVE=0` (off) | 2 + 3 | Tier 3 confirms the value-semantics rule |
| `ADMT_ENV` override | 2 + 3 | Tier 3 confirms the override actually targets the right project (two-project form in [Worktree Configuration Coverage](#worktree-configuration-coverage)) |
| `NO_COLOR` env var | 2 + 3 | Tier 3 confirms ANSI codes are stripped from real output |
| Short alias | 2 + 3 | Tier 3 confirms `admt b` works on the binary |

### Per-Command Coverage

The 24 concrete commands (see [ARCHITECTURE.md §Command Reference](ARCHITECTURE.md#command-reference)) split into two categories:

- **3 pure-host commands** (`env init`, `env use`, `env list`) -- no *running* container required. `env init` *creates* the project and shells `docker compose config` to derive its resolved metadata (docker CLI required; daemon not). `env use` writes the active selection -- this terminal's session entry plus the global default for new terminals. `env list` reads config and, when a controlling terminal exists, pins the resolution its `*` reports. Tier 3 coverage: a single happy-path test that the binary works against `tests/container/_workspace/adamant/`, plus [Worktree Configuration Coverage](#worktree-configuration-coverage).
- **21 project + container commands** -- everything else. Tier 3 coverage: each gets one happy-path test plus the full failure-path matrix where applicable to the command.

### Per-Alias Coverage

11 aliases in `cli.py` (`e`, `b`, `t`, `s`, `an`, `cl`, `p`, `cov`, `pub`, `w`, `tmpl`). Tier 3 confirms each invokes the right command with one minimal test per alias (`test_aliases.py`).

---

## Architectural Enforcement

Architectural enforcement lives in `tests/unit/test_architecture.py` (see [TEST_PLAN.md §Architectural Enforcement Tests](TEST_PLAN.md#architectural-enforcement-tests)): it asserts the import/dependency rules, the `Command` metadata contract, CLI↔Command parity (every `Command` has a Click entry and vice versa), the `cli.py` size cap, and the no-circular-imports rule. This plan relies on it as the architectural baseline; the gate runs it like any other tier-1 test.

---

## Artifacts and Provenance

Every CI run produces two surfaces, each carrying one signal:

- **In-PR check view** (auto-rendered by GitHub from JUnit) -- per-test pass/fail, scannable on the PR's "Checks" tab without leaving the browser. Powered by [`mikepenz/action-junit-report@v4`](https://github.com/mikepenz/action-junit-report). One step in each workflow, one POSTed check-run per job.
- **Downloadable artifact bundle** (uploaded by `actions/upload-artifact@v4`) -- forensic detail when a failure needs more than the check view: themed coverage HTML (per-file line + branch drilldown), raw JUnit XML, raw coverage XML (Cobertura format), and tier-3-only diagnostics (`docker compose logs`, version stamps).

Test results surface as PASS/FAIL in the JUnit-rendered check view. A reviewer who wants more than "PASS" clicks the test name in the check view and gets the assertion message verbatim from JUnit -- which already names the disagreeing file or missing test for parametrized failures. The custom dashboard renderer is intentionally absent; coverage HTML is the only piece worth owning the rendering of, and the `--extra-css` hook does that without owning anything else.

### Bundle Layout

```
_artifacts/
  gate/
    gate-junit.xml        # tier 1 + tier 2 (all of pytest, including the audits)
    coverage.xml          # Cobertura format
    htmlcov/              # coverage.py's HTML report, themed via --extra-css
      index.html
      ...

  container/              # tier 3 only
    container-junit.xml
    versions.txt          # admt --version, ADAMANT_TAG, ADAMANT_REF, image digest
    container-logs.txt    # docker compose logs (only on failure)
```

Two artifacts uploaded per workflow run (`gate-<os>-<sha>`, `container-<project>-<sha>`); the matrix-leg key keeps parallel legs from clashing, and the sha keeps links from PR comments stable across re-runs.

### Provenance

Provenance ships as a small `versions.txt` artifact in the container job (the gate's provenance is already implicit in the workflow run metadata: commit, ref, run ID, OS). The file is plain text so it lands cleanly in the artifact ZIP and reads at a glance:

```
admt version: 0.2.0
admt commit: <full SHA>
adamant pin tag: 0.2
adamant pin ref: v0.2.0
adamant image digest: sha256:...
runner OS: ubuntu-24.04
uv: 0.11.7
python: 3.14
started: 2026-05-01T12:34:56Z
```

`versions.txt` is generated by the workflow at the start of the container job (`admt --version`, `cat tests/container/_pins.env`, `docker inspect ghcr.io/lasp/adamant:${ADAMANT_TAG} -f '{{.Id}}'`). The image-digest line lets a future reviewer answer "exactly which Adamant binary did this run test against?" without re-running.

### Per-test rendering via `mikepenz/action-junit-report@v4`

Each workflow's pytest step writes JUnit XML; a follow-up step posts that XML as a check-run via `mikepenz/action-junit-report@v4`. The action is well-supported, requires no theming, and renders directly in GitHub's PR Checks tab.

```yaml
- name: Surface per-test results in PR check view
  if: ${{ always() }}
  uses: mikepenz/action-junit-report@<sha>  # v4.x.x
  with:
    report_paths: gate-junit.xml
    detailed_summary: true
    check_name: "gate (per-test)"
```

### Themed Coverage HTML

`coverage.py`'s native HTML reporter accepts `--extra-css` and `--title`:

```bash
uv run coverage html \
  --extra-css tests/ci_assets/admt-dark.css \
  --title "admt coverage @ ${SHORT_SHA}"
```

`tests/ci_assets/admt-dark.css` is a small CSS file (~100 lines) that:

- Sets a dark base palette (`#1a1a1a` background, `#e0e0e0` text).
- Uses `#CFB87C` (admt's signature gold) as the accent color: the `100%` coverage badge, link hover states, the per-file pass markers.
- Sets uncovered lines and branches red so they contrast cleanly with the gold-pass motif.

The output lands in `_artifacts/gate/htmlcov/`. After downloading the artifact zip, opening `htmlcov/index.html` in a browser gives per-file line + branch coverage navigation. Coverage HTML is the only piece of the bundle we own enough rendering of to theme; everything else comes from upstream tooling.

### Why no custom dashboard

GitHub's per-test check view (driven by `mikepenz/action-junit-report@v4`) already shows what a custom dashboard would: every test, its pass/fail, the assertion message on failure. The audits are designed so that JUnit's failure messages are self-describing -- e.g., `"BuildCommand missing tier-3 test in tests/container/"` -- so the per-test view tells the whole story for any audit row.

Theming the coverage HTML via `--extra-css` is the only place owning the rendering pays back, because coverage's per-file drilldown is genuinely useful and the CSS hook is one parameter. Everything else stands on upstream tooling.

If a future need arises for a unified dashboard consolidating cross-job data the check view cannot show (e.g., comparing two runs side-by-side), a Jinja2 renderer can be added under `tests/ci_assets/` without having to retrofit anything in the workflow.

### Retention and Naming

Artifact name: `<workflow>-<leg>-<sha>` (e.g., `gate-ubuntu-24.04-c0ffee1`). Stable enough to link from a PR comment; the `${{ github.sha }}` namespace prevents clashes across runs and the matrix-leg key prevents clashes within one. Retention: 14 days (default for `actions/upload-artifact@v4`). Long enough to debug a failed run a few days later; short enough to not balloon storage.

---

## Toolchain Pinning

Single-source-of-truth principle: every pin lives in exactly one place, referenced from CI. Each pin is read from that single source at runtime where possible, so a consumer cannot drift to a stale copy.

| Tool | Pin file | Pin key | Bump procedure |
|---|---|---|---|
| `uv` | `.uv-version` (new) | (whole-file value) | Bump file, run gate locally, fix any lockfile-format churn per CODING_RULES.md, single commit titled `chore: pin uv X.Y.Z` |
| Python | `.python-version` (existing) | (whole-file value) | Standard convention; uv reads it natively |
| Adamant container image tag | `tests/container/_pins.env` (new) | `ADAMANT_TAG` | Couple with `ADAMANT_REF` (see [Paired Pins](#paired-pins-and-_pinsenv)); single commit |
| Adamant ref for fixture clone | `tests/container/_pins.env` (new) | `ADAMANT_REF` | Same as above |
| Third-party GitHub Actions | Inline `org/action@<sha>  # vX.Y.Z` in workflow YAML | (per `uses:` line) | Renovate/Dependabot opens a PR; pin a 40-hex SHA with a `# vX.Y.Z` comment |

Every pin file is read by its consumer at runtime where possible (e.g., `cat .uv-version`, `source tests/container/_pins.env`) so consumers can never have a stale copy.

A future evolution -- bundle the pinned values in a `[tool.admt.ci]` section of `pyproject.toml` -- is rejected for now: that namespace doesn't exist, and adding it for a handful of values is overkill. Filename conventions readers already know (`.uv-version`, `_pins.env`) cost less.

### Paired Pins and `_pins.env`

The Adamant container image and the Adamant source revision the fixture clones must move together. A container at `ghcr.io/lasp/adamant:0.2` was built from a specific Adamant commit; cloning a different ref into the fixture means the source code in `tests/container/_workspace/adamant/` does not match the binary the container is running. Tier 3 either passes by accident or fails for the wrong reason.

The pair lives in `tests/container/_pins.env`. Both keys use bash's conditional-assign (`:=`) so an environment variable explicitly set by the developer wins over the file's default -- this is what makes pin-override mode (`ADAMANT_REF=main bash run.sh`) work without any branching logic in `run.sh`:

```sh
# Pinned versions for tier 3 fixtures.
# These two values are coupled -- the Adamant container at ADAMANT_TAG was
# built from the source at ADAMANT_REF. Bump them together.
#
# Use `:=` so an explicit env override wins over the file value:
#   ADAMANT_REF=main bash tests/container/run.sh   # tests against upstream main

: "${ADAMANT_TAG:=0.2}"
: "${ADAMANT_REF:=v0.2.0}"
```

`tests/container/run.sh` sources the file:

```bash
source tests/container/_pins.env
docker pull "ghcr.io/lasp/adamant:${ADAMANT_TAG}"
git -C "$ADAMANT_DIR" checkout "${ADAMANT_REF}"
```

Bumping the pair is a single commit: edit `_pins.env`, run tier 3 locally, commit titled `chore: bump Adamant pin to <tag> / <ref>`. The *remote* check -- does `:0.2` actually correspond to `v0.2.0` on Adamant's side? -- runs in [upstream.yml](#workflow-upstreamyml-roadmap): the OCI image label `org.opencontainers.image.revision` is read from the pulled container and compared to `ADAMANT_REF`. Drift opens an `upstream-drift` issue.

---

## Implementation Order

The implementation PR (the placeholder PR #19) brings **Tier 1+2 into CI** -- the four-command gate, at parity with the local gate that already exists. That is the whole initial surface. Tier 3 (the local container suite *and* its CI) and the release/upstream/polish workflows are deferred to separately-planned PRs: Tier 3 is large and central enough to warrant its own concept and mental-model review -- and a re-review of the specification below -- before implementation, which lands more cleanly once Tier 1+2 CI is in place. Per [CODING_RULES.md §Agent-Specific Rules](CODING_RULES.md#agent-specific-rules), the implementation edits the workflow to match this spec, never the reverse; it touches ARCHITECTURE/CODING_RULES/TEST_PLAN only where this plan amends them.

1. **`gate.yml` -- Tier 1+2 in CI (the implementation).** The four-command gate from [TEST_PLAN.md §Quality Gate](TEST_PLAN.md#quality-gate), on a clean machine, on every push and PR -- the same bar developers already run locally.
   - `.github/workflows/gate.yml` (Linux + macOS matrix), `.uv-version`, `.gitignore` additions (`gate-junit.xml`, `coverage.xml`, `_artifacts/`), `tests/ci_assets/admt-dark.css`.
   - `uv build` builds the wheel and uploads it as an artifact every run, so packaging/entry-point breakage surfaces here (see [In Scope](#scope-and-non-goals)).
   - A CLAUDE.md `## CI` section pointing to this plan and the local gate commands.
   - *Green when:* the four-command gate passes on the matrix.
2. **Tier 3 -- deferred to a separate planned PR (spec below left as-is for re-review).** The local container suite and its CI workflow are not in the implementation PR; the specification is preserved unmodified ([Tier 3 Fixture Strategy](#tier-3-fixture-strategy), [Per-Command Coverage Matrix](#per-command-coverage-matrix), [Workflow: container.yml](#workflow-containeryml)) and covers:
   - `tests/container/_pins.env` -- the Adamant tag/ref pair (see [Paired Pins](#paired-pins-and-_pinsenv)).
   - `tests/container/run.sh` -- the host script the workflow invokes ([Tier 3 Fixture Strategy](#tier-3-fixture-strategy)).
   - `tests/container/conftest.py` -- session-scoped fixtures (clone Adamant, register, start container, expose component path).
   - `tests/container/test_*.py` -- one file per command family, covering happy paths and the failure-path matrix from [TEST_PLAN.md §Error Path Tests](TEST_PLAN.md#error-path-tests):
     - `test_env_lifecycle.py` -- start/stop/restart/status/refresh.
     - `test_env_exec_login.py` -- exec, login, env exec.
     - `test_env_init_use_list.py` -- init, use, list, with multi-marker validation.
     - `test_env_worktrees.py` -- parameterized-compose registration with a colocated `.env`, `.env`-edit staleness re-derive, two side-by-side projects selected via `ADMT_ENV`, and no-TTY resolution ([Worktree Configuration Coverage](#worktree-configuration-coverage)).
     - `test_env_image.py` -- build, push, pull, rm with `--volumes`/`--image`/`--remove-all`.
     - `test_passthrough.py` -- build, what, test [--all], style [--all], analyze [--all], clean [--all], prove, coverage [--all], publish [--all].
     - `test_templates.py` -- templates, templates --undo, the `~/.admt/backup-latest` marker behavior.
     - `test_failure_paths.py` -- exit-code conformance for every error path TEST_PLAN.md names.
     - `test_global_flags.py` -- `--verbose`, `--quiet`, `--debug`, `--yes`, `--force`, `ADMT_NONINTERACTIVE`, `ADMT_NONINTERACTIVE=0`, `ADMT_ENV`, `NO_COLOR`.
     - `test_aliases.py` -- one minimal invocation per alias (`e`, `b`, `t`, `s`, `an`, `cl`, `p`, `cov`, `pub`, `w`, `tmpl`).
     - `test_signal_handling.py` -- SIGINT propagation, exit code 130 ([ARCHITECTURE.md §Signal Handling](ARCHITECTURE.md#signal-handling)).
   - `tests/CI.md` -- an operator runbook for running the suite locally via `tests/container/run.sh`.
   - `.github/workflows/container.yml` -- runs the suite via `run.sh` on every non-draft PR and push to `main`.
3. **Release, upstream-contract, and polish -- deferred to their own plan + PR.** `release.yml` (PyPI publish), `upstream.yml` (weekly tier-3 against `:latest`), and polish are out of scope for the implementation PR; the [release.yml](#workflow-releaseyml-roadmap) and [upstream.yml](#workflow-upstreamyml-roadmap) sections carry enough to spec that follow-on effort when it's prioritized.

---

## Test Plan for the Workflows Themselves

The workflows are code; they have their own acceptance criteria. For the initial implementation the only workflow is `gate.yml`; the `container.yml`/`run.sh` criteria below apply when the deferred Tier 3 lands (see [Implementation Order](#implementation-order)).

### Per-Workflow Acceptance

Every workflow change goes through this checklist before merge:

1. **A YAML parse/lint of the workflow file** passes (catches syntax errors before push).
2. **`bash tests/container/run.sh` succeeds locally** against a real Adamant container (for container.yml; gate.yml's acceptance is its four gate commands green locally).
3. **The PR description includes the local run output** of step 2 (a short paste, not the full log) -- so the reviewer sees the rehearsal happened.

### Per-Job Acceptance

Each new job:

1. Has a `name:` that maps to the spec section that motivates it.
2. Has a comment naming which CI<n> requirement it implements.
3. Carries a `timeout-minutes:` at least 2x the median wall time observed during rehearsal.

---

## Constraints and Assumptions

> The wall-time and upstream constraints below concern Tier 3 and `upstream.yml`, both deferred (see [Implementation Order](#implementation-order)); they are preserved for that planning. The initial `gate.yml` implementation runs the four-command gate, whose wall time is the ordinary local gate's.

### Wall-time reality

Tier-3 wall-time estimates need explicit caveats. ARCHITECTURE.md §Environment Activation warns that first-run activate "can take many minutes (pip installs, alr builds, gprbuild of the Pico runtime)." Cloud CI inherits this fully:

- **Cloud GHA runners are fresh per job.** The activate snapshot at `/tmp/admt/<project>/` does not persist across runs unless cached explicitly (see [Roadmap §Medium-term](#medium-term)).
- **Image pull on cold cache** is ~2-3 minutes for an Adamant image of typical size; warm with `actions/cache@v5` it's ~10-30 seconds.
- **Adamant first-run activate** is the dominant cost. Steady-state on cloud is 5-10 minutes per run until snapshot caching lands.
- **Tier 3 test execution itself** (admt commands against the running container) is 2-5 minutes for the full command suite.

Sum: realistic tier-3 wall time is 8-15 minutes per run on cloud GHA. Locally with image and snapshot reuse, 3-5 minutes is achievable. Appendix B reflects this.

**Action**: every `timeout-minutes` for container-touching jobs is set to 60. Merge SLAs plan around the upper end of these ranges. Treat the activate-snapshot caching optimization as a real deliverable in the medium-term roadmap, not a nice-to-have.

### Upstream assumptions to verify before they ship

A few claims rest on upstream behavior not yet confirmed; verify before shipping the dependent workflow:

- **Adamant container ships OCI labels** (specifically `org.opencontainers.image.revision`). The pin-parity-remote job in upstream.yml depends on this. If the Adamant image is built without those labels, the job fails for the wrong reason ("label missing" rather than "ref mismatch"). Verify with `docker inspect ghcr.io/lasp/adamant:${ADAMANT_TAG}` *before* writing the upstream workflow. If labels are absent, the remote check needs a different mechanism (e.g., maintain a manual map of `_pins.env` values to upstream release notes).
- **`actions/cache@v5` semantics for the activate snapshot are strong enough.** The proposed cache key includes `hash(env/activate, requirements.txt, _pins.env)`. If Adamant's activate has untracked dependencies (e.g., a `setup.sh` it `source`s), the hash misses them and stale snapshots restore. Verify by reading the upstream activate script before relying on the cache.

---

## Roadmap

The initial CI surface is deliberately small: the four-command gate (`gate.yml`). Everything below is real and useful, but it builds on that surface and should not delay it from landing.

### Near-term (within a release or two of the initial CI surface shipping)

- **release.yml** -- PyPI publishing.
- **upstream.yml** -- weekly contract test against `:latest`.
- **arm64-verification job** in release.yml.

### Medium-term

- **Activate-snapshot caching across runs.** Highest-value optimization for tier 3. `actions/cache@v5` keyed on `hash(env/activate, requirements.txt, _pins.env)` saves `/tmp/admt/<project>/env_snapshot.sh` and `exec.sh` between runs. On a cache hit, admt skips the 5-10-minute first-run activate and uses the snapshot directly. Expected wall-time reduction: 50-60% on the steady-state run, larger when the runner is otherwise fresh.
- **Reusable workflow** for the gate command sequence.
- **Container-test parallelization** -- shard tier 3 by file across 2-3 jobs once it has 50+ tests.
- **Self-hosted GHCR mirror** for the Adamant image so cold tier-3 runs are sub-30s on the pull side.

### Long-term

- **Windows CI** when the spec adds Windows as a target platform.
- **Plugin-author CI template** -- a reusable `gate.yml` workflow plus the tier-3 fixture pattern, for plugins that register `Command` subclasses via entry points ([ARCHITECTURE.md §Plugin System](ARCHITECTURE.md#plugin-system-roadmap)).
- **Schema-based contract tests** for tier 3 once `admt validate` lands.

---

## Appendix A: Trigger and Permission Matrix

| Workflow | `push:main` | `pull_request:open/sync/reopen` | `pull_request:ready_for_review` | `release:published` | `schedule` | `workflow_dispatch` | Permissions |
|---|---|---|---|---|---|---|---|
| `gate.yml` | run | run | -- | -- | -- | run | `contents:read` |
| `container.yml` | run | run if non-draft | run | -- | -- | run | `contents:read`, `packages:read` |
| `release.yml` | -- | -- | -- | run | -- | run | `contents:write`, `id-token:write`, `attestations:write` |
| `upstream.yml` | -- | -- | -- | -- | run (weekly) | run | `contents:read`, `packages:read`, `issues:write` |

`push:non-main` triggers nothing; CI fires when the branch is associated with a PR (the `pull_request` triggers handle that).

---

## Appendix B: Job Reference

These are *order-of-magnitude* estimates, not commitments. Wall times are dominated by uncached external work (`uv sync`, image pull, Adamant `env/activate`). See [Constraints and Assumptions](#constraints-and-assumptions) for the assumptions baked into each row.

| Job | Workflow | OS | Wall Time (steady) | Wall Time (cold) |
|---|---|---|---|---|
| `gate (ubuntu-24.04)` | gate.yml | linux | ~90s -- 2m | ~3m |
| `gate (macos-14)` | gate.yml | macos | ~2m -- 3m | ~4m |
| `container (standalone)` | container.yml | linux | **~8m -- 12m** | **~12m -- 18m** |
| `gate-release (ubuntu-24.04)` | release.yml | linux | ~90s -- 2m | ~3m |
| `gate-release (macos-14)` | release.yml | macos | ~2m -- 3m | ~4m |
| `container-release` | release.yml | linux | ~8m -- 12m | ~12m -- 18m |
| `build-wheel` | release.yml | linux | ~30s | ~1m |
| `publish-pypi` | release.yml | linux | ~10s | ~30s |
| `arm64-verification` | release.yml | linux+QEMU | ~15m -- 25m | ~25m -- 35m |
| `upstream-tier3` | upstream.yml | linux | ~8m -- 12m | ~12m -- 18m |

"Cold" = first run with no cache (no uv cache, no Adamant image cache). "Steady" = a typical PR after caches are warm. **The container-touching jobs are slow.** Cloud GHA runners are fresh per job, so Adamant's `env/activate` (which can take 5-10 minutes for first-run alr/gprbuild work) re-runs every time -- caching the activate snapshot across runs is a medium-term optimization (see [Roadmap](#medium-term)). Plan branch protections and review SLAs around the upper end of these ranges, not the lower.
