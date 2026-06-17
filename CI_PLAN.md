# admt CI Plan

This document specifies how admt's continuous-integration pipeline implements the testing strategy from [TEST_PLAN.md](TEST_PLAN.md), the architectural guarantees from [ARCHITECTURE.md](ARCHITECTURE.md), and the authoring rules from [CODING_RULES.md](CODING_RULES.md). It is a planning spec, not a runbook -- the runbook recipes for [`act`](https://github.com/nektos/act) live further down because the rehearsal story is a first-class concern of the design.

admt has no CI today. The local four-command gate ([TEST_PLAN.md §Quality Gate](TEST_PLAN.md#quality-gate)) is the only gate, and it has held: full line + branch coverage, ruff/mypy clean. CI exists to close the gaps the local gate cannot solve: spec-vs-implementation drift, drift between code and CI itself, lockfile churn, and the merge-as-test problem where independently-green PRs are not validated as a coherent whole until after they land.

This plan is the spec for the CI implementation that follows. Like every admt spec doc, code that does not trace to this document is rejected; behavior that diverges from this document is a defect to either fix in the workflow or amend here first -- not both, and not silently.

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
- [act Rehearsal Protocol](#act-rehearsal-protocol)
- [Tier 3 Fixture Strategy](#tier-3-fixture-strategy)
- [Per-Command Coverage Matrix](#per-command-coverage-matrix)
- [Architectural Self-Audit](#architectural-self-audit)
- [Artifacts and Provenance](#artifacts-and-provenance)
- [Toolchain Pinning](#toolchain-pinning)
- [Drift-Prevention Guards](#drift-prevention-guards)
- [Implementation Order](#implementation-order)
- [Test Plan for the Workflows Themselves](#test-plan-for-the-workflows-themselves)
- [Plugin Convention (Forward-Looking)](#plugin-convention-forward-looking)
- [Risks, Tradeoffs, and Honest Estimates](#risks-tradeoffs-and-honest-estimates)
- [Roadmap](#roadmap)
- [Appendix A: Trigger and Permission Matrix](#appendix-a-trigger-and-permission-matrix)
- [Appendix B: Job Reference](#appendix-b-job-reference)
- [Appendix C: Command Inventory](#appendix-c-command-inventory)
- [Appendix D: act Capability Matrix (Verified)](#appendix-d-act-capability-matrix-verified)
- [Appendix E: Glossary](#appendix-e-glossary)

---

## Why CI Now

The project retrospectives named four problems the local gate is structurally unable to solve. Each one is a specific failure mode CI is designed to catch:

1. **Spec-vs-implementation drift is invisible until tier 3 runs.** Tier 1+2 tests pin on exception classes; the user-facing exit code is observable only by running the real binary. Several exit-code mismatches stayed latent for the whole pre-CI period because no tier-1 test pinned on the user-facing exit code, and no tier-3 suite existed to catch the discrepancy at the binary boundary. CI is where tier 3 finally runs continuously.

2. **Single-day batch-merge made the merge itself the test.** Multiple PRs landed in the same window, each independently green, but the post-merge state on `main` was not validated as a single coherent run before the merges. CI on every push to `main` provides exactly that validation -- a clean checkout of the merged tip, the four-command gate, and the tier-3 sweep, with no developer-machine state in the picture.

3. **`uv.lock` churn slips into unrelated PRs.** Mechanical lockfile rewrites from uv-version drift have polluted the history more than once. The `uv.lock` policy paragraph is in [CODING_RULES.md §uv.lock policy](CODING_RULES.md#uvlock-policy); CI is where the policy gets *enforced*, by pinning the `uv` version that runs the gate so the reference rewrite is deterministic.

4. **The act + Docker-Desktop interaction is non-obvious.** This CI work must be act-rehearsable locally. That is not free: act under Docker Desktop refuses to bind-mount the desktop socket into the runner unless File Sharing is configured, and the same docker-compose paths that work on cloud GHA need a specific fixture layout to work under act's `--bind` mode. The recipes -- and the boundary between "this works locally" and "this only works on cloud GHA" -- belong in this document, not in tribal knowledge.

This list is also the test plan for whether CI is doing its job. If a future drift slips through CI without being caught here, that is a CI gap, not a development gap.

---

## Scope and Non-Goals

### In Scope

- **The four-command quality gate**, run on every push and every pull request, on Linux and macOS. The gate is the same gate developers run locally; CI is the second eye, not a different bar.
- **Tier 3 container tests**, run on every non-draft pull request and every push to `main`. Tier 3 is the spec-conformance backstop named in [TEST_PLAN.md §Tier 3](TEST_PLAN.md#tier-3-container-tests).
- **Per-command, per-flag, per-alias coverage** -- every concrete `Command` subclass, every short alias (`e`, `b`, `t`, `s`, `an`, `cl`, `p`, `cov`, `pub`, `w`, `tmpl`), and every global flag and env variable in [TEST_PLAN.md §What to Test](TEST_PLAN.md#what-to-test) is exercised at the appropriate tier.
- **Architectural self-audit** -- a parametrized test that walks every `Command`, `Service`, and `Adapter` module and asserts test-file coverage exists at the right tier for each. The audit fails when a contributor adds a new command without writing tests for it.
- **Local rehearsal via `act` where possible**, with documented recipes covering both native Docker on Linux and Docker Desktop on macOS or Linux. Where act cannot rehearse a workflow (verified empirically; see [Appendix D](#appendix-d-act-capability-matrix-verified)), the workflow's logic is exposed as a host script (`tests/container/run.sh`) so CI and local share the same entry point even when act is unavailable.
- **Toolchain pinning** for `uv`, the Python interpreter, the Adamant container image, and any GitHub Actions third-party action versions.
- **Failure forensics** -- every failure produces an artifact bundle with provenance metadata (commit SHA, run ID, branch, OS) and human-navigable HTML reports.

### Roadmap (in scope to *describe* here, not to land in the initial CI surface)

- **PyPI publishing on release** -- `uv build`, `uv publish`, attestations.
- **ARM64 verification on release** -- echo the Adamant ecosystem pattern (`test_all_arm64.yml`).
- **Upstream contract tests** -- a weekly schedule that re-runs tier 3 against the latest `ghcr.io/lasp/adamant:*` image so we notice when an Adamant change breaks our integration.
- **Status badges** in `README.md` (gate, container, release, upstream).
- **Codecov** or equivalent coverage trend dashboard.
- **Plugin-author CI template** -- the same self-audit + gate definition packaged for plugin authors to reuse.

### Non-Goals

- **No relaxation of the local gate.** CI does not enforce a *different* bar than the one developers see locally; it enforces the *same* bar from a clean machine. A change that passes CI but fails the local gate is broken.
- **No hidden CI-only commands.** Anything CI runs is either (a) one of the four gate commands, (b) `pytest -m container` for tier 3, (c) a documented packaging command. No bespoke "CI thinks the gate is X" pseudo-checks.
- **No new top-level directory.** Workflow files live in `.github/workflows/`. CI helper assets live in `tests/ci_assets/`. There is no `ci/` or `scripts/` top-level directory.
- **No Windows runners.** Per [CODING_RULES.md §Language and Runtime](CODING_RULES.md#language-and-runtime), Windows is not yet a target. CI matches the spec.

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

The `-m "not container"` clause is the only deviation: tier 3 tests are marked `@pytest.mark.container` and run in a separate workflow ([CI3](#ci3-tier-3-runs-on-every-non-draft-pr)). The drift-prevention test in [CI9](#ci9-drift-prevention-guards) verifies the rest of the command string matches `TEST_PLAN.md` byte-for-byte.

### CI2. The Coverage Threshold Is Hard

100% line + branch on every gate run. If a future change drops coverage to 99.9%, the gate fails. Enforced by `pytest --cov-fail-under=100` and by `[tool.coverage.report] fail_under = 100` in `pyproject.toml`. CI does not loosen the threshold; CI does not split the threshold across "important" and "less important" modules; CI does not exclude lines via `pragma: no cover` without the rare-and-justified rationale documented in TEST_PLAN.md.

### CI3. Tier 3 Runs on Every Non-Draft PR

Tier 3 catches the spec-vs-implementation drift the local gate cannot. It must run before merge, not as a post-merge afterthought. The `pull_request` trigger filters with `types: [opened, synchronize, reopened, ready_for_review]` and a job-level `if: ${{ github.event.pull_request.draft == false }}` so draft PRs are exempt. It also runs on every push to `main` so the post-merge state is validated coherently.

### CI4. Every Command, Every Flag, Every Alias

Tier 3 exercises every concrete `Command` subclass, every short alias, and every global flag listed in [TEST_PLAN.md §What to Test](TEST_PLAN.md#what-to-test). The mapping is enforced by the [architectural self-audit](#architectural-self-audit), not by hand-maintained tables. A new command added to `src/admt/commands/` without a tier-3 test fails the gate; a new alias added to `cli.py` without a tier-3 test fails the gate.

### CI5. act-Rehearsable Where Possible

Every workflow file declares whether it is act-rehearsable, and if so, under which Docker host environment. The recipes are validated empirically (see [Appendix D](#appendix-d-act-capability-matrix-verified)), not assumed. Where act cannot rehearse a workflow's full intent, the workflow's logic is exposed as a host script in `tests/container/run.sh` (or equivalent) so a developer can rehearse the *test logic* locally even when the *workflow yaml itself* is not runnable. CI invokes the same host script, so the entry point is shared between cloud and local.

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

### CI9. Drift-Prevention Guards

CI cannot become a separate spec. A handful of small unit tests in `tests/unit/test_ci_alignment.py` keep the workflow YAML in sync with this plan and TEST_PLAN.md. The tests run in tier 1 (no Docker required) and protect the spec/CI alignment. See [Drift-Prevention Guards](#drift-prevention-guards).

### CI10. Spec Traceability

Every step in every workflow file ties back to a section of ARCHITECTURE / CODING_RULES / TEST_PLAN / this document. Steps with no spec home are rejected at review (per [CODING_RULES.md §What to Review Per PR](CODING_RULES.md#what-to-review-per-pr)). The workflow YAML carries a top-of-file comment naming the spec sections it implements.

---

## Workflow Architecture

Four workflow files, each with one purpose. A change to one workflow does not require changes to the others; a failure in one does not gate the others. The split mirrors the project's design culture: each file does one thing, the file-size guideline (~300 lines) applies per-workflow.

```
.github/
  workflows/
    gate.yml          # Tier 1+2: ruff format, ruff check, mypy, pytest -m "not container"
    container.yml     # Tier 3: pytest -m container, against the pinned Adamant image
    release.yml       # Roadmap: build wheel + publish to PyPI on release
    upstream.yml      # Roadmap: weekly tier 3 against latest Adamant image
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

Every workflow uses the predictable-shell convention from `fp32-fsw-xmera`:

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

The gate runs on drafts too (a draft with broken style/types/coverage should still be visible). Tier 3 is the one that waits for `ready_for_review`.

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
9. **Surface per-test results** (always) -- `mikepenz/action-junit-report` posts the JUnit XML as a check-run (see [Artifacts and Provenance](#artifacts-and-provenance)); skipped under act.
10. **Upload artifact bundle** (always) -- `actions/upload-artifact@v4` with everything in `_artifacts/` (coverage HTML, coverage XML, JUnit XML, log tail). Name: `gate-${{ matrix.os }}-${{ github.sha }}`.
11. **Post step summary** (always) -- a tabular summary on `$GITHUB_STEP_SUMMARY` with gate command results, coverage %, and a link to the artifact.

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

### act Compatibility

`act -j gate` runs this workflow locally on Linux. The macOS leg cannot be rehearsed under act (act runs Linux containers). All steps are act-compatible because the gate does not need a Docker daemon. See [Recipe: gate](#recipe-gate).

The docker-free property is load-bearing and deliberate: tier 1+2 inject a fake compose resolver, so the only places that shell `docker compose config` are `env init`/`env refresh` at runtime (and therefore tier 3). A unit or integration test that invokes the real resolver would silently make the gate require a docker CLI -- treat that as a defect, not a dependency to install on the runner.

---

## Workflow: container.yml

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

The job logic is *not* inlined into the workflow YAML; it is implemented as a host script (`tests/container/run.sh`) that the workflow invokes. This is deliberate (per [CI5](#ci5-act-rehearsable-where-possible)): the same script runs locally when act cannot rehearse the workflow YAML directly, so CI and local share the entry point.

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

### act Compatibility

`act pull_request -j container --bind` is the rehearsal recipe. It works under native Docker on Linux out of the box. Under Docker Desktop, the rehearsal requires either (a) native dockerd also running, or (b) running the host script directly without act (`bash tests/container/run.sh`). Verified empirically -- see [Appendix D](#appendix-d-act-capability-matrix-verified) and [Recipe: container](#recipe-container).

---

## Workflow: release.yml (Roadmap)

Build the admt wheel, publish it to PyPI, and verify ARM64 on release. Implements *after* gate.yml and container.yml are stable on `main`.

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
3. **build-wheel** -- `uv build` on `ubuntu-24.04`. Produces `dist/admt-X.Y.Z-py3-none-any.whl` and `dist/admt-X.Y.Z.tar.gz`. Uploads both as artifacts.
4. **publish-pypi** -- `pypa/gh-action-pypi-publish@release/v1` with trusted publishing (no API token in secrets). `needs: [gate-release, container-release, build-wheel]`. Skipped under act (`if: ${{ !env.ACT }}`).
5. **arm64-verification** -- `docker/setup-qemu-action@v4` + `linux/arm64` execution of the wheel against `ghcr.io/lasp/adamant:${ADAMANT_TAG}-arm64`. Echoes `adamant/.github/workflows/test_all_arm64.yml`. Advisory-only for the first published release; required-blocking once the first arm64 admt user emerges.

### act Compatibility

Build-wheel runs under act. publish-pypi is skipped (`!env.ACT`). gate-release and container-release follow the same recipes as gate.yml and container.yml.

---

## Workflow: upstream.yml (Roadmap)

Weekly verification that admt still works against the latest Adamant container.

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
2. **pin-parity-remote** -- pulls `ghcr.io/lasp/adamant:${ADAMANT_TAG}` (the *pinned* tag, not `:latest`), reads the OCI image label `org.opencontainers.image.revision`, and asserts it matches `ADAMANT_REF` from `tests/container/_pins.env`. This is the *remote* arm of the [Version-Pin Parity Audit](#version-pin-parity-audit): the local audit can only verify that the consumer files reference `_pins.env` correctly; only the remote check answers "does the upstream container at this tag actually correspond to this source ref?". Drift opens an issue titled `Adamant pin drift: <tag> ≠ <ref>` with the OCI label and the local `_pins.env` snapshot; the fix is to bump `_pins.env` (or, rarely, to re-tag upstream).

The first job uses `:latest` deliberately to detect format-level contract drift. The rest of CI uses pinned tags ([CI6](#ci6-toolchain-pinning)); this workflow's job is to *break* when upstream moves.

### act Compatibility

Cron-only on cloud GHA. `workflow_dispatch` works under act with the same Docker socket recipes as container.yml.

---

## act Rehearsal Protocol

`act` is the local-rehearsal contract. Recipes below were validated against this user's environment (active Docker context: `desktop-linux`; native dockerd also running at `/var/run/docker.sock`). The act behaviors named here are inspected from `nektos/act` source plus end-to-end probe runs; see [Appendix D](#appendix-d-act-capability-matrix-verified).

### Why Both Forms?

act behaves differently on each host because *Docker* behaves differently on each host:

- **Native Docker on Linux**: socket at `/var/run/docker.sock`. act binds it transparently. Containers spawned from inside the runner are siblings of the runner (host-daemon namespace).
- **Docker Desktop on Linux/macOS/Windows**: the daemon runs in a VM. act discovers the socket at `~/.docker/run/docker.sock` or via `docker context inspect`. Docker Desktop refuses to bind-mount its own socket into act-spawned runners unless that path is in its File Sharing config -- which is GUI-only on macOS and not present in the Linux Desktop UI by default.

The recipes below cover both. A developer who runs both Docker Desktop and native Docker side-by-side (as the user of this plan does) can pick the form that matches their daemon-of-the-moment.

### Prerequisites

- `act` 0.2.86+ installed (`pacman -S act` on Manjaro, `brew install act` on macOS, or built from source). Versions before 0.2.86 carry CVEs.
- A user-level `~/.config/act/actrc` mapping the runner platform:

  ```
  -P ubuntu-latest=catthehacker/ubuntu:act-latest
  -P ubuntu-24.04=catthehacker/ubuntu:act-22.04
  ```

  `catthehacker/ubuntu:act-latest` is the medium-sized image; it covers Python and the build toolchain and ships with `docker` CLI installed (verified). The full image is overkill; the slim image is missing Python.
- For tier 3 rehearsal: a working Docker daemon. Either native (`/var/run/docker.sock`) or Docker Desktop (any path; recipes below).

Per-invocation flags belong on the command line, not in `actrc`. Earlier hardcoded socket flags in the actrc broke things; the lesson is documented in the user's actrc as a self-warning.

### Recipe: gate

The gate workflow does not need a Docker daemon. The recipe works under both native Docker and Docker Desktop:

```bash
DOCKER_HOST="$(docker context inspect --format '{{.Endpoints.docker.Host}}')" \
  act -j gate -W .github/workflows/gate.yml --container-daemon-socket -
```

- `DOCKER_HOST` exports the active Docker context's endpoint so act itself can pull and start its runner image.
- `--container-daemon-socket -` (the literal dash) tells act *not* to bind a socket into the runner, sidestepping the Docker Desktop File Sharing wall.

Single-OS rehearsal is sufficient for local validation; the macOS leg of the matrix can only be exercised on actual macOS, and not via act.

Expected wall time: ~3 minutes for a cold `setup-uv` install, ~90 seconds with the uv cache warmed.

### Recipe: container

Tier 3 needs the runner to talk to a Docker daemon. Use the form that matches your daemon-of-the-moment:

**Native Docker on Linux** (preferred for rehearsal stability):

```bash
DOCKER_HOST="unix:///var/run/docker.sock" \
  act pull_request -j container -W .github/workflows/container.yml --bind
```

- `DOCKER_HOST` overrides act's auto-discovery to point at the native socket. Required when the active context is `desktop-linux` but native dockerd is also running.
- `--bind` (`-b`) bind-mounts the workspace at the same absolute path inside the runner -- *required* so the relative paths in the Adamant `docker-compose.yml` resolve identically on both sides of the runner boundary. Without `--bind`, the workspace is copied into a Docker volume and host paths become unmappable.
- `pull_request` triggers the right event so the `if: ${{ ... draft == false }}` job-level guard evaluates true.

**Docker Desktop with File Sharing configured** (works only after one-time setup):

1. Open Docker Desktop -> Settings -> Resources -> File Sharing.
2. Add `~/.docker/desktop/docker.sock`. Apply.
3. Run:

```bash
DOCKER_HOST="$(docker context inspect --format '{{.Endpoints.docker.Host}}')" \
  act pull_request -j container -W .github/workflows/container.yml --bind
```

This is fragile (Docker Desktop sometimes resets sharing config on upgrade). Prefer the native form when possible.

**Pure Docker Desktop without File Sharing**: act cannot rehearse the workflow yaml. Use the host script instead:

```bash
bash tests/container/run.sh
```

This runs the same logic the workflow runs -- pull image, bootstrap fixtures, `pytest -m container` -- but directly on the host's Docker daemon, without act in between. CI invokes the same script (per [CI5](#ci5-act-rehearsable-where-possible)), so this is a first-class entry point, not a fallback.

Expected wall time: realistic figures are higher than the gate. See [Appendix B](#appendix-b-job-reference) and [Risks, Tradeoffs, and Honest Estimates](#risks-tradeoffs-and-honest-estimates) -- the dominant cost on cloud CI is Adamant's first-run `env/activate`, which runs fresh in every fresh runner. Plan for 8-15 minutes per cloud run; locally with image cache and snapshot reuse, 3-5 minutes is achievable.

### Recipe: container against a *live* local Adamant

A common development scenario: the developer is iterating on admt *and* on a local Adamant working tree (often on a feature branch with uncommitted changes). They need to test admt against that live state, not against the fixture's pinned ref. The `ADMT_LOCAL_ADAMANT` env var enables this; under act, it requires one extra flag.

Because act's `--bind` only mounts the admt workspace, paths outside it are not visible inside the runner by default. To expose the live Adamant tree at the same absolute path on both sides, pass it explicitly via `--container-options`:

```bash
ADMT_LOCAL_ADAMANT=/path/to/your/adamant \
DOCKER_HOST="unix:///var/run/docker.sock" \
  act pull_request -j container -W .github/workflows/container.yml --bind \
  --container-options "-v $ADMT_LOCAL_ADAMANT:$ADMT_LOCAL_ADAMANT"
```

What happens inside:

1. `tests/container/run.sh` sees `ADMT_LOCAL_ADAMANT` set and creates a symlink `tests/container/_workspace/adamant -> $ADMT_LOCAL_ADAMANT` instead of cloning.
2. The symlink lives in the bind-mounted admt workspace, so it appears at the same path on host and runner.
3. The bind-mount from `--container-options` makes the symlink target also valid on both sides.
4. `admt env init` resolves the symlink and derives the *live* compose configuration via `docker compose config` (loading any colocated `.env`), registering volume mounts that point at the live host path.
5. `admt env start` tells the host Docker daemon to bind the live path into the Adamant container -- the daemon resolves the path on the host, where it really exists.
6. Tests exercise admt against the developer's actual in-progress Adamant changes.

**Caveats:**

- The mount is read-write, not read-only -- a real Adamant build mutates `build/` directories under the source tree. The developer's live working tree will accumulate `build/` artifacts during the test run. This is intentional (matches what would happen if they ran `redo` directly), but the developer should `git status` before and after to avoid surprises.
- Permissions matter. The runner image's default user (`ubuntu` at UID 1001 in `catthehacker/ubuntu:act-latest`) may not match the host's UID. If the live Adamant tree is not world-readable and writable, append `--user $(id -u):$(id -g)` to `--container-options` so the runner inherits the host UID.
- The pin audit ([Version-Pin Parity Audit](#version-pin-parity-audit)) is unaffected: it checks `_pins.env` against consumer files, not against the actual fixture contents. Live mode does not violate any pin.

For day-to-day "I'm changing admt and want to verify against my live Adamant" iteration, the simpler form is to skip act entirely:

```bash
ADMT_LOCAL_ADAMANT=/path/to/your/adamant bash tests/container/run.sh
```

This is what `tests/container/run.sh` is for ([CI5](#ci5-act-rehearsable-where-possible)) -- it always works, it doesn't need act flags, and it shares the same entry point CI uses. The act form above is for verifying the *workflow YAML itself* still drives the right behavior under live-Adamant conditions.

### Recipe: container against the *latest* upstream Adamant (`adamant:main`)

The complement of the live-local mode: pin-overriding to test admt against current upstream `main` without bumping `_pins.env`. No special act flags -- the ref is overridden via env var:

```bash
ADAMANT_REF=main \
DOCKER_HOST="unix:///var/run/docker.sock" \
  act pull_request -j container -W .github/workflows/container.yml --bind
```

`tests/container/run.sh` honors `ADAMANT_REF` over the `_pins.env` value. The clone is fresh inside the workspace, isolated from the developer's other working trees. This is the recommended way to spot-check whether an admt change works against current upstream Adamant before bumping the pin.

The two override modes (`ADMT_LOCAL_ADAMANT` and `ADAMANT_REF`) are mutually exclusive: if both are set, `ADMT_LOCAL_ADAMANT` wins (the symlink path is taken before the clone path is considered). Document both in `tests/CI.md` so the runbook tells developers which mode answers which question.

### Recipe: list, validate, dryrun

Pre-flight commands that work the same way regardless of socket recipe:

```bash
act -l                                         # list all jobs
act --validate -W .github/workflows/gate.yml   # strict schema check
act -n -j gate                                 # dryrun -- validate the execution plan
```

These do not require a Docker socket bind and so work uniformly under any Docker host.

### What act Cannot Rehearse

Documented limits (none of these are bugs in act -- they are first-principles limits of local rehearsal):

- **The macOS leg of the matrix.** act runs Linux containers; macOS jobs are skipped.
- **PyPI publishing** (release.yml). Trusted publishing requires GitHub's OIDC token, which act cannot mint. The job is gated by `if: ${{ !env.ACT }}`.
- **Cross-architecture verification** (release.yml's arm64-verification). `docker/setup-qemu-action@v4` is theoretically possible under act but extremely slow; documented as cloud-only.
- **`secrets.GITHUB_TOKEN`-bound steps** that talk to the GitHub API (e.g., upstream.yml's issue creation). act sets a placeholder token; the API calls fail. Use `--secret GITHUB_TOKEN=<a real PAT>` if you need to test locally; otherwise the step is gated on `!env.ACT`.
- **`actions/cache@v5`** writes are no-ops under act (act has its own cache server but doesn't persist across `act` invocations the way GitHub's does). Reads succeed but always miss; warm-cache rehearsal is not meaningful locally.

When a developer adds a step that won't run under act, they add a one-line `# act: skip <reason>` or `# act: ok` comment so future readers know whether the skip is by design.

### Alternative act Methods (Tradeoff Space)

The recipes above use `--bind` + automatic socket discovery. That is one point on a tradeoff curve, not the only method. When the default recipe doesn't fit, these alternatives are worth investigating:

- **`--privileged` + DinD runner image** (e.g., `catthehacker/ubuntu:full-latest`). Runs a nested Docker daemon inside the act runner; containers spawned by the workflow are isolated from the host's docker namespace. Heavier setup, more isolation, no host-socket-binding required. Whether the workspace path resolution still works (the bind mount is host-runner; the nested dockerd's mount source is the runner's filesystem) needs empirical verification before committing.

- **`--reuse` (`-r`)**. Keeps the runner container alive across `act` invocations. Preserves `/tmp/admt/<project>/` snapshot between runs, eliminating the cold-activate cost on subsequent local iterations. Significant local-iteration win when iterating on a single command's behavior; the next-run startup drops from minutes to seconds.

- **Pre-built runner image with admt + Adamant baked in.** A custom image extending `catthehacker/ubuntu:act-latest` with admt installed and an Adamant clone (and possibly a pre-activated env snapshot) baked in. Eliminates the pull and activate cost entirely. Maintenance burden in exchange for runtime; appropriate once the team is iterating on tier 3 frequently.

- **`catthehacker/ubuntu:full-latest`** vs `act-latest` vs `latest`. Size/feature tradeoff. `act-latest` (medium, default) covers Python and basic build tools. `full-latest` adds language runtimes admt doesn't need but ships the docker CLI plus more dev tools. `latest` is the slim image; missing Python, breaks the gate immediately.

- **`--artifact-server-path`**. Captures the artifacts each upload-artifact step writes during local rehearsal. Without this flag, the upload-artifact step fails with `Unable to get the ACTIONS_RUNTIME_TOKEN env variable`. Recipe: `act -j gate --artifact-server-path /tmp/act-artifacts -W .github/workflows/gate.yml`. After the run, `/tmp/act-artifacts/<run-id>/<artifact-name>/` contains the same artifact zip the cloud upload would produce.

- **`~/.actrc` merge order**. act reads three actrc files and merges them: XDG (`~/.config/act/actrc`) -> `~/.actrc` -> `./.actrc`. A `--container-daemon-socket` line in any of the three silently overrides per-invocation defaults. When debugging a "why does my command-line flag not take effect" mystery, audit all three locations.

- **`--container-options`** for arbitrary docker-create flags. Already used in [Recipe: container against a *live* local Adamant](#recipe-container-against-a-live-local-adamant) for the volume-mount form. Also accepts `--user $(id -u):$(id -g)` for runner-UID parity, `--tmpfs /tmp` for ephemeral test scratch space, and other docker-run flags as needed.

The first PR sticks with the documented `--bind` recipe. Implementation (or a follow-up) explores these alternatives empirically and updates this section with the verified tradeoffs.

---

## Tier 3 Fixture Strategy

Tier 3 needs a real Adamant project to exercise admt against. Two constraints shape the fixture:

1. **act's `--bind` only mounts the workspace root at the same path in the runner.** Sibling directories outside the workspace are invisible from inside the runner (verified empirically). Therefore the Adamant fixture must live *inside* the admt working tree.
2. **The Adamant `docker-compose.yml` uses relative paths** (e.g., `source: ../../adamant`). For docker-compose to find the right host paths whether running locally, on cloud GHA, or via act, the fixture layout must match the relative-path convention.

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
- Permissions matter when running under act with a runner UID that differs from the host UID. See [Recipe: container against a *live* local Adamant](#recipe-container-against-a-live-local-adamant) for the `--user $(id -u):$(id -g)` flag.

#### Pin-Override-Mode Caveats

Pin-override mode (e.g., `ADAMANT_REF=main`) clones a *different* Adamant source than the container at `:${ADAMANT_TAG}` was built from. A test that depends on source-vs-binary parity (e.g., expects a specific `redo what` output that matches a specific source revision) may behave inconsistently. The expected behavior for this mode is the same as for the upstream contract test job ([upstream.yml](#workflow-upstreamyml-roadmap)): tier 3 is allowed to fail, and the failure is the signal that upstream has moved.

### conftest.py Responsibilities

`tests/container/conftest.py` exposes session-scoped fixtures that:

- Point admt at a scratch config home for the whole session (a temp `HOME`), so the suite's registrations and per-terminal session state never touch the developer's real `~/.admt` during local rehearsal -- `config.yml` and `sessions.yml` are both live state now, and polluting them from a test run is not acceptable.
- Locate the Adamant clone (see [Project Resolution Order](#project-resolution-order) below for the four-mode lookup).
- Register the project: `subprocess.run(["admt", "env", "init", <path>])`. Registration shells `docker compose config`, so the docker CLI must be present -- a given on the tier-3 runner, and verified for the act runner image (see [Appendix D](#appendix-d-act-capability-matrix-verified)).
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

Standalone Adamant is the default tier-3 fixture configuration. A `multi-repo` configuration is the planned second matrix leg: it tests admt against a layout that mounts more than one repo (e.g., adamant + a stub component repo). The multi-repo fixture clones two repos into `_workspace/` and ships a hand-written compose file that mounts both. Path-mapping bugs that only manifest with multiple bind mounts are caught here.

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

The 24 concrete commands (see [Appendix C](#appendix-c-command-inventory)) split into two categories:

- **3 pure-host commands** (`env init`, `env use`, `env list`) -- no *running* container required. `env init` *creates* the project and shells `docker compose config` to derive its resolved metadata (docker CLI required; daemon not). `env use` writes the active selection -- this terminal's session entry plus the global default for new terminals. `env list` reads config and, when a controlling terminal exists, pins the resolution its `*` reports. Tier 3 coverage: a single happy-path test that the binary works against `tests/container/_workspace/adamant/`, plus [Worktree Configuration Coverage](#worktree-configuration-coverage).
- **21 project + container commands** -- everything else. Tier 3 coverage: each gets one happy-path test plus the full failure-path matrix where applicable to the command.

### Per-Alias Coverage

11 aliases in `cli.py` (`e`, `b`, `t`, `s`, `an`, `cl`, `p`, `cov`, `pub`, `w`, `tmpl`). Tier 3 confirms each invokes the right command with one minimal test per alias. The alias inventory is enumerated by reading `cli.py`'s `add_alias` calls -- the [self-audit](#architectural-self-audit) catches any alias that has no corresponding test.

### How the Matrix Stays Honest

Hand-maintained tables drift. The matrix is enforced by parametrized tests in `tests/unit/test_command_test_coverage.py` that walk the actual `Command` subclasses, `cli.py` aliases, and the Click option tree at runtime. A new command, alias, global flag, subcommand flag, or env-var flag added without the corresponding tier-2/tier-3 test fails the gate immediately -- discovery is dynamic, no hand-edited list to forget. See [Architectural Self-Audit](#architectural-self-audit).

---

## Architectural Self-Audit

admt's architecture is predictable enough -- one class per command, fixed directory layout, declarative metadata on each command -- that "is this code tested?" can be answered by walking the class hierarchy and grepping the test tree, not by maintaining a separate matrix.

A new test module, `tests/unit/test_command_test_coverage.py`, runs as part of the gate ([CI1](#ci1-same-gate-from-a-clean-machine)). It is parametrized across every concrete `Command`, `Service`, and `Adapter` and asserts coverage at the right tier.

### Sketch

```python
"""Architectural-coverage self-audit -- every command/service/adapter has a test."""

from pathlib import Path
import pytest
import importlib, pkgutil

import admt.commands, admt.services, admt.adapters
from admt.commands.base import Command, ContainerPassthroughCommand


def _all_concrete_command_subclasses() -> list[type[Command]]:
    """Same helper used by tests/unit/test_architecture.py."""
    for info in pkgutil.walk_packages(admt.commands.__path__, prefix="admt.commands."):
        importlib.import_module(info.name)
    result = []
    stack = list(Command.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls is ContainerPassthroughCommand:
            stack.extend(cls.__subclasses__())
            continue
        if not getattr(cls, "__abstractmethods__", set()):
            result.append(cls)
        stack.extend(cls.__subclasses__())
    return result


def _test_dir_contains(directory: Path, needle: str) -> bool:
    """Search every test file under directory for `needle` (substring match)."""
    return any(needle in p.read_text() for p in directory.rglob("test_*.py"))


@pytest.mark.parametrize("cls", _all_concrete_command_subclasses(), ids=lambda c: c.name)
def test_command_has_tier1_test(cls):
    assert _test_dir_contains(Path("tests/unit/commands"), cls.__name__), (
        f"{cls.__name__} ({cls.name!r}) has no tier-1 test in tests/unit/commands/"
    )


@pytest.mark.parametrize("cls", _all_concrete_command_subclasses(), ids=lambda c: c.name)
def test_command_has_tier2_test(cls):
    # Tier 2 references commands by their CLI name, not their Python class name.
    assert _test_dir_contains(Path("tests/integration"), f'"{cls.name.split()[0]}"'), (
        f"{cls.name!r} has no tier-2 test in tests/integration/"
    )


@pytest.mark.parametrize("cls", _all_concrete_command_subclasses(), ids=lambda c: c.name)
def test_command_has_tier3_test(cls):
    if not cls.requires_container and cls.name not in {"env init", "env use", "env list"}:
        pytest.skip("not a container-touching command and not a config-only command")
    assert _test_dir_contains(Path("tests/container"), f'"{cls.name.split()[0]}"'), (
        f"{cls.name!r} has no tier-3 test in tests/container/"
    )


def _aliases_in_cli_py() -> list[tuple[str, str]]:
    """Parse cli.py for `add_alias("alias", "command")` calls."""
    import re
    src = Path("src/admt/cli.py").read_text()
    return re.findall(r'add_alias\(\s*"([^"]+)",\s*"([^"]+)"\s*\)', src)


@pytest.mark.parametrize("alias,target", _aliases_in_cli_py(), ids=lambda v: v[0] if isinstance(v, tuple) else v)
def test_alias_has_tier2_test(alias, target):
    assert _test_dir_contains(Path("tests/integration"), f'"{alias}"'), (
        f"alias {alias!r} -> {target!r} has no tier-2 invocation test"
    )


@pytest.mark.parametrize("alias,target", _aliases_in_cli_py())
def test_alias_has_tier3_test(alias, target):
    assert _test_dir_contains(Path("tests/container"), f'"{alias}"'), (
        f"alias {alias!r} -> {target!r} has no tier-3 invocation test"
    )


# ---- flag coverage ---------------------------------------------------------
# Flags are a first-class user-facing surface. The audit walks every Click
# Option in cli.py (global + subcommand) and every env-var "flag" from
# TEST_PLAN.md §What to Test, and asserts each appears in at least one tier-2
# and one tier-3 test. Discovery is runtime introspection of the Click tree;
# new flags are picked up automatically.

import click as _click
from admt.cli import cli as _cli


def _global_flags() -> list[str]:
    """Long-form names (with leading --) of every global option."""
    return [
        opt
        for p in _cli.params
        if isinstance(p, _click.Option)
        for opt in p.opts
        if opt.startswith("--") and opt != "--version"
    ]


def _subcommand_flags() -> list[tuple[str, str]]:
    """(`command name`, `--flag`) pairs for every subcommand-scoped option."""
    pairs: list[tuple[str, str]] = []

    def _walk(grp: _click.Group, prefix: str = "") -> None:
        for name, cmd in grp.commands.items():
            full = f"{prefix} {name}".strip()
            if isinstance(cmd, _click.Group):
                _walk(cmd, full)
                continue
            for p in cmd.params:
                if not isinstance(p, _click.Option):
                    continue
                for opt in p.opts:
                    if opt.startswith("--"):
                        pairs.append((full, opt))

    _walk(_cli)
    return pairs


# Env-var flags from TEST_PLAN.md §What to Test. The "value-semantics" entry
# for ADMT_NONINTERACTIVE=0 is encoded as a separate row so the audit catches
# that specific test missing even when the on-state test is present.
ENV_VAR_FLAGS: list[str] = [
    "ADMT_NONINTERACTIVE",
    "ADMT_NONINTERACTIVE=0",
    "ADMT_ENV",
    "NO_COLOR",
]


@pytest.mark.parametrize("flag", _global_flags(), ids=lambda f: f)
def test_global_flag_has_tier2_test(flag: str):
    assert _test_dir_contains(Path("tests/integration"), f'"{flag}"'), (
        f"global flag {flag!r} has no tier-2 test in tests/integration/"
    )


@pytest.mark.parametrize("flag", _global_flags(), ids=lambda f: f)
def test_global_flag_has_tier3_test(flag: str):
    assert _test_dir_contains(Path("tests/container"), f'"{flag}"'), (
        f"global flag {flag!r} has no tier-3 test in tests/container/"
    )


@pytest.mark.parametrize(
    "command,flag",
    _subcommand_flags(),
    ids=lambda v: v if isinstance(v, str) else f"{v[0]}{v[1]}",
)
def test_subcommand_flag_has_tier2_test(command: str, flag: str):
    """For each (command, flag) pair, assert the flag appears in a test
    file alongside the command's verb. The matcher is loose -- we don't
    require the test to be *for* this command, only that the flag is
    exercised somewhere at tier 2."""
    assert _test_dir_contains(Path("tests/integration"), flag), (
        f"subcommand flag {command!r} {flag!r} has no tier-2 test"
    )


@pytest.mark.parametrize(
    "command,flag",
    _subcommand_flags(),
    ids=lambda v: v if isinstance(v, str) else f"{v[0]}{v[1]}",
)
def test_subcommand_flag_has_tier3_test(command: str, flag: str):
    assert _test_dir_contains(Path("tests/container"), flag), (
        f"subcommand flag {command!r} {flag!r} has no tier-3 test"
    )


@pytest.mark.parametrize("var", ENV_VAR_FLAGS, ids=lambda v: v)
def test_env_var_flag_has_tier2_test(var: str):
    assert _test_dir_contains(Path("tests/integration"), var), (
        f"env-var flag {var!r} has no tier-2 test"
    )


@pytest.mark.parametrize("var", ENV_VAR_FLAGS, ids=lambda v: v)
def test_env_var_flag_has_tier3_test(var: str):
    assert _test_dir_contains(Path("tests/container"), var), (
        f"env-var flag {var!r} has no tier-3 test"
    )


# Service modules -- each has a test file at tests/unit/services/test_<name>.py
@pytest.mark.parametrize(
    "module_path",
    [p for p in Path("src/admt/services").glob("*.py") if p.stem != "__init__"],
    ids=lambda p: p.stem,
)
def test_service_has_unit_test(module_path):
    expected = Path(f"tests/unit/services/test_{module_path.stem}.py")
    if not expected.exists():
        # Allow split files like test_container_lifecycle.py + test_container_exec.py
        siblings = list(expected.parent.glob(f"test_{module_path.stem}*.py"))
        assert siblings, f"services/{module_path.name} has no test file in tests/unit/services/"


@pytest.mark.parametrize(
    "module_path",
    [p for p in Path("src/admt/adapters").glob("*.py") if p.stem != "__init__"],
    ids=lambda p: p.stem,
)
def test_adapter_has_unit_test(module_path):
    expected = Path(f"tests/unit/adapters/test_{module_path.stem}.py")
    assert expected.exists(), (
        f"adapters/{module_path.name} has no test file at {expected}"
    )
```

### What This Catches

- A new command class added to `commands/` with no tests.
- A new alias added to `cli.py` with no tier-2 or tier-3 invocation.
- A new service module added with no unit-test file.
- A new adapter module added with no unit-test file.
- A `requires_container=True` command added with no tier-3 test (the most common drift).
- **A new flag added to `cli.py` (global or subcommand) with no tier-2 or tier-3 test.** Flag discovery is runtime introspection of the Click tree, so adding `@click.option("--my-flag")` to any command immediately puts a row in the audit; the audit fails until a test mentions the flag at the right tier.
- **A new env-var flag (`ADMT_*`, `NO_COLOR`, etc.) referenced in code without a corresponding test.** The env-var list is hardcoded against TEST_PLAN.md §What to Test; adding a new env-var flag is a two-edit change (the `ENV_VAR_FLAGS` list plus the test) and the audit ensures the test edit isn't forgotten.

### What This Does Not Catch

The audit is a structural check: it confirms a test *exists*, not that the test is *good*. A test that imports the class but asserts nothing meaningful passes the audit. That gap is filled by the 100% line + branch coverage gate ([CI2](#ci2-the-coverage-threshold-is-hard)) -- a no-op test that doesn't actually call the command's `execute()` method leaves coverage holes that the gate fails on. Audit + coverage gate together close the loop.

### Per-Tier Scenario Audit (Follow-Up)

A more aggressive variant walks the [scenarios from TEST_PLAN.md §What to Test](#per-command-coverage-matrix) and asserts each `(command, scenario)` pair has a corresponding test. This requires test functions to be named in a structured way (e.g., `test_<command>_<scenario>`), and is more invasive than the structural audit. Recommended as a follow-up after the structural audit has been in place long enough that contributors are used to the test-naming convention.

### Plugin Compatibility (Forward-Looking)

When the plugin system ([ROADMAP.md](ROADMAP.md) Tier 5) lands, plugin authors register `Command` subclasses via Python entry points. The same self-audit machinery extends naturally:

- The audit discovers plugin commands the same way it discovers built-in commands -- via `Command.__subclasses__()` after entry-point loading.
- Plugin authors point the audit at their plugin's test directory via a config setting in `pyproject.toml`:

  ```toml
  [tool.admt.ci]
  test_dirs = { tier1 = "tests/unit", tier2 = "tests/integration", tier3 = "tests/container" }
  ```

- A plugin author's CI imports admt's audit fixture and runs it against their own commands.

This is forward-looking; the convention should be considered when designing the plugin system, but the initial CI surface does not need to support it.

---

## Artifacts and Provenance

Every CI run produces two surfaces, each carrying one signal:

- **In-PR check view** (auto-rendered by GitHub from JUnit) -- per-test pass/fail, scannable on the PR's "Checks" tab without leaving the browser. Powered by [`mikepenz/action-junit-report@v4`](https://github.com/mikepenz/action-junit-report). One step in each workflow, one POSTed check-run per job.
- **Downloadable artifact bundle** (uploaded by `actions/upload-artifact@v4`) -- forensic detail when a failure needs more than the check view: themed coverage HTML (per-file line + branch drilldown), raw JUnit XML, raw coverage XML (Cobertura, for codecov-style consumers), and tier-3-only diagnostics (`docker compose logs`, version stamps).

The audit results (self-audit, pin-parity, spec-alignment) are pytest tests; their PASS/FAIL surfaces in the same JUnit-rendered check view as everything else. A reviewer who wants more than "PASS" clicks the test name in the check view and gets the assertion message verbatim from JUnit -- which already names the disagreeing file or missing test for parametrized failures. The custom dashboard renderer is intentionally absent; coverage HTML is the only piece worth owning the rendering of, and the `--extra-css` hook does that without owning anything else.

### Bundle Layout

```
_artifacts/
  gate/
    gate-junit.xml        # tier 1 + tier 2 (all of pytest, including the audits)
    coverage.xml          # Cobertura, for codecov-style consumers
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
  if: ${{ always() && !env.ACT }}
  uses: mikepenz/action-junit-report@<sha>  # v4.x.x
  with:
    report_paths: gate-junit.xml
    detailed_summary: true
    check_name: "gate (per-test)"
```

The `!env.ACT` guard skips the API call during local act rehearsal -- act sets `ACT=true` and the API call would either 403 or write a check-run to the wrong place.

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

Single-source-of-truth principle: every pin lives in exactly one place, referenced from CI. Drift across embedded references is the failure mode this section is built to prevent -- humans and AI agents both miss them, so the plan treats every pin as audited surface ([Version-Pin Parity Audit](#version-pin-parity-audit)).

| Tool | Pin file | Pin key | Bump procedure |
|---|---|---|---|
| `uv` | `.uv-version` (new) | (whole-file value) | Bump file, run gate locally, fix any lockfile-format churn per CODING_RULES.md, single commit titled `chore: pin uv X.Y.Z` |
| Python | `.python-version` (existing) | (whole-file value) | Standard convention; uv reads it natively |
| Adamant container image tag | `tests/container/_pins.env` (new) | `ADAMANT_TAG` | Couple with `ADAMANT_REF` (see [Paired Pins](#paired-pins-and-_pinsenv)); single commit |
| Adamant ref for fixture clone | `tests/container/_pins.env` (new) | `ADAMANT_REF` | Same as above |
| Third-party GitHub Actions | Inline `org/action@<sha>  # vX.Y.Z` in workflow YAML | (per `uses:` line) | Renovate/Dependabot opens a PR; the parity audit asserts the SHA is 40-hex and the comment names a version |

Every pin file is read by its consumer at runtime where possible (e.g., `cat .uv-version`, `source tests/container/_pins.env`) so consumers can never have a stale copy. Where a runtime read is awkward (workflow YAML, doc files), the [Version-Pin Parity Audit](#version-pin-parity-audit) verifies the consumer references the pin file by name and contains the current value.

A future evolution -- bundle the pinned values in a `[tool.admt.ci]` section of `pyproject.toml` -- is rejected for now: that namespace doesn't exist, and adding it for a handful of values is overkill. Filename conventions readers already know (`.uv-version`, `_pins.env`) cost less.

### Paired Pins and `_pins.env`

The Adamant container image and the Adamant source revision the fixture clones must move together. A container at `ghcr.io/lasp/adamant:0.2` was built from a specific Adamant commit; cloning a different ref into the fixture means the source code in `tests/container/_workspace/adamant/` does not match the binary the container is running. Tier 3 either passes by accident or fails for the wrong reason.

The pair lives in `tests/container/_pins.env`. Both keys use bash's conditional-assign (`:=`) so an environment variable explicitly set by the developer wins over the file's default -- this is what makes pin-override mode (`ADAMANT_REF=main bash run.sh`) work without any branching logic in `run.sh`:

```sh
# Pinned versions for tier 3 fixtures.
# These two values are coupled -- the Adamant container at ADAMANT_TAG was
# built from the source at ADAMANT_REF. Bump them together; the parity audit
# in tests/unit/test_pin_audit.py verifies they remain coupled.
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

Bumping the pair is a single commit: edit `_pins.env`, run tier 3 locally, commit titled `chore: bump Adamant pin to <tag> / <ref>`. The audit ([Version-Pin Parity Audit](#version-pin-parity-audit)) ensures both values are present, well-formed, and that no consumer file embeds a different tag or ref. The *remote* check -- does `:0.2` actually correspond to `v0.2.0` on Adamant's side? -- runs in [upstream.yml](#workflow-upstreamyml-roadmap): the OCI image label `org.opencontainers.image.revision` is read from the pulled container and compared to `ADAMANT_REF`. Drift opens an `upstream-drift` issue.

---

## Drift-Prevention Guards

CI cannot become a separate spec. Two test modules run as part of the gate and keep CI honest:

- **`tests/unit/test_ci_alignment.py`** -- spec/CI alignment. Verifies the workflow YAML matches TEST_PLAN.md and this plan.
- **`tests/unit/test_pin_audit.py`** -- version-pin parity. Verifies every embedded version reference matches its single source of truth.

The pattern matches the existing `tests/unit/test_architecture.py`: parametrized tier-1 tests, no Docker required, fail loudly with a specific message.

### Tier-3 Spec-Deviation Policy

Tier 3 occasionally surfaces a case where admt's behavior contradicts a `TEST_PLAN.md` "What to Test" or `ARCHITECTURE.md` clause that tier 1+2 didn't catch (because mocks faithfully reproduced the wrong behavior). When this happens, the right path is:

1. **Pin the tier 3 test to current behavior** with a comment naming the spec line and the discrepancy. Use a relaxed assertion (`returncode != 0` plus a substring match on the user-visible message) so the test still provides regression value while the spec/impl decision is open.
2. **File a follow-up** -- separate PR or issue -- to reconcile spec and impl. The default expected resolution is to *tighten the spec* (impl is most often the surface that drifted); loosening the spec is rare but possible when new information or use-cases surface.
3. **Tighten the tier 3 assertion** in the follow-up PR once spec and impl agree.

This keeps tier 3 from becoming a test-vs-impl tug-of-war and ensures spec discrepancies surface as explicit decisions rather than as silent test relaxations. The policy lives in TEST_PLAN.md §Regression Policy as the long-term home; this CI plan references it because tier 3 is where the policy fires most often.

### Tier-2 Subprocess Scope

Tier 2's "dual approach" -- CliRunner plus subprocess invocation -- narrows in scope: subprocess form is reserved for packaging-sensitive smokes (entry-point wiring, `[project.scripts]` regression catch), not per-command parity. CliRunner covers per-command behavior at tier 2 because it exercises real CLI parsing, dispatch, and Context wiring while staying fast. Subprocess invocation is slow and adds little signal beyond CliRunner for command-level behavior, so it stays scoped to a small set of packaging smokes -- one or two tests, not one-per-command.

This refinement lives in TEST_PLAN.md §Test Tiers §Tier 2 as the long-term home; this CI plan references it so the audit's tier-2 arm doesn't grow a "every command also has a subprocess test" expectation.

### Spec/CI Alignment Guards

- **The gate-command strings in `gate.yml` match `TEST_PLAN.md` byte-for-byte** (modulo the `--junitxml=...` and `-m "not container"` extensions documented in [CI1](#ci1-same-gate-from-a-clean-machine)).
- **`container.yml` invokes `tests/container/run.sh`** -- so the act-incompatible fallback path stays available.
- **Every `Command` subclass listed in [Appendix C](#appendix-c-command-inventory) appears at least once in `tests/container/`** (the structural arm of the [self-audit](#architectural-self-audit)).
- **Every alias defined via `add_alias` in `cli.py` appears at least once in `tests/integration/` and once in `tests/container/`.**

```python
# tests/unit/test_ci_alignment.py
from pathlib import Path
import re

import yaml


def _gate_commands_in_test_plan() -> list[str]:
    text = Path("TEST_PLAN.md").read_text()
    block = re.search(r"## Quality Gate.*?```bash\n(.*?)\n```", text, flags=re.DOTALL)
    assert block, "Could not locate Quality Gate block in TEST_PLAN.md"
    return [line.strip() for line in block.group(1).splitlines() if line.strip()]


def test_gate_workflow_runs_the_four_commands():
    spec_commands = _gate_commands_in_test_plan()
    assert len(spec_commands) == 4

    workflow = yaml.safe_load(Path(".github/workflows/gate.yml").read_text())
    run_strings = " ".join(
        s["run"] for s in workflow["jobs"]["gate"]["steps"] if "run" in s
    )

    for cmd in spec_commands:
        # The CI version may add `--junitxml=...` or `-m "not container"` to pytest;
        # match by the leading three tokens of each command (the verb + key flags).
        leading = " ".join(cmd.split()[:3])
        assert leading in run_strings, (
            f"Gate command {leading!r} from TEST_PLAN.md not found in gate.yml"
        )


def test_container_workflow_invokes_run_script():
    workflow = yaml.safe_load(Path(".github/workflows/container.yml").read_text())
    runs = " ".join(
        s["run"] for s in workflow["jobs"]["container"]["steps"] if "run" in s
    )
    assert "tests/container/run.sh" in runs, (
        "container.yml must call tests/container/run.sh so the host-script "
        "fallback stays the canonical entry point (CI5)."
    )
```

A change to TEST_PLAN.md's gate block without a corresponding workflow update fails the gate. A change to the workflow without a corresponding doc update fails the gate. The spec/CI contract is enforced symmetrically.

### Version-Pin Parity Audit

Embedded version references rot silently. A workflow file that hardcodes `uv 0.7.13` while `.uv-version` says `0.7.14`, an Adamant tag in a comment that lags the real tag in `_pins.env`, a third-party action SHA whose comment-version doesn't match the SHA -- humans skim past these, and AI agents skim past them faster. The pin audit makes every embedded value an asserted invariant.

The audit walks a registered manifest of pins. Each entry names a single source of truth and a list of consumers (file + matcher). The test parametrizes over the manifest; per-pin failures name exactly which file disagrees with the source.

#### Pin Manifest

```python
# tests/unit/test_pin_audit.py
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Pin:
    """One version pin and its consumers.

    `source` is the source-of-truth file. If `key` is None, the entire
    file's stripped content is the value (the `.uv-version` / `.python-version`
    convention). If `key` is set, the file is parsed as KEY=VALUE pairs and
    `key` selects which one.

    Each consumer is (file, matcher). A matcher is either:
    - a string: the literal value must appear somewhere in the file, OR
    - a compiled regex: must match (with named group `value` capturing the
      consumer's local copy of the value, which is then compared to source).
    """

    name: str
    source: Path
    key: str | None
    consumers: tuple[tuple[Path, str | re.Pattern[str]], ...]


PINS: tuple[Pin, ...] = (
    Pin(
        name="uv",
        source=Path(".uv-version"),
        key=None,
        consumers=(
            # gate.yml reads .uv-version at runtime; just verify the
            # filename is referenced.
            (Path(".github/workflows/gate.yml"), ".uv-version"),
            (Path(".github/workflows/container.yml"), ".uv-version"),
            # CLAUDE.md mentions the file by name so contributors find it.
            (Path("CLAUDE.md"), ".uv-version"),
        ),
    ),
    Pin(
        name="python",
        source=Path(".python-version"),
        key=None,
        consumers=(
            (Path(".github/workflows/gate.yml"), ".python-version"),
            (Path(".github/workflows/container.yml"), ".python-version"),
            # pyproject.toml's requires-python uses the same minor version.
            # Use a regex so the source's "3.14" matches "requires-python = >=3.14".
            (Path("pyproject.toml"), re.compile(r'requires-python\s*=\s*">=(?P<value>\d+\.\d+)"')),
        ),
    ),
    Pin(
        name="adamant_tag",
        source=Path("tests/container/_pins.env"),
        key="ADAMANT_TAG",
        consumers=(
            # run.sh sources _pins.env at runtime, but the pin file itself is
            # the only place the tag is allowed to appear textually.
            # Verifying that `:0.2` is NOT hardcoded anywhere is a paired check;
            # see test_no_orphan_adamant_tags below.
            (Path("tests/container/run.sh"), "_pins.env"),
            (Path(".github/workflows/container.yml"), "_pins.env"),
        ),
    ),
    Pin(
        name="adamant_ref",
        source=Path("tests/container/_pins.env"),
        key="ADAMANT_REF",
        consumers=(
            (Path("tests/container/run.sh"), "_pins.env"),
        ),
    ),
)


def _read_pin(pin: Pin) -> str:
    text = pin.source.read_text()
    if pin.key is None:
        return text.strip()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip() == pin.key:
                return v.strip().strip('"').strip("'")
    pytest.fail(f"Key {pin.key!r} not found in {pin.source}")


@pytest.mark.parametrize("pin", PINS, ids=lambda p: p.name)
def test_pin_source_exists_and_has_value(pin: Pin):
    assert pin.source.exists(), f"Pin source {pin.source} does not exist"
    value = _read_pin(pin)
    assert value, f"Pin {pin.name} in {pin.source} is empty"


@pytest.mark.parametrize(
    "pin,consumer_path,matcher",
    [(pin, p, m) for pin in PINS for (p, m) in pin.consumers],
    ids=lambda v: v.name if isinstance(v, Pin) else str(v),
)
def test_pin_consumer_references_source(pin, consumer_path, matcher):
    text = consumer_path.read_text()
    if isinstance(matcher, str):
        assert matcher in text, (
            f"Consumer {consumer_path} of pin {pin.name!r} does not "
            f"reference the pin source ({matcher!r} missing)."
        )
    else:
        m = matcher.search(text)
        assert m, f"Pattern {matcher.pattern!r} not found in {consumer_path}"
        local = m.group("value")
        source_value = _read_pin(pin)
        # Prefix-match so the source "3.14" matches consumer "3.14" exactly,
        # but the constraint version `>=3.14` parsing already extracted "3.14".
        assert local == source_value, (
            f"{consumer_path} embeds {pin.name}={local!r} but {pin.source} "
            f"says {source_value!r}. Bump together."
        )


def test_no_orphan_adamant_tags():
    """No executable file outside _pins.env may hardcode a `ghcr.io/lasp/adamant:<tag>` value.

    Catches the case where a contributor copies a tag into a workflow comment
    or a docstring and forgets to update it when _pins.env moves. Markdown is
    exempt by design: docs (including this plan) legitimately mention tags for
    exposition, and enforcement targets the executable surfaces where a stale
    tag changes behavior.
    """
    pin = next(p for p in PINS if p.name == "adamant_tag")
    pinned_value = _read_pin(pin)
    bad = re.compile(rf"ghcr\.io/lasp/adamant:(?!\$\{{ADAMANT_TAG\}})[^\s\"']+")
    excluded_dirs = {
        ".git", ".venv", "_artifacts", "__pycache__",
        ".mypy_cache", ".ruff_cache", ".pytest_cache", "htmlcov",
        "node_modules", "dist", "build",
    }
    for path in Path(".").rglob("*"):
        if not path.is_file() or path.suffix not in {".yml", ".yaml", ".sh", ".py", ".toml"}:
            continue
        if path == pin.source or any(part in excluded_dirs for part in path.parts):
            continue
        for n, line in enumerate(path.read_text(errors="ignore").splitlines(), start=1):
            for m in bad.finditer(line):
                tag = m.group(0).split(":", 1)[1]
                # `:latest` is allowed only in upstream.yml.
                if tag == "latest" and path.name == "upstream.yml":
                    continue
                pytest.fail(
                    f"{path}:{n} hardcodes Adamant tag {tag!r}; reference "
                    f"$ADAMANT_TAG (sourced from tests/container/_pins.env) instead."
                )


def test_third_party_action_pins_have_sha_and_version_comment():
    """Every `uses: org/action@<ref>` either uses a first-party or vendor-official
    action (`actions/*`, `github/*`, `astral-sh/*`) at a major version (`@v6`), or
    pins a 40-char SHA with a `# vX.Y.Z` comment naming the human version. Catches
    drift between the SHA and the comment, and unpinned third-party actions.
    """
    sha40 = re.compile(r"^[0-9a-f]{40}$")
    use_line = re.compile(
        r"^\s*-?\s*uses:\s*(?P<repo>[\w./-]+)@(?P<ref>[\w.-]+)"
        r"(?:\s*#\s*(?P<comment>.*))?$"
    )
    for wf in Path(".github/workflows").glob("*.yml"):
        for n, line in enumerate(wf.read_text().splitlines(), start=1):
            m = use_line.match(line)
            if not m:
                continue
            repo, ref, comment = m["repo"], m["ref"], m["comment"]
            first_party = repo.startswith(("actions/", "github/", "astral-sh/"))
            if first_party:
                # Major-version refs are acceptable: actions/checkout@v6, etc.
                continue
            assert sha40.match(ref), (
                f"{wf}:{n} third-party action {repo}@{ref} must pin a 40-char SHA"
            )
            assert comment and re.search(r"\bv?\d+\.\d+", comment), (
                f"{wf}:{n} third-party action {repo}@{ref} must carry a "
                f"`# vX.Y.Z` comment naming the human version"
            )
```

#### What This Catches

- A workflow that hardcodes `uv 0.7.13` while `.uv-version` moves to `0.7.14`. (Caught: workflow's `.uv-version` reference is structural; the audit also re-runs the gate, which would fail to find the right uv if the workflow's runtime read got broken.)
- A `pyproject.toml` `requires-python = ">=3.13"` while `.python-version` says `3.14`.
- An `ADAMANT_TAG=0.2` in `_pins.env` while a stale `ghcr.io/lasp/adamant:0.1` lurks in a workflow comment, helper script, or test docstring.
- An `ADAMANT_REF` that is empty or non-existent.
- A third-party GitHub Action `uses: third-party/foo@v1` (unpinned) or pinned to a SHA without a `# vX.Y.Z` comment.

#### What This Does Not Catch

- The *remote* parity question -- "does `:0.2` actually equal `v0.2.0` in Adamant's source?" That requires a network round-trip to GHCR to read the OCI label `org.opencontainers.image.revision`. Performed in [upstream.yml](#workflow-upstreamyml-roadmap), not in the gate.
- Drift between a tagged third-party action (`@v6`) and the actual code at that tag. Renovate/Dependabot is the tool for this; the audit only enforces the local-pinning convention.
- Semantic correctness of the value (e.g., does the pinned uv version still install on Python 3.14?). That's caught by the gate run itself.

The audit runs in tier 1, costs a few milliseconds, and fails parametrized so contributors see exactly which (pin, consumer) pair is broken. Adding a new pin -- a new tool, a new paired version -- is a one-entry edit to the `PINS` tuple.

---

## Implementation Order

The first CI PR ships gate.yml plus the alignment tests and the self-audit. Tier 3 follows in a second PR because it has materially more setup. Roadmap workflows follow in their own PRs.

**Each PR opens green** -- it merges only when its own gate run passes against the proposed workflow.

### PR 1: gate.yml + alignment + self-audit (structural only)

- `.github/workflows/gate.yml` -- the four-command gate, Linux + macOS matrix.
- `.uv-version` -- pin uv to the version that wrote the current `uv.lock`.
- `.gitignore` additions for `gate-junit.xml`, `coverage.xml`, `_artifacts/`.
- `tests/ci_assets/admt-dark.css` -- the coverage-HTML theme.
- `tests/unit/test_ci_alignment.py` -- the spec/CI sync tests.
- `tests/unit/test_pin_audit.py` -- the version-pin parity audit. The initial `PINS` manifest covers `uv`, `python`, and the third-party-action SHA convention; the Adamant pair (`ADAMANT_TAG`/`ADAMANT_REF`) joins the manifest when `_pins.env` lands alongside the container workflow.
- `tests/unit/test_command_test_coverage.py` -- the structural self-audit (parametrized over commands/services/adapters/aliases plus the global, subcommand, and env-var flag families). The tier-3 arm of every audit family is conditional: it skips when `tests/container/` is empty, so the gate workflow can land before tier-3 tests exist. The tier-2 arm runs unconditionally -- existing `tests/integration/` tests already exercise most flags, and the audit makes the coverage explicit.
- One CLAUDE.md update: a `## CI` section pointing to this plan and to `act -j gate`.
- README badge for gate status.

The PR does *not* touch ARCHITECTURE.md, CODING_RULES.md, or TEST_PLAN.md, except where this plan amends them. Per [CODING_RULES.md §Agent-Specific Rules](CODING_RULES.md#agent-specific-rules), the spec doesn't get edited to match what the workflow happens to do -- the workflow gets edited to match the spec.

The PR's quality gate acceptance includes one extra rehearsal: `act -j gate -W .github/workflows/gate.yml --container-daemon-socket -` succeeds locally before pushing.

### PR 2: container.yml + tier-3 tests + activate full self-audit

- `.github/workflows/container.yml`.
- `tests/container/_pins.env` -- the Adamant tag/ref pair file (see [Paired Pins](#paired-pins-and-_pinsenv)). Adds the `ADAMANT_TAG` and `ADAMANT_REF` entries to the `PINS` manifest in `tests/unit/test_pin_audit.py`, and turns on `test_no_orphan_adamant_tags`.
- `tests/container/run.sh` -- the host script CI invokes.
- `tests/container/conftest.py` -- session-scoped fixtures (clone Adamant, register, start container, expose component path).
- `tests/container/test_*.py` -- one file per command family, mirroring `tests/integration/`. The first cut covers happy paths and the failure-path matrix from [TEST_PLAN.md §Error Path Tests](TEST_PLAN.md#error-path-tests):
  - `test_env_lifecycle.py` -- start/stop/restart/status/refresh.
  - `test_env_exec_login.py` -- exec, login, env exec.
  - `test_env_init_use_list.py` -- init, use, list, with multi-marker validation.
  - `test_env_worktrees.py` -- parameterized-compose registration with a colocated `.env`, `.env`-edit staleness re-derive, two side-by-side projects selected via `ADMT_ENV`, and no-TTY resolution ([Worktree Configuration Coverage](#worktree-configuration-coverage)).
  - `test_env_image.py` -- build, push, pull, rm with `--volumes`/`--image`/`--remove-all`.
  - `test_passthrough.py` -- build, what, test [--all], style [--all], analyze [--all], clean [--all], prove, coverage [--all], publish [--all].
  - `test_templates.py` -- templates, templates --undo, the `~/.admt/backup-latest` marker behavior.
  - `test_failure_paths.py` -- exit-code conformance for every error path TEST_PLAN.md names (the lesson from spec-vs-impl drift retros).
  - `test_global_flags.py` -- `--verbose`, `--quiet`, `--debug`, `--yes`, `--force`, `ADMT_NONINTERACTIVE`, `ADMT_NONINTERACTIVE=0`, `ADMT_ENV`, `NO_COLOR`.
  - `test_aliases.py` -- one minimal invocation per alias (`e`, `b`, `t`, `s`, `an`, `cl`, `p`, `cov`, `pub`, `w`, `tmpl`).
  - `test_signal_handling.py` -- SIGINT propagation, exit code 130 ([ARCHITECTURE.md §Signal Handling](ARCHITECTURE.md#signal-handling)).
- `tests/CI.md` -- the act recipes split out from this plan into a runbook (since they are operator instructions rather than spec).
- README badge for container status.
- The previously-conditional tier-3 arm of the self-audit is enabled (no longer skipping; if a command is missing tier-3 coverage, the gate fails).

The PR's quality gate acceptance includes:

- `bash tests/container/run.sh` succeeds locally (the act-independent host-script form).
- `act pull_request -j container -W .github/workflows/container.yml --bind` succeeds locally with `DOCKER_HOST=unix:///var/run/docker.sock` (the native-Docker rehearsal form).

### PR 3 (roadmap): release.yml

- `.github/workflows/release.yml`.
- PyPI trusted-publishing setup (one-time, in repo settings).
- Wheel-publish smoke against TestPyPI before the first real release.
- Optional `arm64-verification` job (advisory-only for v0.2; required-blocking once the first arm64 user appears).

### PR 4 (roadmap): upstream.yml

- `.github/workflows/upstream.yml`.
- `.github/ISSUE_TEMPLATE/upstream-drift.md` so auto-opened issues have a consistent format.
- `tests/contract/test_redo_what_format.py` and friends -- frozen samples of upstream output formats with parser tests, so contract drift surfaces locally first.

### PR 5+ (roadmap): polish

- ARM64 release verification (post-`release.yml`).
- Codecov integration (after coverage is stable on `main`).
- Whole-file-size trip-wire.
- Status badges, README updates.
- Per-tier scenario audit (the more aggressive self-audit variant).
- Plugin-author CI template once the plugin entry-point system lands.

---

## Test Plan for the Workflows Themselves

The workflows are code; they have their own acceptance criteria.

### Per-Workflow Acceptance

Every workflow change goes through this checklist before merge:

1. **`act --validate -W .github/workflows/<file>.yml`** -- strict schema check.
2. **`act -n -j <job> -W .github/workflows/<file>.yml`** -- dryrun confirms the execution plan is what's intended.
3. **`act -j <job> ...`** with the appropriate Docker-socket recipe -- end-to-end local run. Required for gate.yml. Required for container.yml when act can rehearse it; the host-script equivalent (`bash tests/container/run.sh`) is required regardless.
4. **The drift-prevention tests in `tests/unit/test_ci_alignment.py`** still pass.
5. **The self-audit in `tests/unit/test_command_test_coverage.py`** still passes.
6. **The PR description includes the local act output** of step 3 (a short paste, not the full log) -- so the reviewer sees the rehearsal happened.

### Per-Job Acceptance

Each new job:

1. Has a `name:` that maps to the spec section that motivates it.
2. Has a comment naming which CI<n> requirement it implements.
3. Carries a `timeout-minutes:` at least 2x the median wall time observed during rehearsal.
4. Is annotated `# act: ok` or `# act: skip <reason>` so the act-rehearsability boundary is locally legible.

---

## Plugin Convention (Forward-Looking)

The plugin system ([ROADMAP.md](ROADMAP.md) Tier 5) registers `Command` subclasses via Python entry points. The CI surface should generalize to plugin authors without forcing them to re-invent the wheel.

The convention:

1. **Plugins inherit admt's gate.** A plugin's pyproject.toml declares `[dependency-groups]` mirroring admt's; their CI runs the same four-command gate. This plan describes a reusable workflow file (`gate.yml`'s logic published as a callable workflow) that plugins can `uses:` directly.
2. **Plugins extend the self-audit.** A plugin's `tests/unit/test_command_test_coverage.py` imports the audit machinery from admt and runs it against the plugin's `Command` subclasses (same pattern as importing a pytest fixture). The audit's test-directory configuration is read from `[tool.admt.ci]` in `pyproject.toml`.
3. **Plugins exercise tier 3 against admt itself.** Plugins that expose container-touching commands need their own tier-3 fixture (since the plugin's commands operate on a real Adamant project). The fixture pattern from `tests/container/conftest.py` is reusable: clone Adamant, register, start, exercise the plugin's commands.

This is forward-looking, but the conventions land in this plan now so the plugin-system design has a target.

---

## Risks, Tradeoffs, and Honest Estimates

This plan is exhaustive by design (admt's culture is "spec the boring stuff" -- agents stay inside specs that are complete enough to stay inside). Exhaustive specs accumulate cost. This section calls out where the plan's reach exceeds its grasp, what's likely to spiral, and what to defer if implementation scope feels tight.

### Wall-time reality

Tier-3 wall-time estimates need explicit caveats. ARCHITECTURE.md §Environment Activation warns that first-run activate "can take many minutes (pip installs, alr builds, gprbuild of the Pico runtime)." Cloud CI inherits this fully:

- **Cloud GHA runners are fresh per job.** The activate snapshot at `/tmp/admt/<project>/` does not persist across runs unless cached explicitly (see [Roadmap §Medium-term](#medium-term)).
- **Image pull on cold cache** is ~2-3 minutes for an Adamant image of typical size; warm with `actions/cache@v5` it's ~10-30 seconds.
- **Adamant first-run activate** is the dominant cost. Steady-state on cloud is 5-10 minutes per run until snapshot caching lands.
- **Tier 3 test execution itself** (admt commands against the running container) is 2-5 minutes for the suite covering every command at the audit's required tier.

Sum: realistic tier-3 wall time is 8-15 minutes per run on cloud GHA. Locally with image and snapshot reuse, 3-5 minutes is achievable. Appendix B reflects this.

**Action**: every `timeout-minutes` for container-touching jobs is set to 60. Merge SLAs plan around the upper end of these ranges. Treat the activate-snapshot caching optimization as a real deliverable in the medium-term roadmap, not a nice-to-have.

### Items that could spiral if pursued before they're load-bearing

Several items read as "good engineering" but whose value scales with codebase size or contributor count, not with the workflow shipping. If implementation scope feels tight, defer in this order:

- **A unified HTML dashboard.** GitHub's check view (driven by `mikepenz/action-junit-report@v4`) plus the themed coverage HTML cover the forensic surface. A unified dashboard with provenance banner and per-audit detail pages is UX polish -- valuable when comparing runs side-by-side or consolidating cross-job data, not necessary for a CI that just needs to gate merges and surface failures. Reappears as a follow-up if a real need arises.
- **`test_no_orphan_adamant_tags`** (the orphan-tag scanner). Walks every executable text file in the repo (Markdown is exempt by design -- docs mention tags for exposition). The exclusion list is broad, but a docstring example that mentions `ghcr.io/lasp/adamant:0.1` for historical reasons would still false-positive. Soft-mode (warn, not fail) is reasonable on first introduction, hardening to fail-mode after a few weeks of false-positive review.
- **`test_third_party_action_pins_have_sha_and_version_comment`.** Catches a real failure mode (unpinned third-party actions), but Renovate/Dependabot is the tool for the bump-the-SHA-and-the-comment-together job. The audit is paranoia insurance. Cheap to keep, but if it triggers more false-positives than real catches in the first month, drop it.
- **ARM64 verification** in `release.yml`. Deferred to the roadmap for good reason: QEMU under cloud GHA is very slow (15-25 minutes for a representative test subset; see Appendix B). Not free even deferred. Treat as advisory until the first arm64 admt user emerges.

### Assumptions worth verifying before they ship

A few claims in this plan rest on upstream behavior I have not empirically confirmed:

- **Adamant container ships OCI labels** (specifically `org.opencontainers.image.revision`). The pin-parity-remote job in upstream.yml depends on this. If the Adamant image is built without those labels, the job fails for the wrong reason ("label missing" rather than "ref mismatch"). Verify with `docker inspect ghcr.io/lasp/adamant:${ADAMANT_TAG}` *before* writing the upstream workflow. If labels are absent, the remote check needs a different mechanism (e.g., maintain a manual map of `_pins.env` values to upstream release notes).
- **`catthehacker/ubuntu:act-latest` ships `docker` CLI 29.x.** Confirmed in this plan's act capability matrix probe (29.4.1-1 was observed). If a future image bump removes or downgrades the docker CLI, the act recipes break silently -- and not just for daemon-facing tests: `admt env init`/`env refresh` themselves shell `docker compose config`, so registration inside the runner requires the docker CLI even before any container starts. The pin audit should grow a "platform image version" entry once it stabilizes.
- **`actions/cache@v5` semantics for the activate snapshot are strong enough.** The proposed cache key includes `hash(env/activate, requirements.txt, _pins.env)`. If Adamant's activate has untracked dependencies (e.g., a `setup.sh` it `source`s), the hash misses them and stale snapshots restore. Verify by reading the upstream activate script before relying on the cache.

### Where the plan is conservative

A few places where the plan errs toward more verification than is strictly needed -- intentionally, given admt's "spec the boring stuff" culture, but worth acknowledging:

- The **Spec/CI Alignment Guards** assert the gate command appears verbatim in `gate.yml`. This catches benign reformattings as failures. The reformatting failure is the right behavior (force a deliberate update to TEST_PLAN.md when CI changes), but a contributor will hit it once and grumble.
- The **per-flag tier-3 audit** asserts every flag has a tier-3 test. For flags whose semantics are entirely host-side (e.g., `--quiet` toggling stdout suppression), the tier-3 invocation is just "the binary accepted the flag without erroring" -- a one-line test. The audit insists on it anyway. Worth it for the regression-free guarantee, but not free.
- **`fail-fast: false`** on the gate matrix means a Linux-only flake doesn't hide a macOS-only flake. It also doubles the cost of a deterministic failure. Acceptable tradeoff.

### Where the plan is aggressive

- **Tier 3 on every non-draft PR.** This is a deliberate departure from ROADMAP.md's "container smoke tests on PR merge" wording, on the grounds that the spec-vs-impl drift retros showed merge-time validation is too late. The cost is 8-15 minutes per PR. If the project has many small PRs (docs, typo fixes, dependency bumps), this cost compounds. The mitigation is the draft-PR exemption: keep WIP work in draft until ready for serious review, then flip to "ready" once tier 3 has something to test.
- **No `[skip ci]` bypass.** The plan rejects skip directives entirely. This is the right default for a 24-command CLI where every command is one PR away from breaking. A future contributor will want a skip for "I changed only README.md" -- the answer is "let CI run; it's 90 seconds".

### Recommended deferral order

When implementation scope is tight, defer in this order:

1. The unified HTML dashboard renderer.
2. The third-party-action SHA-comment audit (nice; not load-bearing).
3. The orphan-tag scanner (or run it in soft mode first).
4. The plugin-convention prose (forward-looking; safe to thin if not informing current design choices).

Do **not** defer:

- The four-command gate ([CI1](#ci1-same-gate-from-a-clean-machine)).
- The 100% coverage threshold ([CI2](#ci2-the-coverage-threshold-is-hard)).
- The architectural self-audit's command/service/adapter/alias/flag arms ([CI4](#ci4-every-command-every-flag-every-alias)).
- The version-pin parity audit's local arm (`uv`, `python`, third-party-action SHA-pin format -- not necessarily the comment-version pairing).
- The drift-prevention tests in `tests/unit/test_ci_alignment.py` for the gate command and run-script invocation.

These five are the load-bearing safety net. Everything else is decoration on top.

---

## Roadmap

The initial CI surface is deliberately small: gate.yml + container.yml + alignment + self-audit. Everything below is real and useful, but it builds on that surface and should not delay it from landing.

### Near-term (within a release or two of the initial CI surface shipping)

- **release.yml** -- PyPI publishing.
- **upstream.yml** -- weekly contract test against `:latest`.
- **arm64-verification job** in release.yml.
- **Codecov integration**.
- **Status badges** in README.

### Medium-term

- **Activate-snapshot caching across runs.** Highest-value optimization for tier 3. `actions/cache@v5` keyed on `hash(env/activate, requirements.txt, _pins.env)` saves `/tmp/admt/<project>/env_snapshot.sh` and `exec.sh` between runs. On a cache hit, admt skips the 5-10-minute first-run activate and uses the snapshot directly. Expected wall-time reduction: 50-60% on the steady-state run, larger when the runner is otherwise fresh.
- **Reusable workflow** for the gate command sequence.
- **Container-test parallelization** -- shard tier 3 by file across 2-3 jobs once it has 50+ tests.
- **Self-hosted GHCR mirror** for the Adamant image so cold tier-3 runs are sub-30s on the pull side.
- **Whole-file-size trip-wire**.
- **Per-tier scenario audit** -- the more aggressive variant of the self-audit.
- **Mutation testing** via `mutmut` on a schedule against `main`, not blocking PRs.
- **Step-output bridge for the Gate dashboard panel** -- a workflow step that writes ruff/mypy results to JSON the renderer reads, so the four gate commands appear with per-command granularity in the dashboard. Currently they live in GitHub's native step view.

### Long-term

- **Windows CI** when the spec adds Windows as a target platform.
- **Plugin-author CI template**.
- **Schema-based contract tests** for tier 3 once `admt validate` lands.
- **Self-hosted runner pool** if cloud-runner minutes become a bottleneck.

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

These are *order-of-magnitude* estimates, not commitments. Wall times are dominated by uncached external work (`uv sync`, image pull, Adamant `env/activate`). See [Risks, Tradeoffs, and Honest Estimates](#risks-tradeoffs-and-honest-estimates) for the assumptions baked into each row.

| Job | Workflow | OS | Wall Time (steady) | Wall Time (cold) | act-Rehearsable |
|---|---|---|---|---|---|
| `gate (ubuntu-24.04)` | gate.yml | linux | ~90s -- 2m | ~3m | yes |
| `gate (macos-14)` | gate.yml | macos | ~2m -- 3m | ~4m | no (linux-only act) |
| `container (standalone)` | container.yml | linux | **~8m -- 12m** | **~12m -- 18m** | yes (native-socket recipe) or via `tests/container/run.sh` directly |
| `gate-release (ubuntu-24.04)` | release.yml | linux | ~90s -- 2m | ~3m | yes |
| `gate-release (macos-14)` | release.yml | macos | ~2m -- 3m | ~4m | no |
| `container-release` | release.yml | linux | ~8m -- 12m | ~12m -- 18m | yes (same recipe as container.yml) |
| `build-wheel` | release.yml | linux | ~30s | ~1m | yes |
| `publish-pypi` | release.yml | linux | ~10s | ~30s | no (OIDC trust) |
| `arm64-verification` | release.yml | linux+QEMU | ~15m -- 25m | ~25m -- 35m | no (slow under act) |
| `upstream-tier3` | upstream.yml | linux | ~8m -- 12m | ~12m -- 18m | yes (same recipe as container.yml) |

"Cold" = first run with no cache (no uv cache, no Adamant image cache). "Steady" = a typical PR after caches are warm. **The container-touching jobs are slow.** Cloud GHA runners are fresh per job, so Adamant's `env/activate` (which can take 5-10 minutes for first-run alr/gprbuild work) re-runs every time -- caching the activate snapshot across runs is a medium-term optimization (see [Roadmap](#medium-term)). Plan branch protections and review SLAs around the upper end of these ranges, not the lower.

---

## Appendix C: Command Inventory

24 concrete `Command` subclasses on `main`. Auto-generated from `admt.commands.*`; the [self-audit](#architectural-self-audit) keeps the generator and the tier-3 coverage in sync. Three categories:

### Pure host (no project, no container)

| `name` | Class | Notes |
|---|---|---|
| `env init` | `EnvInitCommand` | Creates a project; doesn't depend on one |
| `env use` | `EnvUseCommand` | Switches active project; reads config |
| `env list` | `EnvListCommand` | Reads config |

### Container-touching env subcommands

| `name` | Class |
|---|---|
| `env start` | `EnvStartCommand` |
| `env stop` | `EnvStopCommand` |
| `env restart` | `EnvRestartCommand` |
| `env login` | `EnvLoginCommand` |
| `env status` | `EnvStatusCommand` |
| `env exec` | `EnvExecCommand` |
| `env refresh` | `EnvRefreshCommand` |
| `env build` | `EnvBuildCommand` |
| `env push` | `EnvPushCommand` |
| `env pull` | `EnvPullCommand` |
| `env rm` | `EnvRmCommand` |

### Container-passthrough commands

| `name` | Class | redo target | `--all` flag |
|---|---|---|---|
| `build` | `BuildCommand` | `all` (or arbitrary target) | -- |
| `what` | `WhatCommand` | `what` | -- |
| `test` | `TestCommand` | `test` | yes |
| `style` | `StyleCommand` | `style` | yes |
| `analyze` | `AnalyzeCommand` | `analyze` | yes |
| `clean` | `CleanCommand` | `clean` | yes |
| `prove` | `ProveCommand` | `prove` | -- |
| `coverage` | `CoverageCommand` | `coverage` | yes |
| `publish` | `PublishCommand` | `publish` | yes |
| `templates` | `TemplatesCommand` | `templates` | -- (`--undo`) |

### Aliases (defined in `cli.py` via `add_alias`)

| Alias | Target | Tier required |
|---|---|---|
| `e` | `env` (group) | 2 + 3 |
| `b` | `build` | 2 + 3 |
| `t` | `test` | 2 + 3 |
| `s` | `style` | 2 + 3 |
| `an` | `analyze` | 2 + 3 |
| `cl` | `clean` | 2 + 3 |
| `p` | `prove` | 2 + 3 |
| `cov` | `coverage` | 2 + 3 |
| `pub` | `publish` | 2 + 3 |
| `w` | `what` | 2 + 3 |
| `tmpl` | `templates` | 2 + 3 |

The self-audit parametrizes over both tables and asserts each entry has at least one test invocation in the right tier directory.

---

## Appendix D: act Capability Matrix (Verified)

The recipes in [act Rehearsal Protocol](#act-rehearsal-protocol) were validated empirically against `act 0.2.84` on `Manjaro Linux` (the 0.2.86 floor in [Prerequisites](#prerequisites) is a CVE floor, not a behavioral one; the probed capabilities are unchanged) with both `desktop-linux` (active) and native (`/var/run/docker.sock`) Docker contexts available. Each row was probed with a small workflow that exercised the relevant capability.

| Capability | Native Docker | Docker Desktop (no File Sharing config) | Docker Desktop (with File Sharing) |
|---|---|---|---|
| Run a workflow that needs no Docker daemon | works (default socket bind silently unused) | works with `--container-daemon-socket -` | works |
| Bind workspace at same absolute path on host and runner | works with `--bind` | works with `--bind` | works with `--bind` |
| See sibling directories outside workspace from the runner | **does not work** -- only the workspace is bound | **does not work** | **does not work** |
| Run `docker run`/`docker compose` from inside the runner | works with default socket bind | **fails**: "mounts denied: socket path not shared" | works |
| Use act-default socket discovery | works | discovers Desktop socket but mount fails | works |

**Operational consequence**: the tier-3 fixture must clone Adamant *into* the workspace (`tests/container/_workspace/adamant/`), because the workspace is the only thing visible inside the runner. This shaped [Tier 3 Fixture Strategy](#tier-3-fixture-strategy).

The Docker-Desktop-without-File-Sharing failure is a hard wall in this environment; the fallback is the host script `tests/container/run.sh`, which CI invokes directly. This is why [CI5](#ci5-act-rehearsable-where-possible) requires the workflow logic to be exposed as a script -- so the act-incompatible case still has a first-class entry point.

The findings here will eventually move into `tests/CI.md` as operator runbook content; this appendix carries the design rationale for why the recipes look the way they do.

---

## Appendix E: Glossary

- **Gate** -- the four-command quality gate from [TEST_PLAN.md §Quality Gate](TEST_PLAN.md#quality-gate). The non-negotiable bar that every change clears.
- **Tier 1 / Tier 2 / Tier 3** -- the three test tiers from [TEST_PLAN.md §Test Tiers](TEST_PLAN.md#test-tiers). Tier 1 is unit, tier 2 is integration via CliRunner + subprocess, tier 3 is container ground-truth.
- **act** -- [nektos/act](https://github.com/nektos/act), the local GitHub Actions runner. Reads `.github/workflows/`, builds an execution plan, runs each job in a Docker container that approximates the GitHub-hosted runner.
- **Native Docker** -- the system Docker daemon on Linux (socket at `/var/run/docker.sock`).
- **Docker Desktop** -- Docker Inc.'s VM-backed daemon (socket typically at `~/.docker/desktop/docker.sock` on Linux, `~/.docker/run/docker.sock` on macOS).
- **`--bind`** -- act flag that bind-mounts the workspace at the same absolute path on host and runner. Required for tier 3 because path-mapping in admt depends on host and runner agreeing on absolute paths.
- **Drift** -- the spec saying one thing and the code (or workflow) doing another. Three categories: spec-vs-impl, spec-vs-fact, spec-vs-CI. CI9 (drift-prevention guards) targets the third.
- **Self-audit** -- a parametrized test that walks `Command`/`Service`/`Adapter` modules and asserts each has a test at the right tier. The structural arm of the more general "is this code tested" question; complements the 100% line + branch coverage gate.
- **Provenance** -- the metadata that answers "exactly what did this run test?" (commit SHA, run ID, branch/PR, OS, timestamp, pins, image digest). Captured in the container job's `versions.txt` and in the workflow run's own metadata.

---

This plan is the spec for `.github/workflows/*.yml` and the supporting test-side scaffolding. Code that implements the workflows must trace to a section here. A workflow change that does not trace is rejected; behavior that diverges from this plan is a defect to either fix in YAML or amend here first -- not both, and not silently.
