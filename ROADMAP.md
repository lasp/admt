# admt Roadmap

Capabilities that are not yet built. The architecture ([ARCHITECTURE.md](ARCHITECTURE.md)) supports all of them without refactoring; each item lands as one or more PRs that pass the [quality gate](TEST_PLAN.md#quality-gate). `TODO(roadmap):` markers in code must reference an item on this page.

## Next Up: CI Pipeline and Packaging

- **CI pipeline, remaining tiers ([CI_PLAN.md](CI_PLAN.md)):** the tier-3 container suite and its `container.yml` workflow, then `release.yml` (PyPI publish) -- each behind its own plan and review. The four-command [quality gate](TEST_PLAN.md#quality-gate) runs in GitHub Actions on every push and pull request (`gate.yml`).
- **Documentation and packaging:** shell completion setup scripts, publishable package.
- **Upstream contract tests:** weekly verification of `docker compose` output format, `redo what` output format, compose file structure.

## Tier 2: Creation and Introspection

- `admt create component <name>` -- Schema-driven component creation (YAML + directory structure)
- `admt create record <name>`, `admt create array <name>`, `admt create enum <name>` -- Type creation
- `admt create test`, `admt create doc` -- Scaffolding for component subdirectories
- `admt info <name>` -- Inspect an entity's structure (read YAML, display summary)
- `admt validate [file]` -- Host-side or container-side schema validation
- `admt init [path]` -- Generate and copy implementation stubs (promoted from templates flow)
- Schema service and filesystem service (staging/commit/rollback)
- Interactive wizard mode for creation commands
- Per-project `.admt.yml` configuration file

## Tier 3: Mutation

- `admt add event`, `admt add command`, `admt add connector`, `admt add data-product`
- `admt add` auto-detects context from cwd (component, test, assembly)
- `--dry-run` for all commands (preview changes)

## Tier 4: Assembly and Project

- `admt create assembly <name>` -- Assembly scaffolding with wiring
- `admt add connection` -- Wire components in an assembly
- `admt create project <name>` -- Full project from scratch
- Cross-model validation (events have matching connectors, etc.)

## Tier 5: Advanced

- Plugin system via Python entry points
- Shell completion for component names and paths
- Batch operations (`admt build` on multiple directories)
- Build-path awareness (`.all_path` markers, `BUILD_PATH`, `BUILD_ROOTS`)
- COSMOS plugin generation
