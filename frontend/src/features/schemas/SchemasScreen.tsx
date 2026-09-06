/** Protobuf descriptors, and the subject rules that pick between them.
 *
 * Reference: the Schemas artboard.
 *
 * This screen is the reason the Core inspector can name a type at all. Two
 * things on it are worth reading carefully:
 *
 *   * Rule order is *computed*, not authored. Specificity decides -- `orders.new`
 *     beats `orders.*` beats `orders.>` -- and `precedence` only breaks ties. So
 *     the list is shown in resolution order rather than insertion order, because
 *     insertion order is not what the decoder uses.
 *   * Unmapped subjects are observations, not records. They are what nats-lens
 *     watched fall through to raw wire format, which is why they carry the
 *     `sampled` badge and reset when the process does.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiPath, apiQuery } from "@/lib/api";
import { cn } from "@/lib/cn";
import { bytes as fmtBytes, compact, since, toBase64 } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  DataTable,
  ErrorPanel,
  Field,
  Input,
  ListPane,
  ListRow,
  Mono,
  NoRows,
  Page,
  PageHeader,
  Select,
  Shell,
  SourceBadge,
  Split,
  SplitMain,
  Tabs,
  type Column,
  type Tab,
} from "@/components";
import type { components } from "@/lib/api.d";

type TabId = "types" | "rules" | "unmapped";
type SubjectRuleOut = components["schemas"]["SubjectRuleOut"];
type TypeSummary = components["schemas"]["TypeSummary"];
type UnmappedSubject = components["schemas"]["UnmappedSubject"];

const TABS: readonly Tab<TabId>[] = [
  { id: "types", label: "Message types" },
  { id: "rules", label: "Subject rules" },
  { id: "unmapped", label: "Unmapped subjects" },
];

export function SchemasScreen() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<TabId>("types");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [newPattern, setNewPattern] = useState("");
  const [newType, setNewType] = useState("");
  const [uploading, setUploading] = useState(false);

  const rescan = useMutation({
    mutationFn: async () => api.post("/api/schemas/scan"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: apiPath("/api/schemas/descriptors") });
      void queryClient.invalidateQueries({ queryKey: apiPath("/api/schemas/types") });
    },
  });

  const descriptors = useQuery(apiQuery("/api/schemas/descriptors"));
  const rules = useQuery(apiQuery("/api/schemas/rules"));
  const unmapped = useQuery(apiQuery("/api/schemas/unmapped"));
  const order = useQuery(apiQuery("/api/schemas/resolution-order"));

  const list = useMemo(() => descriptors.data ?? [], [descriptors.data]);
  const selected = list.find((d) => d.id === selectedId) ?? list[0] ?? null;

  const detail = useQuery({
    ...apiQuery("/api/schemas/descriptors/{descriptor_id}", {
      path: { descriptor_id: selected?.id ?? "" },
    }),
    enabled: Boolean(selected),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: apiPath("/api/schemas/rules") });
    void queryClient.invalidateQueries({ queryKey: apiPath("/api/schemas/unmapped") });
  };

  const addRule = useMutation({
    mutationFn: async () =>
      api.post("/api/schemas/rules", {
        body: {
          pattern: newPattern.trim(),
          type_full_name: newType.trim(),
          server_id: null,
          precedence: 0,
          enabled: true,
        },
      }),
    onSuccess: () => {
      setNewPattern("");
      setNewType("");
      invalidate();
    },
  });

  const deleteRule = useMutation({
    mutationFn: async (id: string) =>
      api.delete("/api/schemas/rules/{rule_id}", { path: { rule_id: id } }),
    onSuccess: invalidate,
  });

  const deleteDescriptor = useMutation({
    mutationFn: async (id: string) =>
      api.delete("/api/schemas/descriptors/{descriptor_id}", { path: { descriptor_id: id } }),
    onSuccess: () => {
      setSelectedId(null);
      void queryClient.invalidateQueries({ queryKey: apiPath("/api/schemas/descriptors") });
      invalidate();
    },
  });

  const visible = list.filter((d) => d.package.toLowerCase().includes(filter.trim().toLowerCase()));

  const typeColumns: readonly Column<TypeSummary>[] = [
    {
      key: "name",
      header: "Type",
      width: "minmax(0, 1fr)",
      cell: (t) => (
        <div className="min-w-0">
          <Mono className="block truncate text-foreground">{t.full_name}</Mono>
          <div className="mt-0.5 truncate text-[11px] text-ink-dim">{t.field_names.join(", ")}</div>
        </div>
      ),
    },
    {
      key: "fields",
      header: "Fields",
      width: "80px",
      align: "right",
      cell: (t) => <Mono>{t.field_count}</Mono>,
    },
    {
      key: "rules",
      header: "Rules",
      width: "80px",
      align: "right",
      cell: (t) => (
        <Mono className={t.rule_count ? "text-foreground" : "text-ink-faint"}>{t.rule_count}</Mono>
      ),
    },
    {
      key: "seen",
      header: "Last decoded",
      width: "140px",
      align: "right",
      cell: (t) => (
        <Mono className="text-ink-subtle">{t.last_seen ? since(t.last_seen) : "never"}</Mono>
      ),
    },
  ];

  const ruleColumns: readonly Column<SubjectRuleOut>[] = [
    {
      key: "pattern",
      header: "Subject pattern",
      width: "minmax(0, 1fr)",
      cell: (r) => <Mono className="truncate text-foreground">{r.pattern}</Mono>,
    },
    {
      key: "type",
      header: "Decodes as",
      width: "minmax(0, 1fr)",
      cell: (r) => <Mono className="truncate text-muted-foreground">{r.type_full_name}</Mono>,
    },
    {
      key: "specificity",
      header: "Specificity",
      width: "100px",
      align: "right",
      cell: (r) => <Mono className="text-ink-subtle">{r.specificity}</Mono>,
    },
    {
      key: "hits",
      header: "Matches",
      width: "96px",
      align: "right",
      cell: (r) => <Mono className="text-ink-subtle">{compact(r.hits)}</Mono>,
    },
    {
      key: "remove",
      header: "",
      width: "72px",
      align: "right",
      cell: (r) => (
        <Button size="xs" variant="ghost" onClick={() => deleteRule.mutate(r.id)}>
          Remove
        </Button>
      ),
    },
  ];

  const unmappedColumns: readonly Column<UnmappedSubject>[] = [
    {
      key: "subject",
      header: "Subject seen on the wire",
      width: "minmax(0, 1fr)",
      cell: (u) => <Mono className="truncate text-foreground">{u.subject}</Mono>,
    },
    {
      key: "suggested",
      header: "Suggested pattern",
      width: "minmax(0, 1fr)",
      cell: (u) => <Mono className="truncate text-muted-foreground">{u.suggested_pattern}</Mono>,
    },
    {
      key: "hits",
      header: "Seen",
      width: "80px",
      align: "right",
      cell: (u) => <Mono>{compact(u.hits)}</Mono>,
    },
    {
      key: "map",
      header: "",
      width: "90px",
      align: "right",
      cell: (u) => (
        <Button
          size="xs"
          variant="outline"
          onClick={() => {
            setNewPattern(u.suggested_pattern);
            setTab("rules");
          }}
        >
          Map
        </Button>
      ),
    },
  ];

  return (
    <Shell crumbs={["Schemas"]}>
      {/* Pinned above the split, so the two panes scroll independently. */}
      <div className="flex-none border-b border-hairline px-8 pb-[18px] pt-[26px]">
        <PageHeader
          title="Schemas"
          description="Protobuf descriptors, and the rules that decide which type a subject decodes as. JSON and MessagePack need none of this — they are read straight off the bytes."
          actions={
            <Button
              variant="outline"
              size="sm"
              disabled={rescan.isPending}
              onClick={() => rescan.mutate()}
              title="Re-read the upload and mounted directories"
            >
              {rescan.isPending ? "Scanning…" : "Rescan"}
            </Button>
          }
        />
        {rescan.data && <ScanSummary report={rescan.data} />}
        {rescan.isError && <ErrorPanel className="mt-3" error={rescan.error} />}
      </div>

      <Split className="min-h-0 flex-1">
        <ListPane
          title={`${list.length} descriptors`}
          filter={filter}
          onFilterChange={setFilter}
          placeholder="Filter packages"
          onAdd={() => setUploading(true)}
          addLabel="Upload a descriptor"
        >
          {visible.map((d) => (
            <ListRow
              key={d.id}
              selected={d.id === selected?.id}
              onClick={() => setSelectedId(d.id)}
            >
              <div className="flex items-center justify-between gap-2">
                <Mono className="truncate text-foreground">{d.package}</Mono>
                <div className="flex flex-none items-center gap-1.5">
                  {d.imported_only && (
                    <Badge tone="idle" size="xs">
                      imported
                    </Badge>
                  )}
                  {/* Where it came from decides whether it can be deleted here,
                      so it belongs on the row rather than only in the detail. */}
                  <Badge tone={d.origin === "mounted" ? "primary" : "idle"} size="xs">
                    {d.origin}
                  </Badge>
                </div>
              </div>
              <div className="mt-1 text-[11px] text-ink-dim">
                {d.type_count} types · {d.rule_count} rules · {fmtBytes(d.size_bytes)}
              </div>
            </ListRow>
          ))}
          {visible.length === 0 && (
            <div className="px-3 py-4 text-[11.5px] text-ink-dim">
              {list.length === 0
                ? "No descriptors registered. Protobuf payloads will fall through to raw wire format until one is."
                : "No package matches that filter."}
            </div>
          )}
        </ListPane>

        <SplitMain className="min-w-0">
          {/* `Page` gives this column its padding and its own scrollbar --
                without it a table row's -mx-3 hover bleed crosses the list border. */}
          <Page>
            {uploading ? (
              <UploadDescriptor
                onDone={(id) => {
                  setUploading(false);
                  setSelectedId(id);
                }}
                onCancel={() => setUploading(false)}
                onUploaded={() => {
                  void queryClient.invalidateQueries({ queryKey: apiPath("/api/schemas/descriptors") });
                }}
              />
            ) : descriptors.isError ? (
              <ErrorPanel error={descriptors.error} onRetry={() => descriptors.refetch()} />
            ) : (
              <>
                {selected && (
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <h2 className="t-page-title truncate text-foreground">{selected.package}</h2>
                      <p className="mt-1 text-[12px] text-muted-foreground">
                        {selected.source_filename}
                        {selected.protoc_version ? ` · protoc ${selected.protoc_version}` : ""} ·
                        registered {since(selected.registered_at)}
                      </p>
                    </div>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => deleteDescriptor.mutate(selected.id)}
                    >
                      Remove
                    </Button>
                  </div>
                )}

                <Tabs className="mt-4" tabs={TABS} value={tab} onChange={setTab} />

                <div className="mt-4 min-h-0 flex-1 overflow-auto">
                  {tab === "types" && (
                    <DataTable
                      columns={typeColumns}
                      rows={detail.data?.types ?? []}
                      rowKey={(t) => t.full_name}
                      rowHeight={52}
                      empty={
                        <NoRows
                          title="No message types"
                          body="This descriptor carries no messages, or none have been indexed yet."
                        />
                      }
                      footnote="Last decoded is sampled — what nats-lens has watched since it started, not a server-side total."
                    />
                  )}

                  {tab === "rules" && (
                    <div>
                      <Card className="mb-3.5">
                        <CardBody>
                          <CardHeader
                            title="Add a rule"
                            description="Most specific pattern wins. orders.new beats orders.* beats orders.> regardless of the order they were added."
                          />
                          <div className="mt-3 flex items-end gap-2">
                            <Field label="Subject pattern" className="flex-1">
                              <Input
                                className="font-mono"
                                placeholder="orders.new"
                                value={newPattern}
                                onChange={(e) => setNewPattern(e.currentTarget.value)}
                              />
                            </Field>
                            <Field label="Decodes as" className="flex-1">
                              <TypePicker value={newType} onChange={setNewType} />
                            </Field>
                            <Button
                              disabled={!newPattern.trim() || !newType.trim() || addRule.isPending}
                              onClick={() => addRule.mutate()}
                            >
                              Add
                            </Button>
                          </div>
                          {addRule.isError && <ErrorPanel className="mt-3" error={addRule.error} />}
                        </CardBody>
                      </Card>

                      <DataTable
                        columns={ruleColumns}
                        rows={rules.data ?? []}
                        rowKey={(r) => r.id}
                        rowHeight={48}
                        empty={
                          <NoRows
                            title="No subject rules"
                            body="Without a rule, protobuf payloads fall through to raw wire format — field numbers and types, but no names."
                          />
                        }
                        footnote="Shown in resolution order: this is the sequence the decoder actually tries."
                      />
                    </div>
                  )}

                  {tab === "unmapped" && (
                    <div>
                      <div className="mb-3 flex items-center gap-2">
                        <SourceBadge source="sampled" />
                        <span className="text-[11.5px] text-muted-foreground">
                          Subjects nats-lens has watched fall through to raw wire format since it
                          started. Not a server-side list.
                        </span>
                      </div>
                      <DataTable
                        columns={unmappedColumns}
                        rows={unmapped.data ?? []}
                        rowKey={(u) => u.subject}
                        rowHeight={48}
                        empty={
                          <NoRows
                            title="Nothing unmapped"
                            body="Every protobuf payload seen so far resolved to a type. Subjects appear here only once one has not."
                          />
                        }
                      />
                    </div>
                  )}
                </div>

                {order.data && order.data.length > 0 && (
                  <Card className="mt-4">
                    <CardBody>
                      <CardHeader
                        title="Resolution order"
                        description="Tried in this order, and it always terminates: the last step can read any bytes."
                      />
                      <div className="mt-1">
                        {order.data.map((step) => (
                          <div key={step.n} className="flex items-baseline gap-3 py-[5px]">
                            <Mono size="sm" className="w-5 flex-none text-ink-subtle">
                              {step.n}
                            </Mono>
                            <span className="w-[150px] flex-none text-[12px] text-card-foreground">
                              {step.name}
                            </span>
                            <span className="min-w-0 flex-1 text-[11.5px] leading-[1.5] text-muted-foreground text-pretty">
                              {step.description}
                            </span>
                          </div>
                        ))}
                      </div>
                    </CardBody>
                  </Card>
                )}
              </>
            )}
          </Page>
        </SplitMain>
      </Split>
    </Shell>
  );
}


/** Registering a descriptor.
 *
 * Two shapes go in and the difference matters, so the form names it rather than
 * hiding it behind one button:
 *
 *   * a `.proto` source, compiled by protoc *on the server*. Enough for a
 *     self-contained file.
 *   * a compiled `FileDescriptorSet`. Necessary the moment a file imports
 *     another of yours, because an upload is compiled on its own and protoc
 *     cannot follow an import it was not given.
 *
 * Guessed from the extension and then shown rather than hidden: a wrong guess is
 * silent otherwise, and a descriptor set fed to protoc as source fails with a
 * parse error that reads like a corrupt file.
 */
function UploadDescriptor({
  onDone,
  onCancel,
  onUploaded,
}: {
  onDone: (id: string) => void;
  onCancel: () => void;
  onUploaded: () => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<{ name: string; size: number; b64: string } | null>(null);
  const [isSet, setIsSet] = useState(false);
  const [note, setNote] = useState("");

  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Choose a file first.");
      return api.post("/api/schemas/descriptors", {
        body: {
          filename: file.name,
          content_b64: file.b64,
          is_descriptor_set: isSet,
          note: note.trim() || null,
        },
      });
    },
    onSuccess: (created) => {
      onUploaded();
      onDone(created.id);
    },
  });

  async function choose(picked: File) {
    const bytes = new Uint8Array(await picked.arrayBuffer());
    setFile({ name: picked.name, size: picked.size, b64: toBase64(bytes) });
    setIsSet(!picked.name.toLowerCase().endsWith(".proto"));
    upload.reset();
  }

  return (
    <div className="max-w-[720px]">
      <PageHeader
        title="Add a descriptor"
        description="Registering a type is what lets the decoder name fields instead of falling through to raw wire format. It decodes nothing on its own — a subject rule, or a Nats-Msg-Type header, is what decides which type a message is."
      />

      <Card className="mt-5">
        <CardBody>
          <input
            ref={fileInput}
            type="file"
            accept=".proto,.desc,.pb,.protoset,.bin"
            className="hidden"
            onChange={(e) => {
              const picked = e.currentTarget.files?.[0];
              if (picked) void choose(picked);
              // Cleared so choosing the same file twice still fires.
              e.currentTarget.value = "";
            }}
          />

          <div className="flex items-center gap-3">
            <Button variant="outline" onClick={() => fileInput.current?.click()}>
              {file ? "Choose a different file" : "Choose a file"}
            </Button>
            {file ? (
              <Mono size="sm" className="min-w-0 truncate text-card-foreground">
                {file.name} · {fmtBytes(file.size)}
              </Mono>
            ) : (
              <span className="text-[12px] text-ink-dim">
                A .proto source, or a FileDescriptorSet
              </span>
            )}
          </div>

          {file && (
            <div className="mt-4">
              <Field label="How to read it">
                <Select
                  value={isSet ? "set" : "proto"}
                  onChange={(e) => setIsSet(e.currentTarget.value === "set")}
                >
                  <option value="proto">.proto source — compile it here with protoc</option>
                  <option value="set">FileDescriptorSet — already compiled</option>
                </Select>
              </Field>
              <p className="mt-2 text-[11.5px] leading-[1.55] text-ink-faint">
                {isSet
                  ? "Read as it is. This is the shape to use when a file imports another of yours: build it with protoc --include_imports --descriptor_set_out=schema.desc"
                  : "Compiled on the server. A file that imports another of yours will fail here — protoc only sees the one file — and the error will say exactly that."}
              </p>
            </div>
          )}

          <Field label="Note" className="mt-4">
            <Input
              placeholder="optional — where this came from, which build"
              value={note}
              onChange={(e) => setNote(e.currentTarget.value)}
            />
          </Field>

          {upload.isError && <ErrorPanel className="mt-4" error={upload.error} />}

          <div className="mt-5 flex items-center gap-2">
            <Button disabled={!file || upload.isPending} onClick={() => upload.mutate()}>
              {upload.isPending ? "Registering…" : "Register"}
            </Button>
            <Button variant="outline" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </CardBody>
      </Card>

      <p className="mt-4 t-caption text-ink-faint text-pretty">
        Re-uploading a recompiled file replaces whatever held that package. Subject rules point at
        type names rather than at descriptor ids, so they survive it.
      </p>
    </div>
  );
}


/** Choosing a type for a subject rule.
 *
 * A combobox rather than a `<select>`, and over *every* registered type rather
 * than the selected descriptor's. Two reasons:
 *
 *   * a rule is written by knowing the type name, not by first finding the file
 *     it arrived in -- the old select only offered whatever descriptor happened
 *     to be selected, which made half the registry unreachable;
 *   * a real schema estate is hundreds of types, and a native select with no
 *     filter is a scroll, not a choice.
 *
 * Matching is on the whole name and its package, so `order` finds
 * `acme.orders.v1.OrderCreated` and so does `acme.orders`.
 */
function TypePicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}) {
  const types = useQuery(apiQuery("/api/schemas/types"));
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);

  const all = useMemo(() => types.data ?? [], [types.data]);
  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return all.slice(0, 50);
    return all
      .filter(
        (t) =>
          t.full_name.toLowerCase().includes(needle) || t.package.toLowerCase().includes(needle),
      )
      .slice(0, 50);
  }, [all, query]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  const choose = (fullName: string) => {
    onChange(fullName);
    setQuery("");
    setOpen(false);
  };

  if (!types.isLoading && all.length === 0) {
    // Nothing registered yet: a free-text box is more use than an empty menu,
    // and the rule is still valid the moment a matching descriptor arrives.
    return (
      <Input
        className="font-mono"
        placeholder="acme.orders.v1.OrderCreated"
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
      />
    );
  }

  return (
    <div ref={root} className="relative">
      <Input
        className="font-mono"
        placeholder={all.length ? `Search ${all.length} types…` : "Loading…"}
        value={open ? query : value}
        onFocus={() => {
          setOpen(true);
          setActive(0);
        }}
        onChange={(e) => {
          setQuery(e.currentTarget.value);
          setActive(0);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            setOpen(true);
            setActive((i) =>
              Math.max(0, Math.min(matches.length - 1, i + (e.key === "ArrowDown" ? 1 : -1))),
            );
          } else if (e.key === "Enter" && open && matches[active]) {
            e.preventDefault();
            choose(matches[active].full_name);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
      />

      {open && (
        <div
          role="listbox"
          className="absolute left-0 right-0 top-[calc(100%+4px)] z-50 max-h-[280px] overflow-y-auto rounded-card border border-border bg-card"
        >
          {matches.length === 0 ? (
            <div className="px-3 py-3 text-[11.5px] text-ink-dim">
              No registered type matches that.
            </div>
          ) : (
            matches.map((t, index) => (
              <button
                key={t.full_name}
                type="button"
                role="option"
                aria-selected={t.full_name === value}
                onPointerEnter={() => setActive(index)}
                onClick={() => choose(t.full_name)}
                className={cn(
                  "flex w-full items-center gap-2 border-b border-hairline px-3 py-2 text-left last:border-b-0",
                  index === active && "bg-row-hover",
                )}
              >
                <div className="min-w-0 flex-1">
                  <Mono size="sm" className="block truncate text-foreground">
                    {t.full_name}
                  </Mono>
                  <div className="mt-0.5 truncate text-[10.5px] text-ink-dim">
                    {t.field_count} fields · {t.source_filename}
                    {t.rule_count > 0 && ` · ${t.rule_count} rules`}
                  </div>
                </div>
                <Badge tone={t.origin === "mounted" ? "primary" : "idle"} size="xs">
                  {t.origin}
                </Badge>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}


/** What a rescan did.
 *
 * The counts matter less than the two things that go wrong: a directory that is
 * configured but not actually mounted (the usual reason a scan finds nothing),
 * and a file protoc refused. Both are named here rather than left to the log.
 */
function ScanSummary({ report }: { report: components["schemas"]["ScanReport"] }) {
  const failed = report.entries.filter((e) => e.status === "failed");
  const registered = report.entries.filter((e) => e.status === "registered").length;
  const removed = report.entries.filter((e) => e.status === "removed").length;

  return (
    <div className="mt-3">
      <div className="text-[11.5px] text-muted-foreground">
        {registered} registered · {report.entries.length - registered - removed - failed.length}{" "}
        unchanged
        {removed > 0 && ` · ${removed} removed`}
        {failed.length > 0 && ` · ${failed.length} failed`} — uploads{" "}
        <span className="font-mono">{report.upload_dir}</span>
        {report.mount_dir ? (
          <>
            , mounted <span className="font-mono">{report.mount_dir}</span>
            {!report.mount_dir_present && " (not mounted)"}
          </>
        ) : (
          ", no mounted directory configured"
        )}
      </div>
      {failed.map((entry) => (
        <div
          key={entry.path}
          className="mt-2 rounded-card border border-destructive-border px-3 py-2"
        >
          <Mono size="sm" className="block text-destructive">
            {entry.path}
          </Mono>
          <p className="mt-1 whitespace-pre-wrap text-[11.5px] leading-[1.5] text-muted-foreground">
            {entry.detail}
          </p>
        </div>
      ))}
    </div>
  );
}
