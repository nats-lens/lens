/** `/routez`, plus the gateway and leafnode counts that come with it.
 *
 * Reference: the Monitor artboard, the "routes" tab. Routes, leafnodes and
 * gateways are three different kinds of remote with the same shape, so they
 * share one table and are told apart by a badge rather than by three tables.
 */
import { useQuery } from "@tanstack/react-query";
import { apiQuery } from "@/lib/api";
import { bytes, compact } from "@/lib/format";
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  DataTable,
  ErrorPanel,
  FactRow,
  Mono,
  NoRows,
  SourceBadge,
  StatusDot,
  type BadgeTone,
  type Column,
} from "@/components";
import type { components } from "@/lib/api.d";

type RouteRow = components["schemas"]["RouteRow"];

const KIND_TONE: Record<string, BadgeTone> = {
  route: "primary",
  leaf: "neutral",
  gateway: "neutral",
};

export function RoutesTab({ serverId, pollMs }: { serverId: string; pollMs: number | false }) {
  const query = useQuery({
    ...apiQuery("/api/servers/{server_id}/monitor/routes", { path: { server_id: serverId } }),
    refetchInterval: pollMs,
  });

  if (query.isError) return <ErrorPanel error={query.error} onRetry={() => query.refetch()} />;

  const summary = query.data;
  const rows = summary?.routes ?? [];

  const columns: readonly Column<RouteRow>[] = [
    {
      key: "rid",
      header: "RID",
      width: "72px",
      cell: (r) => <Mono>{r.rid}</Mono>,
    },
    {
      key: "remote",
      header: "Remote",
      width: "minmax(0, 1fr)",
      cell: (r) => (
        <div className="flex min-w-0 items-center gap-2">
          {/* A route with a pending queue is the early sign of a struggling link. */}
          <StatusDot tone={r.pending_size > 1_000_000 ? "degraded" : "healthy"} />
          <span className="truncate text-foreground">{r.remote_id ?? "unnamed"}</span>
          <Badge tone={KIND_TONE[r.kind] ?? "neutral"} size="xs">
            {r.kind}
          </Badge>
        </div>
      ),
    },
    {
      key: "address",
      header: "Address",
      width: "190px",
      cell: (r) => (
        <Mono className="text-muted-foreground">
          {r.ip}:{r.port}
        </Mono>
      ),
    },
    {
      key: "subs",
      header: "Subs",
      width: "84px",
      align: "right",
      cell: (r) => <Mono>{compact(r.subscriptions)}</Mono>,
    },
    {
      key: "pending",
      header: "Pending",
      width: "96px",
      align: "right",
      cell: (r) => (
        <Mono className={r.pending_size > 1_000_000 ? "text-degraded" : undefined}>
          {bytes(r.pending_size)}
        </Mono>
      ),
    },
    {
      key: "rtt",
      header: "RTT",
      width: "84px",
      align: "right",
      cell: (r) => <Mono className="text-muted-foreground">{r.rtt ?? "—"}</Mono>,
    },
  ];

  return (
    <div className="grid grid-cols-[1fr_320px] gap-6">
      <div>
        <div className="flex items-center justify-between">
          <span className="t-card-title text-foreground">Cluster routes — /routez</span>
          <SourceBadge source="monitor" />
        </div>
        <DataTable
          className="mt-3.5"
          columns={columns}
          rows={rows}
          rowKey={(r) => `${r.kind}-${r.rid}`}
          rowHeight={52}
          empty={
            <NoRows
              title="No remotes"
              body="This server has no routes, leafnodes or gateways. A single-node server is expected to look like this."
            />
          }
          footnote={summary ? `as of ${summary.now}` : undefined}
        />
      </div>

      <Card>
        <CardBody>
          <CardHeader
            title="Remotes"
            description="Counted by the server itself, so these include remotes whose detail /routez does not list."
            right={<SourceBadge source="monitor" />}
          />
          <div className="mt-1">
            <FactRow label="Routes" value={compact(summary?.num_routes ?? 0)} />
            <FactRow label="Leafnodes" value={compact(summary?.num_leafnodes ?? 0)} />
            <FactRow label="Gateways" value={compact(summary?.num_gateways ?? 0)} />
          </div>
          <p className="mt-3.5 text-[11.5px] leading-[1.55] text-ink-dim text-pretty">
            Routes carry traffic between servers in one cluster. Leafnodes and gateways connect
            separate clusters, so a high round-trip on one of those is geography rather than a
            fault.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}
