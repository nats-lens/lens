/** `/subsz` -- who is actually listening.
 *
 * This is the only view of another connection's subscriptions: the client
 * protocol cannot see them, so without this there is no way to tell a subject
 * nobody is listening to from one that is merely quiet. A core NATS publish to
 * the former is dropped silently, which is the single most common "my messages
 * are disappearing" question.
 *
 * The `test` box is the point of the screen. Type a concrete subject and the
 * server reports which subscriptions would match it -- a question, not a search
 * through the table.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiQuery } from "@/lib/api";
import { compact, percent } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  DataTable,
  ErrorPanel,
  FactRow,
  Field,
  Input,
  Mono,
  NoRows,
  SourceBadge,
  StatCard,
  type Column,
} from "@/components";
import { fromMonitor } from "@/lib/sourced";
import type { components } from "@/lib/api.d";

type SubRow = components["schemas"]["SubRow"];

export function SubscriptionsTab({
  serverId,
  pollMs,
}: {
  serverId: string;
  pollMs: number | false;
}) {
  const [subject, setSubject] = useState("");
  const [test, setTest] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const limit = 100;

  const query = useQuery({
    ...apiQuery("/api/servers/{server_id}/monitor/subscriptions", {
      path: { server_id: serverId },
      query: { subs: true, offset, limit, test: test ?? undefined },
    }),
    // A `test` is a question asked once; only the listing follows the poll.
    refetchInterval: test ? false : pollMs,
  });

  if (query.isError) return <ErrorPanel error={query.error} onRetry={() => query.refetch()} />;

  const data = query.data;
  const rows = data?.subscriptions ?? [];

  const columns: readonly Column<SubRow>[] = [
    {
      key: "subject",
      header: "Subject",
      width: "minmax(0, 1fr)",
      cell: (r) => <Mono className="truncate text-foreground">{r.subject}</Mono>,
    },
    {
      key: "account",
      header: "Account",
      width: "110px",
      cell: (r) => <span className="text-muted-foreground">{r.account ?? "—"}</span>,
    },
    {
      key: "queue",
      header: "Queue group",
      width: "140px",
      cell: (r) =>
        r.queue_group ? (
          <Badge tone="primary" size="xs">
            {r.queue_group}
          </Badge>
        ) : (
          <span className="text-ink-faint">—</span>
        ),
    },
    {
      key: "cid",
      header: "CID",
      width: "80px",
      align: "right",
      cell: (r) => <Mono className="text-muted-foreground">{r.cid ?? "—"}</Mono>,
    },
    {
      key: "msgs",
      header: "Messages",
      width: "100px",
      align: "right",
      cell: (r) => <Mono>{compact(r.msgs)}</Mono>,
    },
  ];

  return (
    <div className="grid grid-cols-[1fr_320px] gap-6">
      <div>
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <span className="t-card-title text-foreground">Subscriptions — /subsz</span>
            <SourceBadge source="monitor" />
          </div>
        </div>

        <div className="mt-3 flex items-end gap-2">
          <Field
            label="Does a subject reach anyone?"
            hint="A concrete subject, not a pattern — the server matches it against every subscription."
            className="flex-1"
          >
            <Input
              font="mono"
              placeholder="orders.new"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  setOffset(0);
                  setTest(subject.trim() || null);
                }
              }}
            />
          </Field>
          <Button
            variant="outline"
            onClick={() => {
              setOffset(0);
              setTest(subject.trim() || null);
            }}
          >
            Test
          </Button>
          {test && (
            <Button
              variant="ghost"
              onClick={() => {
                setTest(null);
                setSubject("");
              }}
            >
              Clear
            </Button>
          )}
        </div>

        {test && (
          <div
            className={
              "mt-3 rounded-card border px-3.5 py-3 " +
              (rows.length ? "border-healthy-border" : "border-degraded-border")
            }
          >
            <div
              className={
                "text-[12.5px] font-medium " + (rows.length ? "text-healthy" : "text-degraded")
              }
            >
              {rows.length
                ? `${compact(rows.length)} subscription${rows.length === 1 ? "" : "s"} would receive a message on ${test}`
                : `Nothing is listening on ${test}`}
            </div>
            {rows.length === 0 && (
              <p className="mt-1 text-[11.5px] leading-[1.55] text-muted-foreground text-pretty">
                A core NATS publish to this subject is dropped -- there is no queue and no error.
                A JetStream stream capturing the subject would still store it.
              </p>
            )}
          </div>
        )}

        <DataTable
          className="mt-3.5"
          columns={columns}
          rows={rows}
          rowKey={(r) => `${r.cid}-${r.sid}`}
          rowHeight={46}
          empty={
            <NoRows
              title={test ? "No matching subscription" : "No subscriptions"}
              body={
                test
                  ? "No client has expressed interest in this subject."
                  : "This server is holding no subscriptions at all, which is unusual for a running server."
              }
            />
          }
          footnote={
            data && !test ? (
              <div className="flex items-center justify-between">
                <span>
                  {data.total > 0 ? `${offset + 1}–${offset + rows.length} of ${compact(data.total)}` : "none"}
                  {` · as of ${data.now}`}
                </span>
                <span className="flex items-center gap-2">
                  <Button
                    size="xs"
                    variant="outline"
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - limit))}
                  >
                    Previous
                  </Button>
                  <Button
                    size="xs"
                    variant="outline"
                    disabled={offset + rows.length >= data.total}
                    onClick={() => setOffset(offset + limit)}
                  >
                    Next
                  </Button>
                </span>
              </div>
            ) : undefined
          }
        />
      </div>

      <div className="flex flex-col gap-3.5">
        <div className="grid grid-cols-2 gap-3.5">
          <StatCard
            label="Subscriptions"
            sourced={fromMonitor(compact(data?.num_subscriptions ?? 0))}
            sub="held by this server"
          />
          <StatCard
            label="Cache hit rate"
            sourced={fromMonitor(percent(data?.cache_hit_rate ?? 0, 1))}
            sub="subject matches served from cache"
          />
        </div>

        <Card>
          <CardBody>
            <CardHeader
              title="Routing"
              description="How much work each publish costs the server before it reaches anyone."
              right={<SourceBadge source="monitor" />}
            />
            <div className="mt-1">
              <FactRow label="Cache entries" value={compact(data?.num_cache ?? 0)} />
              <FactRow label="Matches" value={compact(data?.num_matches ?? 0)} />
              <FactRow label="Inserts" value={compact(data?.num_inserts ?? 0)} />
              <FactRow label="Removes" value={compact(data?.num_removes ?? 0)} />
              <FactRow label="Max fanout" value={compact(data?.max_fanout ?? 0)} />
              <FactRow label="Average fanout" value={(data?.avg_fanout ?? 0).toFixed(2)} />
            </div>
            <p className="mt-3.5 text-[11.5px] leading-[1.55] text-ink-dim text-pretty">
              A falling cache hit rate usually means subjects are being generated per message
              rather than reused. The server pays for that match on every publish.
            </p>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
