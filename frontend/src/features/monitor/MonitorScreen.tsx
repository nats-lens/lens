/** The Monitor screen: four tabs over one server's HTTP monitoring port.
 *
 * Reference: the Monitor artboard. Everything here is `monitor` provenance
 * and nothing else -- this whole screen exists because the client protocol
 * cannot see any of it.
 *
 * So the screen has a precondition the other screens do not: if the server has
 * no monitoring URL, or the port does not answer, there is nothing to render and
 * the honest thing is to say which of those it is and how to fix it. That check
 * happens once, here, rather than in each tab.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiQuery } from "@/lib/api";
import { millis } from "@/lib/format";
import {
  Badge,
  EmptyState,
  ErrorPanel,
  Mono,
  NoRows,
  Page,
  PageHeader,
  Shell,
  Tabs,
  type Tab,
} from "@/components";
import { useServerScope } from "@/lib/useServerScope";
import { PollToggle, type PollMs } from "@/features/servers/PollToggle";
import { ServerTab } from "./ServerTab";
import { ConnectionsTab } from "./ConnectionsTab";
import { RoutesTab } from "./RoutesTab";
import { HealthTab } from "./HealthTab";
import { SubscriptionsTab } from "./SubscriptionsTab";

type TabId = "server" | "connections" | "subscriptions" | "routes" | "health";

const TABS: readonly Tab<TabId>[] = [
  { id: "server", label: "Server" },
  { id: "connections", label: "Connections" },
  { id: "subscriptions", label: "Subscriptions" },
  { id: "routes", label: "Routes" },
  { id: "health", label: "Health" },
];

export function MonitorScreen() {
  const { serverId, shellServer, isLoading: serversLoading } = useServerScope();
  const [tab, setTab] = useState<TabId>("server");
  const [pollMs, setPollMs] = useState<PollMs>(5000);

  // /connz paging is screen state, not tab state: it must survive tab switches
  // so returning to Connections does not silently jump back to page one.
  const [sort, setSort] = useState("cid");
  const [limit, setLimit] = useState(100);
  const [offset, setOffset] = useState(0);
  const [subs, setSubs] = useState(false);

  const overview = useQuery({
    ...apiQuery("/api/servers/{server_id}/monitor", { path: { server_id: serverId ?? "" } }),
    enabled: Boolean(serverId),
    refetchInterval: pollMs,
  });

  const data = overview.data;
  const poll = pollMs === false ? false : pollMs;

  return (
    <Shell
      crumbs={[shellServer?.name ?? "Servers", "Monitor"]}
    >
      <Page>
        <PageHeader
          title="Monitor"
          description="The server's HTTP monitoring port. None of this is visible to a NATS client, which is why it is a screen of its own."
          actions={
            <div className="flex items-center gap-3">
              {data?.reachable && (
                <span className="flex items-center gap-2">
                  <Mono className="text-ink-dim">{data.url}</Mono>
                  <Badge tone="healthy" size="sm">
                    {data.status_code ?? 200}
                  </Badge>
                  {data.latency_ms != null && (
                    <Mono className="text-ink-dim">{millis(data.latency_ms)}</Mono>
                  )}
                </span>
              )}
              <PollToggle value={pollMs} onChange={setPollMs} />
            </div>
          }
        />

        {!serverId ? (
          serversLoading ? null : (
            <NoRows
              title="No server selected"
              body="Register a server on the Servers screen, then come back here."
            />
          )
        ) : overview.isError ? (
          <ErrorPanel error={overview.error} onRetry={() => overview.refetch()} />
        ) : !data ? null : !data.reachable ? (
          // The screen's whole precondition, and the design's central claim in one
          // place: name the fix rather than render four tabs of zeros.
          <EmptyState
            title="No monitoring data for this server"
            unavailable={{
              reason: data.url ? "monitoring_unreachable" : "monitoring_not_configured",
              fix: data.url
                ? `nats-lens could not reach ${data.url}. ${data.error ?? "The port did not answer."} Check that the server was started with monitoring enabled and that the port is open to nats-lens.`
                : "This server has no monitoring URL. Start nats-server with `-m 8222` (or set `http_port: 8222` in its config) and add the URL under the server's settings.",
              doc: "https://docs.nats.io/running-a-nats-service/nats_admin/monitoring",
            }}
          />
        ) : (
          <>
            <Tabs tabs={TABS} value={tab} onChange={setTab} className="mt-5" />
            <div className="mt-5">
              {tab === "server" && <ServerTab overview={data} />}
              {tab === "connections" && (
                <ConnectionsTab
                  serverId={serverId}
                  pollMs={poll}
                  sort={sort}
                  limit={limit}
                  offset={offset}
                  subs={subs}
                  onSort={setSort}
                  onLimit={setLimit}
                  onOffset={setOffset}
                  onSubs={setSubs}
                />
              )}
              {tab === "subscriptions" && (
                <SubscriptionsTab serverId={serverId} pollMs={poll} />
              )}
              {tab === "routes" && <RoutesTab serverId={serverId} pollMs={poll} />}
              {tab === "health" && <HealthTab serverId={serverId} pollMs={poll} />}
            </div>
          </>
        )}
      </Page>
    </Shell>
  );
}
