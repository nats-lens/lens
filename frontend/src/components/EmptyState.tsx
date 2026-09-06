import { cn } from "@/lib/cn";
import { Button } from "./ui/button";

/** The `Unavailable` half of the envelope, from either type source. */
export type UnavailableLike = { reason: string; fix: string; doc?: string | null };

/** What a panel shows instead of a zero (design rule 04).
 *
 * The backend supplies the reason and the fix, so this component cannot invent a
 * placeholder even by accident -- there is nothing to render without them. The
 * fix is the whole point: "monitoring not configured" is a complaint, "start the
 * server with http_port: 8222" is something a person can go and do.
 */
export function EmptyState({
  unavailable,
  title,
  action,
  className,
}: {
  unavailable: UnavailableLike;
  title?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-card border border-degraded-border bg-surface-degraded p-[13px]",
        className,
      )}
    >
      <div className="text-[12.5px] font-medium text-degraded">
        {title ?? "Not available from this server"}
      </div>
      <p className="mt-[6px] text-[11.5px] leading-[1.55] text-muted-foreground text-pretty">
        {unavailable.fix}
      </p>
      {(action || unavailable.doc) && (
        <div className="mt-3 flex items-center gap-2">
          {action}
          {unavailable.doc && (
            <a
              href={unavailable.doc}
              target="_blank"
              rel="noreferrer"
              className="text-[11.5px] text-muted-foreground underline underline-offset-2 hover:text-foreground"
            >
              NATS docs
            </a>
          )}
        </div>
      )}
    </div>
  );
}

/** The same shape for a list that came back empty for an ordinary reason.
 *
 * Kept separate from `EmptyState` so the two never blur: this one means "the
 * server answered, and the answer was none", which is not a missing source and
 * must not borrow its amber.
 */
export function NoRows({
  title,
  body,
  action,
  actionLabel,
  className,
}: {
  title: string;
  body?: string;
  action?: () => void;
  actionLabel?: string;
  className?: string;
}) {
  return (
    <div className={cn("rounded-card border border-border bg-card p-[13px]", className)}>
      <div className="text-[12.5px] font-medium text-card-foreground">{title}</div>
      {body && (
        <p className="mt-[6px] text-[11.5px] leading-[1.55] text-muted-foreground text-pretty">
          {body}
        </p>
      )}
      {action && actionLabel && (
        <Button size="xs" className="mt-3" onClick={action}>
          {actionLabel}
        </Button>
      )}
    </div>
  );
}
