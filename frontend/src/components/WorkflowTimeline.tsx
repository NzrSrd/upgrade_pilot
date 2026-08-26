/**
 * The eight steps, always all of them, always in order.
 *
 * A sibling of the workspace view rather than a child of any one of them, so
 * `HumanReviewPanel` renders *above a still-incomplete timeline*. That is what
 * makes "the workflow can never look finished while waiting" structural rather
 * than a thing each view has to remember.
 */

import { AlertTriangle, Check, Circle, Loader, MinusCircle, UserCheck } from "lucide-react";
import type { ReactNode } from "react";

import type { RunSnapshot } from "../api/types";
import { stepStates } from "../derive/steps";
import type { StepState } from "../derive/steps";

/**
 * The word for each state, and its icon.
 *
 * The word is not decoration: `DESIGN.md` §Accessibility requires that status
 * never be communicated by colour alone, and it is what the accessible name of
 * each row is built from.
 */
const APPEARANCE: Record<StepState, { word: string; icon: ReactNode; className: string }> = {
  pending: {
    word: "pending",
    icon: <Circle className="size-3.5" aria-hidden />,
    className: "text-ink-faint",
  },
  running: {
    word: "running",
    icon: <Loader className="size-3.5 animate-spin" aria-hidden />,
    className: "text-ink",
  },
  completed: {
    word: "completed",
    icon: <Check className="size-3.5" aria-hidden />,
    className: "text-risk-low",
  },
  skipped: {
    // The reason travels with the word. Without it "skipped" reads as an
    // omission rather than a decision the constraints already made (spec 8.2).
    word: "skipped, resolved by constraints",
    icon: <MinusCircle className="size-3.5" aria-hidden />,
    className: "text-ink-faint",
  },
  awaiting: {
    word: "waiting for you",
    icon: <UserCheck className="size-3.5" aria-hidden />,
    className: "text-pending-input",
  },
  failed: {
    word: "failed",
    icon: <AlertTriangle className="size-3.5" aria-hidden />,
    className: "text-risk-high",
  },
};

export function WorkflowTimeline({ snapshot }: { snapshot: RunSnapshot | null }) {
  const steps = stepStates(snapshot);

  return (
    <ol className="flex flex-wrap items-stretch gap-1.5" aria-label="Workflow progress">
      {steps.map((step) => {
        const { word, icon, className } = APPEARANCE[step.state];
        return (
          <li
            key={step.node}
            aria-label={`${step.label}: ${word}`}
            className={`flex min-w-0 flex-1 basis-40 items-center gap-2 rounded-md border border-edge bg-surface-raised px-2.5 py-2 ${className}`}
          >
            {icon}
            <span className="min-w-0">
              <span className="block truncate text-xs font-medium text-ink">{step.label}</span>
              <span className="block truncate text-[11px]">{word}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
