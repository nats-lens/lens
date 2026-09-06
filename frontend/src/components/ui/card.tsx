import { cn } from "@/lib/cn";

/** 10px radius, 1px border, card ground, no shadow.
 *
 * `tone` tints the border and the ground for a panel that is already saying
 * something -- a failing source, a healthy probe. It is not decoration.
 */
export function Card({
  className,
  tone = "default",
  ...props
}: React.ComponentProps<"div"> & { tone?: "default" | "healthy" | "degraded" | "destructive" }) {
  return (
    <div
      className={cn(
        "rounded-card border bg-card",
        tone === "default" && "border-border",
        tone === "healthy" && "border-healthy-border bg-surface-healthy",
        tone === "degraded" && "border-degraded-border bg-surface-degraded",
        tone === "destructive" && "border-destructive-border bg-surface-degraded",
        className,
      )}
      {...props}
    />
  );
}

/** Title on the left, provenance on the right. That right slot is where a
 * `SourceBadge` goes, which is why it is part of the header rather than
 * something each screen arranges for itself. */
export function CardHeader({
  title,
  description,
  right,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  right?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <div className="flex items-center justify-between gap-3">
        <span className="t-card-title truncate text-foreground">{title}</span>
        {right}
      </div>
      {description && (
        <div className="t-caption mt-1 text-ink-subtle text-pretty">{description}</div>
      )}
    </div>
  );
}

export function CardBody({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("p-4", className)} {...props} />;
}

/** One fact: label left, value right, hairline under.
 *
 * Values are mono because they came from the server -- this row is the design's
 * `Connection` and `JetStream account` panels. Pass a `SourcedValue` as the
 * value when the fact has provenance to declare.
 */
export function FactRow({
  label,
  value,
  className,
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-baseline justify-between gap-3.5 border-b border-hairline py-2 last:border-b-0",
        className,
      )}
    >
      <span className="text-[12.5px] text-ink-label">{label}</span>
      <span className="min-w-0 text-right font-mono text-[12px] tabular-nums text-card-foreground">
        {value}
      </span>
    </div>
  );
}
