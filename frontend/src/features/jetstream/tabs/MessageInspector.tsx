/** One stored message, read the way you choose to read it.
 *
 * The chain's answer is the default and is labelled `auto`, because it is the
 * reading nats-lens would give this message anywhere else in the app. The rest
 * are deliberate second opinions: bytes are bytes, and an operator looking at an
 * unmapped subject needs to try things.
 *
 * Everything except Protobuf is computed here from the payload -- the bytes are
 * already on the client, and a round trip to re-render them as hex would be a
 * round trip to learn nothing. Protobuf needs the descriptor pool, which lives
 * on the server, so that one asks.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, apiQuery } from "@/lib/api";
import { fromBase64, hexdump } from "@/lib/format";
import { Badge, Field, Input, Mono, Tabs, type Tab } from "@/components";
import { cn } from "@/lib/cn";
import type { components } from "@/lib/api.d";

type Decoded = components["schemas"]["Decoded"];

export type Format = "auto" | "json" | "text" | "hex" | "base64" | "protobuf";

const FORMATS: readonly Tab<Format>[] = [
  { id: "auto", label: "Auto" },
  { id: "json", label: "JSON" },
  { id: "text", label: "Text" },
  { id: "hex", label: "Hex" },
  { id: "base64", label: "Base64" },
  { id: "protobuf", label: "Protobuf" },
];

function asText(bytes: Uint8Array): string {
  // `fatal` so invalid UTF-8 is reported rather than papered over with U+FFFD:
  // "this is not text" is the useful answer when someone picks Text by mistake.
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return "These bytes are not valid UTF-8 text. Try Hex, or Protobuf.";
  }
}

function asJson(bytes: Uint8Array, decoded: Decoded): string {
  const source = decoded.text ?? asText(bytes);
  try {
    return JSON.stringify(JSON.parse(source), null, 2);
  } catch {
    return "These bytes are not JSON. Try Auto to see what they are.";
  }
}

/** The decoded fields the chain (or a chosen type) produced. */
function Fields({ decoded }: { decoded: Decoded }) {
  const fields = decoded.fields ?? [];
  const wire = decoded.wire_fields ?? [];

  if (fields.length > 0) {
    return (
      <div>
        {decoded.type_name && (
          <Mono size="sm" className="mb-1.5 block text-ink-label">
            {decoded.type_name}
          </Mono>
        )}
        {fields.map((f) => (
          <div key={f.field_number} className="flex gap-2 border-b border-hairline py-1 last:border-b-0">
            <Mono size="sm" className="w-[34px] flex-none text-ink-dim">
              {f.field_number}
            </Mono>
            <Mono size="sm" className="w-[120px] flex-none truncate text-muted-foreground">
              {f.name}
            </Mono>
            <Mono size="sm" className="min-w-0 flex-1 break-all text-card-foreground">
              {f.value}
            </Mono>
          </div>
        ))}
      </div>
    );
  }
  if (wire.length > 0) {
    return (
      <div>
        <p className="mb-1.5 text-[11px] text-ink-faint">
          No type chosen, so this is the raw wire format: field numbers and wire types, which every
          protobuf payload carries whether or not a schema is registered.
        </p>
        {wire.map((f, i) => (
          <div key={i} className="flex gap-2 border-b border-hairline py-1 last:border-b-0">
            <Mono size="sm" className="w-[34px] flex-none text-ink-dim">
              {f.field_number}
            </Mono>
            <Mono size="sm" className="min-w-0 flex-1 break-all text-card-foreground">
              {f.render}
            </Mono>
          </div>
        ))}
      </div>
    );
  }
  return <Pre>{decoded.text ?? "(empty)"}</Pre>;
}

function Pre({ children }: { children: React.ReactNode }) {
  return (
    <pre className="max-h-[300px] overflow-auto whitespace-pre-wrap break-all rounded-control bg-background p-[11px] font-mono text-[11.5px] leading-[1.65] text-card-foreground">
      {children}
    </pre>
  );
}

/** Choosing a type to read the bytes as, when no rule claims the subject. */
function TypeChooser({ value, onChange }: { value: string; onChange: (next: string) => void }) {
  const types = useQuery(apiQuery("/api/schemas/types"));
  const [query, setQuery] = useState("");
  const all = useMemo(() => types.data ?? [], [types.data]);
  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (needle ? all.filter((t) => t.full_name.toLowerCase().includes(needle)) : all).slice(0, 8);
  }, [all, query]);

  if (all.length === 0) {
    return (
      <p className="text-[11.5px] leading-[1.5] text-ink-faint">
        No descriptors are registered yet, so there is no type to read these bytes as. Add one on
        the Schemas screen and the wire format below becomes named fields.
      </p>
    );
  }

  return (
    <div>
      <Field label="Read as type">
        <Input
          font="mono"
          placeholder={`Search ${all.length} types…`}
          value={query}
          onChange={(e) => setQuery(e.currentTarget.value)}
        />
      </Field>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {matches.map((t) => (
          <button
            key={t.full_name}
            type="button"
            onClick={() => onChange(t.full_name === value ? "" : t.full_name)}
            className={cn(
              "rounded-badge border px-2 py-[3px] font-mono text-[10.5px]",
              t.full_name === value
                ? "border-primary-border text-primary"
                : "border-border text-muted-foreground hover:bg-control-hover",
            )}
          >
            {t.full_name}
          </button>
        ))}
      </div>
    </div>
  );
}

export function MessageBody({
  subject,
  payloadB64,
  decoded,
  headers,
}: {
  subject: string;
  payloadB64: string;
  decoded: Decoded;
  headers: Record<string, string>;
}) {
  const [format, setFormat] = useState<Format>("auto");
  const [chosenType, setChosenType] = useState("");
  const bytes = useMemo(() => fromBase64(payloadB64), [payloadB64]);

  // Only Protobuf needs the server: the descriptor pool lives there.
  const forced = useQuery({
    queryKey: ["api", "/api/schemas/decode", subject, payloadB64, chosenType],
    queryFn: () =>
      api.post("/api/schemas/decode", {
        body: { subject, payload_b64: payloadB64, headers, type_full_name: chosenType },
      }),
    enabled: format === "protobuf" && chosenType !== "",
  });

  const shown = format === "protobuf" && forced.data ? forced.data.decoded : decoded;

  return (
    <div>
      <Tabs tabs={FORMATS} value={format} onChange={setFormat} />

      <div className="mt-3">
        {format === "auto" && (
          <>
            <div className="mb-2 flex items-center gap-2">
              <Badge size="xs" tone="primary">
                {decoded.codec}
              </Badge>
              <span className="text-[11px] text-ink-faint">resolved by {decoded.resolved_by}</span>
            </div>
            <Fields decoded={decoded} />
          </>
        )}
        {format === "json" && <Pre>{asJson(bytes, decoded)}</Pre>}
        {format === "text" && <Pre>{asText(bytes)}</Pre>}
        {format === "hex" && <Pre>{hexdump(bytes, 2048)}</Pre>}
        {format === "base64" && <Pre>{payloadB64}</Pre>}
        {format === "protobuf" && (
          <>
            <TypeChooser value={chosenType} onChange={setChosenType} />
            <div className="mt-3">
              {forced.isPending && chosenType ? (
                <p className="text-[11.5px] text-ink-faint">Decoding…</p>
              ) : (
                <Fields decoded={shown} />
              )}
            </div>
            {(shown.warnings ?? []).length > 0 && (
              <div className="mt-2 rounded-card border border-degraded-border px-2.5 py-2">
                {(shown.warnings ?? []).map((w) => (
                  <p key={w} className="text-[11px] leading-[1.5] text-degraded">
                    {w}
                  </p>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
