/**
 * How a cost figure is allowed to be worded. One function, because the rule is
 * about honesty rather than formatting and a second copy of it would be a
 * second chance to get it wrong.
 *
 * Four states, and each exists because the alternative misleads:
 *
 *   - `estimated_cost_usd === null` prints "not priced", never `$0.00`. Zero
 *     reads as free. `_totalled` (`models/usage.py`) returns `null` for two
 *     different reasons, and the note must not conflate them: `calls === 0`
 *     means no model has been called yet, so there is nothing to price;
 *     `calls > 0` means calls were made and no price is known for the model
 *     used. The first is not a pricing gap.
 *   - `pricing_complete === false` prints a `≥` prefix. Spec 9.4: when this is
 *     false the cost is a lower bound, and the flag is the only thing that
 *     says so. Phase 0 resolved the stack to OpenRouter, so this is the
 *     ordinary case rather than the edge one.
 *   - `estimated` is separate and orthogonal: it says a token count came from
 *     a local tokenizer rather than the provider. Either flag can be true
 *     without the other.
 */

import type { UsageView } from "../api/types";

export type CostLabel = {
  text: string;
  note: string | null;
  lowerBound: boolean;
  estimated: boolean;
};

/**
 * Five decimals below a cent. A real run of this product costs $0.00056, which
 * two decimals would render as `$0.00` — every run it has ever performed,
 * reported as free.
 */
function money(value: number): string {
  return value < 0.01 ? `$${value.toFixed(5)}` : `$${value.toFixed(2)}`;
}

export function costLabel(usage: UsageView): CostLabel {
  const estimated = usage.estimated;

  if (usage.estimated_cost_usd === null) {
    // `_totalled([])` and `_totalled` over calls that all lack a cost both
    // return `null` -- `calls` is what tells them apart. Zero calls means
    // no model has been used yet, which is not a pricing gap.
    return {
      text: "not priced",
      note: usage.calls === 0 ? "no model has been used yet" : "no price is known for the model used",
      lowerBound: false,
      estimated,
    };
  }

  const figure = money(usage.estimated_cost_usd);

  if (!usage.pricing_complete) {
    return {
      text: `≥ ${figure}`,
      note: "lower bound — some calls have no price",
      lowerBound: true,
      estimated,
    };
  }

  return { text: figure, note: null, lowerBound: false, estimated };
}
