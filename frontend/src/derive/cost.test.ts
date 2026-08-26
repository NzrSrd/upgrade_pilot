import { describe, expect, it } from "vitest";

import { anUsageView } from "../test/fixtures";
import { costLabel } from "./cost";

describe("costLabel", () => {
  it("prints a known cost plainly", () => {
    const label = costLabel(anUsageView({ estimated_cost_usd: 0.00056, pricing_complete: true }));

    expect(label.text).toBe("$0.00056");
    expect(label.note).toBeNull();
    expect(label.lowerBound).toBe(false);
  });

  it("says not priced rather than printing zero", () => {
    // `$0.00` for an unpriced run is the single most misleading thing this
    // panel could say: it reads as "this was free" when it means "we do not
    // know". Spec 11 layer 1 asserts the backend returns None here for the
    // same reason.
    const label = costLabel(anUsageView({ estimated_cost_usd: null, calls: 4 }));

    expect(label.text).toBe("not priced");
    expect(label.note).toBe("no price is known for the model used");
    expect(label.lowerBound).toBe(false);
  });

  it("marks an incomplete price as a lower bound", () => {
    const label = costLabel(
      anUsageView({ estimated_cost_usd: 0.00056, pricing_complete: false }),
    );

    expect(label.text).toBe("≥ $0.00056");
    expect(label.note).toBe("lower bound — some calls have no price");
    expect(label.lowerBound).toBe(true);
  });

  it("reports estimated tokens independently of pricing", () => {
    // Two different uncertainties. `estimated` says a token count came from a
    // local tokenizer rather than the provider; `pricing_complete` says a
    // price was missing. Either can be true without the other.
    const label = costLabel(
      anUsageView({ estimated_cost_usd: 0.002, pricing_complete: true, estimated: true }),
    );

    expect(label.estimated).toBe(true);
    expect(label.lowerBound).toBe(false);
    expect(label.text).toBe("$0.00200");
  });

  it("carries both flags at once when both apply", () => {
    const label = costLabel(
      anUsageView({ estimated_cost_usd: 0.002, pricing_complete: false, estimated: true }),
    );

    expect(label.lowerBound).toBe(true);
    expect(label.estimated).toBe(true);
  });

  it("shows five decimals for the small figures this product actually produces", () => {
    // A real run costs $0.00056. Two decimals would render every run this
    // system has ever performed as $0.00.
    expect(costLabel(anUsageView({ estimated_cost_usd: 0.00001 })).text).toBe("$0.00001");
    expect(costLabel(anUsageView({ estimated_cost_usd: 1.5 })).text).toBe("$1.50");
    expect(costLabel(anUsageView({ estimated_cost_usd: 12.3456 })).text).toBe("$12.35");
  });

  it("prints a genuine zero as zero, not as unknown", () => {
    // A free-tier call really can return a cost of `0.0`
    // (`services/llm/tracked.py`'s `_extract_provider_cost` docstring: "a
    // free tier really does return 0.0"). That is a fact about a call that
    // happened, not an absence -- only `null` means unknown. `calls` is
    // nonzero here (the fixture default), which is what makes a real `0.0`
    // reachable at all: `_totalled([])` for zero calls returns `null`, not
    // `0.0` (see the next test).
    const label = costLabel(anUsageView({ estimated_cost_usd: 0, pricing_complete: true }));

    expect(label.text).toBe("$0.00000");
    expect(label.note).toBeNull();
  });

  it("distinguishes 'no model used yet' from 'a price is missing'", () => {
    // Finding I2. `_totalled([])` (`models/usage.py`) returns `null` for
    // zero calls, the same value it returns when calls were made but none
    // priced -- `calls` is the only field that tells the two apart. Before
    // this fix, the zero-call state routed into the "no price is known for
    // the model used" branch, naming a pricing gap for a model that was
    // never called.
    const noCallsYet = costLabel(anUsageView({ calls: 0, estimated_cost_usd: null }));
    expect(noCallsYet.note).toBe("no model has been used yet");

    const calledButUnpriced = costLabel(anUsageView({ calls: 4, estimated_cost_usd: null }));
    expect(calledButUnpriced.note).toBe("no price is known for the model used");
  });
});
