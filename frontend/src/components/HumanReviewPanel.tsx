/**
 * The `awaiting_human` view — one of the two interactions this product exists
 * for.
 *
 * Rendered by `App` *above* a still-incomplete `WorkflowTimeline`, which is
 * where the "can never look finished while waiting" guarantee actually lives.
 * This component's job is narrower: ask the question honestly, and make a
 * second answer impossible.
 *
 * **The triple guard, and why the third is the only real one.** The button is
 * disabled until an option is chosen and while a request is in flight; a local
 * `submitting` flag is set before the request and is deliberately *not*
 * cleared on success. Both are defeated by two tabs, a replayed request, or a
 * resume issued from somewhere else entirely — so the server's 409 is the
 * guarantee, and this panel renders it as a settled fact rather than an error
 * to retry.
 *
 * All four `DecisionKind`s share this layout. The kind sets the framing, not
 * the shape: every one of them is a question, some evidence, and options with
 * trade-offs.
 */

import { AlertTriangle, UserCheck } from "lucide-react";
import { useState } from "react";

import { ApiFailure, resumeRun } from "../api/client";
import type { DecisionOption, InterruptPayload } from "../api/types";
import { EvidenceRefList } from "./EvidenceRefList";
import { Card, LevelBadge, Panel } from "./ui";

/** `DecisionKind` read aloud, for the group's accessible name and the header. */
function readKind(kind: InterruptPayload["kind"]): string {
  return kind.replace(/_/g, " ");
}

export function HumanReviewPanel({
  threadId,
  decision,
  answered,
  onSubmitted,
}: {
  threadId: string;
  decision: InterruptPayload;
  answered: number;
  onSubmitted: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [settled, setSettled] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const blocked = selected === null || submitting || settled;

  // At most one alert renders at a time (`role="alert"` must stay singular:
  // two would break a `getByRole("alert")` query and double-announce to a
  // screen reader). `problem` — this submission's own outcome — takes
  // priority over `validation_error` — the *previous* answer's rejection —
  // because a live 409 or a retryable failure is more current than a stale
  // rejection reason on a decision the server already re-sent.
  const alert: { text: string; className: string } | null =
    problem !== null
      ? {
          text: problem,
          className: settled
            ? "border-edge-strong bg-surface-raised text-ink-muted"
            : "border-risk-high/50 bg-risk-high/10 text-risk-high",
        }
      : decision.validation_error != null
        ? {
            text: decision.validation_error,
            className: "border-risk-medium/50 bg-risk-medium/10 text-risk-medium",
          }
        : null;

  async function submit() {
    if (blocked || selected === null) return;

    // Guard two: set before the request, so a second click in the same tick
    // finds it already true.
    setSubmitting(true);
    setProblem(null);

    try {
      await resumeRun({
        thread_id: threadId,
        decision: { question_id: decision.question_id, selected_option_id: selected, rationale: null },
      });
      // Deliberately *not* clearing `submitting`. The answer is in; the next
      // poll moves the view on. Re-enabling here would offer a second submit
      // against a question that is no longer open.
      onSubmitted();
    } catch (error) {
      if (error instanceof ApiFailure && error.httpStatus === 409) {
        // Guard three. Not a failure to retry — a settled fact, so the panel
        // stays closed and says so.
        setSettled(true);
        setProblem("This question has already been answered.");
        return;
      }
      setProblem(
        error instanceof ApiFailure ? error.error.message : "The backend is unreachable.",
      );
      // A retryable failure is not a duplicate. Leaving the panel dead would
      // strand the user with a question they cannot answer.
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card className="border-pending-input/50 bg-pending-input/5">
        <div className="flex items-start gap-3 p-4">
          <UserCheck className="mt-0.5 size-5 shrink-0 text-pending-input" aria-hidden />
          <div className="min-w-0">
            <p className="flex flex-wrap items-center gap-x-2 text-[11px] font-semibold tracking-wide text-pending-input uppercase">
              <span>The agent is waiting for your decision</span>
              <span>·</span>
              <span>{readKind(decision.kind)}</span>
              {answered > 0 && (
                <>
                  <span>·</span>
                  <span>Question {answered + 1}</span>
                </>
              )}
            </p>
            <h2 className="mt-1.5 text-lg font-semibold">{decision.question}</h2>
            <p className="mt-1 text-sm text-ink-muted">{decision.reason}</p>
            {decision.evidence.length > 0 && (
              <div className="mt-2">
                <p className="text-[11px] tracking-wide text-ink-faint uppercase">Evidence</p>
                {/* `EvidenceRefList` is the one renderer for `EvidenceRef[]`
                    (ruling T11b) -- this panel no longer keeps its own copy. */}
                <EvidenceRefList refs={decision.evidence} />
              </div>
            )}
            {/* `pending-input`, not `risk-medium`: this is information about
                the pending decision, not a risk finding, and DESIGN.md keeps
                those two tokens separate for exactly that reason. */}
            <p className="mt-2 text-sm text-pending-input">
              If you do not answer: {decision.consequences_if_unanswered}
            </p>
          </div>
        </div>
      </Card>

      {alert !== null && (
        <p
          role="alert"
          className={`flex items-start gap-2 rounded-md border px-3 py-2 text-sm ${alert.className}`}
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          {alert.text}
        </p>
      )}

      <Panel title="Options">
        <div role="radiogroup" aria-label={`${readKind(decision.kind)} options`} className="space-y-2">
          {decision.options.map((option) => (
            <OptionCard
              key={option.id}
              option={option}
              recommended={option.id === decision.recommendation_id}
              checked={selected === option.id}
              disabled={submitting || settled}
              onChoose={() => setSelected(option.id)}
            />
          ))}
        </div>
      </Panel>

      <button
        type="button"
        onClick={submit}
        disabled={blocked}
        className="rounded-md border border-pending-input/60 bg-pending-input/15 px-4 py-2 text-sm font-semibold text-pending-input disabled:opacity-50"
      >
        {settled ? "Submitted" : submitting ? "Submitting…" : "Submit decision"}
      </button>
    </div>
  );
}

function OptionCard({
  option,
  recommended,
  checked,
  disabled,
  onChoose,
}: {
  option: DecisionOption;
  recommended: boolean;
  checked: boolean;
  disabled: boolean;
  onChoose: () => void;
}) {
  return (
    <label
      className={`flex cursor-pointer gap-3 rounded-md border px-3 py-2.5 ${
        checked ? "border-pending-input bg-pending-input/10" : "border-edge bg-surface hover:border-edge-strong"
      }`}
    >
      <input
        type="radio"
        name="decision-option"
        value={option.id}
        checked={checked}
        disabled={disabled}
        onChange={onChoose}
        className="mt-1"
      />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{option.label}</span>
          {recommended && (
            // Marked, never preselected: a preselected recommendation is a
            // decision the agent made and submitted under a human's name.
            <span className="rounded border border-edge-strong px-1.5 py-0.5 text-[10px] tracking-wide text-ink-muted uppercase">
              Recommended
            </span>
          )}
        </span>
        <span className="mt-1 block text-sm text-ink-muted">{option.summary}</span>
        <span className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
          <LevelBadge level={option.risk_level}>{option.risk_level} risk</LevelBadge>
          <span className="rounded border border-edge px-1.5 py-0.5 text-ink-muted">
            {option.effort} effort
          </span>
          {/* `DecisionOption.downtime` (`models/decision.py`) is a fact
              about the option, not a verdict -- it carries no severity of
              its own, and `option.risk_level` two spans to the left is the
              field that is actually graded. Styling this `risk-medium`
              would invent a severity nothing assigned, right beside a real
              one -- the same defect `PlanTab.tsx`'s `requires_downtime`
              badge was corrected to avoid (fix round 2, finding 2). Neutral
              chrome. */}
          {option.downtime && (
            <span className="rounded border border-edge px-1.5 py-0.5 text-ink-muted">
              requires downtime
            </span>
          )}
        </span>
        <ul className="mt-2 space-y-0.5">
          {option.consequences.map((consequence) => (
            <li key={consequence} className="text-xs text-ink-muted">
              — {consequence}
            </li>
          ))}
        </ul>
      </span>
    </label>
  );
}
