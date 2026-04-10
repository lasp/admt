# CLAUDE.md

Read these four documents in full, in this exact order, before any work in this repository:

1. **[ARCHITECTURE.md](ARCHITECTURE.md)** -- what to build. Layers, services, adapters, requirements, exact command templates.
2. **[CODING_RULES.md](CODING_RULES.md)** -- how to write the code. Toolchain, type discipline, subprocess rules, error handling, dependencies.
3. **[TEST_PLAN.md](TEST_PLAN.md)** -- how to test the code. Tiers, fixture strategy, mocking boundaries, quality gate, coverage.
4. **[MVP_PLAN.md](MVP_PLAN.md)** -- in what order to build it. Phased plan with deliverables and per-phase quality gate.

**No exceptions. Skim is not read.** The design documents are the specification. Code that does not trace to them is rejected.

After reading, before writing anything, also read the **Agent-Specific Rules** at the end of [CODING_RULES.md](CODING_RULES.md#agent-specific-rules). They are mandatory and supplement -- not replace -- the rest of CODING_RULES.

When in doubt at any point: stop and ask. Re-open the spec docs rather than guessing.
