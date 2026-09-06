/** Colours a consumer row from the server's own verdict.
 *
 * The backend computes `ConsumerHealth` from `num_pending`, `num_ack_pending`
 * and `num_redelivered` against the consumer's own limits -- this screen does
 * not re-derive it, only maps the three-value enum to the shared `Tone` scale
 * that `StatusDot` and `Meter` already use.
 */
import type { components } from "@/lib/api.d";
import type { Tone } from "@/components";

export function toneForConsumerHealth(health: components["schemas"]["ConsumerHealth"]): Tone {
  switch (health) {
    case "healthy":
      return "healthy";
    case "degraded":
      return "degraded";
    case "failing":
      return "destructive";
  }
}
