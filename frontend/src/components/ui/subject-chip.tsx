import { cn } from "@/lib/cn";
import { compact } from "@/lib/format";
import { StatusDot, type Tone } from "./status-dot";

/** A saved subject filter with what nats-lens has actually seen on it.
 *
 * The count is `sampled` by definition -- it is what this process observed while
 * it was watching, not a server-side total -- so it is drawn quietly and the
 * screen states that once, near the row of chips.
 */
export function SubjectChip({
  subject,
  seen,
  active = false,
  tone = "healthy",
  onClick,
  className,
}: {
  subject: string;
  seen?: number;
  active?: boolean;
  tone?: Tone;
  onClick?: () => void;
  className?: string;
}) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      {...(onClick ? { type: "button" as const, onClick } : {})}
      className={cn(
        "flex h-[27px] flex-none items-center gap-2 rounded-control border px-[10px]",
        active ? "border-border-strong bg-control-hover" : "border-idle-border bg-transparent",
        onClick && "hover:border-border-strong",
        className,
      )}
    >
      <StatusDot tone={tone} size={5} />
      <span
        className={cn(
          "font-mono text-[11.5px]",
          active ? "text-foreground" : "text-muted-foreground",
        )}
      >
        {subject}
      </span>
      {seen !== undefined && (
        <span className="text-[10.5px] tabular-nums text-ink-faint">{compact(seen)}</span>
      )}
    </Tag>
  );
}
