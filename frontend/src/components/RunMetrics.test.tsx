import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { aSnapshot, anUsageView } from "../test/fixtures";
import { RunMetrics } from "./RunMetrics";

describe("RunMetrics", () => {
  it("shows the three token counts and the call count", () => {
    render(
      <RunMetrics
        snapshot={aSnapshot({
          usage: anUsageView({
            calls: 4,
            input_tokens: 320,
            output_tokens: 40,
            total_tokens: 360,
            estimated_cost_usd: 0.00042,
          }),
        })}
      />,
    );

    expect(screen.getByText("320")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("360")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("prints a lower bound when some calls have no price", () => {
    render(
      <RunMetrics
        snapshot={aSnapshot({
          usage: anUsageView({ estimated_cost_usd: 0.00042, pricing_complete: false }),
        })}
      />,
    );

    expect(screen.getByText("≥ $0.00042")).toBeInTheDocument();
    expect(screen.getByText(/lower bound/i)).toBeInTheDocument();
  });

  it("says not priced rather than showing zero", () => {
    render(
      <RunMetrics snapshot={aSnapshot({ usage: anUsageView({ estimated_cost_usd: null, calls: 3 }) })} />,
    );

    expect(screen.getByText(/not priced/i)).toBeInTheDocument();
    expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
  });

  it("flags estimated token counts", () => {
    render(
      <RunMetrics
        snapshot={aSnapshot({ usage: anUsageView({ estimated: true, estimated_cost_usd: 0.001 }) })}
      />,
    );

    expect(screen.getByText(/partly estimated/i)).toBeInTheDocument();
  });

  it("shows where the tokens went", () => {
    // Spec 9.4: the second question a developer asks.
    render(
      <RunMetrics
        snapshot={aSnapshot({
          usage: anUsageView({ by_node: [["assess_risk", 210], ["generate_plan", 150]] }),
        })}
      />,
    );

    expect(screen.getByText("assess_risk")).toBeInTheDocument();
    expect(screen.getByText("210")).toBeInTheDocument();
  });

  it("shows nothing rather than zeroes before a run exists", () => {
    render(<RunMetrics snapshot={null} />);

    expect(screen.getByText(/no run started/i)).toBeInTheDocument();
  });

  it("shows the model in use, from usage.by_model", () => {
    // The brief's docstring promised this and its code never rendered it --
    // DESIGN.md's Telemetry section and READINESS.md 2.5 both require it.
    render(
      <RunMetrics
        snapshot={aSnapshot({ usage: anUsageView({ by_model: [["openai/gpt-4.1-mini", 300]] }) })}
      />,
    );

    expect(screen.getByText("openai/gpt-4.1-mini")).toBeInTheDocument();
    expect(screen.getByText("300")).toBeInTheDocument();
  });

  it("says not recorded yet before any call has a model attached", () => {
    render(<RunMetrics snapshot={aSnapshot({ usage: anUsageView({ by_model: [] }) })} />);

    expect(screen.getByText(/not recorded yet/i)).toBeInTheDocument();
  });
});
