import { cn } from "@/lib/cn";
import { Card } from "./ui/card";
import { SourceBadge } from "./SourceBadge";
import { hasValue, type SourcedLike } from "./SourcedValue";

/** The tile at the top of Servers, JetStream and Monitor.
 *
 * It takes the whole `Sourced`, not a number, so the badge and the missing case
 * are decided here once rather than by each screen. A tile whose source is not
 * configured shows a dash and the fix -- never a zero, and never a bare dash
 * with no explanation of what would make it a number.
 */
export function StatCard<T>({
  label,
  sourced,
  value,
  format,
  sub,
  variant = "mono",
  className,
}: {
  label: React.ReactNode;
  /** The server's number. Preferred: it carries its own provenance. */
  sourced?: SourcedLike<T>;
  /** A number nats-lens itself knows -- how many servers are registered, say.
   * No badge, because no NATS source produced it. */
  value?: React.ReactNode;
  format?: (value: T) => string;
  sub?: React.ReactNode;
  /** `mono` for a figure the server reported, `sans` for a count of our own. */
  variant?: "mono" | "sans";
  className?: string;
}) {
  const known = sourced ? hasValue(sourced) : false;
  const missing = sourced && !known ? sourced.unavailable : null;

  const figure = sourced
    ? known
      ? (format ?? String)(sourced.value as T)
      : "—"
    : value;

  return (
    <Card className={cn("px-4 py-[15px]", className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-[12px] text-ink-label">{label}</span>
        {sourced && known && <SourceBadge source={sourced.source} />}
      </div>
      <div
        className={cn(
          "mt-[9px] truncate",
          variant === "mono"
            ? "font-mono text-[19px] font-medium tracking-[-0.03em] tabular-nums"
            : "text-[24px] font-semibold tracking-[-0.03em]",
          known || !sourced ? "text-foreground" : "text-ink-faint",
        )}
      >
        {figure}
      </div>
      <div
        className={cn(
          "mt-1 text-[11.5px] leading-[1.45] text-pretty",
          missing ? "text-degraded" : "text-ink-subtle",
        )}
      >
        {missing ? missing.fix : sub}
      </div>
    </Card>
  );
}
