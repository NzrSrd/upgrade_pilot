# CLAUDE.md — Working rules for UpgradePilot

Rules to follow when working in this repository. For *what* we are building and in what order see `PLANNING.md`. For *why* the architecture is the way it is see `docs/adr/`.

## The one rule that matters most

**Every claim the product makes must trace to a real line of code or a real corpus document.** This is the product's entire value proposition. When in doubt, report uncertainty rather than filling the gap with plausible prose. Uncited output is a bug, not a style issue.

## Process

1. Inspect existing code before modifying it.
2. Follow `PLANNING.md` phase order unless explicitly told otherwise.
3. Do not implement future phases early.
4. Make small, atomic changes.
5. Run tests after any meaningful change.
6. Update `PLANNING.md` when a phase completes.
7. Update the relevant ADR when architecture changes.
8. If an architectural assumption looks invalid, stop and explain the problem before making a major deviation.

## Verification

9. Do not mark a requirement complete without a test or a demonstrated run.
10. Do not claim functionality works unless it has been tested. Show the output.
11. A phase is not done because the code exists. It is done when its exit criteria are demonstrably met.

## Dependencies and configuration

12. No new dependency without a stated reason. Prefer the standard library.
13. Pin versions and verify them; record resolved versions in ADR-001.
14. Configuration lives in environment variables via `pydantic-settings`.
15. Never commit API keys or secrets. `.env` is ignored; `.env.example` is committed.

## Code shape

16. Layer dependencies flow one way: `api/ → graph/ → services/ → models/`. `services/` must not import LangGraph. `graph/` must not import FastAPI.
17. Prefer typed models over dictionaries. `dict[str, Any]` in a signature needs justification.
18. All chat-model access goes through the `TrackedLLM` service. Nothing else may construct a chat model — that is the only place token usage can be missed.
19. The LLM never produces a file path, a line number, or a risk factor level. Those come from `ast` and from the threshold table.
20. Never `except: pass`. A caught exception produces an `AppError` in state and a trace event, always.
21. Derived values (`UsageSummary`, `RunStatus`) are computed, not stored. Stored duplicates drift.

## Testing

22. Unit tests touch no network and no LLM. Use the fake chat model and the fake embedding function.
23. Graph-path tests use the scripted fake chat model so they are deterministic.
24. Keep the opt-in live test (`@pytest.mark.live`). Fake-LLM tests can pass while real usage extraction is broken.
25. A new corpus document requires a golden-set case in the same change.

## What not to expose

26. The agent trace shows observable events — node boundaries, queries issued, sources retrieved and selected, decisions, validation outcomes. It does not show internal prompts or private reasoning.
27. `AppError.message` is user-facing and comprehensible. `AppError.detail` is technical and logged, correlated by `thread_id`.

## UI Design Reference

The canonical UI references are the six screenshots in `docs/ui/screenshots/`,
indexed and captioned in `docs/ui/DESIGN.md`.

**They are normative for layout, not for content.** They depict a React
17 → 18 JavaScript migration the product cannot run, and they contain figures
no field backs. Never copy a number, a label, or a scenario out of them —
`docs/ui/DESIGN.md` carries the per-screenshot list of what not to copy.

The design specification is `docs/ui/DESIGN.md`; the component hierarchy is
`docs/ui/COMPONENTS.md`.

When implementing or modifying the frontend:
1. Read `docs/ui/DESIGN.md` first.
2. Inspect the reference screenshot when visual hierarchy or layout is unclear.
3. Preserve the three-column developer-console structure unless explicitly instructed otherwise.
4. Do not replace the design with a generic chatbot layout.
5. Match the visual hierarchy, spacing, density, and interaction patterns before adding new UI patterns.
6. Keep the HITL experience as the primary interaction when the LangGraph is interrupted.
