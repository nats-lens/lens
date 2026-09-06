/** Overview: the five headline numbers, retention and limits, and cluster peers. */
import { bytes, compact, count, duration } from "@/lib/format";
import { FactRow, Section, SourceBadge, StatCard, StatusDot } from "@/components";
import type { components } from "@/lib/api.d";
import { fromJetStream } from "@/lib/sourced";

type StreamDetail = components["schemas"]["StreamDetail"];

function limitRows(detail: StreamDetail): { k: string; v: string }[] {
  const l = detail.limits;
  return [
    { k: "Retention", v: detail.retention },
    { k: "Max age", v: l.max_age_seconds > 0 ? duration(l.max_age_seconds) : "unlimited" },
    { k: "Max messages", v: l.max_msgs > 0 ? count(l.max_msgs) : "unlimited" },
    { k: "Max bytes", v: l.max_bytes > 0 ? bytes(l.max_bytes) : "unlimited" },
    { k: "Max msg size", v: l.max_msg_size > 0 ? bytes(l.max_msg_size) : "unlimited" },
    { k: "Discard", v: l.discard },
    { k: "Duplicate window", v: duration(l.duplicate_window_seconds) },
    { k: "Allow direct get", v: String(l.allow_direct) },
  ];
}

export function OverviewTab({ stream }: { stream: StreamDetail }) {
  const s = stream.state;

  return (
    <div>
      <div className="grid grid-cols-5 gap-4">
        <StatCard label="messages" sourced={fromJetStream(s.messages)} format={compact} />
        <StatCard label="bytes" sourced={fromJetStream(s.bytes)} format={bytes} />
        <StatCard label="first seq" sourced={fromJetStream(s.first_seq)} format={count} />
        <StatCard label="last seq" sourced={fromJetStream(s.last_seq)} format={count} />
        <StatCard label="consumers" sourced={fromJetStream(s.consumer_count)} format={count} />
      </div>

      <div className="mt-6 grid grid-cols-2 gap-8">
        <Section title="Retention and limits" right={<SourceBadge source="jetstream" />}>
          {limitRows(stream).map((row) => (
            <FactRow key={row.k} label={row.k} value={row.v} />
          ))}
        </Section>

        <div>
          <Section title="Cluster" right={<SourceBadge source="jetstream" />}>
            {(stream.cluster.replicas ?? []).length === 0 ? (
              <p className="text-[12px] text-ink-faint">
                Single-server stream: no cluster to report peers for.
              </p>
            ) : (
              (stream.cluster.replicas ?? []).map((peer) => (
                <div
                  key={peer.name}
                  className="flex items-center gap-2.5 border-b border-hairline py-2.5 last:border-b-0"
                >
                  <StatusDot
                    tone={peer.offline ? "destructive" : peer.current ? "healthy" : "degraded"}
                    label={peer.offline ? "offline" : peer.current ? "current" : "behind"}
                  />
                  <span className="font-mono text-[12px] text-card-foreground">{peer.name}</span>
                  <span className="text-[11.5px] text-ink-subtle">
                    {peer.is_leader ? "leader" : "replica"}
                  </span>
                  <span className="flex-1" />
                  <span className="font-mono text-[11.5px] text-ink-label">
                    {peer.offline ? "offline" : !peer.lag ? "current" : `lag ${count(peer.lag)}`}
                  </span>
                </div>
              ))
            )}
          </Section>
          {(stream.state.num_deleted ?? 0) > 0 && (
            <p className="mt-3 text-[11.5px] text-ink-faint">
              {count((stream.state.num_deleted ?? 0))} messages deleted or purged from the middle of the
              sequence range.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
