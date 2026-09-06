/** A request that failed, distinct from a source that is merely unconfigured.
 *
 * `EmptyState` renders `Sourced.unavailable` -- a value the server told us it
 * cannot produce. A failed HTTP call (the stream does not exist, JetStream is
 * disabled on this account, the endpoint is not built yet) is a different
 * thing and must not borrow that component's "add a source" framing. This is
 * the plain "something went wrong, here is what and here is retry" panel.
 */
import { ApiError } from "@/lib/api";
// Straight from the modules, not the barrel. A component that imports the barrel
// that re-exports it is a cycle, and Rollup then has to guess which chunk each
// half lands in -- which it warns will break execution order.
import { Button } from "./ui/button";
import { Card, CardBody } from "./ui/card";

export function ErrorPanel({
  error,
  onRetry,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  const detail =
    error instanceof ApiError
      ? (error.problem?.detail ?? error.message)
      : error instanceof Error
        ? error.message
        : "Something went wrong.";
  const status = error instanceof ApiError ? error.status : null;

  return (
    <Card tone="destructive" className={className}>
      <CardBody>
        <div className="text-[12.5px] font-medium text-destructive">
          {status ? `Request failed (${status})` : "Request failed"}
        </div>
        {/* `whitespace-pre-wrap` because some details are written as several
            lines and mean it -- protoc reports file, line and column one per
            line, and collapsing that into a paragraph loses the shape that makes
            it readable. Single-line details are unaffected. */}
        <p className="mt-[6px] whitespace-pre-wrap text-[11.5px] leading-[1.55] text-muted-foreground">
          {detail}
        </p>
        {onRetry && (
          <Button size="xs" variant="outline" className="mt-3" onClick={onRetry}>
            Retry
          </Button>
        )}
      </CardBody>
    </Card>
  );
}
