/** JetStream advisories and $SYS events.
 *
 * Reference: the Advisories artboard.
 *
 * The premise this screen has to keep saying out loud: advisories are published
 * once and never stored. There is no history to fetch -- the feed holds only
 * what nats-lens has seen since it started listening, which is why the counts
 * are `sampled` and why the banner offers a durable capture stream. A screen
 * that quietly showed an empty list would look like a healthy cluster.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, apiQuery } from "@/lib/api";
import { ws } from "@/lib/ws";
import { compact, timestamp } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ErrorPanel,
  FactRow,
  Mono,
  NoRows,
  Page,
  PageHeader,
  Shell,
  SourceBadge,
  Split,
  SplitMain,
  StatusDot,
  type BadgeTone,
  type Tone,
} from "@/components";
import { useServerScope } from "@/lib/useServerScope";
import type { components } from "@/lib/api.d";

type AdvisoryEvent = components["schemas"]["AdvisoryEvent"];
type Severity = components["schemas"]["Severity"];
type AdvisoryKind = components["schemas"]["AdvisoryKind"];

const SEVERITY_TONE: Record<Severity, BadgeTone> = {
  info: "neutral",
  notice: "primary",
  warning: "degraded",
  alert: "destructive",
};

const SEVERITY_DOT: Record<Severity, Tone> = {
  info: "idle",
  notice: "healthy",
  warning: "degraded",
  alert: "destructive",
};

export function AdvisoriesScreen() {
  const { serverId, isLoading: serversLoading } = useServerScope();
  const [kind, setKind] = useState<AdvisoryKind | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [live, setLive] = useState<AdvisoryEvent[]>([]);

  const state = useQuery({
    ...apiQuery("/api/servers/{server_id}/advisories/state", {
      path: { server_id: serverId ?? "" },
    }),
    enabled: Boolean(serverId),
  });

  const counts = useQuery({
    ...apiQuery("/api/servers/{server_id}/advisories/counts", {
      path: { server_id: serverId ?? "" },
    }),
    enabled: Boolean(serverId),
    refetchInterval: 5000,
  });

  const events = useQuery({
    ...apiQuery("/api/servers/{server_id}/advisories", {
      path: { server_id: serverId ?? "" },
      query: { kind: kind ?? undefined, limit: 200 },
    }),
    enabled: Boolean(serverId),
  });

  // New advisories arrive on the socket. They are prepended rather than merged
  // into the query cache so an open detail panel does not jump under the cursor.
  useEffect(() => {
    if (!serverId) return;
    setLive([]);
    return ws.join(`advisories:${serverId}`, {
      onAdvisory: (frame) => setLive((prev) => [frame.event, ...prev].slice(0, 200)),
    });
  }, [serverId]);

  const capture = useMutation({
    mutationFn: async () =>
      api.post("/api/servers/{server_id}/advisories/capture", {
        path: { server_id: serverId ?? "" },
        body: {
          name: "ADVISORIES",
          subjects: ["$JS.EVENT.ADVISORY.>"],
          max_age_seconds: 604800,
          max_msgs: 1_000_000,
          replicas: 1,
        },
      }),
    onSuccess: () => void state.refetch(),
  });

  const all = useMemo(() => {
    const seen = new Set<string>();
    const merged = [...live, ...(events.data ?? [])];
    const unique = merged.filter((e) => !seen.has(e.id) && seen.add(e.id));
    return kind ? unique.filter((e) => e.kind === kind) : unique;
  }, [live, events.data, kind]);

  const selected = all.find((e) => e.id === selectedId) ?? all[0] ?? null;
  const feed = state.data;

  if (!serverId) {
    return (
      <Shell crumbs={["Advisories"]}>
        <Page>
          <PageHeader title="Advisories" />
          {!serversLoading && (
            <NoRows
              title="No server selected"
              body="Register a server on the Servers screen, then come back here."
            />
          )}
        </Page>
      </Shell>
    );
  }

  return (
    <Shell crumbs={["Advisories"]}>
      <Page>
        <PageHeader
          title="Advisories"
          description="What JetStream and the system account report about themselves: redeliveries, give-ups, leadership changes, connects and disconnects."
          actions={<SourceBadge source="sampled" />}
        />

        {/* The screen's central caveat, and the one action that changes it. */}
        {feed && (
          <div
            className={
              "mt-4 flex items-start justify-between gap-4 rounded-card border px-3.5 py-3 " +
              (feed.capture_stream ? "border-healthy-border" : "border-degraded-border")
            }
          >
            <div className="min-w-0">
              <div
                className={
                  "text-[12.5px] font-medium " +
                  (feed.capture_stream ? "text-healthy" : "text-degraded")
                }
              >
                {feed.capture_stream
                  ? `Capturing to ${feed.capture_stream}`
                  : "Advisories are not being kept"}
              </div>
              <p className="mt-1 text-[11.5px] leading-[1.55] text-muted-foreground text-pretty">
                {feed.capture_stream
                  ? "A JetStream stream is storing these events, so they survive a restart and can be read back later."
                  : feed.note}
              </p>
            </div>
            {!feed.capture_stream && (
              <Button
                size="sm"
                variant="outline"
                disabled={capture.isPending}
                onClick={() => capture.mutate()}
              >
                {capture.isPending ? "Creating…" : "Capture to a stream"}
              </Button>
            )}
          </div>
        )}
        {capture.isError && <ErrorPanel className="mt-2" error={capture.error} />}

        <Split className="mt-4 min-h-0 flex-1">
          {/* The filter rail doubles as the count of what has been seen. */}
          <div className="w-[220px] flex-none">
            <div className="t-label px-1 pb-1.5 text-ink-dim">Event types</div>
            <button
              type="button"
              onClick={() => setKind(null)}
              className={
                "flex w-full items-center justify-between rounded-control px-2 py-1.5 text-[12.5px] hover:bg-control-hover " +
                (kind === null ? "bg-muted text-foreground" : "text-muted-foreground")
              }
            >
              <span>All events</span>
              <Mono size="sm" className="text-ink-subtle">
                {compact(all.length)}
              </Mono>
            </button>
            {counts.data
              ?.filter((c) => c.count > 0)
              .map((c) => (
                <button
                  key={c.kind}
                  type="button"
                  onClick={() => setKind(kind === c.kind ? null : c.kind)}
                  className={
                    "flex w-full items-center gap-2 rounded-control px-2 py-1.5 text-[12.5px] hover:bg-control-hover " +
                    (kind === c.kind ? "bg-muted text-foreground" : "text-muted-foreground")
                  }
                >
                  <StatusDot tone={SEVERITY_DOT[c.severity]} size={6} label={c.severity} />
                  <span className="min-w-0 flex-1 truncate text-left">{c.label}</span>
                  <Mono size="sm" className="text-ink-subtle">
                    {compact(c.count)}
                  </Mono>
                </button>
              ))}
          </div>

          <SplitMain className="min-w-0">
            {events.isError ? (
              <ErrorPanel error={events.error} onRetry={() => events.refetch()} />
            ) : (
              <div className="grid min-h-0 grid-cols-[1fr_400px] gap-5">
                <div className="min-h-0 overflow-auto rounded-card border border-border">
                  {all.length === 0 ? (
                    <div className="px-3 py-8">
                      <NoRows
                        title="Nothing seen yet"
                        body="Advisories are published once and never stored, so this feed starts empty and fills as events happen. A quiet cluster looks exactly like this."
                      />
                    </div>
                  ) : (
                    all.map((e) => (
                      <button
                        key={e.id}
                        type="button"
                        onClick={() => setSelectedId(e.id)}
                        className={
                          "flex w-full items-start gap-3 border-b border-hairline px-3 py-2 text-left hover:bg-control-hover" +
                          (selected?.id === e.id ? " bg-muted" : "")
                        }
                      >
                        <Mono size="sm" className="w-[70px] flex-none text-ink-subtle">
                          {timestamp(e.at).slice(-8)}
                        </Mono>
                        <Badge tone={SEVERITY_TONE[e.severity]} size="xs">
                          {e.kind}
                        </Badge>
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[12.5px] text-foreground">{e.target}</div>
                          <div className="mt-0.5 truncate text-[11.5px] text-ink-dim">
                            {e.summary}
                          </div>
                        </div>
                      </button>
                    ))
                  )}
                </div>

                <div className="min-h-0 overflow-auto">
                  {selected ? (
                    <div className="flex flex-col gap-3">
                      <div>
                        <div className="flex items-center gap-2">
                          <Badge tone={SEVERITY_TONE[selected.severity]} size="sm">
                            {selected.kind}
                          </Badge>
                          <span className="text-[12.5px] text-foreground">{selected.target}</span>
                        </div>
                        <Mono size="sm" className="mt-2 block break-all text-ink-subtle">
                          {selected.subject}
                        </Mono>
                      </div>

                      <Card>
                        <CardBody>
                          <CardHeader title="What this means" />
                          <p className="mt-1 text-[12px] leading-[1.6] text-muted-foreground text-pretty">
                            {selected.explanation}
                          </p>
                        </CardBody>
                      </Card>

                      {(selected.actions ?? []).length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {(selected.actions ?? []).map((a) => (
                            <Button key={a.label} size="xs" variant="outline" disabled title={a.target}>
                              {a.label}
                            </Button>
                          ))}
                        </div>
                      )}

                      <Card>
                        <CardBody>
                          <CardHeader
                            title="Raw event"
                            description="Exactly what the server published, not a summary of it."
                          />
                          <Mono
                            size="sm"
                            className="mt-1 block max-h-[300px] overflow-auto whitespace-pre-wrap break-all text-card-foreground"
                          >
                            {selected.body}
                          </Mono>
                          <div className="mt-2">
                            <FactRow label="Type" value={<Mono size="sm">{selected.type_url}</Mono>} />
                            <FactRow label="At" value={timestamp(selected.at)} />
                          </div>
                        </CardBody>
                      </Card>
                    </div>
                  ) : (
                    <NoRows
                      title="No event selected"
                      body="Pick an event to see the raw advisory the server published and what it usually means."
                    />
                  )}
                </div>
              </div>
            )}
          </SplitMain>
        </Split>
      </Page>
    </Shell>
  );
}
