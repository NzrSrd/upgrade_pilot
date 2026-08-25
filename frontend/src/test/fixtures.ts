/**
 * Typed builders for server shapes.
 *
 * Typed against the generated schema on purpose: a hand-shaped literal can
 * describe a response the API is incapable of sending, and a test that passes
 * against an impossible fixture is worse than no test.
 */

import type { RunSnapshot, UsageView } from "../api/types";

export function anUsageView(overrides: Partial<UsageView> = {}): UsageView {
  return {
    calls: 0,
    input_tokens: 0,
    output_tokens: 0,
    total_tokens: 0,
    estimated: false,
    pricing_complete: true,
    estimated_cost_usd: 0,
    by_node: [],
    by_model: [],
    ...overrides,
  };
}

export function aSnapshot(overrides: Partial<RunSnapshot> = {}): RunSnapshot {
  return {
    thread_id: "t-1",
    status: "running",
    current_step: null,
    completed_steps: [],
    trace: [],
    usage: anUsageView(),
    affected_files: [],
    breaking_changes: [],
    retrieved_sources: [],
    rag_context: null,
    risk_analysis: null,
    migration_plan: null,
    validation: null,
    human_decisions: [],
    pending_decision: null,
    final_report: null,
    errors: [],
    ...overrides,
  };
}
