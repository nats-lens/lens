/** The registry: every server nats-lens knows about, and what each one reports.
 *
 * Reference: the Main artboard. One deliberate departure from it -- the
 * per-row sparkline and the "1,204 /s" throughput figure in the mock are drawn
 * from a fixed local series that has no equivalent in the API. nats-lens keeps
 * no time series (see README), so a server's row here shows the message totals
 * `TrafficFacts` actually reports instead of a fabricated rate curve.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { apiQuery, ApiError } from "@/lib/api";
import type { components } from "@/lib/api.d";
import {
  Badge,
  Button,
  DataTable,
  type Column,
  Mono,
  NoRows,
  Page,
  PageHeader,
  Shell,
  SourcedValue,
  StatCard,
  StatusDot,
  toneForState,
} from "@/components";
import { bytes, compact, millis } from "@/lib/format";
import { useServerScope } from "@/lib/useServerScope";
import { DetailPanel } from "./DetailPanel";
import { PollToggle, POLL_OPTIONS, type PollMs } from "./PollToggle";
import { totalStreams, totalConsumers, totalStored, storageLimitCaption, summarizeStates } from "./aggregate";

type ServerSummary = components["schemas"]["ServerSummary"];

const COLUMNS: Column<ServerSummary>[] = [
  {
    key: "name",
    header: "Server",
    width: "1fr",
    cell: (row) => (
      <div className="min-w-0">
        <div className="flex items-center gap-2.5">
          <StatusDot tone={toneForState(row.state)} size={6} label={row.state} />
          <span className="truncate text-[13.5px] font-medium text-foreground">{row.name}</span>
          <Badge size="sm" tone={toneForState(row.state)}>
            {row.state}
          </Badge>
        </div>
        <div className="mt-1 truncate pl-[15px] text-[11.5px] text-ink-subtle">{row.note}</div>
      </div>
    ),
  },
  {
    key: "url",
    header: "Endpoint",
    width: "244px",
    cell: (row) => (
      <Mono size="sm" truncate className="text-muted-foreground">
        {row.primary_url}
      </Mono>
    ),
  },
  {
    key: "jetstream",
    header: "JetStream",
    width: "112px",
    cell: (row) => (
      <SourcedValue
        sourced={row.jetstream}
        format={(f) => `${compact(f.streams)} · ${compact(f.consumers)}`}
        showBadge={false}
      />
    ),
  },
  {
    key: "traffic",
    header: "Messages",
    width: "122px",
    cell: (row) => (
      <SourcedValue sourced={row.traffic} format={(t) => compact(t.in_msgs + t.out_msgs)} showBadge={false} />
    ),
  },
  {
    key: "rtt",
    header: "RTT",
    width: "68px",
    align: "right",
    cell: (row) => <SourcedValue sourced={row.rtt} format={millis} showBadge={false} />,
  },
];

export function ServersScreen() {
  const navigate = useNavigate();
  const { servers: scopeServers, serverId, selectServer } = useServerScope();
  const [pollMs, setPollMs] = useState<PollMs>(POLL_OPTIONS[1].ms);

  const listQuery = useQuery({
    ...apiQuery("/api/servers"),
    refetchInterval: pollMs,
  });
  const servers = listQuery.data ?? scopeServers;

  const streams = useMemo(() => totalStreams(servers), [servers]);
  const consumers = useMemo(() => totalConsumers(servers), [servers]);
  const stored = useMemo(() => totalStored(servers), [servers]);
  const storageLimit = useMemo(() => storageLimitCaption(servers), [servers]);

  return (
    <Shell
      crumbs={["Servers"]}
      actions={
        <>
          <PollToggle value={pollMs} onChange={setPollMs} />
          <Button variant="primary" size="sm" onClick={() => void navigate({ to: "/servers/new" })}>
            Add server
          </Button>
        </>
      }
    >
      <Page>
        <PageHeader
          title="Servers"
          description="Every connection is a live nats-py client. Select a server to see what it reports, and where each number actually comes from."
        />

        {listQuery.isError && (
          <p className="mt-4 text-[12.5px] text-destructive">
            {listQuery.error instanceof ApiError
              ? (listQuery.error.problem?.detail ?? listQuery.error.message)
              : "Could not load the registry."}
          </p>
        )}

        <div className="mt-[22px] grid grid-cols-4 gap-3.5">
          <StatCard
            label="Servers connected"
            value={`${servers.filter((s) => s.state === "connected").length} / ${servers.length}`}
            variant="sans"
            sub={summarizeStates(servers)}
          />
          <StatCard label="Streams" sourced={streams} format={compact} sub="across all connected servers" />
          <StatCard label="Consumers" sourced={consumers} format={compact} sub="across all connected servers" />
          <StatCard
            label="Stored"
            sourced={stored}
            format={bytes}
            sub={storageLimit ? `of ${bytes(Number(storageLimit))} in account limits` : "across all connected servers"}
          />
        </div>

        {servers.length === 0 && !listQuery.isLoading ? (
          <NoRows
            className="mt-6"
            title="No servers registered yet"
            body="Add the first server nats-lens should watch. Everything else on this screen fills in once it can connect."
            actionLabel="Add a server"
            action={() => void navigate({ to: "/servers/new" })}
          />
        ) : (
          <div className="mt-6 flex min-h-0 items-start gap-6">
            <div className="min-w-0 flex-1">
              <DataTable
                columns={COLUMNS}
                rows={servers}
                rowKey={(row) => row.id}
                selectedKey={serverId}
                onSelect={(row) => selectServer(row.id)}
                footnote="Message totals and JetStream figures need a live connection; server-wide counters also need the monitoring port or a system account. Servers without either show a dash."
              />
            </div>
            <aside className="w-[400px] flex-none">
              {serverId ? (
                <DetailPanel serverId={serverId} />
              ) : (
                <div className="text-[12.5px] text-ink-subtle">Select a server to see its detail.</div>
              )}
            </aside>
          </div>
        )}
      </Page>
    </Shell>
  );
}
