import { HARNESS_ORIGINS } from "../types/version";

/**
 * Non-component helpers for the Models tab. Kept in their own module so
 * ``ModelsTab.tsx`` exports only components (react-refresh fast-refresh
 * boundary), while tests and sibling components can still import them.
 */

/**
 * #267: a harness origin "passes" the filter when either it's not one
 * of the four chip-able origins (e.g., ``"training-daemon"`` rows in
 * Live Runs are governed by their own daemon control, not the chips)
 * OR it's explicitly enabled in the ``harnessFilter`` set. Default
 * state has all 4 chip-able origins enabled, so this is a no-op until
 * the operator toggles a chip off.
 */
const CHIPPABLE_HARNESSES = new Set<string>(HARNESS_ORIGINS);

export function passesHarnessFilter(
  harness: string,
  filter: Set<string>,
): boolean {
  if (!CHIPPABLE_HARNESSES.has(harness)) return true;
  return filter.has(harness);
}
