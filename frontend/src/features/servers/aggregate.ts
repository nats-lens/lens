/** The four stat tiles at the top of the Servers screen.
 *
 * Each JetStream figure is summed across whichever registered servers actually
 * have one to report -- a server that is disconnected, or has no JetStream
 * account, contributes nothing rather than a zero. `hasValue` is what makes
 * "contributes nothing" and "contributes zero" different outcomes.
 */
import { hasValue, type SourcedLike } from "@/components";
import type { components } from "@/lib/api.d";

type ServerSummary = components["schemas"]["ServerSummary"];
type JetStreamAccountFacts = components["schemas"]["JetStreamAccountFacts"];

const NOW = () => new Date().toISOString();

function sumJetstream(
  servers: readonly ServerSummary[],
  pick: (facts: JetStreamAccountFacts) => number,
): SourcedLike<number> {
  let total = 0;
  let any = false;
  for (const server of servers) {
    if (hasValue(server.jetstream)) {
      total += pick(server.jetstream.value);
      any = true;
    }
  }
  return any
    ? { value: total, source: "jetstream", at: NOW(), unavailable: null }
    : {
        value: null,
        source: "jetstream",
        at: NOW(),
        unavailable: {
          reason: "not_connected",
          fix: "Connect at least one server to see JetStream totals here.",
          doc: null,
        },
      };
}

export function totalStreams(servers: readonly ServerSummary[]): SourcedLike<number> {
  return sumJetstream(servers, (f) => f.streams);
}

export function totalConsumers(servers: readonly ServerSummary[]): SourcedLike<number> {
  return sumJetstream(servers, (f) => f.consumers);
}

export function totalStored(servers: readonly ServerSummary[]): SourcedLike<number> {
  return sumJetstream(servers, (f) => f.storage_used);
}

/** `bytesOf`'s "of 4.1 TB" half, or nothing when a limit is unlimited (-1) --
 * summing an unlimited account's limit with anyone else's would just be wrong. */
export function storageLimitCaption(servers: readonly ServerSummary[]): string | null {
  let total = 0;
  let any = false;
  for (const server of servers) {
    if (!hasValue(server.jetstream)) continue;
    const limit = server.jetstream.value.storage_limit;
    if (limit < 0) return null;
    total += limit;
    any = true;
  }
  return any ? String(total) : null;
}

/** "one reconnecting, one offline" -- the connected-tile's sub-line, built from
 * states nats-lens already knows about its own registry (no source badge: this
 * is not a figure a server reported). */
export function summarizeStates(servers: readonly ServerSummary[]): string {
  const reconnecting = servers.filter(
    (s) => s.state === "reconnecting" || s.state === "connecting",
  ).length;
  const offline = servers.filter((s) => s.state === "disconnected" || s.state === "error").length;
  const parts: string[] = [];
  if (reconnecting > 0) parts.push(`${reconnecting} reconnecting`);
  if (offline > 0) parts.push(`${offline} offline`);
  if (parts.length === 0) return servers.length > 0 ? "all reachable" : "none registered yet";
  return parts.join(", ");
}
