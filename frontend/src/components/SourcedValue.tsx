import type { Source, Unavailable } from "@/lib/provenance";
import { SourceBadge } from "./SourceBadge";
import { cn } from "@/lib/cn";

/** The `Sourced[T]` envelope, as it arrives from either type source.
 *
 * `lib/provenance.ts` declares the hand-written mirror; `api.d.ts` generates one
 * struct per instantiation (`Sourced_float_`, `Sourced_..._ClientFacts_`, …).
 * They are the same shape apart from `unavailable` being optional in the
 * generated form, so this accepts both and screens never convert between them.
 */
export type SourcedLike<T> = {
  value: T | null;
  source: Source;
  at: string;
  unavailable?: Unavailable | { reason: string; fix: string; doc?: string | null } | null;
};

/** `provenance.isKnown` widened to the generated shape. Named apart from it so
 * an import of the wrong one is a compile error rather than a subtle mismatch. */
export function hasValue<T>(sourced: SourcedLike<T>): sourced is SourcedLike<T> & { value: T } {
  return sourced.value !== null && sourced.value !== undefined && !sourced.unavailable;
}

/** The only way a server-sourced number reaches the DOM.
 *
 * Given a `Sourced`, it renders either the value with its badge, or an em-dash
 * carrying the reason and the fix. Screens never unwrap a `Sourced` themselves,
 * which is what makes "never a zero for something we could not see" structural
 * rather than a habit.
 *
 * The missing case shows no source badge, deliberately. Foundations: "a missing
 * source reads as a missing badge rather than a silent zero." A badge next to a
 * dash would claim that something reported nothing, when in fact nothing
 * reported. The reason and the fix are on the element, and `fix` renders them
 * inline where there is room for it.
 */
export function SourcedValue<T>({
  sourced,
  format,
  showBadge = true,
  fix = false,
  className,
}: {
  sourced: SourcedLike<T>;
  /** How the value is written. Use `lib/format` -- `bytes`, `count`, `millis`. */
  format?: (value: T) => string;
  showBadge?: boolean;
  /** Also write the fix beside the dash. For a wide row; not for a table cell. */
  fix?: boolean;
  className?: string;
}) {
  const render = format ?? ((value: T) => String(value));

  if (!hasValue(sourced)) {
    const missing = sourced.unavailable;
    return (
      <span
        className={cn("inline-flex min-w-0 items-baseline gap-2", className)}
        title={missing ? `${sourced.source}: ${missing.reason} — ${missing.fix}` : undefined}
      >
        <span className="font-mono text-ink-faint">&mdash;</span>
        {fix && missing && (
          <span className="t-caption min-w-0 text-degraded text-pretty">{missing.fix}</span>
        )}
      </span>
    );
  }

  return (
    <span className={cn("inline-flex min-w-0 items-baseline gap-2", className)}>
      <span className="font-mono tabular-nums text-foreground">{render(sourced.value)}</span>
      {showBadge && <SourceBadge source={sourced.source} />}
    </span>
  );
}
