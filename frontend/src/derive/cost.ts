/**
 * How a cost figure is allowed to be worded. One function, because the rule is
 * about honesty rather than formatting and a second copy of it would be a
 * second chance to get it wrong.
 *
 * Three states, and each exists because the alternative misleads:
 *
 *   - `estimated_cost_usd === null` prints "not priced", never `$0.00`. Zero
 *     reads as free; the truth is that no price is known for the model.
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
    return {
      text: "not priced",
      note: "no price is known for the model used",
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
