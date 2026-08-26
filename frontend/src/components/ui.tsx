/**
 * Shared primitives. No domain knowledge lives here — that is what keeps the
 * report tabs short enough to read.
 */

import type { ReactNode } from "react";

import type { RiskLevel } from "../api/types";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-edge bg-surface-raised ${className}`}>{children}</div>
  );
}

export function Panel({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card>
      <div className="flex items-center justify-between border-b border-edge px-4 py-2.5">
        <h2 className="text-xs font-semibold tracking-wide text-ink-muted uppercase">{title}</h2>
        {action}
      </div>
      <div className="p-4">{children}</div>
    </Card>
  );
}

const LEVEL_CLASS: Record<RiskLevel | "pending", string> = {
  low: "border-risk-low/40 bg-risk-low/10 text-risk-low",
  medium: "border-risk-medium/40 bg-risk-medium/10 text-risk-medium",
  high: "border-risk-high/40 bg-risk-high/10 text-risk-high",
  pending: "border-pending-input/40 bg-pending-input/10 text-pending-input",
};

/**
 * A level, as a colour *and* a word. `DESIGN.md` §Accessibility: status is
 * never communicated by colour alone, so the word is not optional decoration.
 */
export function LevelBadge({
  level,
  children,
}: {
  level: RiskLevel | "pending";
  children?: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-semibold uppercase ${LEVEL_CLASS[level]}`}
    >
      {children ?? level}
    </span>
  );
}

export function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono text-[13px] text-ink-muted">{children}</span>;
}

export function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] tracking-wide text-ink-faint uppercase">{label}</dt>
      <dd className="mt-0.5 text-sm text-ink">{value}</dd>
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="text-sm text-ink-faint">{children}</p>;
}
