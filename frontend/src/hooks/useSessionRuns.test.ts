import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useSessionRuns } from "./useSessionRuns";

const A = { threadId: "t-1", dependency: "pydantic", from: "1.10.13", to: "2.9.2" };
const B = { threadId: "t-2", dependency: "pydantic", from: "1.9.0", to: "2.9.2" };

beforeEach(() => {
  sessionStorage.clear();
});

describe("useSessionRuns", () => {
  it("starts empty", () => {
    expect(renderHook(() => useSessionRuns()).result.current.runs).toEqual([]);
  });

  it("remembers a run, newest first", () => {
    const { result } = renderHook(() => useSessionRuns());

    act(() => result.current.remember(A));
    act(() => result.current.remember(B));

    expect(result.current.runs.map((run) => run.threadId)).toEqual(["t-2", "t-1"]);
  });

  it("does not list the same thread twice", () => {
    const { result } = renderHook(() => useSessionRuns());

    act(() => result.current.remember(A));
    act(() => result.current.remember(A));

    expect(result.current.runs).toHaveLength(1);
  });

  it("survives a remount, because it is in sessionStorage", () => {
    const first = renderHook(() => useSessionRuns());
    act(() => first.result.current.remember(A));
    first.unmount();

    expect(renderHook(() => useSessionRuns()).result.current.runs[0].threadId).toBe("t-1");
  });

  it("ignores a corrupt stored value rather than throwing on load", () => {
    // sessionStorage is user-writable and survives a deploy that changes this
    // shape. A crash on read would make the whole app unreachable until the
    // user knew to clear site data.
    sessionStorage.setItem("upgradepilot.runs", "{not json");

    expect(renderHook(() => useSessionRuns()).result.current.runs).toEqual([]);
  });
});
