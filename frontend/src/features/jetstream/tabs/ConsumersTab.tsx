/** Consumers: the three numbers that actually diagnose one, coloured by health.
 *
 * `num_pending` is lag (how far behind the stream's last_seq this consumer
 * is), `num_ack_pending` is in-flight unacked deliveries, `num_redelivered`
 * is messages that had to be sent more than once. `health` is the server's
 * own verdict from all three against the consumer's limits -- this screen
 * only maps it to a colour, never re-derives it.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiPath, apiQuery } from "@/lib/api";
import { count, millis, percent } from "@/lib/format";
import {
  Button,
  DataTable,
  Field,
  Input,
  NoRows,
  Select,
  StatusDot,
  type Column,
} from "@/components";
import type { components } from "@/lib/api.d";
import { toneForConsumerHealth } from "../consumerHealth";
import { ErrorPanel } from "@/components/ErrorPanel";

type ConsumerSummary = components["schemas"]["ConsumerSummary"];
type ConsumerCreate = components["schemas"]["ConsumerCreate"];
type ConsumerUpdate = components["schemas"]["ConsumerUpdate"];

/** Creating a consumer, with the options that actually decide how it behaves.
 *
 * The push/pull choice comes first because it changes what the rest of the form
 * means: a queue group, flow control and heartbeats only exist for push, and
 * `max_waiting` only for pull. Showing all of them at once would offer
 * combinations the server refuses.
 */
function NewConsumerForm({
  streamName,
  onCreate,
  onCancel,
  pending,
  error,
}: {
  streamName: string;
  onCreate: (data: ConsumerCreate) => void;
  onCancel: () => void;
  pending: boolean;
  error: unknown;
}) {
  const [name, setName] = useState("");
  const [filter, setFilter] = useState("");
  const [push, setPush] = useState(false);
  const [deliverSubject, setDeliverSubject] = useState("");
  const [deliverGroup, setDeliverGroup] = useState("");
  const [ackPolicy, setAckPolicy] = useState<ConsumerCreate["ack_policy"]>("explicit");
  const [deliverPolicy, setDeliverPolicy] = useState<ConsumerCreate["deliver_policy"]>("all");
  const [startSeq, setStartSeq] = useState("1");
  const [startTime, setStartTime] = useState("");
  const [ackWait, setAckWait] = useState(30);
  const [maxDeliver, setMaxDeliver] = useState(-1);
  const [backoff, setBackoff] = useState("");
  const [maxAckPending, setMaxAckPending] = useState(1000);

  const backoffList = backoff
    .split(",")
    .map((x) => Number(x.trim()))
    .filter((n) => Number.isFinite(n) && n > 0);

  const needsSeq = deliverPolicy === "by_start_sequence";
  const needsTime = deliverPolicy === "by_start_time";
  const valid =
    name.trim().length > 0 &&
    (!push || deliverSubject.trim().length > 0) &&
    (!needsTime || startTime.length > 0);

  return (
    <div className="mt-5 max-w-[620px] rounded-card border border-border bg-card p-4">
      <div className="t-card-title text-foreground">New consumer on {streamName}</div>

      <div className="mt-4 flex gap-2">
        <Button size="xs" variant={push ? "ghost" : "primary"} onClick={() => setPush(false)}>
          Pull
        </Button>
        <Button size="xs" variant={push ? "primary" : "ghost"} onClick={() => setPush(true)}>
          Push
        </Button>
        <span className="self-center text-[11.5px] text-ink-faint">
          {push
            ? "The server sends to a subject; instances share the load through a queue group."
            : "Clients ask for messages; instances share the load by competing for fetches."}
        </span>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4">
        <Field label="Name">
          <Input font="mono" value={name} onChange={(e) => setName(e.target.value)} placeholder="order-picker" />
        </Field>
        <Field label="Filter subject" hint="Blank matches every subject on the stream">
          <Input font="mono" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="orders.new" />
        </Field>

        {push && (
          <>
            <Field label="Deliver subject" hint="Where the server pushes messages">
              <Input
                font="mono"
                value={deliverSubject}
                onChange={(e) => setDeliverSubject(e.target.value)}
                placeholder="deliver.orders"
              />
            </Field>
            <Field label="Queue group" hint="Optional. Several instances share the load.">
              <Input
                font="mono"
                value={deliverGroup}
                onChange={(e) => setDeliverGroup(e.target.value)}
                placeholder="workers"
              />
            </Field>
          </>
        )}

        <Field label="Ack policy">
          <Select value={ackPolicy} onChange={(e) => setAckPolicy(e.target.value as ConsumerCreate["ack_policy"])}>
            <option value="explicit">explicit</option>
            <option value="all">all</option>
            <option value="none">none</option>
          </Select>
        </Field>
        <Field label="Deliver policy">
          <Select
            value={deliverPolicy}
            onChange={(e) => setDeliverPolicy(e.target.value as ConsumerCreate["deliver_policy"])}
          >
            <option value="all">all</option>
            <option value="last">last</option>
            <option value="new">new</option>
            <option value="last_per_subject">last_per_subject</option>
            <option value="by_start_sequence">by_start_sequence</option>
            <option value="by_start_time">by_start_time</option>
          </Select>
        </Field>

        {needsSeq && (
          <Field label="Start sequence" hint="Where in the stream to begin">
            <Input font="mono" type="number" min={1} value={startSeq} onChange={(e) => setStartSeq(e.target.value)} />
          </Field>
        )}
        {needsTime && (
          <Field label="Start time" hint="Messages stored from this moment on">
            <Input type="datetime-local" value={startTime} onChange={(e) => setStartTime(e.target.value)} />
          </Field>
        )}

        <Field
          label="Ack wait (seconds)"
          hint={backoffList.length ? "Overridden by the first backoff delay" : undefined}
        >
          <Input
            font="mono"
            type="number"
            min={1}
            disabled={backoffList.length > 0}
            value={backoffList.length ? backoffList[0] : ackWait}
            onChange={(e) => setAckWait(Number(e.target.value) || 30)}
          />
        </Field>
        <Field label="Max deliveries" hint="-1 means keep retrying">
          <Input
            font="mono"
            type="number"
            value={maxDeliver}
            onChange={(e) => setMaxDeliver(Number(e.target.value) || -1)}
          />
        </Field>
        <Field
          label="Backoff (seconds)"
          hint="Comma-separated delays before each retry, e.g. 1, 5, 30"
        >
          <Input font="mono" value={backoff} onChange={(e) => setBackoff(e.target.value)} placeholder="1, 5, 30" />
        </Field>
        <Field label="Max ack pending">
          <Input
            font="mono"
            type="number"
            min={1}
            value={maxAckPending}
            onChange={(e) => setMaxAckPending(Number(e.target.value) || 1000)}
          />
        </Field>
      </div>

      {backoffList.length > 0 && (
        <p className="mt-3 text-[11.5px] leading-[1.5] text-ink-faint text-pretty">
          The server takes ack wait from the first backoff delay, so it will report{" "}
          {backoffList[0]}s regardless of the value above. Max deliveries must be at least{" "}
          {backoffList.length + 1} for every delay to be used.
        </p>
      )}
      {error !== null && <ErrorPanel className="mt-3" error={error} />}

      <div className="mt-4 flex gap-2">
        <Button
          variant="primary"
          disabled={!valid || pending}
          onClick={() =>
            onCreate({
              stream: streamName,
              name: name.trim(),
              durable: true,
              push,
              deliver_subject: push ? deliverSubject.trim() : null,
              deliver_group: push && deliverGroup.trim() ? deliverGroup.trim() : null,
              filter_subjects: filter.trim() ? [filter.trim()] : [],
              ack_policy: ackPolicy,
              deliver_policy: deliverPolicy,
              opt_start_seq: needsSeq ? Number(startSeq) : null,
              opt_start_time: needsTime ? new Date(startTime).toISOString() : null,
              ack_wait_seconds: ackWait,
              max_deliver: maxDeliver,
              backoff_seconds: backoffList,
              max_ack_pending: maxAckPending,
            })
          }
        >
          {pending ? "Creating…" : "Create consumer"}
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

export function ConsumersTab({
  serverId,
  streamName,
  onChanged,
}: {
  serverId: string;
  streamName: string;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);

  const consumersQuery = useQuery(
    apiQuery("/api/servers/{server_id}/jetstream/streams/{name}/consumers", {
      path: { server_id: serverId, name: streamName },
    }),
  );

  const invalidate = () => {
    queryClient.invalidateQueries({
      queryKey: apiPath("/api/servers/{server_id}/jetstream/streams/{name}/consumers"),
    });
    onChanged();
  };

  const createConsumer = useMutation({
    mutationFn: (data: ConsumerCreate) =>
      api.post("/api/servers/{server_id}/jetstream/streams/{name}/consumers", {
        path: { server_id: serverId, name: streamName },
        body: data,
      }),
    onSuccess: () => {
      invalidate();
      setAdding(false);
    },
  });

  const pauseConsumer = useMutation({
    mutationFn: (consumer: string) =>
      api.post(
        "/api/servers/{server_id}/jetstream/streams/{name}/consumers/{consumer}/pause",
        { path: { server_id: serverId, name: streamName, consumer }, body: {} },
      ),
    onSuccess: invalidate,
  });

  const resumeConsumer = useMutation({
    mutationFn: (consumer: string) =>
      api.post(
        "/api/servers/{server_id}/jetstream/streams/{name}/consumers/{consumer}/resume",
        { path: { server_id: serverId, name: streamName, consumer } },
      ),
    onSuccess: invalidate,
  });

  const updateConsumer = useMutation({
    mutationFn: ({ consumer, changes }: { consumer: string; changes: ConsumerUpdate }) =>
      api.patch("/api/servers/{server_id}/jetstream/streams/{name}/consumers/{consumer}", {
        path: { server_id: serverId, name: streamName, consumer },
        body: changes,
      }),
    onSuccess: () => {
      invalidate();
      setEditing(null);
    },
  });

  const deleteConsumer = useMutation({
    mutationFn: (consumer: string) =>
      api.delete("/api/servers/{server_id}/jetstream/streams/{name}/consumers/{consumer}", {
        path: { server_id: serverId, name: streamName, consumer },
      }),
    onSuccess: invalidate,
  });

  if (consumersQuery.isLoading) {
    return <p className="text-[12.5px] text-ink-subtle">Loading consumers…</p>;
  }
  if (consumersQuery.isError) {
    return <ErrorPanel error={consumersQuery.error} onRetry={() => consumersQuery.refetch()} />;
  }

  const consumers = consumersQuery.data ?? [];

  const columns: Column<ConsumerSummary>[] = [
    {
      key: "name",
      header: "Consumer",
      width: "180px",
      cell: (c) => (
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <StatusDot tone={toneForConsumerHealth(c.health)} label={c.health} />
            <span className="truncate font-mono text-[12px] text-foreground">{c.name}</span>
          </div>
          <div className="mt-1 pl-[13px] text-[11px] text-ink-dim">
            {c.push ? "push" : "pull"} · {c.durable ? "durable" : "ephemeral"} · {c.ack_policy}
          </div>
        </div>
      ),
    },
    {
      key: "filter",
      header: "Filter subject",
      width: "1fr",
      cell: (c) => (
        <span className="truncate font-mono text-[11.5px] text-ink-label">
          {c.filter_subjects.length > 0 ? c.filter_subjects.join(", ") : "(all subjects)"}
        </span>
      ),
    },
    {
      key: "ack_pending",
      header: "Ack pending",
      width: "96px",
      align: "right",
      cell: (c) => <span className="font-mono text-[12.5px] text-ink-strong">{count(c.num_ack_pending)}</span>,
    },
    {
      key: "pending",
      header: "Unprocessed",
      width: "96px",
      align: "right",
      cell: (c) => {
        const tone = toneForConsumerHealth(c.health);
        return (
          <span
            className={
              "font-mono text-[12.5px] " +
              (tone === "destructive" ? "text-destructive" : tone === "degraded" ? "text-degraded" : "text-ink-strong")
            }
          >
            {count(c.num_pending)}
          </span>
        );
      },
    },
    {
      key: "redelivered",
      header: "Redelivered",
      width: "92px",
      align: "right",
      cell: (c) => (
        <span className={"font-mono text-[12.5px] " + (c.num_redelivered > 0 ? "text-degraded" : "text-ink-dim")}>
          {count(c.num_redelivered)}
        </span>
      ),
    },
    {
      key: "lag",
      header: "Lag",
      width: "64px",
      align: "right",
      cell: (c) => (
        <span className="font-mono text-[11.5px] text-ink-label">
          {c.lag === null ? "—" : percent(c.lag, 1)}
        </span>
      ),
    },
    {
      key: "ack_wait",
      header: "Ack wait",
      width: "76px",
      align: "right",
      cell: (c) => <span className="font-mono text-[11.5px] text-ink-label">{millis(c.ack_wait_seconds * 1000)}</span>,
    },
    {
      key: "actions",
      header: "",
      width: "220px",
      align: "right",
      cell: (c) => (
        <div className="flex items-center justify-end gap-1">
          {/* Pausing keeps the consumer's position, which is what makes it the
              right response to a redelivery loop rather than deleting it. */}
          <Button
            size="xs"
            variant="ghost"
            disabled={pauseConsumer.isPending || resumeConsumer.isPending}
            onClick={(e) => {
              e.stopPropagation();
              if (c.paused) resumeConsumer.mutate(c.name);
              else pauseConsumer.mutate(c.name);
            }}
          >
            {c.paused ? "Resume" : "Pause"}
          </Button>
          <Button
            size="xs"
            variant="ghost"
            onClick={(e) => {
              e.stopPropagation();
              setEditing(editing === c.name ? null : c.name);
            }}
          >
            Edit
          </Button>
          <Button
            size="xs"
            variant="ghost"
            className="text-destructive"
            disabled={deleteConsumer.isPending}
            onClick={(e) => {
              e.stopPropagation();
              if (window.confirm(`Delete consumer ${c.name}?`)) deleteConsumer.mutate(c.name);
            }}
          >
            Delete
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div>
      {(deleteConsumer.isError || pauseConsumer.isError || resumeConsumer.isError) && (
        <ErrorPanel
          className="mb-3"
          error={deleteConsumer.error ?? pauseConsumer.error ?? resumeConsumer.error}
        />
      )}
      {consumers.length === 0 && !adding && (
        <NoRows
          title="No consumers on this stream"
          body="Nothing is reading from it yet."
          actionLabel="Add consumer"
          action={() => setAdding(true)}
        />
      )}
      {consumers.length > 0 && (
        <DataTable
          columns={columns}
          rows={consumers}
          rowKey={(c) => c.name}
          footnote="Every column here comes from jsm.consumers_info()."
        />
      )}
      {editing && (
        <ConsumerEditor
          consumer={consumers.find((c) => c.name === editing) as ConsumerSummary}
          pending={updateConsumer.isPending}
          error={updateConsumer.error}
          onSave={(changes) => updateConsumer.mutate({ consumer: editing, changes })}
          onCancel={() => setEditing(null)}
        />
      )}
      {!adding && consumers.length > 0 && (
        <div className="mt-[18px] flex items-center gap-3">
          <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
            Add consumer
          </Button>
          <span className="text-[11.5px] text-ink-faint">Every column here comes from jsm.consumers_info().</span>
        </div>
      )}
      {adding && (
        <NewConsumerForm
          streamName={streamName}
          pending={createConsumer.isPending}
          error={createConsumer.isError ? createConsumer.error : null}
          onCreate={(data) => createConsumer.mutate(data)}
          onCancel={() => setAdding(false)}
        />
      )}
    </div>
  );
}


/** Retuning a running consumer.
 *
 * Only the fields NATS accepts a change to: a consumer's ack policy, deliver
 * policy and whether it is push or pull are its identity, and re-adding it with
 * those changed is refused. Those are shown as context rather than offered.
 */
function ConsumerEditor({
  consumer,
  pending,
  error,
  onSave,
  onCancel,
}: {
  consumer: ConsumerSummary;
  pending: boolean;
  error: unknown;
  onSave: (changes: ConsumerUpdate) => void;
  onCancel: () => void;
}) {
  const [description, setDescription] = useState(consumer.description ?? "");
  const [ackWait, setAckWait] = useState(consumer.ack_wait_seconds);
  const [maxDeliver, setMaxDeliver] = useState(consumer.max_deliver);
  const [maxAckPending, setMaxAckPending] = useState(consumer.max_ack_pending ?? 1000);
  const [backoff, setBackoff] = useState((consumer.backoff_seconds ?? []).join(", "));

  const backoffList = backoff
    .split(",")
    .map((x) => Number(x.trim()))
    .filter((n) => Number.isFinite(n) && n > 0);

  return (
    <div className="mt-5 max-w-[620px] rounded-card border border-border bg-card p-4">
      <div className="flex items-baseline justify-between">
        <span className="t-card-title text-foreground">Edit {consumer.name}</span>
        <span className="text-[11.5px] text-ink-dim">
          {consumer.push ? "push" : "pull"} · {consumer.ack_policy} · {consumer.deliver_policy} ·
          fixed
        </span>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4">
        <Field label="Description" className="col-span-2">
          <Input value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field
          label="Ack wait (seconds)"
          hint={backoffList.length ? "Overridden by the first backoff delay" : undefined}
        >
          <Input
            font="mono"
            type="number"
            min={1}
            disabled={backoffList.length > 0}
            value={backoffList.length ? backoffList[0] : ackWait}
            onChange={(e) => setAckWait(Number(e.target.value) || 30)}
          />
        </Field>
        <Field label="Max deliveries" hint="-1 means keep retrying">
          <Input
            font="mono"
            type="number"
            value={maxDeliver}
            onChange={(e) => setMaxDeliver(Number(e.target.value) || -1)}
          />
        </Field>
        <Field label="Backoff (seconds)" hint="Comma-separated delays before each retry">
          <Input font="mono" value={backoff} onChange={(e) => setBackoff(e.target.value)} placeholder="1, 5, 30" />
        </Field>
        <Field label="Max ack pending">
          <Input
            font="mono"
            type="number"
            min={1}
            value={maxAckPending}
            onChange={(e) => setMaxAckPending(Number(e.target.value) || 1000)}
          />
        </Field>
      </div>

      {error !== null && <ErrorPanel className="mt-3" error={error} />}

      <div className="mt-4 flex gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={pending}
          onClick={() =>
            onSave({
              description: description || null,
              ack_wait_seconds: ackWait,
              max_deliver: maxDeliver,
              max_ack_pending: maxAckPending,
              backoff_seconds: backoffList,
            })
          }
        >
          {pending ? "Saving…" : "Save changes"}
        </Button>
        <Button variant="ghost" size="sm" disabled={pending} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
