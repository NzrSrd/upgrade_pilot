import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LeftSidebar } from "./LeftSidebar";

describe("LeftSidebar", () => {
  it("does not claim the knowledge base is ready from a directory stat alone", () => {
    // Finding I4. `api/routes/health.py`'s `chroma_dir`/`checkpoint_ready`
    // checks "deliberately do not open the Chroma store, connect to the
    // checkpointer database, or call the model provider" -- they report
    // "the readiness of the store locations ... and no more." A
    // never-ingested, empty Chroma directory passes this check and must
    // not read as "ready" to use.
    render(
      <LeftSidebar
        runs={[]}
        current={null}
        summary={null}
        health={{
          status: "ok",
          version: "test",
          checkpoint_backend: "sqlite",
          checks: { chroma_dir: true, checkpoint_ready: true, llm_configured: true },
        }}
        onNewRun={() => {}}
        onSelectRun={() => {}}
      />,
    );

    expect(screen.queryByText(/knowledge base: ready/i)).not.toBeInTheDocument();
    expect(screen.getByText(/knowledge base: storage location writable/i)).toBeInTheDocument();
    expect(screen.getByText(/checkpoints: storage location writable/i)).toBeInTheDocument();
    expect(screen.getByText(/model key: configured/i)).toBeInTheDocument();
  });

  it("says the location is not writable, not that the store is unreachable", () => {
    render(
      <LeftSidebar
        runs={[]}
        current={null}
        summary={null}
        health={{
          status: "degraded",
          version: "test",
          checkpoint_backend: "sqlite",
          checks: { chroma_dir: false, checkpoint_ready: true, llm_configured: false },
        }}
        onNewRun={() => {}}
        onSelectRun={() => {}}
      />,
    );

    expect(screen.getByText(/knowledge base: storage location not writable/i)).toBeInTheDocument();
    expect(screen.queryByText(/knowledge base: unavailable/i)).not.toBeInTheDocument();
    expect(screen.getByText(/model key: missing/i)).toBeInTheDocument();
  });

  it("does not claim a writable directory when the checkpointer is Postgres", () => {
    // The same finding as above, one setting over. On Postgres,
    // `checks.checkpoint_ready` reports only that a DSN is configured --
    // `api/routes/health.py` opens no connection -- so the SQLite wording
    // would assert a directory nobody stat-ed and a database nobody reached,
    // off a green tick. The row has to say the weaker, true thing.
    render(
      <LeftSidebar
        runs={[]}
        current={null}
        summary={null}
        health={{
          status: "ok",
          version: "test",
          checkpoint_backend: "postgres",
          checks: { chroma_dir: true, checkpoint_ready: true, llm_configured: true },
        }}
        onNewRun={() => {}}
        onSelectRun={() => {}}
      />,
    );

    expect(screen.getByText(/checkpoints: configured \(reachability not checked\)/i)).toBeInTheDocument();
    expect(screen.queryByText(/checkpoints: storage location writable/i)).not.toBeInTheDocument();
    // The knowledge-base row is still measuring a directory, so it must be
    // unaffected -- this asserts the label is chosen per row, not per response.
    expect(screen.getByText(/knowledge base: storage location writable/i)).toBeInTheDocument();
  });
});
