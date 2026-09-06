/** Core NATS: subscribe, publish, request, and read what came back.
 *
 * Reference: the CoreExplorer artboard.
 *
 * The split between HTTP and the websocket is deliberate and worth knowing
 * before reading this file: subscriptions are *created* over HTTP, which returns
 * the channel name, and the socket only *joins* that channel. Publishing and
 * requesting are HTTP too. So the socket carries no side effects, a reconnect
 * can blindly rejoin, and nothing here has to reason about delivery.
 *
 * Transcript rows are deliberately thin -- the firehose never carries hex dumps.
 * Selecting a row fetches the full message by `capture_id`.
 */
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { api, apiQuery } from "@/lib/api";
import { ws, type TranscriptRow } from "@/lib/ws";
import { bytes as fmtBytes, clock, compact, toBase64 } from "@/lib/format";
import {
  Badge,
  Button,
  DroppedRow,
  Card,
  CardBody,
  ErrorPanel,
  Field,
  Input,
  Mono,
  NoRows,
  Page,
  PageHeader,
  Shell,
  Split,
  SplitMain,
  SubjectChip,
  Tabs,
  Textarea,
  type Tab,
} from "@/components";
import { useServerScope } from "@/lib/useServerScope";
import { Inspector } from "./Inspector";
import { useTranscript } from "./transcript";
import type { components } from "@/lib/api.d";

type Mode = "sub" | "pub" | "req";
type SubscriptionInfo = components["schemas"]["SubscriptionInfo"];

const MODES: readonly Tab<Mode>[] = [
  { id: "sub", label: "Subscribe" },
  { id: "pub", label: "Publish" },
  { id: "req", label: "Request" },
];

const encode = (text: string) => toBase64(new TextEncoder().encode(text));

export function CoreScreen() {
  const navigate = useNavigate();
  const { serverId, isLoading: serversLoading } = useServerScope();

  const [mode, setMode] = useState<Mode>("sub");
  const [subject, setSubject] = useState("");
  const [queueGroup, setQueueGroup] = useState("");
  const [paused, setPaused] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState<string | null>(null);

  const [payload, setPayload] = useState("");
  const [pubSubject, setPubSubject] = useState("");
  const [replyTimeout, setReplyTimeout] = useState(2);

  const transcript = useTranscript(paused);
  const { onMessage, onDropped, clear } = transcript;

  const subs = useQuery({
    ...apiQuery("/api/servers/{server_id}/core/subscriptions", {
      path: { server_id: serverId ?? "" },
    }),
    enabled: Boolean(serverId),
  });
  const subscriptions = useMemo(() => subs.data ?? [], [subs.data]);

  const chips = useQuery({
    ...apiQuery("/api/servers/{server_id}/core/chips", { path: { server_id: serverId ?? "" } }),
    enabled: Boolean(serverId),
  });

  // Join every live subscription's channel. The returned releases are what stop
  // the socket accumulating channels nobody is listening to any more.
  useEffect(() => {
    if (subscriptions.length === 0) return;
    const releases = subscriptions.map((s) =>
      ws.join(s.channel, { onMessage: (row) => onMessage(row), onDropped }),
    );
    return () => releases.forEach((release) => release());
  }, [subscriptions, onMessage, onDropped]);

  const subscribe = useMutation({
    mutationFn: async () =>
      api.post("/api/servers/{server_id}/core/subscriptions", {
        path: { server_id: serverId ?? "" },
        body: { subject: subject.trim(), queue: queueGroup.trim() || null, rate_cap: 200 },
      }),
    onSuccess: () => {
      setSubject("");
      setQueueGroup("");
      void subs.refetch();
    },
  });

  const unsubscribe = useMutation({
    mutationFn: async (id: string) =>
      api.delete("/api/servers/{server_id}/core/subscriptions/{sub_id}", {
        path: { server_id: serverId ?? "", sub_id: id },
      }),
    onSuccess: () => void subs.refetch(),
  });

  const publish = useMutation({
    mutationFn: async () =>
      api.post("/api/servers/{server_id}/core/publish", {
        path: { server_id: serverId ?? "" },
        body: { subject: pubSubject.trim(), payload_b64: encode(payload), headers: {} },
      }),
  });

  const request = useMutation({
    mutationFn: async () =>
      api.post("/api/servers/{server_id}/core/request", {
        path: { server_id: serverId ?? "" },
        body: {
          subject: pubSubject.trim(),
          payload_b64: encode(payload),
          headers: {},
          timeout_seconds: replyTimeout,
        },
      }),
  });

  const message = useQuery({
    ...apiQuery("/api/servers/{server_id}/core/messages/{capture_id}", {
      path: { server_id: serverId ?? "", capture_id: selected ?? "" },
    }),
    enabled: Boolean(serverId && selected),
  });

  // A subject filter hides messages but never the drop markers: a gap is still a
  // gap, and silently removing it would misrepresent the stream.
  const entries = useMemo(
    () =>
      filter
        ? transcript.entries.filter((e) => e.kind === "drop" || e.row.subject.startsWith(filter))
        : transcript.entries,
    [transcript.entries, filter],
  );

  const mapSubject = useCallback(() => void navigate({ to: "/schemas" }), [navigate]);

  if (!serverId) {
    return (
      <Shell crumbs={["Core"]}>
        <Page>
          <PageHeader title="Core NATS" />
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
    <Shell crumbs={["Core"]}>
      <Page>
        <PageHeader
          title="Core NATS"
          description="Plain publish and subscribe, with every payload run through the decoding chain."
          actions={
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant={paused ? "primary" : "outline"}
                onClick={() => setPaused((p) => !p)}
                title="Pausing stops the screen moving; the subscription stays open."
              >
                {paused ? "Paused" : "Pause"}
              </Button>
              <Button size="sm" variant="outline" onClick={clear}>
                Clear
              </Button>
            </div>
          }
        />

        <Tabs className="mt-4" tabs={MODES} value={mode} onChange={setMode} />

        <Card className="mt-3.5">
          <CardBody>
            {mode === "sub" ? (
              <div className="flex items-end gap-2">
                <Field label="Subject" className="flex-1">
                  <Input
                    className="font-mono"
                    placeholder="orders.>"
                    value={subject}
                    onChange={(e) => setSubject(e.currentTarget.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && subject.trim()) subscribe.mutate();
                    }}
                  />
                </Field>
                <Field label="Queue group" className="w-[180px] flex-none">
                  <Input
                    className="font-mono"
                    placeholder="optional"
                    value={queueGroup}
                    onChange={(e) => setQueueGroup(e.currentTarget.value)}
                  />
                </Field>
                <Button
                  disabled={!subject.trim() || subscribe.isPending}
                  onClick={() => subscribe.mutate()}
                >
                  Subscribe
                </Button>
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                <div className="flex items-end gap-2">
                  <Field label="Subject" className="flex-1">
                    <Input
                      className="font-mono"
                      placeholder={mode === "req" ? "service.echo" : "orders.new"}
                      value={pubSubject}
                      onChange={(e) => setPubSubject(e.currentTarget.value)}
                    />
                  </Field>
                  {mode === "req" && (
                    <Field label="Timeout" className="w-[120px] flex-none">
                      <Input
                        type="number"
                        value={String(replyTimeout)}
                        onChange={(e) => setReplyTimeout(Number(e.currentTarget.value))}
                      />
                    </Field>
                  )}
                  <Button
                    disabled={!pubSubject.trim() || publish.isPending || request.isPending}
                    onClick={() => (mode === "pub" ? publish.mutate() : request.mutate())}
                  >
                    {mode === "pub" ? "Publish" : "Send request"}
                  </Button>
                </div>
                <Field label="Payload">
                  <Textarea
                    rows={4}
                    className="font-mono"
                    placeholder='{"id": "ord_8813"}'
                    value={payload}
                    onChange={(e) => setPayload(e.currentTarget.value)}
                  />
                </Field>
                {publish.isError && <ErrorPanel error={publish.error} />}
                {request.isError && <ErrorPanel error={request.error} />}
                {request.data && <RequestOutcome result={request.data} />}
              </div>
            )}
            {subscribe.isError && <ErrorPanel className="mt-3" error={subscribe.error} />}
          </CardBody>
        </Card>

        {(subscriptions.length > 0 || (chips.data?.length ?? 0) > 0) && (
          <div className="mt-3.5 flex flex-wrap items-center gap-2">
            {subscriptions.map((s) => (
              <ActiveSubscription
                key={s.id}
                subscription={s}
                active={filter === s.subject.replace(/[>*].*$/, "")}
                onFilter={() => {
                  const prefix = s.subject.replace(/[>*].*$/, "");
                  setFilter(filter === prefix ? null : prefix);
                }}
                onStop={() => unsubscribe.mutate(s.id)}
              />
            ))}
            {chips.data?.map((c) => (
              <SubjectChip
                key={c.subject}
                subject={c.subject}
                seen={c.seen}
                onClick={() => setSubject(c.subject)}
              />
            ))}
          </div>
        )}

        <Split className="mt-4 min-h-0 flex-1">
          <SplitMain className="flex min-w-0 flex-col">

            <div className="min-h-0 flex-1 overflow-auto rounded-card border border-border">
              {entries.length === 0 ? (
                <div className="px-3 py-8">
                  <NoRows
                    title={subscriptions.length ? "Waiting for messages" : "Not subscribed"}
                    body={
                      subscriptions.length
                        ? "Nothing has been published on these subjects since you subscribed. Core NATS has no history — only what arrives from now on."
                        : "Subscribe to a subject above. Core NATS delivers only what is published while you are listening."
                    }
                  />
                </div>
              ) : (
                entries.map((entry) =>
                  entry.kind === "drop" ? (
                    <DroppedRow key={entry.key} count={entry.count} since={entry.since} />
                  ) : (
                    <TranscriptLine
                      key={entry.key}
                      row={entry.row}
                      selected={entry.row.capture_id === selected}
                      onSelect={setSelected}
                    />
                  ),
                )
              )}
            </div>
          </SplitMain>

          <div className="w-[420px] flex-none">
            {message.data ? (
              <Inspector message={message.data} onMapSubject={mapSubject} />
            ) : (
              <NoRows
                title="No message selected"
                body="Pick a row to see its headers, its decoded fields, and which step of the chain resolved it."
              />
            )}
          </div>
        </Split>
      </Page>
    </Shell>
  );
}

/** One transcript row, memoised.
 *
 * The buffer holds up to 2000 rows and grows by a whole animation frame's worth
 * at a time, so without this every arriving message re-rendered every row
 * already on screen. `TranscriptRow` objects are never mutated -- the socket
 * hands over a fresh one per message -- so referential equality is exactly the
 * right test, and only genuinely new rows plus the two whose selection changed
 * do any work.
 */
const TranscriptLine = memo(function TranscriptLine({
  row,
  selected,
  onSelect,
}: {
  row: TranscriptRow;
  selected: boolean;
  onSelect: (captureId: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(row.capture_id)}
      className={
        "flex w-full items-center gap-3 border-b border-hairline px-3 py-[7px] text-left hover:bg-control-hover" +
        (selected ? " bg-muted" : "")
      }
    >
      <Mono size="sm" className="w-[76px] flex-none text-ink-subtle">
        {clock(row.at)}
      </Mono>
      <Badge tone={row.direction === "IN" ? "neutral" : "primary"} size="xs">
        {row.direction}
      </Badge>
      <Mono size="sm" className="w-[220px] flex-none truncate text-foreground">
        {row.subject}
      </Mono>
      <Mono size="sm" className="min-w-0 flex-1 truncate text-ink-dim">
        {row.preview}
      </Mono>
      <Mono size="sm" className="w-[64px] flex-none text-right text-ink-subtle">
        {fmtBytes(row.size)}
      </Mono>
    </button>
  );
});

function ActiveSubscription({
  subscription,
  active,
  onFilter,
  onStop,
}: {
  subscription: SubscriptionInfo;
  active: boolean;
  onFilter: () => void;
  onStop: () => void;
}) {
  return (
    <span
      className={
        "inline-flex items-center gap-2 rounded-badge border px-2 py-[3px] text-[11px] " +
        (active ? "border-primary-border text-primary" : "border-border text-muted-foreground")
      }
    >
      <button type="button" onClick={onFilter} className="font-mono">
        {subscription.subject}
      </button>
      {subscription.queue && <span className="text-ink-faint">Q:{subscription.queue}</span>}
      <span className="text-ink-faint">{compact(subscription.delivered)}</span>
      {subscription.dropped > 0 && (
        <span className="text-degraded">−{compact(subscription.dropped)}</span>
      )}
      <button
        type="button"
        onClick={onStop}
        aria-label={`Unsubscribe from ${subscription.subject}`}
        className="text-ink-subtle hover:text-foreground"
      >
        ×
      </button>
    </span>
  );
}

/** A request nobody answered is an outcome, not a failure. */
function RequestOutcome({ result }: { result: components["schemas"]["RequestResult"] }) {
  if (!result.ok) {
    return (
      <div className="rounded-card border border-degraded-border px-3 py-2">
        <div className="text-[12.5px] font-medium text-degraded">No reply</div>
        <Mono size="sm" className="mt-1 block break-all text-muted-foreground">
          {result.error}
        </Mono>
      </div>
    );
  }
  return (
    <div className="rounded-card border border-healthy-border px-3 py-2">
      <div className="flex items-center justify-between">
        <span className="text-[12.5px] font-medium text-healthy">Replied</span>
        <Mono size="sm" className="text-ink-subtle">
          {result.elapsed_ms.toFixed(1)} ms
        </Mono>
      </div>
      {result.reply && (
        <Mono size="sm" className="mt-1.5 block break-all text-card-foreground">
          {result.reply.decoded.text ?? result.reply.decoded.codec}
        </Mono>
      )}
    </div>
  );
}
