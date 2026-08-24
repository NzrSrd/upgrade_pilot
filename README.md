# UpgradePilot

A dependency upgrade risk and migration planning tool. Give it a repository and a version change; it tells you what will break, how risky it is, what evidence supports that assessment, and what to do about it.

The distinguishing constraint: **every claim traces to a real line of code or a real source document.** File and line numbers come from AST analysis, not from a language model. Breaking changes come from retrieved documentation, not from a hardcoded table. Where evidence is missing, the report says so and caps its own confidence.

## Status

In development. See `PLANNING.md` for the current phase.

## How it works

```
repository ──► AST analysis ──┐
                              ├──► risk assessment ──► human decision ──► plan ──► validation
knowledge base ──► agentic RAG ┘        (when a genuine tradeoff exists)
```

Orchestrated with LangGraph; retrieval over ChromaDB; thread state checkpointed so a run can pause for a human decision and resume exactly where it stopped.

## Documentation

- `PLANNING.md` — what we are building, in what order
- `docs/adr/ADR-001-system-architecture.md` — why the architecture is what it is
- `docs/superpowers/specs/` — detailed designs
- `CLAUDE.md` — working rules

## Stack

Backend: Python, FastAPI, LangGraph, LangChain, ChromaDB, OpenAI.
Frontend: React, Vite, TypeScript, Tailwind, Lucide.
