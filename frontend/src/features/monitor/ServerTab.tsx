/** `/varz` and `/jsz`, plus the rate nats-lens itself samples between polls.
 *
 * Reference: the Monitor artboard, the "server" tab. The mock's sparkline
 * is drawn from a fixed local series; the real API has no such series to draw
 * (nats-lens keeps none), so this shows the current sampled rate as a number
 * with its `sampled` badge instead of a fabricated chart.
 */
import type { components } from "@/lib/api.d";
import { Card, CardBody, CardHeader, EmptyState, FactRow, SourceBadge, StatCard, SourcedValue } from "@/components";
import { bytes, compact, rate, timestamp } from "@/lib/format";
import { fromMonitor, sampledRate } from "@/lib/sourced";

type MonitorOverview = components["schemas"]["MonitorOverview"];

export function ServerTab({ overview }: { overview: MonitorOverview }) {
  const varz = overview.varz;
  if (!varz) return null; // MonitorScreen only mounts this tab once `reachable` is true.

  const rows = new Map((overview.varz_rows ?? []).map((r) => [r.k, r.v] as const));
  const cpuLine = rows.get("CPU") ?? "";
  const rates = sampledRate(overview.rates);

  return (
    <div>
      <div className="flex items-center justify-between">
        <span className="t-card-title text-foreground">Server — /varz</span>
        <SourceBadge source="monitor" />
      </div>

      <div className="mt-3.5 grid grid-cols-4 gap-3.5">
        <StatCard label="Uptime" sourced={fromMonitor(rows.get("Uptime") ?? "")} sub={`since ${timestamp(varz.start)}`} />
        <StatCard
          label="Connections"
          sourced={fromMonitor(compact(varz.connections))}
          sub={`${compact(varz.leafnodes)} leaf, ${compact(varz.remotes)} remote`}
        />
        <StatCard label="Total connections" sourced={fromMonitor(compact(varz.total_connections))} sub="lifetime" />
        <StatCard
          label="Slow consumers"
          sourced={fromMonitor(compact(varz.slow_consumers))}
          sub="disconnected for falling behind"
        />
        <StatCard
          label="Messages in"
          sourced={fromMonitor(compact(varz.in_msgs))}
          sub={overview.rates ? `${rate(overview.rates.in_msgs_per_sec)} now` : undefined}
        />
        <StatCard
          label="Messages out"
          sourced={fromMonitor(compact(varz.out_msgs))}
          sub={overview.rates ? `${rate(overview.rates.out_msgs_per_sec)} now` : undefined}
        />
        <StatCard
          label="Bytes in"
          sourced={fromMonitor(bytes(varz.in_bytes))}
          sub={overview.rates ? `${bytes(overview.rates.in_bytes_per_sec)}/s now` : undefined}
        />
        <StatCard label="Memory" sourced={fromMonitor(bytes(varz.mem))} sub={cpuLine || undefined} />
      </div>

      <div className="mt-6 grid grid-cols-[1fr_380px] gap-6">
        <Card>
          <CardBody>
            <CardHeader
              title="Message rate"
              description="The difference between the last two polls of the in_msgs and out_msgs counters -- not a server-side total, and not a history."
              right={<SourceBadge source="sampled" />}
            />
            <div className="mt-1">
              <FactRow label="In" value={<SourcedValue sourced={rates} format={(r) => rate(r.in_msgs_per_sec)} showBadge={false} />} />
              <FactRow label="Out" value={<SourcedValue sourced={rates} format={(r) => rate(r.out_msgs_per_sec)} showBadge={false} />} />
              <FactRow
                label="Bytes in"
                value={<SourcedValue sourced={rates} format={(r) => `${bytes(r.in_bytes_per_sec)}/s`} showBadge={false} />}
              />
              <FactRow
                label="Bytes out"
                value={<SourcedValue sourced={rates} format={(r) => `${bytes(r.out_bytes_per_sec)}/s`} showBadge={false} />}
              />
            </div>
          </CardBody>
        </Card>

        <div>
          <div className="flex items-center justify-between">
            <span className="t-card-title text-foreground">JetStream — /jsz</span>
            <SourceBadge source="monitor" />
          </div>
          {overview.jsz ? (
            <div className="mt-1">
              <FactRow label="Meta cluster leader" value={overview.jsz.meta_leader ?? "none"} />
              <FactRow label="Streams / consumers" value={`${compact(overview.jsz.streams)} · ${compact(overview.jsz.consumers)}`} />
              <FactRow label="Messages" value={compact(overview.jsz.messages)} />
              <FactRow label="Storage used" value={bytes(overview.jsz.storage)} />
              <FactRow label="Memory used" value={bytes(overview.jsz.memory)} />
              <FactRow label="API total / errors" value={`${compact(overview.jsz.api_total)} · ${compact(overview.jsz.api_errors)}`} />
            </div>
          ) : (
            <EmptyState
              className="mt-3"
              unavailable={{
                reason: "jetstream_not_enabled",
                fix: "JetStream is not enabled on this server, or /jsz did not answer. Enable JetStream in the server's config to see stream and consumer counts here.",
                doc: null,
              }}
            />
          )}
          <p className="mt-3.5 text-[11.5px] leading-[1.55] text-ink-dim text-pretty">
            /jsz?streams=true&amp;consumers=true returns per-stream and per-consumer state in one
            request -- cheaper than walking the JetStream API when only an overview is needed.
          </p>
        </div>
      </div>
    </div>
  );
}
