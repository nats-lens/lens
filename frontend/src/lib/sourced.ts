/** Wraps a plain figure as the `Sourced` envelope `SourcedValue` needs.
 *
 * Most screens receive per-field `Sourced[T]` from the API and pass it straight
 * through. JetStream, KV and the object store do not: each domain's `schemas.py`
 * documents every field on those responses as uniformly `jetstream` provenance,
 * because the JetStream API is reachable over a plain client connection — a
 * stream that could not be read fails the whole request rather than returning a
 * null field.
 *
 * So the provenance for those numbers is single and always true, and this is
 * where it is made explicit: at the one point a figure reaches a `SourcedValue`
 * or `StatCard`. The design rule is "say how this reached the screen", not "wrap
 * it only when the schema happens to spell `Sourced[T]`".
 */
import type { SourcedLike } from "@/components";
import type { Source } from "@/lib/provenance";

export function known<T>(value: T, source: Source): SourcedLike<T> {
  return { value, source, at: new Date().toISOString(), unavailable: null };
}

/** The two sources that need this, named so call sites read as prose. */
export const fromJetStream = <T,>(value: T): SourcedLike<T> => known(value, "jetstream");
export const fromMonitor = <T,>(value: T): SourcedLike<T> => known(value, "monitor");

/** A rate delta, which is only ever `sampled`.
 *
 * `MonitorOverview.rates` is null on the first poll -- one reading is a total,
 * not a rate -- and the screen must say "not yet" rather than show a zero that
 * looks like an idle server. Returning the unavailable envelope puts that in
 * `SourcedValue`'s hands instead of every call site's.
 */
export function sampledRate<T>(rates: T | null | undefined): SourcedLike<T> {
  if (rates == null) {
    return {
      value: null,
      source: "sampled",
      at: new Date().toISOString(),
      unavailable: {
        reason: "awaiting_second_poll",
        fix: "Rates are the difference between two polls. The first reading has arrived; the next one produces a rate.",
        doc: null,
      },
    };
  }
  return known(rates, "sampled");
}
