/** Configuration: grouped, read-only. Every value here is what the stream was
 * actually created with, not a form that could drift from it. */
import { bytes, count, duration } from "@/lib/format";
import { FactRow } from "@/components";
import type { components } from "@/lib/api.d";

type StreamDetail = components["schemas"]["StreamDetail"];

export function ConfigTab({ stream }: { stream: StreamDetail }) {
  const l = stream.limits;

  const groups: { group: string; rows: { k: string; v: string }[] }[] = [
    {
      group: "Identity",
      rows: [
        { k: "Name", v: stream.name },
        { k: "Subjects", v: stream.subjects.join(", ") || "—" },
        { k: "Description", v: stream.description ?? "—" },
      ],
    },
    {
      group: "Storage",
      rows: [
        { k: "Storage", v: stream.storage },
        { k: "Replicas", v: String(stream.replicas) },
        { k: "Placement tags", v: (stream.placement_tags ?? []).join(", ") || "—" },
      ],
    },
    {
      group: "Retention and limits",
      rows: [
        { k: "Policy", v: stream.retention },
        { k: "Discard", v: l.discard },
        { k: "Max age", v: l.max_age_seconds > 0 ? duration(l.max_age_seconds) : "unlimited" },
        { k: "Max consumers", v: l.max_consumers > 0 ? count(l.max_consumers) : "unlimited" },
        { k: "Max messages", v: l.max_msgs > 0 ? count(l.max_msgs) : "unlimited" },
        { k: "Max messages / subject", v: l.max_msgs_per_subject > 0 ? count(l.max_msgs_per_subject) : "unlimited" },
        { k: "Max bytes", v: l.max_bytes > 0 ? bytes(l.max_bytes) : "unlimited" },
        { k: "Max message size", v: l.max_msg_size > 0 ? bytes(l.max_msg_size) : "unlimited" },
        { k: "Duplicate window", v: duration(l.duplicate_window_seconds) },
        { k: "Allow direct get", v: String(l.allow_direct) },
        { k: "Allow rollup", v: String(l.allow_rollup) },
        { k: "Deny delete", v: String(l.deny_delete) },
        { k: "Deny purge", v: String(l.deny_purge) },
      ],
    },
    {
      group: "Mirrors and sources",
      rows: [
        { k: "Mirror", v: stream.mirror ?? "—" },
        { k: "Sources", v: (stream.sources ?? []).join(", ") || "—" },
        { k: "Republish to", v: stream.republish_to ?? "—" },
        { k: "Sealed", v: String(stream.sealed) },
      ],
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-8">
      {groups.map((g) => (
        <div key={g.group}>
          <div className="t-card-title text-foreground">{g.group}</div>
          <div className="mt-1">
            {g.rows.map((row) => (
              <FactRow key={row.k} label={row.k} value={row.v} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
