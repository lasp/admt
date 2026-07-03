# CLAUDE.md

For AI agents working on the admt codebase. Humans -- start with [README.md](README.md).

## Orient first, code second

admt is a CLI for the Adamant software framework -- a thin Python 3.14+ layer that composes redo, Docker, and Adamant's generators. Behavior, structure, rules, tests, and roadmap are all spec'd. Your job is to make code trace to the specs, not to invent.

The README gives you the product surface (the commands users run); the spec docs give you the implementation rules. You need both.

Before writing anything, read these in full -- in this order. **Skim is not read.**

1. [ARCHITECTURE.md](ARCHITECTURE.md) -- what to build. Layers, services, adapters, requirements (R1-R14), exact command templates.
2. [CODING_RULES.md](CODING_RULES.md) -- how to write the code. Toolchain, type discipline, subprocess rules, error handling, dependencies. The **Agent-Specific Rules** at the end are mandatory and supplement -- not replace -- everything before them.
3. [TEST_PLAN.md](TEST_PLAN.md) -- how to test. Tiers, fixture strategy, mocking boundaries, quality gate, coverage.
4. [ROADMAP.md](ROADMAP.md) -- what is not yet built. Capabilities land from here; `TODO(roadmap)` markers in code reference its items.

## When in doubt: stop and ask

Re-open the spec docs rather than guessing. Code that doesn't trace to the spec is rejected. If a task seems to require something the spec doesn't cover, propose a design amendment before writing code -- don't quietly invent semantics.

## Local development

```bash
uv sync --dev                                 # build the venv
uv run pytest                                 # run tests
uv run admt --help                            # run admt directly from source
uv tool install --python 3.14 --reinstall --editable .      # rebuild the `admt` CLI on PATH
```

The `admt` binary on PATH is the **installed** tool, not your live source. After editing, either invoke `uv run admt ...` (which executes from source without reinstalling), or run `uv tool install --python 3.14 --reinstall --editable .` to refresh the installed binary before testing end-to-end against a real container (`admt env start` and friends).

## The quality gate

A change is not done until all four pass. See [TEST_PLAN.md](TEST_PLAN.md) for the rationale, the mocking boundaries, and the 100% line+branch coverage rule.

```bash
uv run ruff format --check src/ tests/
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest --cov --cov-branch --cov-fail-under=100
```

Run it locally before each commit and again immediately before opening or updating a PR.

## CI

The same four-command gate runs in GitHub Actions on every push and pull request (`.github/workflows/gate.yml`, Linux + macOS), per [CI_PLAN.md](CI_PLAN.md) -- the same bar from a clean machine, with `-m "not container"` deselecting tier 3. The `uv` version CI runs is pinned in `.uv-version`. CI is the second eye, not a substitute: run the gate locally before pushing.
