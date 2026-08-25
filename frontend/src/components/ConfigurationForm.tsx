/**
 * The `idle` view. Six fields, no form library (spec §10).
 *
 * The client-side checks mirror the backend's own validators to save a round
 * trip, and nothing more: the backend stays authoritative, and its 422 renders
 * against the field its error code names. Duplicating more of the rules here
 * would create a second implementation that can disagree with the first.
 *
 * What is *not* here is the point of READINESS §2. No provider, model or
 * temperature control — configuration is environment variables via
 * `pydantic-settings` (rule 14) and the API exposes no configuration endpoint.
 * No dependency dropdown — nothing enumerates a repository's manifest before a
 * run starts. No "Additional Context" — unbacked prose entering the judgment
 * path. And the four constraint fields are exactly `UserConstraints`: a form
 * without `deadline` silently weakens `constraint_pressure`, which is derived
 * partly from it, and makes the `scope_tradeoff` decision kind unreachable.
 */

import { useState } from "react";
import type { FormEvent } from "react";

import { ApiFailure, startRun } from "../api/client";
import type { ErrorCode, RiskLevel } from "../api/types";
import type { SessionRun } from "../hooks/useSessionRuns";
import { Panel } from "./ui";

type FormField = "repo" | "dependency" | "versions";

/**
 * Which field an error code belongs beside.
 *
 * A code with no entry renders in the banner instead — a `kb_unavailable` is
 * about the system, not about something the user typed, and attaching it to an
 * input would tell them to fix the wrong thing.
 */
export const FIELD_FOR_CODE: Partial<Record<ErrorCode, FormField>> = {
  invalid_repo_url: "repo",
  local_path_forbidden: "repo",
  repo_unavailable: "repo",
  repo_too_large: "repo",
  dependency_not_found: "dependency",
  version_invalid: "versions",
};

const RISK_OPTIONS: RiskLevel[] = ["low", "medium", "high"];

export function ConfigurationForm({ onStarted }: { onStarted: (run: SessionRun) => void }) {
  const [source, setSource] = useState<"remote" | "local">("remote");
  const [url, setUrl] = useState("");
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [zeroDowntime, setZeroDowntime] = useState(false);
  const [minimizeEffort, setMinimizeEffort] = useState(false);
  const [deadline, setDeadline] = useState("");
  const [riskTolerance, setRiskTolerance] = useState<RiskLevel>("medium");

  const [submitting, setSubmitting] = useState(false);
  const [fieldError, setFieldError] = useState<{ field: FormField; message: string } | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const errorFor = (field: FormField) =>
    fieldError !== null && fieldError.field === field ? fieldError.message : null;

  function localCheck(): { field: FormField; message: string } | null {
    if (source === "remote" && url.trim() === "") {
      return { field: "repo", message: "A repository URL is required." };
    }
    if (source === "local" && path.trim() === "") {
      return { field: "repo", message: "A local path is required." };
    }
    if (name.trim() === "") {
      return { field: "dependency", message: "A dependency name is required." };
    }
    if (from.trim() === "" || to.trim() === "") {
      return { field: "versions", message: "Both versions are required." };
    }
    if (from.trim() === to.trim()) {
      // `DependencySpec` rejects this with its own validator; mirroring it here
      // saves a round trip.
      return { field: "versions", message: "The two versions must differ." };
    }
    return null;
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBanner(null);

    const problem = localCheck();
    if (problem !== null) {
      setFieldError(problem);
      return;
    }
    setFieldError(null);
    setSubmitting(true);

    try {
      const response = await startRun({
        // Exactly one, never both. Spec 9.1 refuses a request naming both
        // rather than resolving it by precedence.
        repo: source === "remote" ? { url: url.trim(), path: null } : { url: null, path: path.trim() },
        dependency: {
          name: name.trim(),
          current_version: from.trim(),
          target_version: to.trim(),
        },
        constraints: {
          zero_downtime: zeroDowntime,
          minimize_effort: minimizeEffort,
          // `date | None`, so an empty input is null rather than "" — an empty
          // string is a 422 the user cannot act on.
          deadline: deadline === "" ? null : deadline,
          risk_tolerance: riskTolerance,
        },
      });
      onStarted({
        threadId: response.thread_id,
        dependency: name.trim(),
        from: from.trim(),
        to: to.trim(),
      });
    } catch (error) {
      if (error instanceof ApiFailure) {
        const field = FIELD_FOR_CODE[error.error.code];
        if (field !== undefined) setFieldError({ field, message: error.error.message });
        else setBanner(error.error.message);
      } else {
        setBanner("The backend is unreachable.");
      }
      // Re-enabled on failure, unlike the decision panel: this request charged
      // nothing and started nothing, so correcting and retrying is the whole
      // point.
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="max-w-2xl space-y-4" noValidate>
      <Panel title="Repository">
        <fieldset className="mb-3">
          <legend className="sr-only">Repository source</legend>
          <div className="flex gap-4 text-sm">
            {(["remote", "local"] as const).map((option) => (
              <label key={option} className="flex items-center gap-1.5 capitalize">
                <input
                  type="radio"
                  name="source"
                  value={option}
                  checked={source === option}
                  onChange={() => setSource(option)}
                />
                {option}
              </label>
            ))}
          </div>
        </fieldset>

        {source === "remote" ? (
          <TextField
            id="repo-url"
            label="Repository URL"
            value={url}
            onChange={setUrl}
            placeholder="https://github.com/owner/project.git"
            error={errorFor("repo")}
            mono
          />
        ) : (
          <TextField
            id="repo-path"
            label="Local path"
            value={path}
            onChange={setPath}
            placeholder="/srv/repo"
            error={errorFor("repo")}
            mono
          />
        )}
      </Panel>

      <Panel title="Dependency">
        <div className="space-y-3">
          <TextField
            id="dependency"
            label="Dependency"
            value={name}
            onChange={setName}
            placeholder="pydantic"
            error={errorFor("dependency")}
          />
          <div className="grid grid-cols-2 gap-3">
            <TextField
              id="version-from"
              label="Current version"
              value={from}
              onChange={setFrom}
              placeholder="1.10.13"
              error={errorFor("versions")}
              describedBy="versions-error"
              hideMessage
              mono
            />
            <TextField
              id="version-to"
              label="Target version"
              value={to}
              onChange={setTo}
              placeholder="2.9.2"
              error={errorFor("versions")}
              describedBy="versions-error"
              hideMessage
              mono
            />
          </div>
          {errorFor("versions") != null && (
            // Rendered once, outside either input: the relationship error
            // implicates both `version-from` and `version-to`, and a message
            // duplicated per field would make `getByText` ambiguous while
            // saying nothing more than the single copy already says. Both
            // inputs point `aria-describedby` at this one id.
            <p id="versions-error" className="text-xs text-risk-high">
              {errorFor("versions")}
            </p>
          )}
          <p className="text-[11px] text-ink-faint">
            The version you state is compared against what the manifests declare; the report shows
            both when they disagree.
          </p>
        </div>
      </Panel>

      <Panel title="Constraints">
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm" htmlFor="zero-downtime">
            <input
              id="zero-downtime"
              type="checkbox"
              checked={zeroDowntime}
              onChange={(event) => setZeroDowntime(event.target.checked)}
            />
            Zero downtime required
          </label>
          <label className="flex items-center gap-2 text-sm" htmlFor="minimize-effort">
            <input
              id="minimize-effort"
              type="checkbox"
              checked={minimizeEffort}
              onChange={(event) => setMinimizeEffort(event.target.checked)}
            />
            Minimize effort
          </label>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] tracking-wide text-ink-faint uppercase" htmlFor="deadline">
                Deadline
              </label>
              <input
                id="deadline"
                type="date"
                value={deadline}
                onChange={(event) => setDeadline(event.target.value)}
                className="mt-1 w-full rounded-md border border-edge bg-surface px-2 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-[11px] tracking-wide text-ink-faint uppercase" htmlFor="risk-tolerance">
                Risk tolerance
              </label>
              <select
                id="risk-tolerance"
                value={riskTolerance}
                onChange={(event) => setRiskTolerance(event.target.value as RiskLevel)}
                className="mt-1 w-full rounded-md border border-edge bg-surface px-2 py-1.5 text-sm capitalize"
              >
                {RISK_OPTIONS.map((level) => (
                  <option key={level} value={level}>
                    {level}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>
      </Panel>

      {banner !== null && (
        <p
          role="alert"
          className="rounded-md border border-risk-high/50 bg-risk-high/10 px-3 py-2 text-sm text-risk-high"
        >
          {banner}
        </p>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="rounded-md border border-edge-strong bg-surface-raised px-4 py-2 text-sm font-medium disabled:opacity-50"
      >
        {submitting ? "Starting…" : "Start migration audit"}
      </button>
    </form>
  );
}

function TextField({
  id,
  label,
  value,
  onChange,
  placeholder,
  error,
  mono = false,
  describedBy: describedByOverride,
  hideMessage = false,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  error?: string | null;
  mono?: boolean;
  /**
   * Point `aria-describedby` at an id this field does not own -- for an error
   * that implicates more than one input (the versions-must-differ case) and
   * so is rendered once, by the caller, rather than once per field.
   */
  describedBy?: string;
  /**
   * Suppress this field's own `<p>` message while still applying
   * `aria-invalid` and `describedBy`. Used together with `describedBy` so a
   * shared relationship error is not duplicated in the DOM.
   */
  hideMessage?: boolean;
}) {
  const describedBy = error != null ? (describedByOverride ?? `${id}-error`) : undefined;
  return (
    <div>
      <label className="block text-[11px] tracking-wide text-ink-faint uppercase" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error != null ? "true" : undefined}
        aria-describedby={describedBy}
        className={`mt-1 w-full rounded-md border bg-surface px-2 py-1.5 text-sm ${
          mono ? "font-mono text-[13px]" : ""
        } ${error != null ? "border-risk-high" : "border-edge"}`}
      />
      {error != null && !hideMessage && (
        <p id={`${id}-error`} className="mt-1 text-xs text-risk-high">
          {error}
        </p>
      )}
    </div>
  );
}
