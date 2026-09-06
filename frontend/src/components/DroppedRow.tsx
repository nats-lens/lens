import { clock, count } from "@/lib/format";
import { cn } from "@/lib/cn";

/** nats-lens fell behind and discarded messages.
 *
 * The websocket's `dropped` frame becomes this row, inline in the transcript
 * where the messages would have been. The same honesty rule as the source
 * badges, turned on our own limits: a gap in the stream is stated, not smoothed
 * over, because a transcript that quietly skips is worse than one that admits it.
 */
export function DroppedRow({
  count: dropped,
  since,
  className,
}: {
  count: number;
  /** ISO instant from the frame: when the run of drops began. */
  since: string;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex items-center gap-2.5 border-b border-hairline bg-surface-degraded px-3.5 py-2",
        className,
      )}
    >
      <span className="font-mono text-[11px] tabular-nums text-ink-faint">{clock(since)}</span>
      <span className="font-mono text-[12.5px] text-degraded">
        {count(dropped)} messages dropped
      </span>
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-label">
        nats-lens could not keep up with this subject and discarded them. Narrow the subject or
        raise the rate cap.
      </span>
    </div>
  );
}
