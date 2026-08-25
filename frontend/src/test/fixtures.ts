/**
 * Typed builders for server shapes.
 *
 * Typed against the generated schema on purpose: a hand-shaped literal can
 * describe a response the API is incapable of sending, and a test that passes
 * against an impossible fixture is worse than no test.
 */

import type { BreakingChange, RunSnapshot, SourceRef, UsageView } from "../api/types";

export function aSourceRef(overrides: Partial<SourceRef> = {}): SourceRef {
  return {
    source_id: "src-1",
    chunk_id: "chunk-1",
    title: "Migrating to Pydantic V2",
    source_type: "migration_guide",
    url_or_reference: "https://docs.pydantic.dev/latest/migration/",
    relevance: 0.8,
    ...overrides,
  };
}

export function aBreakingChange(overrides: Partial<BreakingChange> = {}): BreakingChange {
  return {
    id: "bc-1",
    title: "Validators must be class methods",
    description: "`@validator` is replaced by `@field_validator`.",
    severity: "medium",
    affected_symbols: [],
    old_form: null,
    new_form: null,
    source: aSourceRef(),
    ...overrides,
  };
}

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
