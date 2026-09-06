import { cn } from "@/lib/cn";
import { percent } from "@/lib/format";
import type { Tone } from "./status-dot";

const FILL: Record<Tone, string> = {
  healthy: "bg-healthy",
  degraded: "bg-degraded",
  destructive: "bg-destructive",
  idle: "bg-idle",
  primary: "bg-primary",
};

/** A 3px bar with a mono caption above it.
 *
 * `tone="auto"` colours by how full it is, which is right for a quota. It is
 * wrong for anything whose meaning is not "fuller is worse" -- a dead-letter
 * stream at 61% is still rose -- so those pass a tone explicitly.
 */
export function Meter({
  value,
  label,
  caption,
  tone = "auto",
  className,
}: {
  /** A fraction from 0 to 1. Use `format.ratio(used, limit)` to get one. */
  value: number;
  label?: React.ReactNode;
  /** Right-hand caption. Defaults to the percentage. */
  caption?: React.ReactNode;
  tone?: Tone | "auto";
  className?: string;
}) {
  const clamped = Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
  const resolved: Tone =
    tone !== "auto" ? tone : clamped >= 0.95 ? "destructive" : clamped >= 0.8 ? "degraded" : "healthy";

  return (
    <div className={cn("min-w-0", className)}>
      {(label || caption !== null) && (
        <div className="flex justify-between gap-3 font-mono text-[11px] tabular-nums text-ink-label">
          <span className="min-w-0 truncate">{label}</span>
          <span className="flex-none">{caption ?? percent(clamped)}</span>
        </div>
      )}
      <div
        role="meter"
        aria-valuenow={Math.round(clamped * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        className="mt-[6px] h-[3px] overflow-hidden rounded-[2px] bg-track"
      >
        <div
          className={cn("h-[3px] rounded-[2px]", FILL[resolved])}
          style={{ width: `${clamped * 100}%` }}
        />
      </div>
    </div>
  );
}
