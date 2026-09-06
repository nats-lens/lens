/** The Key-Value screen: bucket list, key table, and a Value/History inspector.
 *
 * Watching is real: it creates a subject subscription over HTTP for
 * `$KV.<bucket>.>` (the same way Core creates any subscription) and joins the
 * websocket channel it gets back. The backend recognises that subject shape
 * and pushes `kv` frames rather than generic `msg` frames -- this screen only
 * has to react to one arriving by refreshing the key list.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { api, apiPath, apiQuery } from "@/lib/api";
import { ws } from "@/lib/ws";
import { bytes, count, duration, since } from "@/lib/format";
import {
  Badge,
  Button,
  Field,
  Input,
  ListPane,
  ListRow,
  Meter,
  Mono,
  NoRows,
  Page,
  PageHeader,
  Select,
  Shell,
  Split,
  SplitMain,
} from "@/components";
import type { components } from "@/lib/api.d";
import { useServerScope } from "@/lib/useServerScope";
import { useDebounced } from "./useDebounced";
import { ErrorPanel } from "@/components/ErrorPanel";
import { KeyInspector } from "./KeyInspector";

type BucketCreate = components["schemas"]["BucketCreate"];
type KvKeyRow = components["schemas"]["KvKeyRow"];

function opTone(op: KvKeyRow["operation"]): "healthy" | "degraded" | "destructive" {
  if (op === "PUT") return "healthy";
  if (op === "DEL") return "degraded";
  return "destructive";
}

function NewBucketForm({
  onCreate,
  onCancel,
  pending,
  error,
}: {
  onCreate: (data: BucketCreate) => void;
  onCancel: () => void;
  pending: boolean;
  error: unknown;
}) {
  const [name, setName] = useState("");
  const [history, setHistory] = useState(1);
  const [storage, setStorage] = useState<BucketCreate["storage"]>("file");
  const valid = name.trim().length > 0;

  return (
    <div className="max-w-[480px]">
      <div className="t-section">New bucket</div>
      <div className="mt-5 flex flex-col gap-4">
        <Field label="Name">
          <Input font="mono" value={name} onChange={(e) => setName(e.target.value)} placeholder="CONFIG" />
        </Field>
        <div className="flex gap-4">
          <Field label="History" className="flex-1">
            <Input
              font="mono"
              type="number"
              min={1}
              max={64}
              value={history}
              onChange={(e) => setHistory(Number(e.target.value) || 1)}
            />
          </Field>
          <Field label="Storage" className="flex-1">
            <Select value={storage} onChange={(e) => setStorage(e.target.value as BucketCreate["storage"])}>
              <option value="file">file</option>
              <option value="memory">memory</option>
            </Select>
          </Field>
        </div>
        {error !== null && <ErrorPanel error={error} />}
        <div className="flex gap-2">
          <Button
            variant="primary"
            disabled={!valid || pending}
            onClick={() =>
              onCreate({
                name: name.trim(),
                history,
                storage,
                replicas: 1,
                max_value_size: -1,
                max_bytes: -1,
                ttl_seconds: null,
              })
            }
          >
            {pending ? "Creating…" : "Create bucket"}
          </Button>
          <Button variant="ghost" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
        </div>
      </div>
    </div>
  );
}

function NewKeyForm({
  onCreate,
  onCancel,
  pending,
  error,
}: {
  onCreate: (key: string, value: string) => void;
  onCancel: () => void;
  pending: boolean;
  error: unknown;
}) {
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const valid = key.trim().length > 0;

  return (
    <div className="mb-4 rounded-card border border-border bg-card p-4">
      <div className="t-card-title text-foreground">New key</div>
      <div className="mt-3 flex flex-col gap-3">
        <Field label="Key">
          <Input font="mono" value={key} onChange={(e) => setKey(e.target.value)} placeholder="tenant.acme.limits" />
        </Field>
        <Field label="Value">
          <textarea
            value={value}
            onChange={(e) => setValue(e.target.value)}
            rows={4}
            className="w-full rounded-control border border-border bg-card px-[11px] py-[9px] font-mono text-[12px] leading-[1.6] text-foreground focus-visible:border-primary focus-visible:outline-none"
          />
        </Field>
        {error !== null && <ErrorPanel error={error} />}
        <div className="flex gap-2">
          <Button variant="primary" disabled={!valid || pending} onClick={() => onCreate(key.trim(), value)}>
            {pending ? "Saving…" : "Put"}
          </Button>
          <Button variant="ghost" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
        </div>
      </div>
    </div>
  );
}

export function KeyValueScreen() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { servers, serverId, shellServer, isLoading: serversLoading } = useServerScope();

  const [bucketFilter, setBucketFilter] = useState("");
  const [selectedBucket, setSelectedBucket] = useState<string | null>(null);
  const [creatingBucket, setCreatingBucket] = useState(false);
  const [keyFilter, setKeyFilter] = useState("");
  const debouncedKeyFilter = useDebounced(keyFilter);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [creatingKey, setCreatingKey] = useState(false);
  const [watching, setWatching] = useState(false);
  const [watchError, setWatchError] = useState<string | null>(null);

  const bucketsQuery = useQuery({
    ...apiQuery("/api/servers/{server_id}/kv", { path: { server_id: serverId ?? "" } }),
    enabled: !!serverId,
  });
  const buckets = useMemo(() => bucketsQuery.data ?? [], [bucketsQuery.data]);

  useEffect(() => {
    if (!bucketsQuery.data) return;
    if (selectedBucket && bucketsQuery.data.some((b) => b.name === selectedBucket)) return;
    setSelectedBucket(bucketsQuery.data[0]?.name ?? null);
    setSelectedKey(null);
  }, [bucketsQuery.data, selectedBucket]);

  const bucket = buckets.find((b) => b.name === selectedBucket) ?? null;

  const keysQuery = useQuery({
    ...apiQuery("/api/servers/{server_id}/kv/{bucket}/keys", {
      path: { server_id: serverId ?? "", bucket: selectedBucket ?? "" },
      query: { filter: debouncedKeyFilter.trim() || null, limit: 500 },
    }),
    enabled: !!serverId && !!selectedBucket,
  });

  useEffect(() => {
    if (!keysQuery.data) return;
    if (selectedKey && keysQuery.data.keys.some((k) => k.key === selectedKey)) return;
    setSelectedKey(keysQuery.data.keys[0]?.key ?? null);
  }, [keysQuery.data, selectedKey]);

  // Watching: a real subject subscription for this bucket's internal stream,
  // torn down whenever the bucket changes, the toggle turns off, or the
  // screen unmounts -- never left dangling on the server.
  useEffect(() => {
    if (!watching || !selectedBucket || !serverId) return;
    let cancelled = false;
    let leave: (() => void) | null = null;
    let subId: string | null = null;
    const server = serverId;

    void (async () => {
      try {
        const sub = await api.post("/api/servers/{server_id}/core/subscriptions", {
          path: { server_id: server },
          body: { subject: `$KV.${selectedBucket}.>`, rate_cap: 200 },
        });
        if (cancelled) {
          void api.delete("/api/servers/{server_id}/core/subscriptions/{sub_id}", {
            path: { server_id: server, sub_id: sub.id },
          });
          return;
        }
        subId = sub.id;
        leave = ws.join(sub.channel, {
          onKv: () => {
            void queryClient.invalidateQueries({
              queryKey: apiPath("/api/servers/{server_id}/kv/{bucket}/keys"),
            });
          },
        });
        setWatchError(null);
      } catch (err) {
        if (!cancelled) {
          setWatchError(err instanceof Error ? err.message : "Could not start watching.");
          setWatching(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      leave?.();
      if (subId) {
        void api.delete("/api/servers/{server_id}/core/subscriptions/{sub_id}", {
          path: { server_id: server, sub_id: subId },
        });
      }
    };
  }, [watching, selectedBucket, serverId, queryClient]);

  const createBucket = useMutation({
    mutationFn: (data: BucketCreate) =>
      api.post("/api/servers/{server_id}/kv", { path: { server_id: serverId! }, body: data }),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: apiPath("/api/servers/{server_id}/kv") });
      setSelectedBucket(created.name);
      setCreatingBucket(false);
    },
  });

  const putKey = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      api.put("/api/servers/{server_id}/kv/{bucket}/keys/{key}", {
        path: { server_id: serverId!, bucket: selectedBucket!, key },
        body: { value_b64: btoa(unescape(encodeURIComponent(value))) },
      }),
    onSuccess: (_entry, vars) => {
      void keysQuery.refetch();
      setSelectedKey(vars.key);
      setCreatingKey(false);
    },
  });

  const parentRef = useRef<HTMLDivElement | null>(null);
  const keyRows = keysQuery.data?.keys ?? [];
  const virtualizer = useVirtualizer({
    count: keyRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 40,
    overscan: 10,
  });
  const virtualItems = virtualizer.getVirtualItems();

  if (serversLoading) {
    return (
      <Shell crumbs={["Key–Value"]}>
        <Page>
          <PageHeader title="Key–Value" description="Loading registered servers…" />
        </Page>
      </Shell>
    );
  }

  if (servers.length === 0) {
    return (
      <Shell crumbs={["Key–Value"]}>
        <Page>
          <PageHeader
            title="Key–Value"
            description="Buckets are JetStream streams under the hood; every figure here is jetstream provenance."
          />
          <div className="mt-6 max-w-[420px]">
            <NoRows
              title="No servers registered"
              body="Add a server before there is anything for Key-Value to show."
              actionLabel="Add a server"
              action={() => navigate({ to: "/servers/new" })}
            />
          </div>
        </Page>
      </Shell>
    );
  }

  const filteredBuckets = buckets.filter(
    (b) => bucketFilter.trim() === "" || b.name.toLowerCase().includes(bucketFilter.toLowerCase()),
  );

  return (
    <Shell crumbs={[shellServer?.name ?? "Server", "Key–Value"]}>
        <Split>
          <ListPane
            title="Buckets"
            width={262}
            filter={bucketFilter}
            onFilterChange={setBucketFilter}
            placeholder="Filter buckets"
            onAdd={() => {
              setCreatingBucket(true);
              setSelectedBucket(null);
            }}
            addLabel="New bucket"
          >
            {bucketsQuery.isLoading && (
              <div className="px-[18px] py-3 text-[11.5px] text-ink-faint">Loading buckets…</div>
            )}
            {bucketsQuery.isError && (
              <div className="px-[18px] py-3">
                <ErrorPanel error={bucketsQuery.error} onRetry={() => bucketsQuery.refetch()} />
              </div>
            )}
            {filteredBuckets.map((b) => (
              <ListRow
                key={b.name}
                selected={!creatingBucket && selectedBucket === b.name}
                onClick={() => {
                  setCreatingBucket(false);
                  setSelectedBucket(b.name);
                  setSelectedKey(null);
                  setWatching(false);
                }}
              >
                <div className="flex items-center gap-2">
                  <Mono size="lg" className="font-medium text-foreground">
                    {b.name}
                  </Mono>
                  <span className="flex-1" />
                  <Badge size="xs">{b.storage}</Badge>
                </div>
                <div className="mt-1.5 text-[11px] text-ink-dim">
                  {count(b.values)} keys · history {b.history} ·{" "}
                  {b.ttl_seconds ? `TTL ${duration(b.ttl_seconds)}` : "no TTL"}
                </div>
                {b.usage !== null && <Meter className="mt-2" value={b.usage} caption={null} />}
                <div className="mt-1.5 flex justify-between font-mono text-[10.5px] text-ink-dim">
                  <span>{count(b.values)} keys</span>
                  <span>{bytes(b.bytes)}</span>
                </div>
              </ListRow>
            ))}
            {!bucketsQuery.isLoading && !bucketsQuery.isError && filteredBuckets.length === 0 && (
              <div className="px-[18px] py-3 text-[11.5px] text-ink-faint">
                {buckets.length === 0 ? "No buckets on this account." : `Nothing matches “${bucketFilter}”.`}
              </div>
            )}
          </ListPane>

          <SplitMain className="min-w-0">
            {creatingBucket ? (
              <Page>
                <NewBucketForm
                  pending={createBucket.isPending}
                  error={createBucket.isError ? createBucket.error : null}
                  onCreate={(data) => createBucket.mutate(data)}
                  onCancel={() => setCreatingBucket(false)}
                />
              </Page>
            ) : !bucket ? (
              <Page>
                <NoRows title="No bucket selected" body="Choose a bucket from the list, or create one." />
              </Page>
            ) : (
              <div className="flex min-h-0 flex-1">
                <div className="flex min-w-0 flex-1 flex-col">
                  <div className="flex-none border-b border-hairline px-[26px] pb-[14px] pt-5">
                    <div className="flex items-start justify-between gap-5">
                      <div>
                        <div className="flex items-center gap-2.5">
                          <Mono className="text-[20px] font-medium tracking-[-0.02em] text-foreground">
                            {bucket.name}
                          </Mono>
                          <Badge size="xs">jetstream</Badge>
                        </div>
                        <div className="mt-1.5 text-[12px] text-ink-label">
                          {bucket.stream_name} · R{bucket.replicas} · history {bucket.history}
                          {bucket.ttl_seconds ? ` · TTL ${duration(bucket.ttl_seconds)}` : ""} · max value{" "}
                          {bucket.max_value_size > 0 ? bytes(bucket.max_value_size) : "unlimited"}
                        </div>
                      </div>
                      <div className="flex flex-none items-center gap-2">
                        <button
                          type="button"
                          onClick={() => setWatching((w) => !w)}
                          className={
                            "flex h-8 items-center gap-2 rounded-control border px-2.5 " +
                            (watching ? "border-healthy-border" : "border-border")
                          }
                        >
                          <span
                            className={"size-1.5 rounded-full " + (watching ? "bg-healthy" : "bg-idle")}
                          />
                          <span className="text-[12.5px] text-foreground">
                            {watching ? "Watching" : "Watch off"}
                          </span>
                        </button>
                        <Button variant="primary" size="sm" onClick={() => setCreatingKey(true)}>
                          New key
                        </Button>
                      </div>
                    </div>
                    {watchError && (
                      <p className="mt-2 text-[11.5px] text-degraded">
                        Could not start watching: {watchError}
                      </p>
                    )}
                    <div className="mt-3.5 flex items-center gap-3">
                      <Input
                        font="mono"
                        className="max-w-[360px]"
                        value={keyFilter}
                        onChange={(e) => setKeyFilter(e.target.value)}
                        placeholder="key filter, e.g. tenant.*.limits"
                      />
                      {keysQuery.data && (
                        <span className="text-[11.5px] text-ink-subtle text-pretty">
                          {keysQuery.data.note}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="min-h-0 flex-1 overflow-y-auto px-[26px] py-3">
                    {creatingKey && (
                      <NewKeyForm
                        pending={putKey.isPending}
                        error={putKey.isError ? putKey.error : null}
                        onCreate={(key, value) => putKey.mutate({ key, value })}
                        onCancel={() => setCreatingKey(false)}
                      />
                    )}
                    {keysQuery.isLoading && <p className="text-[12.5px] text-ink-subtle">Loading keys…</p>}
                    {keysQuery.isError && (
                      <ErrorPanel error={keysQuery.error} onRetry={() => keysQuery.refetch()} />
                    )}
                    {keysQuery.data && keyRows.length === 0 && (
                      <NoRows title="No keys" body="This bucket has no keys, or none match the filter." />
                    )}
                    {keyRows.length > 0 && (
                      <>
                        <div className="grid grid-cols-[1fr_78px_68px_108px_66px] gap-3.5 border-b border-border px-2.5 pb-[9px] text-[11.5px] font-medium text-ink-subtle">
                          <div>Key</div>
                          <div className="text-right">Revision</div>
                          <div className="text-right">Size</div>
                          <div className="text-right">Updated</div>
                          <div className="text-right">Op</div>
                        </div>
                        <div ref={parentRef} className="scroll max-h-[calc(100vh-380px)] overflow-y-auto">
                          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
                            {virtualItems.map((item) => {
                              const row = keyRows[item.index]!;
                              const on = row.key === selectedKey;
                              return (
                                <div
                                  key={row.key}
                                  onClick={() => setSelectedKey(row.key)}
                                  style={{
                                    position: "absolute",
                                    top: 0,
                                    left: 0,
                                    right: 0,
                                    transform: `translateY(${item.start}px)`,
                                    height: item.size,
                                  }}
                                  className={
                                    "grid cursor-pointer grid-cols-[1fr_78px_68px_108px_66px] items-center gap-3.5 px-2.5 " +
                                    (on ? "bg-muted" : "hover:bg-row-hover")
                                  }
                                >
                                  <span className="truncate font-mono text-[12px] text-foreground">{row.key}</span>
                                  <span className="text-right font-mono text-[11.5px] text-ink-label">
                                    r{row.revision}
                                  </span>
                                  <span className="text-right font-mono text-[11.5px] text-ink-dim">
                                    {bytes(row.size)}
                                  </span>
                                  <span className="text-right font-mono text-[11.5px] text-ink-dim">
                                    {since(row.created)}
                                  </span>
                                  <div className="text-right">
                                    <Badge size="xs" tone={opTone(row.operation)}>
                                      {row.operation}
                                    </Badge>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                </div>

                <aside className="scroll w-[384px] flex-none overflow-y-auto border-l border-hairline bg-sidebar px-[22px] py-5">
                  {selectedKey ? (
                    <KeyInspector
                      serverId={serverId!}
                      bucket={bucket.name}
                      historyDepth={bucket.history}
                      keyName={selectedKey}
                      onChanged={() => void keysQuery.refetch()}
                    />
                  ) : (
                    <p className="text-[12.5px] text-ink-subtle">Choose a key to see its value.</p>
                  )}
                </aside>
              </div>
            )}
          </SplitMain>
        </Split>
    </Shell>
  );
}
