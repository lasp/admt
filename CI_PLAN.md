# admt CI Plan

This document specifies how admt's continuous-integration pipeline implements the testing strategy from [TEST_PLAN.md](TEST_PLAN.md), the architectural guarantees from [ARCHITECTURE.md](ARCHITECTURE.md), and the authoring rules from [CODING_RULES.md](CODING_RULES.md). It is a planning spec, not a runbook -- the runbook recipes for [`act`](https://github.com/nektos/act) live further down because the rehearsal story is a first-class concern of the design.

The MVP shipped without CI. The local four-command gate ([TEST_PLAN.md §Quality Gate](TEST_PLAN.md#quality-gate)) was the only gate, and it held: full line + branch coverage, ruff/mypy clean. CI exists to close the gaps the local gate cannot solve: spec-vs-implementation drift, drift between code and CI itself, lockfile churn, and the merge-as-test problem where independently-green PRs are not validated as a coherent whole until after they land.

This plan is the spec for the CI implementation that follows. Like every admt spec doc, code that does not trace to this document is rejected; behavior that diverges from this document is a defect to either fix in the workflow or amend here first -- not both, and not silently.

---

## Table of Contents

- [Why CI Now](#why-ci-now)
- [Scope and Non-Goals](#scope-and-non-goals)
- [Requirements](#requirements)
- [Workflow Architecture](#workflow-architecture)
- [Workflow: gate.yml](#workflow-gateyml)
- [Workflow: container.yml](#workflow-containeryml)
- [Workflow: release.yml (post-MVP)](#workflow-releaseyml-post-mvp)
- [Workflow: upstream.yml (post-MVP)](#workflow-upstreamyml-post-mvp)
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

The MVP retros named four problems the local gate is structurally unable to solve. Each one is a specific failure mode CI is designed to catch:

1. **Spec-vs-implementation drift is invisible until tier 3 runs.** Tier 1+2 tests pin on exception classes; the user-facing exit code is observable only by running the real binary. Several exit-code mismatches were latent for the entire MVP because no tier-1 test pinned on the user-facing exit code, and no tier-3 suite existed to catch the discrepancy at the binary boundary. CI is where tier 3 finally runs continuously.

2. **Single-day batch-merge made the merge itself the test.** Multiple PRs landed in the same window, each independently green, but the post-merge state on `main` was not validated as a single coherent run before the merges. CI on every push to `main` provides exactly that validation -- a clean checkout of the merged tip, the four-command gate, and the tier-3 sweep, with no developer-machine state in the picture.

3. **`uv.lock` churn slips into unrelated PRs.** Mechanical lockfile rewrites from uv-version drift have polluted the history more than once. The `uv.lock` policy paragraph is in [CODING_RULES.md §uv.lock policy](CODING_RULES.md#uvlock-policy); CI is where the policy gets *enforced*, by pinning the `uv` version that runs the gate so the reference rewrite is deterministic.

4. **The act + Docker-Desktop interaction is non-obvious.** The post-MVP CI work needs to be act-rehearsable locally. That is not free: act under Docker Desktop refuses to bind-mount the desktop socket into the runner unless File Sharing is configured, and the same docker-compose paths that work on cloud GHA need a specific fixture layout to work under act's `--bind` mode. The recipes -- and the boundary between "this works locally" and "this only works on cloud GHA" -- belong in this document, not in tribal knowledge.

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

### Post-MVP (in scope to *describe* here, not for the first PR to land)

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
- **No Windows runners for MVP CI.** Per [CODING_RULES.md §Language and Runtime](CODING_RULES.md#language-and-runtime), Windows is not yet a target. CI matches the spec.

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
- Adamant container image -- pinned by tag (e.g., `ghcr.io/lasp/adamant:0.2`), not `:latest`.
- Third-party GitHub Actions -- pinned to a full SHA with a comment (`org/action@<sha>  # vX.Y.Z`).

Renovate or Dependabot handles version bumps via PR; CI itself does nothing dynamic.

### CI7. No Bypass

There is no `[skip ci]` short-circuit. There is no "trivial change" branch protection that lets the gate be optional. Documentation-only PRs run the gate; whitespace PRs run the gate. The single exception: pull-request *draft* status pauses tier 3 (per [CI3](#ci3-tier-3-runs-on-every-non-draft-pr)). Drafts still run the gate.

### CI8. Failure Is Self-Diagnostic

Every failed run uploads enough artifact for forensic diagnosis without a re-run:

- Coverage HTML and XML.
- JUnit XML from pytest, with a rendered summary on the PR's checks tab.
- The `docker compose logs` and `docker inspect` of the test container on tier-3 failures.
- A unified `index.html` artifact bundle linking the above, with a provenance banner naming the commit SHA, branch/PR, GitHub run ID, OS, and run timestamp.

Retention 14 days. Artifact names are stable so links from PR comments do not rot. Theme: dark mode with admt's signature gold (`#CFB87C`) accent on actionable elements -- see [Artifacts and Provenance](#artifacts-and-provenance).

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
    container.yml     # Tier 3: pytest -m container, against ghcr.io/lasp/adamant:0.2
    release.yml       # Post-MVP: build wheel + publish to PyPI on release
    upstream.yml      # Post-MVP: weekly tier 3 against latest Adamant image
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

`fail-fast: false` so a Linux failure doesn't hide a separate macOS failure. Both must pass. [CODING_RULES.md §Language and Runtime](CODING_RULES.md#language-and-runtime) names Linux + macOS as the MVP target platforms; CI matches the spec.

### Steps (high level)

1. **Checkout** -- `actions/checkout@v6` with `persist-credentials: false`.
2. **Set up uv** -- `astral-sh/setup-uv@v6` with `version:` read from `.uv-version` and `enable-cache: true`. The action also reads `.python-version` natively.
3. **`uv sync --dev --frozen`** -- `--frozen` so CI cannot rewrite the lockfile. If the lockfile is stale relative to `pyproject.toml`, this step fails and the developer must rerun `uv sync` locally and commit the lockfile bump.
4. **Run gate command 1**: `uv run ruff format --check src/ tests/`.
5. **Run gate command 2**: `uv run ruff check src/ tests/`.
6. **Run gate command 3**: `uv run mypy src/`.
7. **Run gate command 4**: `uv run pytest --cov --cov-branch --cov-fail-under=100 --junitxml=gate-junit.xml -m "not container"`.
8. **Generate themed coverage HTML** (always) -- `uv run coverage html --extra-css tests/ci_assets/admt-dark.css --title "admt coverage @ ${SHORT_SHA}"`.
9. **Render unified `index.html`** (always) -- `python tests/ci_assets/render_summary.py` (see [Artifacts and Provenance](#artifacts-and-provenance)).
10. **Upload artifact bundle** (always) -- `actions/upload-artifact@v7` with everything in `_artifacts/` (HTML index, coverage HTML, coverage XML, JUnit XML, log tail). Name: `gate-${{ matrix.os }}-${{ github.run_id }}`.
11. **Post step summary** (always) -- a tabular summary on `$GITHUB_STEP_SUMMARY` with gate command results, coverage %, and a link to the unified index inside the artifact.

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
2. `uv tool install --python 3.14 --reinstall --editable .` -- install admt onto PATH.
3. `bash tests/container/run.sh` -- the host script does everything else:
   - Pulls `ghcr.io/lasp/adamant:0.2` (idempotent; `actions/cache@v5` keyed on the image digest amortizes pulls).
   - Bootstraps `tests/container/_workspace/adamant/` by cloning `https://github.com/lasp/adamant.git` at a pinned ref (or symlinking a local checkout if `ADMT_LOCAL_ADAMANT=<path>` is set; see [Tier 3 Fixture Strategy](#tier-3-fixture-strategy)).
   - Runs `pytest tests/container/ -m container --junitxml=container-junit.xml`.
   - On failure, dumps `docker compose ... logs` and `docker inspect` for every container the suite touched into `_artifacts/container-logs/`.
4. Render unified `index.html` (always) -- same renderer as gate.yml, themed.
5. Upload artifact bundle.

### Configuration Matrix

For the first PR, single-config: `project: standalone`. A follow-up extends to `project: [standalone, multi-repo]` so path-mapping is exercised against multiple bind mounts. The matrix dimension lives on the host script via an env var, not in the workflow YAML, so local rehearsal can pick a config too.

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

## Workflow: release.yml (post-MVP)

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
5. **arm64-verification** -- `docker/setup-qemu-action@v4` + `linux/arm64` execution of the wheel against `ghcr.io/lasp/adamant:0.2-arm64`. Echoes `adamant/.github/workflows/test_all_arm64.yml`. Advisory-only for the first published release; required-blocking once the first arm64 admt user emerges.

### act Compatibility

Build-wheel runs under act. publish-pypi is skipped (`!env.ACT`). gate-release and container-release follow the same recipes as gate.yml and container.yml.

---

## Workflow: upstream.yml (post-MVP)

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

- `act` 0.2.84+ installed (`pacman -S act` on Manjaro, `brew install act` on macOS, or built from source in `~/cs/act`).
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
ADMT_LOCAL_ADAMANT=/home/me/cs/adamant \
DOCKER_HOST="unix:///var/run/docker.sock" \
  act pull_request -j container -W .github/workflows/container.yml --bind \
  --container-options "-v $ADMT_LOCAL_ADAMANT:$ADMT_LOCAL_ADAMANT"
```

What happens inside:

1. `tests/container/run.sh` sees `ADMT_LOCAL_ADAMANT` set and creates a symlink `tests/container/_workspace/adamant -> $ADMT_LOCAL_ADAMANT` instead of cloning.
2. The symlink lives in the bind-mounted admt workspace, so it appears at the same path on host and runner.
3. The bind-mount from `--container-options` makes the symlink target also valid on both sides.
4. `admt env init` resolves the symlink, parses the *live* `docker-compose.yml`, and registers volume mounts pointing at the live host path.
5. `admt env start` tells the host Docker daemon to bind the live path into the Adamant container -- the daemon resolves the path on the host, where it really exists.
6. Tests exercise admt against the developer's actual in-progress Adamant changes.

**Caveats:**

- The mount is read-write, not read-only -- a real Adamant build mutates `build/` directories under the source tree. The developer's live working tree will accumulate `build/` artifacts during the test run. This is intentional (matches what would happen if they ran `redo` directly), but the developer should `git status` before and after to avoid surprises.
- Permissions matter. The runner image's default user (`ubuntu` at UID 1001 in `catthehacker/ubuntu:act-latest`) may not match the host's UID. If the live Adamant tree is not world-readable and writable, append `--user $(id -u):$(id -g)` to `--container-options` so the runner inherits the host UID.
- The pin audit ([Version-Pin Parity Audit](#version-pin-parity-audit)) is unaffected: it checks `_pins.env` against consumer files, not against the actual fixture contents. Live mode does not violate any pin.

For day-to-day "I'm changing admt and want to verify against my live Adamant" iteration, the simpler form is to skip act entirely:

```bash
ADMT_LOCAL_ADAMANT=/home/me/cs/adamant bash tests/container/run.sh
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

Pin-override mode (e.g., `ADAMANT_REF=main`) clones a *different* Adamant source than the container at `:${ADAMANT_TAG}` was built from. A test that depends on source-vs-binary parity (e.g., expects a specific `redo what` output that matches a specific source revision) may behave inconsistently. The expected behavior for this mode is the same as for the upstream contract test job ([upstream.yml](#workflow-upstreamyml-post-mvp)): tier 3 is allowed to fail, and the failure is the signal that upstream has moved.

### conftest.py Responsibilities

`tests/container/conftest.py` exposes session-scoped fixtures that:

- Locate `_workspace/adamant/`. If missing, fail with a clear "run `tests/container/run.sh` first" message (the host script bootstraps; conftest does not).
- Register the project: `subprocess.run(["admt", "env", "init", "tests/container/_workspace/adamant"])`.
- Start the container: `admt env start`.
- Yield a `ProjectFixture` dataclass with the project root, container name, and a known-state component path that's safe to build/test/style against.
- Teardown: `admt env stop` + remove the registration.

The fixture is **session-scoped** so the container starts once per pytest run, not once per test. Tests that mutate state restore it (e.g., a test that changes the active project switches it back).

### Why Not a Pre-Built Test Project?

A pre-built minimal Adamant project committed to admt (instead of cloning) is tempting but rejected:

- It would drift from real Adamant immediately.
- It would not exercise the path-mapping code against a layout developers actually use.
- It would have to be re-tested every time Adamant's compose-file format moved.

Cloning standalone Adamant at a pinned ref keeps tier 3 honest about what it is: a contract test against the real upstream.

### Multi-Repo Configuration (Follow-Up)

For the first PR, the matrix is `project: [standalone]`. A follow-up adds `multi-repo`, which tests admt against a layout that mounts more than one repo (e.g., adamant + a stub component repo). The multi-repo fixture clones two repos into `_workspace/` and ships a hand-written compose file that mounts both. Path-mapping bugs that only manifest with multiple bind mounts are caught here.

### Eventual: `admt create test-project`

Once the post-MVP `admt create project` lands, the bootstrap can shift to `admt create project tests/container/_workspace/test-project` and the standalone-Adamant clone becomes one of two configs rather than the default. This is forward-looking, not a blocker for the first CI PR.

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
| `ADMT_ENV` override | 2 + 3 | Tier 3 confirms the override actually targets the right project |
| `NO_COLOR` env var | 2 + 3 | Tier 3 confirms ANSI codes are stripped from real output |
| Short alias | 2 + 3 | Tier 3 confirms `admt b` works on the binary |

### Per-Command Coverage

The 24 concrete commands (see [Appendix C](#appendix-c-command-inventory)) split into two categories:

- **3 pure-host commands** (`env init`, `env use`, `env list`) -- no container, no project required (`env init` *creates* the project, `env use`/`env list` only read config). Tier 3 coverage: a single happy-path test that the binary works against `tests/container/_workspace/adamant/`.
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

When the post-MVP plugin system lands, plugin authors register `Command` subclasses via Python entry points. The same self-audit machinery extends naturally:

- The audit discovers plugin commands the same way it discovers built-in commands -- via `Command.__subclasses__()` after entry-point loading.
- Plugin authors point the audit at their plugin's test directory via a config setting in `pyproject.toml`:

  ```toml
  [tool.admt.ci]
  test_dirs = { tier1 = "tests/unit", tier2 = "tests/integration", tier3 = "tests/container" }
  ```

- A plugin author's CI imports admt's audit fixture and runs it against their own commands.

This is forward-looking; the convention should be considered when designing the plugin system, but the first CI PR does not need to support it.

---

## Artifacts and Provenance

Every CI run produces a single artifact bundle, themed in admt's signature dark + gold palette, with provenance metadata visible at the top of the unified `index.html`. The bundle answers, in one place, every question a reviewer or AI agent might ask about a run: did the gate pass, was coverage met, did every architectural-coverage and pin-parity audit pass, what exactly was tested (commit, OS, pinned versions), and -- if anything failed -- where to click for detail.

The structure deliberately matches the *shape* of the gate, not just its outputs. Coverage and JUnit are necessary; they are not sufficient. The architectural self-audit and the version-pin parity audit each produce a structured table that is more useful as a panel than as a bullet hidden inside JUnit XML, so the bundle gives them dedicated artifacts.

### Bundle Layout

```
_artifacts/
  index.html              # dashboard: provenance banner + status panels + audit tables
  admt-dark.css           # shared theme
  provenance.json         # machine-readable: commit, run_id, branch, OS, pin manifest

  gate/                   # tier 1 + tier 2 results (everything pytest produced)
    junit.xml             # raw -- the source of truth that the rendered views below derive from
    summary.html          # rendered, grouped by test family

  coverage/               # 100% threshold check
    index.html            # coverage.py's HTML report, themed via --extra-css
    coverage.xml          # raw

  audits/                 # architectural-health audits, broken out for visibility
    self-audit.html       # command/service/adapter/alias coverage matrix
    pin-audit.html        # pin manifest with parity status per consumer
    spec-alignment.html   # gate command vs TEST_PLAN, run.sh invocation, etc.

  container/              # tier 3 only
    junit.xml
    summary.html
    docker-compose.log
    docker-inspect.json
```

### Provenance Banner

The unified `index.html` opens with a banner that names exactly what was tested, where, and when:

```
admt CI run
  commit        <SHORT_SHA>           <full SHA, link to GitHub>
  branch / PR   <branch or PR#>       <link to ref/PR>
  workflow      gate (or container)   <link to workflow run>
  runner OS     ubuntu-24.04          <runner-OS info>
  started       2026-05-01T12:34:56Z  <timestamp>
  duration      2m 14s                <elapsed>
  status        PASS                  <gold checkmark / red X>

  pinned versions
    uv          0.7.13                <link to .uv-version line>
    python      3.14                  <link to .python-version>
    adamant     0.2 (v0.2.0)          <link to _pins.env>     [tier 3 only]
```

The "pinned versions" block is read from the [Version-Pin Parity Audit](#version-pin-parity-audit)'s `PINS` manifest at render time, so the banner is always consistent with what the audit asserts. If the audit fails (a consumer disagrees with the source), the banner shows the pin in red with the disagreeing file inlined; the dashboard's pin-audit panel below has full detail.

The banner is also rendered as JSON in `provenance.json` for machine consumption (status-badge generator, PR-description bot, future plugin authors comparing their CI run's pin state to admt's).

### Dashboard Panels (in `index.html`)

Below the banner, the dashboard arranges four status panels in a single column. Each panel is a one-glance summary; each links to the detail view in its dedicated artifact directory.

#### Gate panel

The pytest results panel. The four-command gate's per-step outcomes (`ruff format`, `ruff check`, `mypy`) live in GitHub's native workflow run page (the "checks" tab) rather than in the dashboard, because they are workflow steps, not JUnit-emitted test results -- the renderer cannot synthesize them without a separate inputs file. The dashboard reflects what JUnit XML actually contains:

```
gate                                                 PASS
  pytest          OK                                 N tests, M skipped, 0 failed
                                                     -> gate/summary.html
```

A failure inlines the first three failure assertions verbatim and links to the JUnit summary for the rest. For ruff/mypy/format outcomes, the panel footer links to the workflow run's checks tab where GitHub surfaces step-level results natively. Reproducing those steps inside the dashboard would require either a step-output bridge (each gate step writes a JSON record the renderer reads) or running the four commands from inside a pytest wrapper. Both add complexity for a feature GitHub already provides; deferred ([Risks, Tradeoffs, and Honest Estimates](#risks-tradeoffs-and-honest-estimates)).

#### Coverage panel

```
coverage                                             100.0%
  line            100.00%                            X/Y lines
  branch          100.00%                            P/Q branches
                                                     -> coverage/index.html
```

If coverage drops below 100%, this panel is the loudest -- the cell turns red and lists the top three uncovered modules with line counts. The full report is one click away.

#### Architectural self-audit panel

A compact matrix showing every command/service/adapter/alias/flag and which tiers cover it. The detail view (`audits/self-audit.html`) is a full table; the dashboard cell is a roll-up:

```
architectural coverage                               every surface tested at the right tier
  commands        24 of 24 covered at all required tiers
  services        4 of 4 with unit tests
  adapters        4 of 4 with unit tests
  aliases         11 of 11 covered at tier 2 + tier 3
  global flags    5 of 5 covered at tier 2 + tier 3   (--verbose, --quiet, --debug, --yes, --force)
  subcmd flags    9 of 9 covered at tier 2 + tier 3   (--all, --undo, --volumes, --image, --remove-all)
  env-var flags   4 of 4 covered at tier 2 + tier 3   (ADMT_NONINTERACTIVE, =0, ADMT_ENV, NO_COLOR)
                                                     -> audits/self-audit.html
```

A failure surfaces the specific gap in the panel: `BuildCommand missing tier-3 test`, `--debug missing tier-3 test`, or `ADMT_NONINTERACTIVE=0 missing tier-2 test`, with a direct link to the JUnit failure node.

#### Pin-parity panel

```
pin parity                                           5/5 pins consistent
  uv              .uv-version -> 0.7.13              referenced by 3 consumers
  python          .python-version -> 3.14            referenced by 3 consumers
  adamant_tag     _pins.env -> 0.2                   referenced by 2 consumers   [tier 3 only]
  adamant_ref     _pins.env -> v0.2.0                referenced by 1 consumer    [tier 3 only]
  third-party actions                                7 of 7 SHA-pinned with version comments
                                                     -> audits/pin-audit.html
```

A failure shows the pin in red and inlines the disagreeing consumer (e.g., `gate.yml line 42 embeds uv 0.7.12 != .uv-version (0.7.13)`).

#### Tier-3 panel (only on `container.yml` runs)

```
tier 3                                               PASS  (35 tests, 0 failed, 0 skipped)
  env lifecycle    7 tests OK                        -> container/summary.html#env-lifecycle
  passthrough      11 tests OK
  templates        4 tests OK
  failure paths    8 tests OK
  global flags     12 tests OK
  aliases          11 tests OK (one per alias)
  signal handling  1 test OK
                                                     -> container/summary.html
```

A failure inlines the failing test's name, the assertion, and the relevant tail of `docker-compose.log`.

### Detail Views

The audit detail pages are themed Jinja2 renderings; each is one HTML file with a single table.

#### `audits/self-audit.html` -- coverage matrix

A wide table covering every audited surface:

| Subject | Layer | Tier 1 | Tier 2 | Tier 3 |
|---|---|---|---|---|
| `build` (`BuildCommand`) | command | OK | OK | OK |
| `env init` (`EnvInitCommand`) | command | OK | OK | n/a (config-only) |
| `b` (alias for `build`) | alias | -- | OK | OK |
| `--verbose` | global flag | -- | OK | OK |
| `--all` (on `test`, `style`, ...) | subcommand flag | -- | OK | OK |
| `--volumes` (on `env rm`) | subcommand flag | -- | OK | OK |
| `ADMT_NONINTERACTIVE` | env-var flag | -- | OK | OK |
| `ADMT_NONINTERACTIVE=0` (off) | env-var flag | -- | OK | OK |
| `services/config.py` | service | OK | -- | -- |
| `adapters/docker.py` | adapter | OK | -- | -- |
| ... | | | | |

Cells are gold-OK on pass, red on fail, gray-`n/a` when the tier doesn't apply (e.g., pure-config commands skip tier 3; flags don't have tier-1 unit tests because they're CLI-layer concerns). Each OK cell links to the test file; each fail cell links to the JUnit failure node. The table sorts by Layer, then by Subject. A toolbar at the top toggles "show failures only" for triage mode and "filter by layer" so the reader can focus on (e.g.) just the flag rows.

#### `audits/pin-audit.html` -- pin manifest with parity

For each pin in the `PINS` manifest:

| Pin | Source file | Source value | Consumer | Local value / match | Status |
|---|---|---|---|---|---|
| uv | `.uv-version` | `0.7.13` | `.github/workflows/gate.yml` | references file | OK |
| uv | `.uv-version` | `0.7.13` | `.github/workflows/container.yml` | references file | OK |
| uv | `.uv-version` | `0.7.13` | `CLAUDE.md` | mentions file | OK |
| python | `.python-version` | `3.14` | `pyproject.toml` | `requires-python = ">=3.14"` | OK |
| ... | | | | | |

Plus a footer block listing third-party action pins by file and line number with their SHA + version comment, and the orphan-tag scan results (which files were scanned and how many `ghcr.io/lasp/adamant:<tag>` references were found outside `_pins.env`).

#### `audits/spec-alignment.html` -- spec-vs-CI alignment

A short page for the spec/CI alignment guards. Two sections:

- **Gate command alignment**: side-by-side diff between the four gate commands in `TEST_PLAN.md` and the corresponding `run:` lines in `gate.yml`, with the `--junitxml=...` and `-m "not container"` extensions called out as expected differences.
- **Workflow contracts**: each guard from `tests/unit/test_ci_alignment.py` listed with PASS/FAIL (e.g., "container.yml invokes tests/container/run.sh: OK").

This page is small (one screen) but irreplaceable when a contributor wonders "is the workflow actually running the spec'd gate?" -- one click, one glance, an unambiguous yes or no.

### Theme

`tests/ci_assets/admt-dark.css` is a small CSS file (~150 lines) that:

- Sets a dark base palette (`#1a1a1a` background, `#e0e0e0` text).
- Uses `#CFB87C` (admt's signature gold) as the *accent* color: links, navigation chrome, the `100%` coverage badge, the PASS marker, hover states, the OK cells in audit tables.
- Keeps content (code lines, test names, failure messages) in the default text color so it stays skimmable.
- Sets `.failed` and `.uncovered` red to *contrast* with the gold-passing motif, never overlapping in hue.
- Sets `.na` a desaturated gray so "not applicable" cells (e.g., tier-3 column for pure-config commands) read as neutral, not absent.
- Embeds via `coverage html --extra-css` for the coverage report; via `<link rel="stylesheet">` in every other artifact HTML.

The theme is shared across the dashboard, the gate/container summaries, the audit pages, and the coverage report. One mental model for the reader, gold accent only on actionable elements.

### Renderer

`tests/ci_assets/render_summary.py` is a Jinja2-based renderer that:

- Reads `gate-junit.xml` and `container-junit.xml` (when present).
- Reads `coverage.xml` for the percentages.
- Reads the `PINS` manifest from `tests/unit/test_pin_audit.py` (importing it directly -- the manifest is the source of truth, and the renderer's job is to project it into the UI).
- Reads provenance from the environment (`GITHUB_SHA`, `GITHUB_RUN_ID`, `GITHUB_REF_NAME`, `RUNNER_OS`); falls back to `git rev-parse HEAD`, `git branch --show-current`, etc. when the GitHub envs are absent (so local rehearsal produces the same layout).
- Filters `gate-junit.xml`'s test results into three buckets by name prefix:
  - `test_command_has_*` / `test_alias_has_*` / `test_global_flag_*` / `test_subcommand_flag_*` / `test_env_var_flag_*` / `test_service_has_*` / `test_adapter_has_*` -> the architectural self-audit panel + detail (one row per parametrized item).
  - `test_pin_*` / `test_no_orphan_*` / `test_third_party_action_*` -> the pin-audit panel + detail.
  - `test_gate_workflow_*` / `test_container_workflow_*` -> the spec-alignment panel + detail.
  - Everything else -> the gate panel.
- Emits `_artifacts/index.html`, `_artifacts/gate/summary.html`, `_artifacts/audits/{self-audit,pin-audit,spec-alignment}.html`, `_artifacts/container/summary.html` (tier 3 only), and `_artifacts/provenance.json`.

The renderer is a single Python file (~250 lines including the Jinja2 templates). No new dev dependencies: `jinja2` is already a transitive dep that the dev group can pull in cleanly; if even that is too much, the renderer can fall back to f-string + `html.escape` (the templates are simple enough). The renderer is deterministic: same junit.xml + same coverage.xml -> byte-identical HTML, so a reviewer can compare two runs by diffing the HTML directly.

### Coverage HTML

`coverage.py`'s native HTML reporter accepts `--extra-css` and `--title`:

```
uv run coverage html \
  --extra-css tests/ci_assets/admt-dark.css \
  --title "admt coverage @ ${SHORT_SHA}"
```

The output lands in `_artifacts/coverage/`. The dashboard's coverage panel links into it for per-file detail.

### Why Not pytest-html?

`pytest-html` is mature but doesn't fit the bundle's structure. It produces *one* HTML file per pytest run; we need a dashboard that splits results across panels (gate, audits, tier 3) with audit-specific table layouts. Even if pytest-html could be themed to match, its single-page model would force every audit table into a nested collapsible section -- exactly the noise the user wanted to avoid. A 250-line custom renderer with full control over the bundle structure is cheaper than fighting pytest-html's defaults.

The same logic applies to `coverage`'s HTML reporter -- but in that case, coverage's per-file drilldown is genuinely useful and the `--extra-css` hook lets us theme it without owning the rendering. Coverage HTML stays; pytest-html does not get added.

### Retention and Naming

Artifact name: `<workflow>-<matrix>-${{ github.run_id }}` (e.g., `gate-ubuntu-24.04-12345678`). Stable enough to link from a PR comment; namespaced enough to not clash. Retention: 14 days (default for `actions/upload-artifact@v7`). Long enough to debug a failed run a few days later; short enough to not balloon storage.

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

Bumping the pair is a single commit: edit `_pins.env`, run tier 3 locally, commit titled `chore: bump Adamant pin to <tag> / <ref>`. The audit ([Version-Pin Parity Audit](#version-pin-parity-audit)) ensures both values are present, well-formed, and that no consumer file embeds a different tag or ref. The *remote* check -- does `:0.2` actually correspond to `v0.2.0` on Adamant's side? -- runs in [upstream.yml](#workflow-upstreamyml-post-mvp): the OCI image label `org.opencontainers.image.revision` is read from the pulled container and compared to `ADAMANT_REF`. Drift opens an `upstream-drift` issue.

---

## Drift-Prevention Guards

CI cannot become a separate spec. Two test modules run as part of the gate and keep CI honest:

- **`tests/unit/test_ci_alignment.py`** -- spec/CI alignment. Verifies the workflow YAML matches TEST_PLAN.md and this plan.
- **`tests/unit/test_pin_audit.py`** -- version-pin parity. Verifies every embedded version reference matches its single source of truth.

The pattern matches the existing `tests/unit/test_architecture.py`: parametrized tier-1 tests, no Docker required, fail loudly with a specific message.

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
    """No file outside _pins.env may hardcode a `ghcr.io/lasp/adamant:<tag>` value.

    Catches the case where a contributor copies a tag into a workflow comment
    or a docstring and forgets to update it when _pins.env moves.
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
        if not path.is_file() or path.suffix not in {".md", ".yml", ".yaml", ".sh", ".py", ".toml"}:
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
    """Every `uses: org/action@<ref>` either uses an Anthropic-first-party action
    (actions/*, github/*) at a major version (`@v6`), or pins a 40-char SHA with
    a `# vX.Y.Z` comment naming the human version. Catches drift between the
    SHA and the comment, and unpinned third-party actions.
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
- An `ADAMANT_TAG=0.2` in `_pins.env` while a stale `ghcr.io/lasp/adamant:0.1` lurks in a workflow comment, README badge URL, or doc snippet.
- An `ADAMANT_REF` that is empty or non-existent.
- A third-party GitHub Action `uses: third-party/foo@v1` (unpinned) or pinned to a SHA without a `# vX.Y.Z` comment.

#### What This Does Not Catch

- The *remote* parity question -- "does `:0.2` actually equal `v0.2.0` in Adamant's source?" That requires a network round-trip to GHCR to read the OCI label `org.opencontainers.image.revision`. Performed in [upstream.yml](#workflow-upstreamyml-post-mvp), not in the gate.
- Drift between a tagged third-party action (`@v6`) and the actual code at that tag. Renovate/Dependabot is the tool for this; the audit only enforces the local-pinning convention.
- Semantic correctness of the value (e.g., does the pinned uv version still install on Python 3.14?). That's caught by the gate run itself.

The audit runs in tier 1, costs a few milliseconds, and fails parametrized so contributors see exactly which (pin, consumer) pair is broken. Adding a new pin -- a new tool, a new paired version -- is a one-entry edit to the `PINS` tuple.

---

## Implementation Order

The first CI PR ships gate.yml plus the alignment tests and the self-audit. Tier 3 follows in a second PR because it has materially more setup. Post-MVP workflows follow in their own PRs.

**Each PR opens green** -- it merges only when its own gate run passes against the proposed workflow.

### PR 1: gate.yml + alignment + self-audit (structural only)

- `.github/workflows/gate.yml` -- the four-command gate, Linux + macOS matrix.
- `.uv-version` -- pin uv to the version that wrote the current `uv.lock`.
- `.gitignore` additions for `gate-junit.xml`, `coverage.xml`, `_artifacts/`.
- `tests/ci_assets/admt-dark.css` -- the shared theme.
- `tests/ci_assets/render_summary.py` -- the unified-index renderer.
- `tests/unit/test_ci_alignment.py` -- the spec/CI sync tests.
- `tests/unit/test_pin_audit.py` -- the version-pin parity audit. The `PINS` manifest in PR 1 covers `uv`, `python`, and the third-party-action SHA convention. The Adamant pair (`ADAMANT_TAG`/`ADAMANT_REF`) is added in PR 2 alongside `_pins.env`.
- `tests/unit/test_command_test_coverage.py` -- the structural self-audit (parametrized over commands/services/adapters/aliases plus the global, subcommand, and env-var flag families). The tier-3 arm of every audit family is conditional: it skips when `tests/container/` is empty, so PR 1 doesn't block on tier-3 tests existing yet. The tier-2 arm runs unconditionally -- existing `tests/integration/` tests already exercise most flags; this PR makes the coverage explicit.
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

### PR 3 (post-MVP): release.yml

- `.github/workflows/release.yml`.
- PyPI trusted-publishing setup (one-time, in repo settings).
- Wheel-publish smoke against TestPyPI before the first real release.
- Optional `arm64-verification` job (advisory-only for v0.2; required-blocking once the first arm64 user appears).

### PR 4 (post-MVP): upstream.yml

- `.github/workflows/upstream.yml`.
- `.github/ISSUE_TEMPLATE/upstream-drift.md` so auto-opened issues have a consistent format.
- `tests/contract/test_redo_what_format.py` and friends -- frozen samples of upstream output formats with parser tests, so contract drift surfaces locally first.

### PR 5+ (post-MVP): polish

- ARM64 release verification (post-`release.yml`).
- Codecov integration (after coverage is stable on `main`).
- Whole-file-size trip-wire after the post-MVP refactor pass.
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

The post-MVP plugin system registers `Command` subclasses via Python entry points. The CI surface should generalize to plugin authors without forcing them to re-invent the wheel.

The convention:

1. **Plugins inherit admt's gate.** A plugin's pyproject.toml declares `[dependency-groups]` mirroring admt's; their CI runs the same four-command gate. This plan describes a reusable workflow file (`gate.yml`'s logic published as a callable workflow) that plugins can `uses:` directly.
2. **Plugins extend the self-audit.** A plugin's `tests/unit/test_command_test_coverage.py` imports the audit machinery from admt and runs it against the plugin's `Command` subclasses (same pattern as importing a pytest fixture). The audit's test-directory configuration is read from `[tool.admt.ci]` in `pyproject.toml`.
3. **Plugins exercise tier 3 against admt itself.** Plugins that expose container-touching commands need their own tier-3 fixture (since the plugin's commands operate on a real Adamant project). The fixture pattern from `tests/container/conftest.py` is reusable: clone Adamant, register, start, exercise the plugin's commands.

This is forward-looking, but the conventions land in this plan now so the post-MVP plugin design has a target.

---

## Risks, Tradeoffs, and Honest Estimates

This plan is exhaustive by design (admt's culture is "spec the boring stuff" -- agents stay inside specs that are complete enough to stay inside). Exhaustive specs accumulate cost. This section calls out where the plan's reach exceeds its grasp, what's likely to spiral, and what to defer if the first PR's schedule is tight.

### Wall-time reality vs. estimate

The original Appendix B estimates put tier 3 at "~3m median, ~7m cold". After auditing how cloud GHA runs interact with admt's environment activation -- and remembering that ARCHITECTURE.md §Environment Activation explicitly warns that first-run activate "can take many minutes (pip installs, alr builds, gprbuild of the Pico runtime)" -- the realistic numbers are 2-4x higher:

- **Cloud GHA runners are fresh per job.** The activate snapshot at `/tmp/admt/<project>/` does not persist across runs unless we cache it explicitly (see [Roadmap §Medium-term](#medium-term)).
- **Image pull on cold cache** is ~2-3 minutes for an Adamant image of typical size; warm with `actions/cache@v5` it's ~10-30 seconds.
- **Adamant first-run activate** is the dominant cost. Steady-state on cloud is 5-10 minutes per run *until* snapshot caching lands.
- **Tier 3 test execution itself** (admt commands against the running container) is 2-5 minutes for the suite outlined in PR 2.

Sum: realistic tier-3 wall time is 8-15 minutes per run on cloud GHA. Locally with image and snapshot reuse, 3-5 minutes is achievable. The tightened Appendix B numbers reflect this.

**Action**: bump every `timeout-minutes` for container-touching jobs to 60 (was 30). Plan PR-merge SLAs around the upper end of these ranges. Treat the activate-snapshot caching optimization as a *real* deliverable in the medium-term roadmap, not a nice-to-have.

### Items that could spiral if pursued before they're load-bearing

The plan includes several items that read as "good engineering" but whose value scales with codebase size or contributor count, not with the MVP shipping. If the first PR is tight, defer:

- **The unified HTML dashboard renderer (~250 lines).** Coverage HTML and JUnit XML are forensically sufficient. The dashboard is *user experience polish* -- it makes the artifact bundle navigable, but a developer who knows where to click can survive without it. Recommended split: PR 1 ships the gate workflow and audits with raw JUnit + coverage HTML; the renderer ships in a separate PR (call it "PR 2.5") once the first two gates are stable. The CSS, the provenance banner, and the audit detail pages can land incrementally without blocking.
- **The Gate panel "per-command" granularity.** Now correctly noted as deferred, but worth restating: the four gate commands (ruff format, ruff check, mypy, pytest) live in GitHub's native step view because reproducing them in the dashboard requires either a step-output bridge or a pytest wrapper. Both are real work for a feature GitHub provides. Defer; do not engineer a workaround in PR 1 or PR 2.
- **`test_no_orphan_adamant_tags`** (the orphan-tag scanner). Walks every text file in the repo. The exclusion list is now broader, but the scanner is still fragile: a docstring example or a doc snippet that mentions `ghcr.io/lasp/adamant:0.1` for historical reasons would false-positive. Recommend a soft mode for the first iteration: emit a warning, not a hard fail, until contributors get used to the rule.
- **`test_third_party_action_pins_have_sha_and_version_comment`.** Catches a real failure mode (unpinned third-party actions), but Renovate/Dependabot is the tool for the bump-the-SHA-and-the-comment-together job. The audit is paranoia insurance. Cheap to keep, but if it triggers more false-positives than real catches in the first month, drop it.
- **ARM64 verification** in release.yml. Already correctly marked post-MVP. Worth flagging that QEMU under cloud GHA is *very slow* (15-25 minutes for a representative test subset, per Appendix B). Not free even when "post-MVP". Don't promise it for v0.2.0.

### Assumptions worth verifying before they ship

A few claims in this plan rest on upstream behavior I have not empirically confirmed:

- **Adamant container ships OCI labels** (specifically `org.opencontainers.image.revision`). The pin-parity-remote job in upstream.yml depends on this. If the Adamant image is built without those labels, the job fails for the wrong reason ("label missing" rather than "ref mismatch"). Verify with `docker inspect ghcr.io/lasp/adamant:0.2` *before* writing the upstream workflow. If labels are absent, the remote check needs a different mechanism (e.g., maintain a manual map of `_pins.env` values to upstream release notes).
- **`catthehacker/ubuntu:act-latest` ships `docker` CLI 29.x.** Confirmed in this plan's act capability matrix probe (29.4.1-1 was observed). If a future image bump removes or downgrades the docker CLI, the act recipes break silently. The pin audit should grow a "platform image version" entry once it stabilizes.
- **`actions/cache@v5` semantics for the activate snapshot are strong enough.** The proposed cache key includes `hash(env/activate, requirements.txt, _pins.env)`. If Adamant's activate has untracked dependencies (e.g., a `setup.sh` it `source`s), the hash misses them and stale snapshots restore. Verify by reading the upstream activate script before relying on the cache.

### Where the plan is conservative

A few places where the plan errs toward more verification than is strictly needed -- intentionally, given admt's "spec the boring stuff" culture, but worth acknowledging:

- The **Spec/CI Alignment Guards** assert the gate command appears verbatim in `gate.yml`. This catches benign reformattings as failures. The reformatting failure is the right behavior (force a deliberate update to TEST_PLAN.md when CI changes), but a contributor will hit it once and grumble.
- The **per-flag tier-3 audit** asserts every flag has a tier-3 test. For flags whose semantics are entirely host-side (e.g., `--quiet` toggling stdout suppression), the tier-3 invocation is just "the binary accepted the flag without erroring" -- a one-line test. The audit insists on it anyway. Worth it for the regression-free guarantee, but not free.
- **`fail-fast: false`** on the gate matrix means a Linux-only flake doesn't hide a macOS-only flake. It also doubles the cost of a deterministic failure. Acceptable tradeoff.

### Where the plan is aggressive

- **Tier 3 on every non-draft PR.** This is a deliberate departure from MVP_PLAN.md's "smoke tests on PR merge" wording, on the grounds that the spec-vs-impl drift retros showed merge-time validation is too late. The cost is 8-15 minutes per PR. If the project has many small PRs (docs, typo fixes, dependency bumps), this cost compounds. The mitigation is the draft-PR exemption: keep WIP work in draft until ready for serious review, then flip to "ready" once tier 3 has something to test.
- **No `[skip ci]` bypass.** The plan rejects skip directives entirely. This is the right default for a 24-command CLI where every command is one PR away from breaking. A future contributor will want a skip for "I changed only README.md" -- the answer is "let CI run; it's 90 seconds".

### Recommended deferrals

If PR 1's scope is feeling tight, defer in this order:

1. The unified HTML dashboard renderer (move to PR 2.5 or post-tier-3).
2. The third-party-action SHA-comment audit (it's nice; not load-bearing).
3. The orphan-tag scanner (or run it in soft mode first).
4. The plugin-convention prose (forward-looking; safe to thin if it's not informing PR 1's design choices).

Do **not** defer:

- The four-command gate ([CI1](#ci1-same-gate-from-a-clean-machine)).
- The 100% coverage threshold ([CI2](#ci2-the-coverage-threshold-is-hard)).
- The architectural self-audit's command/service/adapter/alias/flag arms ([CI4](#ci4-every-command-every-flag-every-alias)).
- The version-pin parity audit's local arm (`uv`, `python`, third-party-action SHA-pin format -- not necessarily the comment-version pairing).
- The drift-prevention tests in `tests/unit/test_ci_alignment.py` for the gate command and run-script invocation.

These five are the load-bearing safety net. Everything else is decoration on top.

---

## Roadmap

The MVP CI surface is deliberately small: gate.yml + container.yml + alignment + self-audit. Everything below is real and useful, but it builds on the MVP CI surface and should not delay that surface from landing.

### Near-term (within a release or two of MVP CI shipping)

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

The recipes in [act Rehearsal Protocol](#act-rehearsal-protocol) were validated empirically against `act 0.2.84` on `Manjaro Linux` with both `desktop-linux` (active) and native (`/var/run/docker.sock`) Docker contexts available. Each row was probed with a small workflow that exercised the relevant capability.

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
- **Provenance** -- the metadata attached to every artifact bundle (commit SHA, run ID, branch/PR, OS, timestamp, gate version). Visible in the unified `index.html` banner and in `provenance.json`.

---

This plan is the spec for `.github/workflows/*.yml` and the supporting test-side scaffolding. Code that implements the workflows must trace to a section here. A workflow change that does not trace is rejected; behavior that diverges from this plan is a defect to either fix in YAML or amend here first -- not both, and not silently.
