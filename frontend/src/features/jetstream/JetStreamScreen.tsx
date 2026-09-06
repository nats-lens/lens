/** The JetStream screen: a stream list plus a five-tab detail pane.
 *
 * Layout mirrors the JetStream artboard -- a 282px stream list, then the
 * detail pane's own header (name, tags, description, actions) above the tab
 * strip. Each tab is its own component; this file owns the stream list, the
 * selected stream, and the two mutations (create, purge, delete) that act on
 * the stream itself rather than on something inside one tab.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { api, apiPath, apiQuery } from "@/lib/api";
import { bytes, compact } from "@/lib/format";
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
  Tabs,
  type Tab,
} from "@/components";
import { useServerScope } from "@/lib/useServerScope";
import { ErrorPanel } from "@/components/ErrorPanel";
import { OverviewTab } from "./tabs/OverviewTab";
import { ConsumersTab } from "./tabs/ConsumersTab";
import { SubjectsTab } from "./tabs/SubjectsTab";
import { MessagesTab } from "./tabs/MessagesTab";
import { ConfigTab } from "./tabs/ConfigTab";
import type { components } from "@/lib/api.d";

type StreamCreate = components["schemas"]["StreamCreate"];
type StreamUpdate = components["schemas"]["StreamUpdate"];
type StreamDetail = components["schemas"]["StreamDetail"];
type TabId = "overview" | "consumers" | "subjects" | "messages" | "config";

const TABS: readonly Tab<TabId>[] = [
  { id: "overview", label: "Overview" },
  { id: "consumers", label: "Consumers" },
  { id: "subjects", label: "Subjects" },
  { id: "messages", label: "Messages" },
  { id: "config", label: "Configuration" },
];

function NewStreamForm({ onCreate, onCancel, pending, error }: {
  onCreate: (data: StreamCreate) => void;
  onCancel: () => void;
  pending: boolean;
  error: unknown;
}) {
  const [name, setName] = useState("");
  const [subjects, setSubjects] = useState("");
  const [storage, setStorage] = useState<StreamCreate["storage"]>("file");
  const [retention, setRetention] = useState<StreamCreate["retention"]>("limits");
  const [replicas, setReplicas] = useState(1);
  const valid = name.trim().length > 0 && subjects.trim().length > 0;

  return (
    <div className="max-w-[520px]">
      <div className="t-section">New stream</div>
      <div className="mt-5 flex flex-col gap-4">
        <Field label="Name">
          <Input font="mono" value={name} onChange={(e) => setName(e.target.value)} placeholder="ORDERS" />
        </Field>
        <Field label="Subjects" hint="Comma-separated, e.g. orders.*, orders.>">
          <Input
            font="mono"
            value={subjects}
            onChange={(e) => setSubjects(e.target.value)}
            placeholder="orders.*"
          />
        </Field>
        <div className="flex gap-4">
          <Field label="Storage" className="flex-1">
            <Select value={storage} onChange={(e) => setStorage(e.target.value as StreamCreate["storage"])}>
              <option value="file">file</option>
              <option value="memory">memory</option>
            </Select>
          </Field>
          <Field label="Retention" className="flex-1">
            <Select
              value={retention}
              onChange={(e) => setRetention(e.target.value as StreamCreate["retention"])}
            >
              <option value="limits">limits</option>
              <option value="interest">interest</option>
              <option value="workqueue">workqueue</option>
            </Select>
          </Field>
          <Field label="Replicas" className="w-[100px] flex-none">
            <Input
              font="mono"
              type="number"
              min={1}
              max={5}
              value={replicas}
              onChange={(e) => setReplicas(Number(e.target.value) || 1)}
            />
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
                subjects: subjects.split(",").map((s) => s.trim()).filter(Boolean),
                storage,
                retention,
                replicas,
                // The API's own defaults (see StreamCreate in schemas.py); the
                // form only exposes the choices a new stream actually needs.
                description: null,
                max_age_seconds: 0,
                max_msgs: -1,
                max_bytes: -1,
                max_msg_size: -1,
                duplicate_window_seconds: 120,
              })
            }
          >
            {pending ? "Creating…" : "Create stream"}
          </Button>
          <Button variant="ghost" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
        </div>
      </div>
    </div>
  );
}

export function JetStreamScreen() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { servers, serverId, shellServer, isLoading: serversLoading } = useServerScope();

  const [streamFilter, setStreamFilter] = useState("");
  const [selectedStream, setSelectedStream] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("overview");
  /** Set when a row on the Subjects tab sends us to Messages. Cleared as soon as
   * the Messages tab has taken it, so switching tabs by hand later does not
   * silently re-apply a filter the operator has moved on from. */
  const [readSubject, setReadSubject] = useState("");

  // A different stream is a different question; nothing carries over.
  useEffect(() => {
    setReadSubject("");
  }, [selectedStream]);
  const [creating, setCreating] = useState(false);
  const [editingStream, setEditingStream] = useState(false);

  const streamsQuery = useQuery({
    ...apiQuery("/api/servers/{server_id}/jetstream/streams", { path: { server_id: serverId ?? "" } }),
    enabled: !!serverId,
  });
  const streams = useMemo(() => streamsQuery.data ?? [], [streamsQuery.data]);

  useEffect(() => {
    if (!streamsQuery.data) return;
    if (selectedStream && streamsQuery.data.some((s) => s.name === selectedStream)) return;
    setSelectedStream(streamsQuery.data[0]?.name ?? null);
  }, [streamsQuery.data, selectedStream]);

  const detailQuery = useQuery({
    ...apiQuery("/api/servers/{server_id}/jetstream/streams/{name}", {
      path: { server_id: serverId ?? "", name: selectedStream ?? "" },
    }),
    enabled: !!serverId && !!selectedStream,
  });

  const invalidateStreams = () =>
    queryClient.invalidateQueries({ queryKey: apiPath("/api/servers/{server_id}/jetstream/streams") });
  const invalidateDetail = () =>
    queryClient.invalidateQueries({
      queryKey: apiPath("/api/servers/{server_id}/jetstream/streams/{name}"),
    });

  const createStream = useMutation({
    mutationFn: (data: StreamCreate) =>
      api.post("/api/servers/{server_id}/jetstream/streams", { path: { server_id: serverId! }, body: data }),
    onSuccess: (created) => {
      invalidateStreams();
      setSelectedStream(created.name);
      setCreating(false);
    },
  });

  const updateStream = useMutation({
    mutationFn: async (changes: StreamUpdate) =>
      api.patch("/api/servers/{server_id}/jetstream/streams/{name}", {
        path: { server_id: serverId ?? "", name: selectedStream ?? "" },
        body: changes,
      }),
    onSuccess: () => {
      setEditingStream(false);
      void queryClient.invalidateQueries({
        queryKey: apiPath("/api/servers/{server_id}/jetstream/streams"),
      });
    },
  });

  const purgeStream = useMutation({
    mutationFn: () =>
      api.post("/api/servers/{server_id}/jetstream/streams/{name}/purge", {
        path: { server_id: serverId!, name: selectedStream! },
        body: {},
      }),
    onSuccess: () => {
      invalidateDetail();
      invalidateStreams();
    },
  });

  const deleteStream = useMutation({
    mutationFn: () =>
      api.delete("/api/servers/{server_id}/jetstream/streams/{name}", {
        path: { server_id: serverId!, name: selectedStream! },
      }),
    onSuccess: () => {
      setSelectedStream(null);
      invalidateStreams();
    },
  });

  if (serversLoading) {
    return (
      <Shell crumbs={["JetStream"]}>
        <Page>
          <PageHeader title="JetStream" description="Loading registered servers…" />
        </Page>
      </Shell>
    );
  }

  if (servers.length === 0) {
    return (
      <Shell crumbs={["JetStream"]}>
        <Page>
          <PageHeader
            title="JetStream"
            description="Streams, consumers, per-subject counts and stored messages, read through the JetStream API a plain client can reach."
          />
          <div className="mt-6 max-w-[420px]">
            <NoRows
              title="No servers registered"
              body="Add a server before there is anything for JetStream to show."
              actionLabel="Add a server"
              action={() => navigate({ to: "/servers/new" })}
            />
          </div>
        </Page>
      </Shell>
    );
  }

  const filtered = streams.filter(
    (s) =>
      streamFilter.trim() === "" ||
      s.name.toLowerCase().includes(streamFilter.toLowerCase()) ||
      s.subjects.some((subj) => subj.toLowerCase().includes(streamFilter.toLowerCase())),
  );

  const detail = detailQuery.data;

  return (
    <Shell
      crumbs={[shellServer?.name ?? "Server", "JetStream"]}
    >
        <Split>
          <ListPane
            title="Streams"
            width={282}
            filter={streamFilter}
            onFilterChange={setStreamFilter}
            placeholder="Filter streams"
            onAdd={() => {
              setCreating(true);
              setSelectedStream(null);
            }}
            addLabel="New stream"
          >
            {streamsQuery.isLoading && (
              <div className="px-[18px] py-3 text-[11.5px] text-ink-faint">Loading streams…</div>
            )}
            {streamsQuery.isError && (
              <div className="px-[18px] py-3">
                <ErrorPanel error={streamsQuery.error} onRetry={() => streamsQuery.refetch()} />
              </div>
            )}
            {filtered.map((s) => (
              <ListRow
                key={s.name}
                selected={!creating && selectedStream === s.name}
                onClick={() => {
                  setCreating(false);
                  setSelectedStream(s.name);
                }}
              >
                <div className="flex items-center gap-2">
                  <Mono size="lg" className="font-medium text-foreground">
                    {s.name}
                  </Mono>
                  <span className="flex-1" />
                  <Badge size="xs">{s.storage}</Badge>
                </div>
                <Mono size="sm" truncate className="mt-1.5 text-ink-subtle">
                  {s.subjects.join(", ")}
                </Mono>
                {s.usage !== null && <Meter className="mt-2" value={s.usage} caption={null} />}
                <div className="mt-1.5 flex justify-between font-mono text-[10.5px] text-ink-dim">
                  <span>{compact(s.state.messages)} msgs</span>
                  <span>{bytes(s.state.bytes)}</span>
                </div>
              </ListRow>
            ))}
            {!streamsQuery.isLoading && !streamsQuery.isError && filtered.length === 0 && (
              <div className="px-[18px] py-3 text-[11.5px] text-ink-faint">
                {streams.length === 0 ? "No streams on this account." : `Nothing matches “${streamFilter}”.`}
              </div>
            )}
          </ListPane>

          <SplitMain className="min-w-0">
            {creating ? (
              <Page>
                <NewStreamForm
                  pending={createStream.isPending}
                  error={createStream.isError ? createStream.error : null}
                  onCreate={(data) => createStream.mutate(data)}
                  onCancel={() => setCreating(false)}
                />
              </Page>
            ) : !selectedStream ? (
              <Page>
                <NoRows title="No stream selected" body="Choose a stream from the list, or create one." />
              </Page>
            ) : detailQuery.isLoading ? (
              <Page>
                <div className="text-[12.5px] text-ink-subtle">Loading {selectedStream}…</div>
              </Page>
            ) : detailQuery.isError ? (
              <Page>
                <ErrorPanel error={detailQuery.error} onRetry={() => detailQuery.refetch()} />
              </Page>
            ) : detail ? (
              <>
                <div className="flex-none px-7 pt-[22px]">
                  <div className="flex items-start justify-between gap-5">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2.5">
                        <Mono size="xl" className="text-[22px] font-medium tracking-[-0.02em] text-foreground">
                          {detail.name}
                        </Mono>
                        <Badge size="xs">{detail.storage}</Badge>
                        <Badge size="xs">R{detail.replicas}</Badge>
                        <Badge size="xs">{detail.retention}</Badge>
                        {detail.sealed && <Badge size="xs" tone="degraded">sealed</Badge>}
                      </div>
                      {detail.description && (
                        <div className="mt-2 text-[12.5px] text-ink-label">{detail.description}</div>
                      )}
                    </div>
                    <div className="flex flex-none items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setEditingStream((v) => !v)}
                      >
                        {editingStream ? "Cancel edit" : "Edit"}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={purgeStream.isPending}
                        onClick={() => {
                          if (window.confirm(`Purge all messages from ${detail.name}? This cannot be undone.`)) {
                            purgeStream.mutate();
                          }
                        }}
                      >
                        Purge
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        disabled={deleteStream.isPending}
                        onClick={() => {
                          if (window.confirm(`Delete stream ${detail.name} and every message in it?`)) {
                            deleteStream.mutate();
                          }
                        }}
                      >
                        Delete
                      </Button>
                    </div>
                  </div>
                  {(purgeStream.isError || deleteStream.isError) && (
                    <ErrorPanel className="mt-3" error={purgeStream.error ?? deleteStream.error} />
                  )}
                  {editingStream && (
                    <StreamEditor
                      stream={detail}
                      pending={updateStream.isPending}
                      error={updateStream.error}
                      onSave={(changes) => updateStream.mutate(changes)}
                      onCancel={() => setEditingStream(false)}
                    />
                  )}
                  <div className="mt-[18px] border-b border-border pb-[18px]">
                    <Tabs
                      label="Stream detail"
                      value={tab}
                      onChange={(next) => {
                        // Picking a tab by hand drops any subject the Subjects
                        // tab handed over -- otherwise coming back to Messages
                        // later silently re-applies a filter you had left.
                        setReadSubject("");
                        setTab(next);
                      }}
                      tabs={TABS}
                    />
                  </div>
                </div>
                <div className="scroll min-h-0 flex-1 overflow-y-auto px-7 py-[22px]">
                  {tab === "overview" && <OverviewTab stream={detail} />}
                  {tab === "consumers" && (
                    <ConsumersTab serverId={serverId!} streamName={detail.name} onChanged={invalidateDetail} />
                  )}
                  {tab === "subjects" && (
                    <SubjectsTab
                      serverId={serverId!}
                      streamName={detail.name}
                      onReadSubject={(subject) => {
                        setReadSubject(subject);
                        setTab("messages");
                      }}
                    />
                  )}
                  {tab === "messages" && (
                    <MessagesTab
                      // Remounts when the subject changes, which is what makes
                      // the incoming filter the starting state rather than
                      // something to reconcile against a form already in use.
                      key={readSubject || "messages"}
                      serverId={serverId!}
                      streamName={detail.name}
                      lastSeq={detail.state.last_seq}
                      initialSubject={readSubject}
                    />
                  )}
                  {tab === "config" && <ConfigTab stream={detail} />}
                </div>
              </>
            ) : null}
          </SplitMain>
        </Split>
    </Shell>
  );
}

/** Editing a live stream.
 *
 * Only the fields NATS will actually accept a change to. A stream's name,
 * storage backend and retention policy are fixed at creation -- changing them
 * would mean a different stream holding the same messages -- so they are shown
 * as facts rather than offered as inputs, and the API does not accept them either.
 *
 * Everything left blank is left alone: the request omits it, and the backend
 * carries the current value forward rather than resetting it to a default.
 */
function StreamEditor({
  stream,
  pending,
  error,
  onSave,
  onCancel,
}: {
  stream: StreamDetail;
  pending: boolean;
  error: unknown;
  onSave: (changes: StreamUpdate) => void;
  onCancel: () => void;
}) {
  const [subjects, setSubjects] = useState(stream.subjects.join(", "));
  const [description, setDescription] = useState(stream.description ?? "");
  const [maxAge, setMaxAge] = useState(String(stream.limits.max_age_seconds ?? 0));
  const [maxMsgs, setMaxMsgs] = useState(String(stream.limits.max_msgs ?? -1));
  const [maxBytes, setMaxBytes] = useState(String(stream.limits.max_bytes ?? -1));
  const [replicas, setReplicas] = useState(stream.replicas);
  const [discard, setDiscard] = useState(stream.limits.discard);

  return (
    <div className="mt-4 rounded-card border border-border p-4">
      <div className="flex items-baseline justify-between">
        <span className="t-card-title text-foreground">Edit {stream.name}</span>
        <span className="text-[11.5px] text-ink-dim">
          {stream.storage} · {stream.retention} · fixed at creation
        </span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <Field label="Subjects" hint="Comma-separated" className="col-span-2">
          <Input font="mono" value={subjects} onChange={(e) => setSubjects(e.target.value)} />
        </Field>
        <Field label="Description" className="col-span-2">
          <Input value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label="Max age (seconds)" hint="0 means no age limit">
          <Input font="mono" type="number" value={maxAge} onChange={(e) => setMaxAge(e.target.value)} />
        </Field>
        <Field label="Max messages" hint="-1 means unlimited">
          <Input font="mono" type="number" value={maxMsgs} onChange={(e) => setMaxMsgs(e.target.value)} />
        </Field>
        <Field label="Max bytes" hint="-1 means unlimited">
          <Input font="mono" type="number" value={maxBytes} onChange={(e) => setMaxBytes(e.target.value)} />
        </Field>
        <div className="flex gap-3">
          <Field label="Replicas" className="w-[100px] flex-none">
            <Input
              font="mono"
              type="number"
              min={1}
              max={5}
              value={replicas}
              onChange={(e) => setReplicas(Number(e.target.value) || 1)}
            />
          </Field>
          <Field label="Discard" className="flex-1">
            <Select
              value={discard}
              onChange={(e) => setDiscard(e.target.value as StreamDetail["limits"]["discard"])}
            >
              <option value="old">old</option>
              <option value="new">new</option>
            </Select>
          </Field>
        </div>
      </div>

      {error !== null && <ErrorPanel className="mt-3" error={error} />}

      <div className="mt-4 flex gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={pending}
          onClick={() =>
            onSave({
              subjects: subjects.split(",").map((x) => x.trim()).filter(Boolean),
              description: description || null,
              max_age_seconds: Number(maxAge),
              max_msgs: Number(maxMsgs),
              max_bytes: Number(maxBytes),
              replicas,
              discard,
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
