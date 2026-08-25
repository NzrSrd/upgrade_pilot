/**
 * Typed builders for server shapes.
 *
 * Typed against the generated schema on purpose: a hand-shaped literal can
 * describe a response the API is incapable of sending, and a test that passes
 * against an impossible fixture is worse than no test.
 */

import type {
  BreakingChange,
  DecisionOption,
  FinalReport,
  InterruptPayload,
  RunSnapshot,
  SourceRef,
  TraceEvent,
  UsageView,
} from "../api/types";

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

export function aTraceEvent(overrides: Partial<TraceEvent> = {}): TraceEvent {
  return {
    event_id: "e-1",
    kind: "node_started",
    node: "assess_risk",
    at: "2026-08-25T12:00:00Z",
    summary: "assess_risk started",
    detail: null,
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

export function aFinalReport(overrides: Partial<FinalReport> = {}): FinalReport {
  return {
    thread_id: "t-1",
    repo_ref: { kind: "remote", url: "https://example.com/repo.git" },
    dependency: {
      name: "pydantic",
      canonical_name: "pydantic",
      current_version: "1.10.13",
      target_version: "2.9.2",
      import_root: "pydantic",
    },
    constraints: { zero_downtime: false, minimize_effort: false, deadline: null, risk_tolerance: "medium" },
    commit_sha: null,
    completed_at: "2026-08-25T12:00:00Z",
    completed_with_warnings: false,
    repo_analysis: null,
    usage: { calls: 0, input_tokens: 0, output_tokens: 0 },
    version_discrepancy: null,
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

export function anOption(overrides: Partial<DecisionOption> = {}): DecisionOption {
  return {
    id: "staged_rollout",
    label: "Staged rollout",
    summary: "Migrate module by module behind a feature flag.",
    risk_level: "medium",
    effort: "high",
    downtime: false,
    consequences: ["Two code paths coexist for several weeks."],
    supporting_evidence: [{ kind: "doc", source_id: "s-1", chunk_id: "s-1#0", relevance: 0.82 }],
    ...overrides,
  };
}

export function anInterrupt(overrides: Partial<InterruptPayload> = {}): InterruptPayload {
  return {
    question_id: "q-1",
    kind: "strategy_choice",
    reason: "Zero-downtime and the deadline pull in opposite directions.",
    question: "Which migration strategy should the plan follow?",
    evidence: [{ kind: "constraint", field: "zero_downtime", value: "true" }],
    options: [
      anOption({}),
      anOption({
        id: "direct_migration",
        label: "Direct migration",
        summary: "Change everything in one release.",
        risk_level: "high",
        effort: "low",
        downtime: true,
        consequences: ["A short outage during deploy."],
      }),
    ],
    recommendation_id: "staged_rollout",
    consequences_if_unanswered: "Without an answer the run stops here and produces no plan.",
    validation_error: null,
    ...overrides,
  };
}
