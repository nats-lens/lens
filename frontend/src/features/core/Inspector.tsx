/** One message, in every view the decoder can offer.
 *
 * Reference: the CoreExplorer artboard, the inspector pane.
 *
 * The tab strip is not five independent renderers: `Auto` shows whatever the
 * chain resolved, and the others force a view. Forcing one the payload cannot
 * satisfy -- JSON on protobuf bytes -- says so rather than showing an error, so
 * the tabs stay explorable.
 *
 * `resolved_by` is displayed verbatim. Which of the five steps answered is the
 * single most useful fact about a decoded message, and it is the reason the
 * chain reports it at all.
 */
import { useState } from "react";
import type { components } from "@/lib/api.d";
import { bytes as fmtBytes, fromBase64, hexdump, timestamp } from "@/lib/format";
import { Badge, Card, CardBody, FactRow, Mono, Tabs, type BadgeTone, type Tab } from "@/components";

type CapturedMessage = components["schemas"]["CapturedMessage"];
type Decoded = components["schemas"]["Decoded"];
type WireField = components["schemas"]["WireField"];

type View = "auto" | "json" | "protobuf" | "text" | "hex";

const VIEWS: readonly Tab<View>[] = [
  { id: "auto", label: "Auto" },
  { id: "json", label: "JSON" },
  { id: "protobuf", label: "Protobuf" },
  { id: "text", label: "Text" },
  { id: "hex", label: "Hex" },
];

const CODEC_TONE: Record<string, BadgeTone> = {
  json: "healthy",
  msgpack: "healthy",
  protobuf: "primary",
  text: "neutral",
  binary: "neutral",
  empty: "idle",
};

/** How the chain describes itself, in the words the Schemas screen uses. */
const RESOLVED_BY: Record<string, string> = {
  header: "the Nats-Msg-Type header",
  subject_rule: "a subject rule",
  content_type: "the Content-Type header",
  sniff: "the shape of the bytes",
  wire: "raw protobuf wire format — no schema matched",
};

function WireRows({ fields, depth = 0 }: { fields: readonly WireField[]; depth?: number }) {
  return (
    <div className={depth ? "ml-3 border-l border-hairline pl-3" : ""}>
      {fields.map((f, i) => (
        <div key={`${depth}-${i}-${f.field_number}`} className="py-[3px]">
          <div className="flex items-baseline gap-2">
            <Mono size="sm" className="w-8 flex-none text-ink-subtle">
              {f.field_number}
            </Mono>
            <Mono size="sm" className="text-card-foreground">
              {f.render}
            </Mono>
          </div>
          {(f.nested ?? []).length > 0 && <WireRows fields={f.nested ?? []} depth={depth + 1} />}
        </div>
      ))}
    </div>
  );
}

function DecodedFields({ decoded }: { decoded: Decoded }) {
  if ((decoded.fields ?? []).length === 0) return null;
  return (
    <div>
      {(decoded.fields ?? []).map((f) => (
        <div key={`${f.field_number}-${f.name}`} className="flex items-baseline gap-2 py-[3px]">
          <Mono size="sm" className="w-8 flex-none text-ink-subtle">
            {f.field_number}
          </Mono>
          <span className="w-40 flex-none truncate text-[12px] text-muted-foreground">
            {f.name}
            {f.repeated && <span className="text-ink-faint"> []</span>}
          </span>
          <Mono size="sm" className="min-w-0 flex-1 break-all text-card-foreground">
            {f.value}
          </Mono>
          <Mono size="sm" className="flex-none text-ink-faint">
            {f.type_name}
          </Mono>
        </div>
      ))}
    </div>
  );
}

/** A view the payload cannot satisfy. Explaining beats an error. */
function NotThisView({ reason }: { reason: string }) {
  return (
    <p className="px-1 py-3 text-[12px] leading-[1.55] text-muted-foreground text-pretty">
      {reason}
    </p>
  );
}

function Body({ message, view }: { message: CapturedMessage; view: View }) {
  const decoded = message.decoded;
  const raw = fromBase64(message.payload_b64);

  if (view === "hex") {
    return (
      <Mono size="sm" className="block whitespace-pre overflow-x-auto text-card-foreground">
        {hexdump(raw)}
      </Mono>
    );
  }

  if (view === "text") {
    const text = new TextDecoder("utf-8", { fatal: false }).decode(raw);
    return (
      <Mono size="sm" className="block whitespace-pre-wrap break-all text-card-foreground">
        {text || "(empty payload)"}
      </Mono>
    );
  }

  if (view === "json") {
    if (decoded.codec !== "json" && decoded.codec !== "msgpack") {
      return (
        <NotThisView
          reason={`These bytes are not JSON — the chain read them as ${decoded.codec}. The Auto tab shows what they actually are.`}
        />
      );
    }
    return (
      <Mono size="sm" className="block whitespace-pre-wrap break-all text-card-foreground">
        {decoded.text}
      </Mono>
    );
  }

  if (view === "protobuf") {
    if ((decoded.fields ?? []).length > 0) return <DecodedFields decoded={decoded} />;
    if ((decoded.wire_fields ?? []).length > 0) return <WireRows fields={decoded.wire_fields ?? []} />;
    return (
      <NotThisView reason="These bytes do not parse as protobuf, so there are no fields to show." />
    );
  }

  // Auto: whatever the chain resolved.
  if ((decoded.fields ?? []).length > 0) return <DecodedFields decoded={decoded} />;
  if ((decoded.wire_fields ?? []).length > 0) return <WireRows fields={decoded.wire_fields ?? []} />;
  if (decoded.text != null) {
    return (
      <Mono size="sm" className="block whitespace-pre-wrap break-all text-card-foreground">
        {decoded.text}
      </Mono>
    );
  }
  return (
    <Mono size="sm" className="block whitespace-pre overflow-x-auto text-card-foreground">
      {hexdump(raw)}
    </Mono>
  );
}

export function Inspector({
  message,
  onMapSubject,
}: {
  message: CapturedMessage;
  onMapSubject?: (subject: string) => void;
}) {
  const [view, setView] = useState<View>("auto");
  const decoded = message.decoded;

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Mono className="block truncate text-foreground">{message.subject}</Mono>
          <div className="mt-1 flex items-center gap-2 text-[11.5px] text-ink-subtle">
            <span>{timestamp(message.at)}</span>
            <span>·</span>
            <span>{fmtBytes(message.size)}</span>
            <span>·</span>
            <span>seq {message.seq}</span>
          </div>
        </div>
        <Badge tone={CODEC_TONE[decoded.codec] ?? "neutral"} size="sm">
          {decoded.type_name ?? decoded.codec}
        </Badge>
      </div>

      {/* The chain's own account of itself. */}
      <p className="mt-2.5 text-[11.5px] leading-[1.5] text-muted-foreground text-pretty">
        Resolved by {RESOLVED_BY[decoded.resolved_by] ?? decoded.resolved_by}
        {decoded.type_name ? ` as ${decoded.type_name}` : ""}.
      </p>

      {decoded.unmapped_subject && onMapSubject && (
        <button
          type="button"
          onClick={() => onMapSubject(decoded.unmapped_subject as string)}
          className="mt-2 self-start rounded-control border border-degraded-border px-2 py-1 text-[11.5px] text-degraded hover:bg-control-hover"
        >
          Map {decoded.unmapped_subject} to a type
        </button>
      )}

      {(decoded.warnings ?? []).length > 0 && (
        <div className="mt-2 flex flex-col gap-1">
          {(decoded.warnings ?? []).map((w) => (
            <p key={w} className="text-[11.5px] leading-[1.5] text-degraded text-pretty">
              {w}
            </p>
          ))}
        </div>
      )}

      <Tabs className="mt-3.5" tabs={VIEWS} value={view} onChange={setView} />

      <div className="mt-3 min-h-0 flex-1 overflow-auto rounded-card border border-border bg-card p-3">
        <Body message={message} view={view} />
      </div>

      {Object.keys(message.headers).length > 0 && (
        <Card className="mt-3">
          <CardBody>
            <div className="t-label text-ink-dim">Headers</div>
            <div className="mt-1">
              {Object.entries(message.headers).map(([k, v]) => (
                <FactRow key={k} label={k} value={<Mono size="sm">{v}</Mono>} />
              ))}
            </div>
          </CardBody>
        </Card>
      )}

      {message.reply && (
        <div className="mt-2 text-[11.5px] text-ink-subtle">
          Reply to <Mono size="sm">{message.reply}</Mono>
        </div>
      )}
    </div>
  );
}
