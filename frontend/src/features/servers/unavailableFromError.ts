import { ApiError } from "@/lib/api";
import type { UnavailableLike } from "@/components";

/** Turn a failed request -- most often a monitoring 503 -- into the same shape
 * `EmptyState` renders for a `Sourced` absence.
 *
 * The monitor endpoints raise rather than return a field when the port is not
 * configured or unreachable (`ServiceUnavailableException`, see
 * `domain/monitor/service.py::_unavailable`), so a screen that learns "not
 * reachable" from an HTTP failure needs to look identical to one that learns it
 * from a `Sourced.unavailable` field. Both paths end up here.
 */
export function unavailableFromError(
  error: unknown,
  fallbackReason = "monitoring_unreachable",
): UnavailableLike {
  if (error instanceof ApiError) {
    return {
      reason: fallbackReason,
      fix: error.problem?.detail ?? error.message,
      doc: "https://docs.nats.io/running-a-nats-service/nats_admin/monitoring",
    };
  }
  return {
    reason: fallbackReason,
    fix: error instanceof Error ? error.message : "Something went wrong.",
    doc: null,
  };
}
