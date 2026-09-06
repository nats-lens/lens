/** The frontend half of the provenance contract.
 *
 * Mirrors nats_lens.provenance. Regenerate the exact types from the OpenAPI
 * schema with `npm run types`; these hand-written aliases exist so the shared
 * components could be written before the generator had anything to read.
 */

export type Source = "client" | "jetstream" | "monitor" | "system" | "sampled";

export interface Unavailable {
  reason: string;
  fix: string;
  doc: string | null;
}

export interface Sourced<T> {
  value: T | null;
  source: Source;
  at: string;
  unavailable: Unavailable | null;
}

export const SOURCE_MEANING: Record<Source, string> = {
  client: "Read from the NATS client connection itself.",
  jetstream: "Read from the JetStream API over the client connection.",
  monitor: "Polled from the server's HTTP monitoring port.",
  system: "Pushed by the $SYS account.",
  sampled: "Observed by nats-lens while it was watching. Not a server-side total.",
};

export function isKnown<T>(s: Sourced<T>): s is Sourced<T> & { value: T } {
  return s.unavailable === null && s.value !== null;
}
