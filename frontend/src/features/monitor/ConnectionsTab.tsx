/** `/connz`, with the query surface the endpoint actually has.
 *
 * Reference: the Monitor artboard, the "connections" tab. Sort, page size,
 * offset and `subs=true` are real server-side parameters, so the controls here
 * change the request rather than filtering an already-fetched page -- which is
 * the only way paging works on a server with thousands of connections.
 */
import { useQuery } from "@tanstack/react-query";
import { apiQuery } from "@/lib/api";
import { bytes, compact } from "@/lib/format";
import {
  Badge,
  Button,
  DataTable,
  ErrorPanel,
  Mono,
  NoRows,
  Select,
  SourceBadge,
  type Column,
} from "@/components";
import type { components } from "@/lib/api.d";

type ConnRow = components["schemas"]["ConnRow"];

/** What `/connz?sort=` accepts. Anything else is rejected by the server. */
const SORTS = [
  { value: "cid", label: "Connection id" },
  { value: "start", label: "Started" },
  { value: "subs", label: "Subscriptions" },
  { value: "pending_bytes", label: "Pending bytes" },
  { value: "msgs_to", label: "Messages out" },
  { value: "msgs_from", label: "Messages in" },
  { value: "idle", label: "Idle" },
  { value: "last", label: "Last activity" },
] as const;

const PAGE_SIZES = [25, 50, 100, 250] as const;

/** Pending bytes is the slow-consumer tell, so it is the one column that colours. */
function pendingTone(pendingBytes: number): "destructive" | "degraded" | undefined {
  if (pendingBytes > 1_000_000) return "destructive";
  if (pendingBytes > 100_000) return "degraded";
  return undefined;
}

export function ConnectionsTab({
  serverId,
  pollMs,
  sort,
  limit,
  offset,
  subs,
  onSort,
  onLimit,
  onOffset,
  onSubs,
}: {
  serverId: string;
  pollMs: number | false;
  sort: string;
  limit: number;
  offset: number;
  subs: boolean;
  onSort: (v: string) => void;
  onLimit: (v: number) => void;
  onOffset: (v: number) => void;
  onSubs: (v: boolean) => void;
}) {
  const query = useQuery({
    ...apiQuery("/api/servers/{server_id}/monitor/connections", {
      path: { server_id: serverId },
      query: { sort, limit, offset, subs },
    }),
    refetchInterval: pollMs,
  });

  if (query.isError) return <ErrorPanel error={query.error} onRetry={() => query.refetch()} />;

  const page = query.data;
  const rows = page?.connections ?? [];
  const total = page?.total ?? 0;
  const showing = offset + rows.length;

  const columns: readonly Column<ConnRow>[] = [
    {
      key: "cid",
      header: "CID",
      width: "84px",
      cell: (r) => <Mono>{r.cid}</Mono>,
    },
    {
      key: "name",
      header: "Name",
      width: "minmax(0, 1fr)",
      cell: (r) => (
        <div className="min-w-0">
          <div className="truncate text-foreground">{r.name || "unnamed"}</div>
          <div className="mt-0.5 truncate text-[11px] text-ink-dim">
            {r.lang ? `${r.lang} ${r.version ?? ""}`.trim() : r.kind}
          </div>
        </div>
      ),
    },
    {
      key: "account",
      header: "Account",
      width: "110px",
      cell: (r) => <span className="text-muted-foreground">{r.account ?? "—"}</span>,
    },
    {
      key: "address",
      header: "Address",
      width: "180px",
      cell: (r) => (
        <Mono className="text-muted-foreground">
          {r.ip}:{r.port}
        </Mono>
      ),
    },
    {
      key: "subs",
      header: "Subs",
      width: "72px",
      align: "right",
      cell: (r) => <Mono>{compact(r.subscriptions)}</Mono>,
    },
    {
      key: "pending",
      header: "Pending",
      width: "96px",
      align: "right",
      cell: (r) => {
        const tone = pendingTone(r.pending_bytes);
        return (
          <Mono className={tone === "destructive" ? "text-destructive" : tone === "degraded" ? "text-degraded" : undefined}>
            {bytes(r.pending_bytes)}
          </Mono>
        );
      },
    },
    {
      key: "rtt",
      header: "RTT",
      width: "78px",
      align: "right",
      cell: (r) => <Mono className="text-muted-foreground">{r.rtt ?? "—"}</Mono>,
    },
    {
      key: "idle",
      header: "Idle",
      width: "72px",
      align: "right",
      cell: (r) => <Mono className="text-muted-foreground">{r.idle}</Mono>,
    },
  ];

  return (
    <div>
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <span className="t-card-title text-foreground">Connections — /connz</span>
          <SourceBadge source="monitor" />
        </div>
        <div className="flex items-center gap-2">
          <Select
            aria-label="Sort by"
            value={sort}
            onChange={(e) => {
              onSort(e.currentTarget.value);
              onOffset(0);
            }}
          >
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </Select>
          <Select
            aria-label="Page size"
            value={String(limit)}
            onChange={(e) => {
              onLimit(Number(e.currentTarget.value));
              onOffset(0);
            }}
          >
            {PAGE_SIZES.map((n) => (
              <option key={n} value={n}>
                {n} rows
              </option>
            ))}
          </Select>
          <Button
            size="xs"
            variant={subs ? "primary" : "outline"}
            onClick={() => onSubs(!subs)}
            title="Ask the server to include each connection's subscription list"
          >
            subs=true
          </Button>
        </div>
      </div>

      <DataTable
        className="mt-3.5"
        columns={columns}
        rows={rows}
        rowKey={(r) => String(r.cid)}
        rowHeight={52}
        empty={<NoRows title="No connections" body="No clients are connected to this server right now." />}
        footnote={
          <div className="flex items-center justify-between">
            <span>
              {total > 0 ? `${offset + 1}–${showing} of ${compact(total)}` : "0 connections"}
              {page ? ` · as of ${page.now}` : ""}
            </span>
            <span className="flex items-center gap-2">
              <Button
                size="xs"
                variant="outline"
                disabled={offset === 0}
                onClick={() => onOffset(Math.max(0, offset - limit))}
              >
                Previous
              </Button>
              <Button
                size="xs"
                variant="outline"
                disabled={showing >= total}
                onClick={() => onOffset(offset + limit)}
              >
                Next
              </Button>
            </span>
          </div>
        }
      />

      {subs && rows.length > 0 && (
        <div className="mt-4">
          <div className="t-card-title text-foreground">Subscriptions</div>
          <div className="mt-2 flex flex-col gap-2">
            {rows
              .filter((r) => (r.subjects ?? []).length > 0)
              .map((r) => (
                <div key={r.cid} className="flex items-start gap-3">
                  <Mono className="w-[84px] flex-none text-ink-dim">{r.cid}</Mono>
                  <div className="flex flex-wrap gap-1.5">
                    {(r.subjects ?? []).map((s) => (
                      <Badge key={s} tone="neutral" size="xs">
                        <Mono>{s}</Mono>
                      </Badge>
                    ))}
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
