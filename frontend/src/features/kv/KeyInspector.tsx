/** The right-hand panel: one key's value or its history.
 *
 * Editing is a compare-and-set on the revision, never a plain overwrite --
 * `kv.update(key, value, last=revision)`. A 409 means someone else wrote a
 * newer revision while this form was open; that is shown as what the
 * revision actually is now, not swallowed into a generic error and not
 * silently retried over the top of it.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, apiPath, apiQuery } from "@/lib/api";
import { fromBase64, hexdump, since, timestamp, toBase64 } from "@/lib/format";
import { Badge, Button, FactRow, Tabs, type Tab } from "@/components";
import type { components } from "@/lib/api.d";
import { ErrorPanel } from "@/components/ErrorPanel";

type KvEntry = components["schemas"]["KvEntry"];

type InspectorTab = "value" | "history";
const TABS: readonly Tab<InspectorTab>[] = [
  { id: "value", label: "Value" },
  { id: "history", label: "History" },
];

function opTone(op: KvEntry["operation"]): { tone: "healthy" | "degraded" | "destructive" } {
  if (op === "PUT") return { tone: "healthy" };
  if (op === "DEL") return { tone: "degraded" };
  return { tone: "destructive" };
}

function entryText(entry: KvEntry): string {
  if (entry.decoded?.text) return entry.decoded.text;
  if (entry.payload_b64) return hexdump(fromBase64(entry.payload_b64));
  return "(no value)";
}

export function KeyInspector({
  serverId,
  bucket,
  historyDepth,
  keyName,
  onChanged,
}: {
  serverId: string;
  bucket: string;
  historyDepth: number;
  keyName: string;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<InspectorTab>("value");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [conflict, setConflict] = useState<{ revision: number; text: string } | null>(null);

  const entryQuery = useQuery(
    apiQuery("/api/servers/{server_id}/kv/{bucket}/keys/{key}", {
      path: { server_id: serverId, bucket, key: keyName },
    }),
  );
  const historyQuery = useQuery({
    ...apiQuery("/api/servers/{server_id}/kv/{bucket}/history/{key}", {
      path: { server_id: serverId, bucket, key: keyName },
    }),
    enabled: tab === "history",
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: apiPath("/api/servers/{server_id}/kv/{bucket}/keys/{key}") });
    queryClient.invalidateQueries({ queryKey: apiPath("/api/servers/{server_id}/kv/{bucket}/history/{key}") });
    onChanged();
  };

  const save = useMutation({
    mutationFn: (lastRevision: number) =>
      api.put("/api/servers/{server_id}/kv/{bucket}/keys/{key}", {
        path: { server_id: serverId, bucket, key: keyName },
        body: { value_b64: toBase64(new TextEncoder().encode(draft)), last_revision: lastRevision },
      }),
    onSuccess: () => {
      setEditing(false);
      setConflict(null);
      invalidate();
    },
    onError: async (err) => {
      if (!(err instanceof ApiError) || err.status !== 409) return;
      try {
        const fresh = await api.get("/api/servers/{server_id}/kv/{bucket}/keys/{key}", {
          path: { server_id: serverId, bucket, key: keyName },
        });
        setConflict({ revision: fresh.revision, text: entryText(fresh) });
      } catch {
        // The conflict itself is still shown via save.isError; the revision
        // just could not be refreshed on top of it.
      }
    },
  });

  const del = useMutation({
    mutationFn: (purge: boolean) =>
      api.delete("/api/servers/{server_id}/kv/{bucket}/keys/{key}", {
        path: { server_id: serverId, bucket, key: keyName },
        query: { purge },
      }),
    onSuccess: invalidate,
  });

  if (entryQuery.isLoading) {
    return <p className="text-[12.5px] text-ink-subtle">Loading {keyName}…</p>;
  }
  if (entryQuery.isError) {
    return <ErrorPanel error={entryQuery.error} onRetry={() => entryQuery.refetch()} />;
  }
  const entry = entryQuery.data;
  if (!entry) return null;

  return (
    <div>
      <div className="text-[12px] font-medium text-ink-quiet">Key</div>
      <div className="mt-2 break-all font-mono text-[13.5px] text-foreground">{keyName}</div>

      <div className="mt-[14px]">
        <Tabs label="Key detail" value={tab} onChange={setTab} tabs={TABS} />
      </div>

      {tab === "value" && (
        <div className="mt-4">
          <FactRow label="revision" value={`r${entry.revision}`} />
          <FactRow label="operation" value={<Badge size="xs" tone={opTone(entry.operation).tone}>{entry.operation}</Badge>} />
          <FactRow label="updated" value={since(entry.created)} />
          <FactRow label="value size" value={`${entry.size} B`} />

          {!editing ? (
            <>
              <pre className="mt-3.5 max-h-[280px] overflow-auto whitespace-pre-wrap break-all rounded-control border border-border bg-background p-[13px] font-mono text-[12px] leading-[1.75] text-card-foreground">
                {entryText(entry)}
              </pre>
              <div className="mt-3.5 flex gap-2">
                <Button
                  variant="primary"
                  block
                  onClick={() => {
                    setDraft(entryText(entry) === "(no value)" ? "" : entryText(entry));
                    setConflict(null);
                    setEditing(true);
                  }}
                >
                  Edit
                </Button>
                <Button
                  variant="outline"
                  block
                  disabled={del.isPending}
                  onClick={() => {
                    if (window.confirm(`Delete ${keyName}? This keeps its history.`)) del.mutate(false);
                  }}
                >
                  Delete
                </Button>
                <Button
                  variant="destructive"
                  block
                  disabled={del.isPending}
                  onClick={() => {
                    if (window.confirm(`Purge ${keyName}? This removes every revision, permanently.`)) {
                      del.mutate(true);
                    }
                  }}
                >
                  Purge
                </Button>
              </div>
            </>
          ) : (
            <div className="mt-3.5">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={8}
                className="w-full rounded-control border border-border bg-card px-[11px] py-[9px] font-mono text-[12px] leading-[1.6] text-foreground focus-visible:border-primary focus-visible:outline-none"
              />
              {conflict && (
                <div className="mt-3 rounded-card border border-degraded-border bg-surface-degraded p-3">
                  <div className="text-[12px] font-medium text-degraded">
                    Someone else wrote revision r{conflict.revision} while this was open.
                  </div>
                  <pre className="mt-2 max-h-[140px] overflow-auto whitespace-pre-wrap break-all font-mono text-[11.5px] text-muted-foreground">
                    {conflict.text}
                  </pre>
                  <Button
                    size="xs"
                    variant="outline"
                    className="mt-2"
                    onClick={() => {
                      setDraft(conflict.text === "(no value)" ? "" : conflict.text);
                      setConflict(null);
                    }}
                  >
                    Load current value
                  </Button>
                </div>
              )}
              {save.isError && !conflict && <ErrorPanel className="mt-3" error={save.error} />}
              <p className="mt-3 text-[11.5px] leading-[1.55] text-ink-faint text-pretty">
                Save does a compare-and-set on revision r{conflict?.revision ?? entry.revision} --
                kv.update(key, value, last={conflict?.revision ?? entry.revision}). A concurrent
                writer makes it fail rather than clobber.
              </p>
              <div className="mt-3 flex gap-2">
                <Button
                  variant="primary"
                  block
                  disabled={save.isPending || conflict !== null}
                  onClick={() => save.mutate(conflict?.revision ?? entry.revision)}
                >
                  {save.isPending ? "Saving…" : "Save"}
                </Button>
                <Button
                  variant="ghost"
                  block
                  onClick={() => {
                    setEditing(false);
                    setConflict(null);
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "history" && (
        <div className="mt-4">
          <p className="text-[11.5px] text-ink-subtle">
            kv.history(key) -- the bucket keeps {historyDepth} revision{historyDepth === 1 ? "" : "s"}{" "}
            per key.
          </p>
          {historyQuery.isLoading && <p className="mt-3 text-[12px] text-ink-faint">Loading history…</p>}
          {historyQuery.isError && (
            <ErrorPanel className="mt-3" error={historyQuery.error} onRetry={() => historyQuery.refetch()} />
          )}
          {historyQuery.data && (
            <div className="mt-2">
              {historyQuery.data.map((rev) => (
                <div key={rev.revision} className="flex items-center gap-2.5 border-b border-hairline py-2.5">
                  <span className="w-[30px] flex-none font-mono text-[11.5px] text-ink-label">
                    r{rev.revision}
                  </span>
                  <Badge size="xs" tone={opTone(rev.operation).tone}>
                    {rev.operation}
                  </Badge>
                  <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-ink-subtle">
                    {rev.size} B · {timestamp(rev.created)}
                  </span>
                  <span className="flex-none font-mono text-[11px] text-ink-dim">{since(rev.created)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

