/** Messages: read by sequence or by subject, with the decoded payload.
 *
 * `jsm.get_msg()` -- a direct get bypasses any consumer, so nothing here changes
 * ack state.
 *
 * Reading is paged, not one-shot. The limit is a page size: reaching the end of
 * the list fetches the next page from the sequence after the last row, so the
 * limit bounds each request rather than the whole session. It used to bound
 * both, which made "limit 20" mean "you may see twenty messages, ever".
 */
import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { api } from "@/lib/api";
import { timestamp } from "@/lib/format";
import { Badge, Button, Field, FactRow, Input, NoRows } from "@/components";
import type { components } from "@/lib/api.d";
import { ErrorPanel } from "@/components/ErrorPanel";
import { MessageBody } from "./MessageInspector";

type StoredMessage = components["schemas"]["StoredMessage"];

export function MessagesTab({
  serverId,
  streamName,
  lastSeq,
  initialSubject = "",
}: {
  serverId: string;
  streamName: string;
  lastSeq: number;
  /** Set when the Subjects tab sent us here. Reads from the start of the stream
   * rather than the tail, because the question that got you here was "which
   * messages are these?" and the answer begins at the first one. */
  initialSubject?: string;
}) {
  const [seq, setSeq] = useState(initialSubject ? "" : String(Math.max(1, lastSeq - 19)));
  const [subject, setSubject] = useState(initialSubject);
  const [limit, setLimit] = useState(20);
  const [selected, setSelected] = useState<number | null>(null);
  const [rows, setRows] = useState<StoredMessage[]>([]);
  const [exhausted, setExhausted] = useState(false);

  /** One page. `from` is null for the first, then the sequence after the last row.
   *
   * `direct` is always requested: the server uses it only when the stream allows
   * it, so the old toggle exposed a transport detail the operator could not act
   * on and which the backend overrode anyway. */
  const readPage = useMutation({
    mutationFn: (from: number | null) =>
      api.post("/api/servers/{server_id}/jetstream/streams/{name}/messages", {
        path: { server_id: serverId, name: streamName },
        body: {
          seq: from ?? (seq.trim() ? Number(seq) : null),
          subject: subject.trim() ? subject.trim() : null,
          direct: true,
          limit,
        },
      }),
    onSuccess: (page, from) => {
      // A page shorter than asked for is the end of the stream, not an error.
      setExhausted(page.length < limit);
      setRows((prev) => {
        if (from === null) return page;
        const seen = new Set(prev.map((m) => m.seq));
        return [...prev, ...page.filter((m) => !seen.has(m.seq))];
      });
      if (from === null) setSelected(page[0]?.seq ?? null);
    },
  });

  const restart = () => {
    setRows([]);
    setExhausted(false);
    readPage.mutate(null);
  };

  // Arriving from the Subjects tab means the question was already asked, so
  // answer it: landing on a filled-in form you still have to submit is half a
  // navigation. Mount-only -- `key` on the parent remounts this for a new
  // subject, so there is nothing to re-run on.
  const started = useRef(false);
  useEffect(() => {
    if (initialSubject && !started.current) {
      started.current = true;
      restart();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSubject]);

  const deleteMessage = useMutation({
    mutationFn: (target: number) =>
      api.delete("/api/servers/{server_id}/jetstream/streams/{name}/messages/{seq}", {
        path: { server_id: serverId, name: streamName, seq: target },
      }),
    onSuccess: (_deleted, target) => {
      setSelected((current) => (current === target ? null : current));
      restart();
    },
  });

  const messages = rows;
  const current = messages.find((m) => m.seq === selected) ?? messages[0] ?? null;

  const parentRef = useRef<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 46,
    overscan: 8,
  });
  // Read on every render, never memoised. The virtualizer re-renders this
  // component as the container scrolls, and caching the window on
  // `[virtualizer, messages.length]` -- neither of which changes while
  // scrolling -- pinned the rows to whatever was visible on the first paint.
  const items = virtualizer.getVirtualItems();

  // Reaching the last row asks for the next page. `exhausted` is what stops it
  // from asking for ever once the stream runs out.
  const lastRendered = items.at(-1)?.index ?? -1;
  useEffect(() => {
    if (exhausted || readPage.isPending || messages.length === 0) return;
    if (lastRendered >= messages.length - 1) {
      readPage.mutate((messages.at(-1)?.seq ?? 0) + 1);
    }
    // `readPage` is a stable mutation object; re-running on it would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastRendered, messages.length, exhausted, readPage.isPending]);

  return (
    <div className="flex items-start gap-6">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-end gap-3 pb-4">
          <Field label="From sequence" className="w-[140px]">
            <Input font="mono" value={seq} onChange={(e) => setSeq(e.target.value)} />
          </Field>
          <Field label="Subject filter" className="w-[220px]">
            <Input
              font="mono"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="orders.new"
            />
          </Field>
          <Field label="Limit" className="w-[90px]">
            <Input
              font="mono"
              type="number"
              min={1}
              max={500}
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value) || 20)}
            />
          </Field>
          <Button
            variant="primary"
            size="sm"
            disabled={readPage.isPending}
            onClick={restart}
          >
            {readPage.isPending ? "Reading…" : "Read"}
          </Button>
          <span className="text-[11.5px] text-ink-faint">
            jsm.get_msg() · {limit} per page, more as you scroll
          </span>
        </div>

        {readPage.isError && <ErrorPanel error={readPage.error} onRetry={restart} />}
        {deleteMessage.isError && <ErrorPanel className="mt-2" error={deleteMessage.error} />}

        {!readPage.isPending && !readPage.isError && readPage.isSuccess && messages.length === 0 && (
          <NoRows title="No messages matched" body="Nothing at that sequence or subject right now." />
        )}
        {!readPage.isSuccess && !readPage.isPending && !readPage.isError && (
          <p className="text-[12.5px] text-ink-subtle">
            Set a starting sequence or a subject, then read.
          </p>
        )}

        {messages.length > 0 && (
          <div ref={parentRef} className="scroll max-h-[520px] overflow-y-auto">
            <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
              {items.map((item) => {
                const m = messages[item.index]!;
                const on = m.seq === current?.seq;
                return (
                  <div
                    key={m.seq}
                    onClick={() => setSelected(m.seq)}
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      right: 0,
                      transform: `translateY(${item.start}px)`,
                      height: item.size,
                    }}
                    className={
                      "flex cursor-pointer items-center gap-3.5 border-b border-hairline px-2.5 " +
                      (on ? "bg-muted" : "hover:bg-row-hover")
                    }
                  >
                    <span className="w-[70px] flex-none font-mono text-[11.5px] text-ink-dim">{m.seq}</span>
                    <span className="w-[130px] flex-none font-mono text-[11px] text-ink-dim">
                      {timestamp(m.time)}
                    </span>
                    <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-foreground">
                      {m.subject}
                    </span>
                    <Badge size="xs" tone="primary">
                      {m.decoded.codec}
                    </Badge>
                    <span className="w-[70px] flex-none text-right font-mono text-[11px] text-ink-dim">
                      {m.size} B
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {current && (
        <div className="w-[380px] flex-none rounded-card border border-border bg-card p-4">
          <div className="text-[12px] font-medium text-ink-quiet">Stored message</div>
          <div className="mt-2 break-all font-mono text-[12.5px] text-foreground">{current.subject}</div>
          <div className="mt-1">
            <FactRow label="sequence" value={current.seq} />
            <FactRow label="timestamp" value={timestamp(current.time)} />
            <FactRow label="size" value={`${current.size} B`} />
            <FactRow label="headers" value={Object.keys(current.headers).length} />
          </div>
          <div className="mt-3">
            <MessageBody
              subject={current.subject}
              payloadB64={current.payload_b64}
              decoded={current.decoded}
              headers={current.headers}
            />
          </div>
          <Button
            variant="destructive"
            size="sm"
            block
            className="mt-3"
            disabled={deleteMessage.isPending}
            onClick={() => {
              if (window.confirm(`Delete message ${current.seq}? This cannot be undone.`)) {
                deleteMessage.mutate(current.seq);
              }
            }}
          >
            Delete
          </Button>
        </div>
      )}
    </div>
  );
}
