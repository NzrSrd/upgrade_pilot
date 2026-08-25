/**
 * The right region. Stays mounted while the workspace changes underneath it,
 * because token and cost tracking is a graded capability rather than a panel
 * one view owns -- and it keeps reporting after the run finishes, which the
 * screenshots disagreed about.
 *
 * Renders the model or models actually in use, from `usage.by_model` --
 * pairs of (model name, tokens), read from calls that actually happened.
 * There is no configuration endpoint to ask (rule 14), so this is the only
 * honest source for "which model": before the first call there is nothing
 * to report, and a hardcoded default would be exactly the guess this field
 * exists to eliminate (`DESIGN.md` Telemetry, `READINESS.md` 2.5).
 */

import type { RunSnapshot } from "../api/types";
import { costLabel } from "../derive/cost";
import { recordedSpan } from "../derive/recordedSpan";
import { EmptyState, Field, Panel } from "./ui";

const integer = new Intl.NumberFormat("en-US");

export function RunMetrics({ snapshot }: { snapshot: RunSnapshot | null }) {
  if (snapshot === null) {
    return (
      <Panel title="Telemetry">
        <EmptyState>No run started.</EmptyState>
      </Panel>
    );
  }

  const { usage } = snapshot;
  const cost = costLabel(usage);
  const byNode = usage.by_node;
  // `RunSnapshot`'s array and object fields carry OpenAPI defaults, which
  // openapi-typescript marks optional (`T | undefined`) even though the
  // fixtures and the real API always send them. `?? []` / `?? null` is the
  // typed equivalent of that default, applied once here rather than at every
  // call site below.
  const completedSteps = snapshot.completed_steps ?? [];
  const ragContext = snapshot.rag_context ?? null;
  const span = recordedSpan(snapshot);

  return (
    <div className="space-y-3">
      <Panel title="Usage">
        <dl className="grid grid-cols-2 gap-3">
          <Field label="Input tokens" value={integer.format(usage.input_tokens)} />
          <Field label="Output tokens" value={integer.format(usage.output_tokens)} />
          <Field label="Total tokens" value={integer.format(usage.total_tokens)} />
          <Field label="LLM calls" value={integer.format(usage.calls)} />
        </dl>
      </Panel>

      <Panel title="Model in use">
        {usage.by_model.length === 0 ? (
          <EmptyState>Not recorded yet.</EmptyState>
        ) : (
          <ul className="space-y-1.5">
            {usage.by_model.map(([model, total]) => (
              <li key={model} className="flex items-baseline justify-between gap-3 text-xs">
                {/* Long model names (`openai/gpt-4.1-mini`) are a reader's
                    check against their own configuration -- truncating one
                    is the same defect as not showing it. */}
                <span className="min-w-0 flex-1 overflow-x-auto font-mono text-ink-muted [overflow-wrap:anywhere]">
                  {model}
                </span>
                <span className="shrink-0 font-mono text-ink">{integer.format(total)}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="Estimated cost">
        <p className={`font-mono text-lg ${cost.lowerBound ? "text-risk-medium" : "text-ink"}`}>
          {cost.text}
        </p>
        {cost.note !== null && <p className="mt-1 text-[11px] text-risk-medium">{cost.note}</p>}
        {cost.estimated && (
          <p className="mt-1 text-[11px] text-risk-medium">
            Token counts partly estimated by a local tokenizer.
          </p>
        )}
      </Panel>

      {byNode.length > 0 && (
        <Panel title="Tokens by node">
          <ul className="space-y-1">
            {byNode.map(([node, total]) => (
              <li key={node} className="flex items-baseline justify-between gap-2 text-xs">
                <span className="truncate font-mono text-ink-muted">{node}</span>
                <span className="font-mono text-ink">{integer.format(total)}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Panel title="Graph execution">
        <dl className="space-y-2">
          <Field
            label="Current node"
            value={<span className="font-mono text-[13px]">{snapshot.current_step ?? "—"}</span>}
          />
          <Field label="Completed" value={`${completedSteps.length} of 8`} />
          {/* "Recorded span", never "elapsed time": this measures the gap
              between the first and last recorded trace event, not
              wall-clock time since the run started. This client cannot
              observe when the server actually began, and a checkpointed run
              can be resumed hours or days after it paused, so wall-clock
              across a resume would be a number that looks authoritative and
              is not. `null` (an empty trace) renders as "--", never "0s". */}
          <Field label="Recorded span" value={span ?? "\u2014"} />
          {ragContext !== null && (
            <>
              <Field label="Retrieval rounds" value={String(ragContext.iterations)} />
              <Field label="Stopped because" value={ragContext.stop_reason.replace(/_/g, " ")} />
            </>
          )}
        </dl>
      </Panel>
    </div>
  );
}
