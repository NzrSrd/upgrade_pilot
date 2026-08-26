import { describe, expect, it } from "vitest";

import { ALL_STATUSES } from "../api/types";
import type { RunStatus, ViewStatus } from "../api/types";
// The raw text of the generated schema, via Vite's `?raw` import (already
// typed by `vite/client`, so no `resolveJsonModule` or Node type dependency is
// needed just to read one enum out of it at test time).
import openapiSource from "../api/openapi.json?raw";
import { viewFor } from "./view";

/**
 * Reads `RunStatus`'s enum values out of the checked-in generated
 * `openapi.json`, rather than a hand-copied literal list.
 *
 * A hand-copied list can only catch a typo made while copying it -- it cannot
 * detect the backend adding a status and regenerating the schema, which is
 * the exact drift class the generated-types approach exists to eliminate.
 * Reading the schema itself is the only version of this check that fails for
 * the right reason.
 */
function runStatusEnumFromSchema(): string[] {
  const doc: unknown = JSON.parse(openapiSource);

  const schemas = (doc as { components?: { schemas?: unknown } }).components?.schemas as
    | Record<string, { enum?: unknown } | undefined>
    | undefined;
  const enumValues = schemas?.["RunStatus"]?.enum;

  if (!Array.isArray(enumValues) || !enumValues.every((value) => typeof value === "string")) {
    throw new Error("openapi.json: components.schemas.RunStatus.enum is not a string array");
  }

  return enumValues;
}

describe("viewFor", () => {
  it("shows the configuration form when no run has been started", () => {
    expect(viewFor("idle")).toBe("configuration");
  });

  it("shows activity for a run that is queued as well as one that is running", () => {
    // A queued run has not started work. Reporting it as running would be a
    // lie about work that has not happened, but the user is still watching a
    // run, not configuring one.
    expect(viewFor("queued")).toBe("activity");
    expect(viewFor("running")).toBe("activity");
  });

  it("shows the human review panel while a decision is outstanding", () => {
    expect(viewFor("awaiting_human")).toBe("human-review");
  });

  it("shows the report for both completed statuses", () => {
    // A run with failed validation checks still produced a report, and hiding
    // it would hide the failures with it.
    expect(viewFor("completed")).toBe("report");
    expect(viewFor("completed_with_warnings")).toBe("report");
  });

  it("shows an error view for a failed run and for an orphaned one", () => {
    // `orphaned` is the status this mapping exists for: a checkpoint that
    // outlived its process cannot be represented by a spinner, and giving it
    // no view of its own ships exactly the spinner that never resolves.
    expect(viewFor("failed")).toBe("error");
    expect(viewFor("orphaned")).toBe("error");
  });

  it("shows an error view for the frontend's own \"unavailable\" state too", () => {
    // Fix round 4: `unavailable` is not a status the backend can derive --
    // like `idle`, it describes what this client knows (a poll already came
    // back refused, with no snapshot ever loaded), not a checkpoint. It
    // routes to the same view a `failed` run does because `ErrorView`
    // already handles a `null` snapshot -- that is the copy branch fix
    // round 1 built for exactly this.
    expect(viewFor("unavailable")).toBe("error");
  });

  it("maps every status the backend can derive", () => {
    // Guards the case the table cannot: a status added to the backend enum and
    // regenerated into the schema, with no view chosen for it.
    for (const status of ALL_STATUSES) {
      expect(viewFor(status)).toBeTypeOf("string");
    }
  });

  it("routes the frontend's own idle state too", () => {
    const every: ViewStatus[] = [...ALL_STATUSES, "idle"];
    expect(every.map(viewFor).filter(Boolean)).toHaveLength(8);
  });

  it("matches the backend's RunStatus enum exactly, read from the generated schema", () => {
    // `types.test.ts` asserts ALL_STATUSES against a hardcoded copy of the same
    // seven literals, which catches a typo inside types.ts but not the backend
    // adding a status and regenerating the schema without anyone touching
    // types.ts. Reading the enum out of openapi.json here closes that gap: a
    // hand-copied list cannot detect the drift it exists to detect.
    const schemaStatuses = runStatusEnumFromSchema();

    expect([...ALL_STATUSES].sort()).toEqual([...schemaStatuses].sort());

    for (const status of schemaStatuses) {
      expect(viewFor(status as RunStatus)).toBeTypeOf("string");
    }
  });
});
