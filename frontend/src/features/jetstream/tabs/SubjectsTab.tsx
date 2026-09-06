/** Subjects: server-side per-subject counts.
 *
 * From `jsm.stream_info(name, subjects_filter=...)` -> `state.subjects` --
 * the singular call, not the paged `streams_info`. nats-lens does not walk
 * the stream to build this list, so the note says so.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiQuery } from "@/lib/api";
import { count } from "@/lib/format";
import { Field, Input, Meter, Mono, NoRows, Section, SourceBadge } from "@/components";
import { ErrorPanel } from "@/components/ErrorPanel";

export function SubjectsTab({
  serverId,
  streamName,
  onReadSubject,
}: {
  serverId: string;
  streamName: string;
  /** Show this subject's messages. A count is a question -- "which ones?" -- and
   * the answer is one tab away, so the row is the way there. */
  onReadSubject: (subject: string) => void;
}) {
  const [filter, setFilter] = useState(">");

  const query = useQuery(
    apiQuery("/api/servers/{server_id}/jetstream/streams/{name}/subjects", {
      path: { server_id: serverId, name: streamName },
      query: { filter: filter || ">" },
    }),
  );

  return (
    <div>
      <Section
        title="Messages per subject"
        right={<SourceBadge source="jetstream" />}
      >
        <p className="mt-[-8px] mb-4 text-[11.5px] text-ink-subtle">
          jsm.stream_info(subjects_filter=&quot;{filter || ">"}&quot;) -- the server counts these,
          nats-lens does not walk the stream. Pick a row to read its messages.
        </p>
        <div className="mb-4 max-w-[320px]">
          <Field label="Filter">
            <Input
              font="mono"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder=">"
            />
          </Field>
        </div>

        {query.isLoading && <p className="text-[12.5px] text-ink-subtle">Loading subject counts…</p>}
        {query.isError && <ErrorPanel error={query.error} onRetry={() => query.refetch()} />}
        {query.data && query.data.length === 0 && (
          <NoRows title="No subjects match" body={`Nothing in this stream matches "${filter}".`} />
        )}
        {query.data && query.data.length > 0 && (
          <div>
            {query.data.map((row) => (
              <button
                key={row.subject}
                type="button"
                onClick={() => onReadSubject(row.subject)}
                title={`Read messages on ${row.subject}`}
                className="-mx-2 flex w-full items-center gap-4 rounded-control border-b border-hairline px-2 py-[11px] text-left hover:bg-row-hover"
              >
                <Mono size="lg" truncate className="w-[260px] flex-none text-card-foreground">
                  {row.subject}
                </Mono>
                <Meter className="flex-1" value={row.share_of_largest} caption={null} />
                <Mono size="md" className="w-[96px] flex-none text-right text-ink-label">
                  {count(row.count)}
                </Mono>
              </button>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
