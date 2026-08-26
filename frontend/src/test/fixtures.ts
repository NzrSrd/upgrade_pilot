/**
 * Typed builders for server shapes.
 *
 * Typed against the generated schema on purpose: a hand-shaped literal can
 * describe a response the API is incapable of sending, and a test that passes
 * against an impossible fixture is worse than no test.
 */

import type {
  ApiError,
  BreakingChange,
  DecisionOption,
  DetectedVersion,
  FinalReport,
  InterruptPayload,
  RepoAnalysis,
  RiskAnalysis,
  RiskFactor,
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
    // `BreakingChange.affected_symbols` is `Field(min_length=1)`
    // (`models/evidence.py`) -- the backend cannot construct an empty
    // tuple here, so a fixture default of `[]` would describe a response
    // the API is incapable of sending (the module docstring's own rule).
    affected_symbols: ["User.validate"],
    old_form: null,
    new_form: null,
    source: aSourceRef(),
    ...overrides,
  };
}

export function anApiError(overrides: Partial<ApiError> = {}): ApiError {
  return {
    // `analyze_repo` is the default node because it is the one whose failure
    // leaves every later step with nothing to work from -- the shape that
    // produced a report of zeroes with eight green checkmarks.
    code: "local_path_forbidden",
    message: "That repository path does not exist.",
    retryable: false,
    node: "analyze_repo",
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
    // `_totalled([])` (`models/usage.py`) returns `None`, never `0.0`, for
    // zero calls -- `calls: 0` paired with `estimated_cost_usd: 0` is a
    // state `UsageSummary.from_calls` cannot produce, which is exactly the
    // shape this module's own docstring forbids describing. `calls: 4`
    // here is an arbitrary genuinely-priced run; callers that want the
    // zero-call state pass `{ calls: 0, estimated_cost_usd: null }`
    // explicitly.
    calls: 4,
    input_tokens: 320,
    output_tokens: 40,
    total_tokens: 360,
    estimated: false,
    pricing_complete: true,
    estimated_cost_usd: 0.00042,
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

export function aFactor(overrides: Partial<RiskFactor> = {}): RiskFactor {
  return {
    id: "breaking_change_exposure",
    name: "Breaking change exposure",
    category: "breaking_change_exposure",
    level: "high",
    weight: 0.25,
    detail: "Four breaking changes touch symbols this repository uses.",
    evidence: [{ kind: "repo", file: "src/app/models.py", line: 12, snippet: null }],
    ...overrides,
  };
}

export function aRiskAnalysis(overrides: Partial<RiskAnalysis> = {}): RiskAnalysis {
  return {
    overall_risk: "high",
    aggregate_risk: "high",
    clamp_floor: null,
    confidence: 0.62,
    confidence_ceilings: [],
    factors: [aFactor()],
    summary: "Four breaking changes reach code this repository executes.",
    qualitative_notes: [],
    ...overrides,
  };
}

export function aDetectedVersion(overrides: Partial<DetectedVersion> = {}): DetectedVersion {
  return {
    value: "1.10.13",
    specifier: "==1.10.13",
    confidence: "exact",
    role: "direct",
    source_manifest: { kind: "pyproject", path: "pyproject.toml", declared_specifier: "==1.10.13" },
    ...overrides,
  };
}

export function aRepoAnalysis(overrides: Partial<RepoAnalysis> = {}): RepoAnalysis {
  return {
    analyzed_files: 42,
    total_python_files: 42,
    commit_sha: null,
    detected_version: aDetectedVersion(),
    symbol_inventory: { entries: [] },
    affected_files: [],
    commit_records: [],
    confidence_reducers: [],
    languages: [],
    manifests: [],
    skipped_files: [],
    test_paths: [],
    ...overrides,
  };
}

/**
 * A full report, for the tabs task 12/13 build. Deliberately a second
 * builder rather than an extension of `aFinalReport` above: that one is
 * minimal (used where only usage/thread-id matter to `recordedSpan`), and
 * widening it here would force every existing call site to reason about the
 * risk/plan/validation fields it now has to carry.
 */
export function aReport(overrides: Partial<FinalReport> = {}): FinalReport {
  return {
    thread_id: "t-1",
    repo_ref: { kind: "local", path: "/srv/repo" },
    dependency: {
      name: "pydantic",
      // Ruling F4: both are `@computed_field`s the schema requires --
      // `canonical_name` is the PEP-503 normalised name, `import_root` is the
      // guessed top-level module name.
      canonical_name: "pydantic",
      current_version: "1.10.13",
      target_version: "2.9.2",
      import_root: "pydantic",
    },
    constraints: { zero_downtime: false, minimize_effort: false, deadline: null, risk_tolerance: "medium" },
    commit_sha: null,
    completed_at: "2026-08-25T12:00:00Z",
    repo_analysis: null,
    affected_files: [],
    breaking_changes: [],
    rag_context: null,
    risk_analysis: aRiskAnalysis(),
    migration_plan: null,
    validation: null,
    human_decisions: [],
    usage: {
      calls: 4,
      input_tokens: 320,
      output_tokens: 40,
      // Ruling F3: `UsageSummary` has no `total_tokens` property at all.
      by_model: [],
      by_node: [],
      estimated: false,
      pricing_complete: true,
      estimated_cost_usd: 0.00042,
    },
    agent_trace: [],
    errors: [],
    completed_with_warnings: false,
    version_discrepancy: null,
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
