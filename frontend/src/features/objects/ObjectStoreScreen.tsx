/** The object store: buckets, their objects, and one object's detail.
 *
 * Reference: the ObjectStore artboard. Layout matches the KV screen -- a
 * bucket list, a table, and an inspector -- because they are the same shape of
 * problem and the design draws them the same way.
 *
 * Two behaviours are worth knowing about before reading the code:
 *
 *   * An empty bucket is an empty table, not an error. nats-py raises
 *     `NotFoundError` when a bucket holds nothing; the backend turns that into
 *     `[]`, and this screen must not reintroduce the confusion.
 *   * A sealed bucket is permanently read-only, so its write actions are gone
 *     rather than present-and-failing.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiPath, apiQuery } from "@/lib/api";
import { bytes, compact, timestamp } from "@/lib/format";
import { fromJetStream } from "@/lib/sourced";
import {
  Badge,
  Button,
  buttonVariants,
  Card,
  CardBody,
  CardHeader,
  DataTable,
  ErrorPanel,
  FactRow,
  Field,
  Input,
  ListPane,
  ListRow,
  Meter,
  Mono,
  NoRows,
  Page,
  PageHeader,
  Shell,
  SourceBadge,
  Split,
  SplitMain,
  StatCard,
  Textarea,
  type Column,
} from "@/components";
import { useServerScope } from "@/lib/useServerScope";
import type { components } from "@/lib/api.d";

type ObjectInfo = components["schemas"]["ObjectInfo"];

export function ObjectStoreScreen() {
  const queryClient = useQueryClient();
  const { serverId, shellServer, isLoading: serversLoading } = useServerScope();

  const [bucketFilter, setBucketFilter] = useState("");
  const [selectedBucket, setSelectedBucket] = useState<string | null>(null);
  const [selectedObject, setSelectedObject] = useState<string | null>(null);
  const [editingMeta, setEditingMeta] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const bucketsQuery = useQuery({
    ...apiQuery("/api/servers/{server_id}/objects", {
      path: { server_id: serverId ?? "" },
    }),
    enabled: Boolean(serverId),
  });
  const buckets = useMemo(() => bucketsQuery.data ?? [], [bucketsQuery.data]);

  useEffect(() => {
    if (!bucketsQuery.data) return;
    if (selectedBucket && bucketsQuery.data.some((b) => b.name === selectedBucket)) return;
    setSelectedBucket(bucketsQuery.data[0]?.name ?? null);
    setSelectedObject(null);
  }, [bucketsQuery.data, selectedBucket]);

  const bucket = buckets.find((b) => b.name === selectedBucket) ?? null;

  const objectsQuery = useQuery({
    ...apiQuery("/api/servers/{server_id}/objects/{bucket}/objects", {
      path: { server_id: serverId ?? "", bucket: selectedBucket ?? "" },
    }),
    enabled: Boolean(serverId && selectedBucket),
  });
  const objects = useMemo(() => objectsQuery.data ?? [], [objectsQuery.data]);

  useEffect(() => {
    if (!objectsQuery.data) return;
    if (selectedObject && objectsQuery.data.some((o) => o.name === selectedObject)) return;
    setSelectedObject(objectsQuery.data[0]?.name ?? null);
  }, [objectsQuery.data, selectedObject]);

  useEffect(() => setEditingMeta(false), [selectedObject, selectedBucket]);

  const current = objects.find((o) => o.name === selectedObject) ?? null;

  const upload = useMutation({
    mutationFn: async (file: File) => {
      // Multipart, not base64: the backend streams it into JetStream chunk by
      // chunk, so a large file never sits whole in memory at either end.
      const form = new FormData();
      form.append("data", file, file.name);
      return api.post("/api/servers/{server_id}/objects/{bucket}/objects", {
        path: { server_id: serverId ?? "", bucket: selectedBucket ?? "" },
        body: form as never,
      });
    },
    onSuccess: (info) => {
      setSelectedObject(info.name);
      void queryClient.invalidateQueries({
        queryKey: apiPath("/api/servers/{server_id}/objects"),
      });
    },
  });

  const saveMeta = useMutation({
    mutationFn: async (changes: {
      description?: string | null;
      headers?: Record<string, string>;
    }) =>
      api.patch("/api/servers/{server_id}/objects/{bucket}/objects/{name}", {
        path: {
          server_id: serverId ?? "",
          bucket: selectedBucket ?? "",
          name: selectedObject ?? "",
        },
        body: changes,
      }),
    onSuccess: (info) => {
      setEditingMeta(false);
      setSelectedObject(info.name);
      void queryClient.invalidateQueries({
        queryKey: apiPath("/api/servers/{server_id}/objects"),
      });
    },
  });

  const remove = useMutation({
    mutationFn: async (name: string) =>
      api.delete("/api/servers/{server_id}/objects/{bucket}/objects/{name}", {
        path: { server_id: serverId ?? "", bucket: selectedBucket ?? "", name },
      }),
    onSuccess: () => {
      setSelectedObject(null);
      void queryClient.invalidateQueries({
        queryKey: apiPath("/api/servers/{server_id}/objects"),
      });
    },
  });

  const visibleBuckets = buckets.filter((b) =>
    b.name.toLowerCase().includes(bucketFilter.trim().toLowerCase()),
  );

  const columns: readonly Column<ObjectInfo>[] = [
    {
      key: "name",
      header: "Object",
      width: "minmax(0, 1fr)",
      cell: (o) => (
        <div className="min-w-0">
          <Mono className="block truncate text-foreground">{o.name}</Mono>
          {o.description && (
            <div className="mt-0.5 truncate text-[11px] text-ink-dim">{o.description}</div>
          )}
        </div>
      ),
    },
    {
      key: "type",
      header: "Type",
      width: "160px",
      cell: (o) => <span className="truncate text-muted-foreground">{o.content_type ?? "—"}</span>,
    },
    {
      key: "size",
      header: "Size",
      width: "92px",
      align: "right",
      cell: (o) => <Mono>{bytes(o.size)}</Mono>,
    },
    {
      key: "chunks",
      header: "Chunks",
      width: "84px",
      align: "right",
      cell: (o) => <Mono className="text-muted-foreground">{compact(o.chunks)}</Mono>,
    },
    {
      key: "modified",
      header: "Modified",
      width: "150px",
      align: "right",
      cell: (o) => <Mono className="text-ink-dim">{timestamp(o.modified)}</Mono>,
    },
  ];

  /** The backend streams the object; the browser saves it. */
  const downloadHref =
    serverId && selectedBucket && current
      ? `/api/servers/${serverId}/objects/${encodeURIComponent(selectedBucket)}/download/${encodeURIComponent(current.name)}`
      : null;

  return (
    <Shell
      crumbs={[shellServer?.name ?? "Servers", "Object store", ...(bucket ? [bucket.name] : [])]}
    >
      {/* Pinned above the split rather than inside it, so the two panes scroll
          independently and the description -- which is where this screen says
          what its numbers can mean -- does not scroll away with the object list. */}
      <div className="flex-none border-b border-hairline px-8 pb-[18px] pt-[26px]">
        <PageHeader
          title="Object store"
          description="Files kept in JetStream, split into chunks with a digest the server computes. Everything here is read over the client connection."
          actions={<SourceBadge source="jetstream" />}
        />
      </div>

      {!serverId ? (
        serversLoading ? null : (
          <Page>
            <NoRows
              title="No server selected"
              body="Register a server on the Servers screen, then come back here."
            />
          </Page>
        )
      ) : bucketsQuery.isError ? (
        <Page>
          <ErrorPanel error={bucketsQuery.error} onRetry={() => bucketsQuery.refetch()} />
        </Page>
      ) : (
        <Split>
          <ListPane
            title={`${buckets.length} buckets`}
            filter={bucketFilter}
            onFilterChange={setBucketFilter}
            placeholder="Filter buckets"
          >
            {visibleBuckets.map((b) => (
              <ListRow
                key={b.name}
                selected={b.name === selectedBucket}
                onClick={() => {
                  setSelectedBucket(b.name);
                  setSelectedObject(null);
                }}
              >
                <div className="flex items-center justify-between gap-2">
                  <Mono className="truncate text-foreground">{b.name}</Mono>
                  {b.sealed && (
                    <Badge tone="degraded" size="xs">
                      sealed
                    </Badge>
                  )}
                </div>
                <div className="mt-1 text-[11px] text-ink-dim">
                  {compact(b.objects)} objects · {bytes(b.bytes)}
                </div>
                {b.usage != null && (
                  <Meter className="mt-1.5" value={b.usage} tone="healthy" />
                )}
              </ListRow>
            ))}
            {visibleBuckets.length === 0 && (
              <div className="px-3 py-4 text-[11.5px] text-ink-dim">
                {buckets.length === 0
                  ? "This server has no object buckets."
                  : "No bucket matches that filter."}
              </div>
            )}
          </ListPane>

          <SplitMain className="min-w-0">
            {/* `Page` is what gives this column its padding and its own
                  scrollbar. Without it the content sits flush on the list's
                  border, and a table row's -mx-3 hover bleed crosses it. */}
            <Page>
              {!bucket ? (
                <NoRows
                  title="No bucket selected"
                  body="Choose a bucket from the list to see what it holds."
                />
              ) : (
                <>
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <h2 className="t-page-title truncate text-foreground">{bucket.name}</h2>
                      <p className="mt-1 text-[12px] text-muted-foreground">
                        {bucket.description ?? bucket.stream_name} · {bucket.storage} · R
                        {bucket.replicas} · chunk {bytes(bucket.max_chunk_size)}
                      </p>
                    </div>
                    <div className="flex flex-none items-center gap-2">
                      {bucket.sealed ? (
                        <Badge tone="degraded">sealed — read only, permanently</Badge>
                      ) : (
                        <>
                          <input
                            ref={fileInput}
                            type="file"
                            className="hidden"
                            onChange={(e) => {
                              const file = e.currentTarget.files?.[0];
                              if (file) upload.mutate(file);
                              // Cleared so choosing the same file twice still fires.
                              e.currentTarget.value = "";
                            }}
                          />
                          <Button
                            size="sm"
                            disabled={upload.isPending}
                            onClick={() => fileInput.current?.click()}
                          >
                            {upload.isPending ? "Uploading…" : "Upload"}
                          </Button>
                        </>
                      )}
                    </div>
                  </div>

                  <div className="mt-4 grid grid-cols-4 gap-3.5">
                    <StatCard label="Objects" sourced={fromJetStream(compact(bucket.objects))} />
                    <StatCard label="Stored" sourced={fromJetStream(bytes(bucket.bytes))} />
                    <StatCard
                      label="Replicas"
                      sourced={fromJetStream(String(bucket.replicas))}
                      sub={bucket.storage}
                    />
                    <StatCard
                      label="Chunk size"
                      sourced={fromJetStream(bytes(bucket.max_chunk_size))}
                    />
                  </div>

                  {upload.isError && <ErrorPanel className="mt-4" error={upload.error} />}

                  {objectsQuery.isError ? (
                    <ErrorPanel
                      className="mt-5"
                      error={objectsQuery.error}
                      onRetry={() => objectsQuery.refetch()}
                    />
                  ) : (
                    <div className="mt-5 grid grid-cols-[1fr_340px] gap-6">
                      <DataTable
                        columns={columns}
                        rows={objects}
                        rowKey={(o) => o.name}
                        selectedKey={selectedObject}
                        onSelect={(o) => setSelectedObject(o.name)}
                        rowHeight={56}
                        empty={
                          <NoRows
                            title="This bucket is empty"
                            body="No objects have been stored here yet. An empty bucket is normal, not a fault."
                          />
                        }
                      />

                      {current && (
                        <div className="flex flex-col gap-3.5">
                          <Card>
                            <CardBody>
                              <CardHeader title={current.name} />
                              <div className="mt-1">
                                <FactRow label="Size" value={bytes(current.size)} />
                                <FactRow label="Chunks" value={compact(current.chunks)} />
                                <FactRow
                                  label="Content type"
                                  value={current.content_type ?? "not set"}
                                />
                                <FactRow label="Modified" value={timestamp(current.modified)} />
                              </div>
                            </CardBody>
                          </Card>

                          <Card>
                            <CardBody>
                              <CardHeader
                                title="Digest"
                                description="Computed by the server as the object was written, so it verifies the bytes rather than the transfer."
                              />
                              <Mono className="mt-1 block break-all text-[11px] text-card-foreground">
                                {current.digest || "not reported"}
                              </Mono>
                            </CardBody>
                          </Card>

                          <Card>
                            <CardBody>
                              <CardHeader
                                title="Metadata"
                                description={
                                  bucket.sealed
                                    ? undefined
                                    : "Editing this rewrites only the metadata entry, never the object's bytes."
                                }
                              />
                              {editingMeta ? (
                                <MetaEditor
                                  object={current}
                                  pending={saveMeta.isPending}
                                  error={saveMeta.error}
                                  onSave={(changes) => saveMeta.mutate(changes)}
                                  onCancel={() => setEditingMeta(false)}
                                />
                              ) : (
                                <>
                                  <div className="mt-1">
                                    <FactRow
                                      label="Description"
                                      value={current.description ?? "not set"}
                                    />
                                    {Object.entries(current.headers ?? {}).map(([k, v]) => (
                                      <FactRow key={k} label={k} value={<Mono>{v}</Mono>} />
                                    ))}
                                  </div>
                                  {!bucket.sealed && (
                                    <Button
                                      size="xs"
                                      variant="outline"
                                      className="mt-3"
                                      onClick={() => setEditingMeta(true)}
                                    >
                                      Edit metadata
                                    </Button>
                                  )}
                                </>
                              )}
                            </CardBody>
                          </Card>

                          <div className="flex items-center gap-2">
                            {downloadHref && (
                              // A real link, not a fetch: the backend streams the
                              // object and the browser writes it straight to disk,
                              // so a multi-gigabyte model never lands in a JS buffer.
                              <a
                                href={downloadHref}
                                download={current.name}
                                className={buttonVariants({
                                  variant: "outline",
                                  size: "sm",
                                })}
                              >
                                Download
                              </a>
                            )}
                            {!bucket.sealed && (
                              <Button
                                variant="destructive"
                                size="sm"
                                disabled={remove.isPending}
                                onClick={() => remove.mutate(current.name)}
                              >
                                Delete
                              </Button>
                            )}
                          </div>
                          {remove.isError && <ErrorPanel error={remove.error} />}
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </Page>
          </SplitMain>
        </Split>
      )}
    </Shell>
  );
}

/** Editing an object's description and headers.
 *
 * Headers are edited as `Key: value` lines rather than a row-per-header widget:
 * they are arbitrary and usually few, and a text area is both quicker to use and
 * quicker to read than a list of paired inputs.
 */
function MetaEditor({
  object,
  pending,
  error,
  onSave,
  onCancel,
}: {
  object: ObjectInfo;
  pending: boolean;
  error: unknown;
  onSave: (changes: { description?: string | null; headers?: Record<string, string> }) => void;
  onCancel: () => void;
}) {
  const [description, setDescription] = useState(object.description ?? "");
  const [headerText, setHeaderText] = useState(
    Object.entries(object.headers ?? {})
      .map(([k, v]) => `${k}: ${v}`)
      .join("\n"),
  );

  function parseHeaders(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const line of headerText.split("\n")) {
      const at = line.indexOf(":");
      if (at <= 0) continue;
      out[line.slice(0, at).trim()] = line.slice(at + 1).trim();
    }
    return out;
  }

  return (
    <div className="mt-3 flex flex-col gap-3">
      <Field label="Description">
        <Input value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <Field label="Headers" hint="One per line, as Key: value">
        <Textarea
          rows={4}
          font="mono"
          value={headerText}
          onChange={(e) => setHeaderText(e.target.value)}
        />
      </Field>
      {error !== null && <ErrorPanel error={error} />}
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={pending}
          onClick={() =>
            onSave({
              description: description || null,
              headers: parseHeaders(),
            })
          }
        >
          {pending ? "Saving…" : "Save"}
        </Button>
        <Button size="sm" variant="ghost" disabled={pending} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
