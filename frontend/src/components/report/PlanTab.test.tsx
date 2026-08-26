import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { aReport } from "../../test/fixtures";
import { PlanTab } from "./PlanTab";

const plan = {
  strategy_id: "staged_rollout" as const,
  summary: "Migrate module by module behind a flag.",
  steps: [
    {
      order: 1,
      title: "Replace @validator with @field_validator",
      description: "Four call sites in two modules.",
      files: ["src/app/models.py"],
      rationale_evidence: [],
      validation: "pytest tests/unit",
      requires_downtime: false,
    },
  ],
  human_decisions_applied: [
    { decision_id: "q-1", how_it_changed_the_plan: "Staged rollout chosen, so step 3 gates on a flag." },
  ],
  unaddressed_with_reason: [
    { path: "tests/test_legacy.py", reason: "Test-only; no runtime exposure." },
  ],
  mitigations: ["Keep the compatibility shim for one release."],
};

describe("PlanTab", () => {
  it("lists the steps in order with their files", () => {
    render(<PlanTab report={aReport({ migration_plan: plan })} />);

    expect(screen.getByText(/replace @validator/i)).toBeInTheDocument();
    expect(screen.getByText("src/app/models.py")).toBeInTheDocument();
  });

  it("shows how each human decision changed the plan", () => {
    // The graded requirement "human decision provably changes downstream
    // generation", shown to a user rather than asserted in a test.
    render(<PlanTab report={aReport({ migration_plan: plan })} />);

    expect(screen.getByText(/step 3 gates on a flag/i)).toBeInTheDocument();
  });

  it("shows unaddressed files with their reasons, not behind a disclosure", () => {
    // Spec 8.4 check 8. Bad news is not detail.
    render(<PlanTab report={aReport({ migration_plan: plan })} />);

    expect(screen.getByText("tests/test_legacy.py")).toBeInTheDocument();
    expect(screen.getByText(/test-only; no runtime exposure/i)).toBeInTheDocument();
  });

  it("does not grade an unaddressed file's reason with a severity colour", () => {
    // Fix round 2, finding 1. `UnaddressedFile` (models/plan.py:84-96)
    // carries only `path` and `reason` -- no grade, no severity, nothing
    // that ranks one unaddressed file against another. `text-risk-medium`
    // invented a rank the backend never assigned (the third instance of
    // this defect in the phase, after Task 11's `consequences_if_unanswered`
    // and Task 12's clamp/ceiling text). `validation` is left `null` here
    // (the fixture's default) so the only risk-relevant text on screen is
    // this reason -- if it renders neutrally, nothing in this render carries
    // a `risk-*` class.
    const { container } = render(<PlanTab report={aReport({ migration_plan: plan })} />);

    expect(screen.getByText(/test-only; no runtime exposure/i)).toBeInTheDocument();
    expect(container.querySelectorAll('[class*="risk-"]')).toHaveLength(0);
  });

  it("lists every validation check, and names the failures", () => {
    // Ruling F2: `ValidationReport` serialises exactly `attempt`, `outcomes`
    // and `passed` -- `failures` is a bare Pydantic property, not a
    // `@computed_field`, so it never reaches the wire. This fixture omits it
    // (an excess-property error against the generated type otherwise), and
    // `PlanTab` derives the failure list itself from `outcomes`.
    render(
      <PlanTab
        report={aReport({
          migration_plan: plan,
          validation: {
            attempt: 2,
            outcomes: [
              { check_id: "sources_resolve", passed: true, detail: "All 3 sources resolve.", offenders: [] },
              { check_id: "plan_is_ordered", passed: false, detail: "Step order is not contiguous.", offenders: ["step 3"] },
            ],
            passed: false,
          },
        })}
      />,
    );

    // The check id label and its detail sentence both contain "sources
    // resolve" ("All 3 sources resolve."), so the id is matched by its exact
    // rendered form (`check_id.replace(/_/g, " ")`) rather than a substring
    // that also matches the detail.
    expect(screen.getByText("sources resolve")).toBeInTheDocument();
    expect(screen.getByText("plan is ordered")).toBeInTheDocument();
    expect(screen.getByText("step 3")).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 checks passed/i)).toBeInTheDocument();
  });

  it("marks a step that requires downtime", () => {
    render(
      <PlanTab
        report={aReport({
          migration_plan: { ...plan, steps: [{ ...plan.steps[0], requires_downtime: true }] },
        })}
      />,
    );

    expect(screen.getByText(/requires downtime/i)).toBeInTheDocument();
  });

  it('does not grade the "requires downtime" badge with a severity colour', () => {
    // Fix round 2, finding 2. `requires_downtime` is a fact about the step;
    // whether that fact is a *problem* is check 10's question
    // (`_check_zero_downtime_respected`, validate.py:488-515), and with no
    // zero-downtime constraint stated that check PASSES -- "No zero-downtime
    // constraint was stated" -- making an unremarkable step. Styling this
    // badge `risk-medium` unconditionally re-derives a verdict the backend
    // already owns, one panel above where check 10's own outcome renders
    // (correctly, in `risk-high` on failure). No `validation` is supplied
    // here (default `null`), so a neutral badge leaves nothing in this
    // render with a `risk-*` class.
    const { container } = render(
      <PlanTab
        report={aReport({
          migration_plan: { ...plan, steps: [{ ...plan.steps[0], requires_downtime: true }] },
        })}
      />,
    );

    expect(screen.getByText(/requires downtime/i)).toBeInTheDocument();
    expect(container.querySelectorAll('[class*="risk-"]')).toHaveLength(0);
  });

  it("says so when no plan was produced", () => {
    render(<PlanTab report={aReport({ migration_plan: null })} />);

    expect(screen.getByText(/no plan was produced/i)).toBeInTheDocument();
  });

  it(
    "does not claim full coverage when unaddressed_with_reason is empty but the " +
      "backend's own coverage check failed",
    () => {
      // Fix round 1, CRITICAL. `unaddressed_with_reason` empty does not mean
      // every file is covered -- planning.py's `_unaddressed` only produces an
      // entry when there is an honest documented reason. A file a documented
      // change covers, that no step addresses and that has no honest reason,
      // produces NO entry and instead fails validate.py's
      // `affected_files_addressed` check. This is exactly that state: the
      // array is empty, the check failed, and offenders are named. The old
      // code rendered "Every affected file is addressed by a step" here
      // regardless -- re-deriving coverage from an empty array is exactly
      // what rule 19 forbids; the fix reads the backend's own outcome instead.
      render(
        <PlanTab
          report={aReport({
            migration_plan: { ...plan, unaddressed_with_reason: [] },
            validation: {
              attempt: 1,
              outcomes: [
                {
                  check_id: "affected_files_addressed",
                  passed: false,
                  detail:
                    "1 file(s) with high-confidence usage are neither addressed by a step " +
                    "nor explained, so the plan reads as complete while leaving them out.",
                  offenders: ["src/app/legacy.py"],
                },
              ],
              passed: false,
            },
          })}
        />,
      );

      expect(screen.queryByText(/every affected file is addressed/i)).not.toBeInTheDocument();
      // The check's own detail sentence -- mirrored, not re-derived (rule
      // 19) -- appears in both the coverage panel and the Validation panel
      // below, since both read the same outcome.
      expect(screen.getAllByText(/neither addressed by a step/i)).toHaveLength(2);
    },
  );

  it(
    "names check 8's offenders even when unaddressed_with_reason also has entries",
    () => {
      // Fix round 3, finding I7. Round 1's fix covered only the *empty*
      // `unaddressed_with_reason` branch; this is the other one. Two files
      // carry a documented reason (from `plan.unaddressed_with_reason`) and
      // a *third* file has no step and no reason, which is exactly the
      // silence check 8's own docstring refuses: "a file that is neither
      // addressed nor mentioned, which is how a partial plan reads as a
      // complete one." Before this fix the panel rendered the two reasoned
      // files and said nothing about the third -- the offender was visible
      // only in the Validation panel below, under a check id.
      render(
        <PlanTab
          report={aReport({
            migration_plan: plan, // unaddressed_with_reason: [tests/test_legacy.py]
            validation: {
              attempt: 1,
              outcomes: [
                {
                  check_id: "affected_files_addressed",
                  passed: false,
                  detail:
                    "1 file(s) with high-confidence usage are neither addressed by a step " +
                    "nor explained, so the plan reads as complete while leaving them out.",
                  offenders: ["src/app/legacy.py"],
                },
              ],
              passed: false,
            },
          })}
        />,
      );

      // The reasoned file is still there.
      expect(screen.getByText("tests/test_legacy.py")).toBeInTheDocument();
      // And the check-8 offender is no longer silent -- it appears in this
      // panel now, as well as in the Validation panel below (which already
      // named it).
      expect(screen.getAllByText("src/app/legacy.py")).toHaveLength(2);
      expect(screen.getAllByText(/neither addressed by a step/i)).toHaveLength(2);
    },
  );

  it("asserts full coverage only by reading the backend's own check, when it passed", () => {
    render(
      <PlanTab
        report={aReport({
          migration_plan: { ...plan, unaddressed_with_reason: [] },
          validation: {
            attempt: 1,
            outcomes: [
              {
                check_id: "affected_files_addressed",
                passed: true,
                detail:
                  "All 3 file(s) with high-confidence usage are either addressed by a step " +
                  "or listed with a reason.",
                offenders: [],
              },
            ],
            passed: true,
          },
        })}
      />,
    );

    // The rendered claim is the backend's own `detail` sentence, not a
    // hardcoded generic string re-derived from the empty array -- shown in
    // both the coverage panel and the Validation panel below.
    expect(screen.getAllByText(/all 3 file\(s\)/i)).toHaveLength(2);
  });

  it("makes no coverage claim when there is no validation to source one from", () => {
    render(
      <PlanTab
        report={aReport({
          migration_plan: { ...plan, unaddressed_with_reason: [] },
          validation: null,
        })}
      />,
    );

    expect(screen.queryByText(/every affected file is addressed/i)).not.toBeInTheDocument();
    expect(screen.getByText(/no files were listed as unaddressed/i)).toBeInTheDocument();
  });

  it("does not name a cause for an empty human-decisions list", () => {
    // Fix round 1, finding 5. The component checks only that
    // `human_decisions_applied` is empty; "the constraints settled every
    // question" is a cause nothing in the component or the types
    // establishes -- the same defect class as Task 12's TRANSITIVE_ONLY
    // bullet, a plausible mechanism asserted as fact because it usually
    // holds.
    render(
      <PlanTab
        report={aReport({ migration_plan: { ...plan, human_decisions_applied: [] } })}
      />,
    );

    expect(screen.queryByText(/settled every question/i)).not.toBeInTheDocument();
    expect(screen.getByText(/no human decision was applied/i)).toBeInTheDocument();
  });
});
