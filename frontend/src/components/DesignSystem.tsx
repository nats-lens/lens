import { useEffect, useState } from "react";
import type { Source, Sourced } from "@/lib/provenance";
import { bytes, clock, compact, count, millis, ratio } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  DataTable,
  DroppedRow,
  EmptyState,
  FactRow,
  Field,
  Figure,
  Input,
  ListPane,
  ListRow,
  Meter,
  Mono,
  NoRows,
  Page,
  PageHeader,
  Section,
  Select,
  Shell,
  SourceBadge,
  SourcedValue,
  Split,
  SplitMain,
  StatCard,
  StatusDot,
  SubjectChip,
  Tabs,
  Textarea,
  Toggle,
} from "@/components";
import type { Column } from "@/components";

/** Every primitive in every state, on one page.
 *
 * This is the handover: the four screen agents read this rather than the
 * design's HTML, and a primitive that is not here is a primitive nobody knows
 * exists. It is also the cheapest regression test we have -- a token that
 * disappears shows up as a black square.
 */

// ------------------------------------------------------------------ helpers

const now = new Date().toISOString();

function known<T>(value: T, source: Source): Sourced<T> {
  return { value, source, at: now, unavailable: null };
}

function missing<T>(source: Source, reason: string, fix: string, doc?: string): Sourced<T> {
  return { value: null, source, at: now, unavailable: { reason, fix, doc: doc ?? null } };
}

const NO_MONITOR = missing<number>(
  "monitor",
  "monitoring_not_configured",
  "Start the server with http_port: 8222, or give nats-lens a $SYS user, and this fills in.",
  "https://docs.nats.io/running-a-nats-service/nats_admin/monitoring",
);

/** Reads the live value of every token so a swatch cannot drift from the CSS.
 *
 * The gallery shows what `index.css` actually resolved to, not a second copy of
 * the palette maintained by hand -- which is the same argument as generating the
 * API types rather than writing them twice. */
function useTokenValues(): Record<string, string> {
  const [values, setValues] = useState<Record<string, string>>({});
  useEffect(() => {
    const style = getComputedStyle(document.documentElement);
    const read: Record<string, string> = {};
    for (const name of TOKEN_NAMES) read[name] = style.getPropertyValue(name).trim();
    setValues(read);
  }, []);
  return values;
}

const PALETTE: { group: string; tokens: { name: string; token: string }[] }[] = [
  {
    group: "Ground — warm neutral, never blue",
    tokens: [
      { name: "Background", token: "--color-background" },
      { name: "Sidebar", token: "--color-sidebar" },
      { name: "Card", token: "--color-card" },
      { name: "Muted", token: "--color-muted" },
      { name: "Border", token: "--color-border" },
      { name: "Hairline", token: "--color-hairline" },
      { name: "Hairline soft", token: "--color-hairline-soft" },
      { name: "Row hover", token: "--color-row-hover" },
      { name: "Control hover", token: "--color-control-hover" },
      { name: "Track", token: "--color-track" },
      { name: "Border strong", token: "--color-border-strong" },
      { name: "Border invalid", token: "--color-border-invalid" },
      { name: "Tab active", token: "--color-tab-active" },
      { name: "Surface healthy", token: "--color-surface-healthy" },
      { name: "Surface degraded", token: "--color-surface-degraded" },
    ],
  },
  {
    group: "Ink",
    tokens: [
      { name: "Foreground", token: "--color-foreground" },
      { name: "Body", token: "--color-card-foreground" },
      { name: "Strong", token: "--color-ink-strong" },
      { name: "Muted fg", token: "--color-muted-foreground" },
      { name: "Label", token: "--color-ink-label" },
      { name: "Quiet", token: "--color-ink-quiet" },
      { name: "Subtle", token: "--color-ink-subtle" },
      { name: "Faint", token: "--color-ink-faint" },
      { name: "Dim", token: "--color-ink-dim" },
      { name: "Dimmest", token: "--color-ink-dimmest" },
    ],
  },
  {
    group: "Accent and signal — one lightness, one chroma, four hues",
    tokens: [
      { name: "Primary", token: "--color-primary" },
      { name: "Primary hover", token: "--color-primary-hover" },
      { name: "Primary fg", token: "--color-primary-foreground" },
      { name: "Healthy", token: "--color-healthy" },
      { name: "Degraded", token: "--color-degraded" },
      { name: "Failing", token: "--color-destructive" },
      { name: "Idle", token: "--color-idle" },
      { name: "Chart 4", token: "--color-chart-4" },
    ],
  },
  {
    group: "Badge borders — the dim companion to each signal hue",
    tokens: [
      { name: "Healthy", token: "--color-healthy-border" },
      { name: "Degraded", token: "--color-degraded-border" },
      { name: "Failing", token: "--color-destructive-border" },
      { name: "Primary", token: "--color-primary-border" },
      { name: "Idle", token: "--color-idle-border" },
    ],
  },
];

const TOKEN_NAMES = PALETTE.flatMap((group) => group.tokens.map((token) => token.token));

const RAMP = [
  { cls: "t-page-title", spec: "Geist 600 / 28 · -0.025em", role: "Page title, one per screen" },
  { cls: "t-section", spec: "Geist 600 / 22 · -0.02em", role: "Section heading" },
  { cls: "t-card-title", spec: "Geist 600 / 14", role: "Card title" },
  { cls: "t-label", spec: "Geist 500 / 12", role: "Field label, table header" },
  { cls: "t-body", spec: "Geist 400 / 13", role: "Body and buttons" },
  { cls: "t-caption", spec: "Geist 400 / 11.5", role: "Helper text and captions" },
  { cls: "t-mono", spec: "Mono 400 / 12", role: "Subjects, URLs, payloads, digests" },
  { cls: "t-figure", spec: "Mono 500 / 20 · -0.03em", role: "Headline figures" },
] as const;

const RULES = [
  "Monospace means the server said it. Never for interface copy, never sans for a subject.",
  "One accent. Green, amber and rose carry state only — they are never decoration.",
  "Every number that is not from the client connection carries a source badge.",
  "A missing data source shows an empty state that names the fix, never a zero.",
  "Selection is a one-step ground lift. No outlines, no glow, no scale.",
  "Every list row fills the same right-hand inspector. Dialogs are for destructive confirmations only.",
  "No italics anywhere. Emphasis is weight, colour or size.",
] as const;

const SOURCES: Source[] = ["client", "jetstream", "monitor", "system", "sampled"];

type Row = { id: string; name: string; url: string; msgs: number; rtt: number };

const ROWS: Row[] = [
  { id: "a", name: "prod-us-east", url: "tls://nats.prod.us-east:4222", msgs: 4_213_884, rtt: 8.4 },
  { id: "b", name: "prod-eu-west", url: "tls://nats.prod.eu-west:4222", msgs: 1_884_120, rtt: 31.7 },
  { id: "c", name: "staging", url: "nats://staging.internal:4222", msgs: 210_400, rtt: 22.1 },
];

// ------------------------------------------------------------------ sections

function Swatches() {
  const values = useTokenValues();

  return (
    <div className="flex flex-col gap-6">
      {PALETTE.map((group) => (
        <div key={group.group}>
          <div className="t-label mb-3 text-ink-subtle">{group.group}</div>
          <div className="grid grid-cols-5 gap-3.5">
            {group.tokens.map((token) => (
              <div key={token.token}>
                <div
                  className="h-12 rounded-[8px] border border-border"
                  style={{ background: `var(${token.token})` }}
                />
                <div className="mt-2 text-[12.5px] text-foreground">{token.name}</div>
                <Mono size="sm" className="mt-[3px] block text-muted-foreground">
                  {values[token.token] || "—"}
                </Mono>
                <Mono size="xs" className="mt-[3px] block text-ink-faint">
                  {token.token.replace("--color-", "")}
                </Mono>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function TypeRamp() {
  return (
    <div className="grid grid-cols-2 gap-8">
      <div>
        {RAMP.map((row) => (
          <div key={row.cls} className="flex items-baseline gap-4 border-b border-hairline py-2.5">
            <Mono size="sm" className="w-[152px] flex-none text-ink-faint">
              {row.spec}
            </Mono>
            <span className="flex-1 text-[12.5px] text-muted-foreground">{row.role}</span>
            <Mono size="xs" className="flex-none text-ink-dim">
              .{row.cls}
            </Mono>
          </div>
        ))}
      </div>
      <Card>
        <CardBody>
          <div className="t-label text-muted-foreground">In use</div>
          <div className="mt-2.5 font-mono text-[22px] font-medium tracking-[-0.02em]">ORDERS</div>
          <div className="mt-2 text-[12.5px] text-ink-label">
            Customer order lifecycle · 6 consumers · leader nats-1
          </div>
          <div className="mt-4 flex gap-6">
            <div>
              <Figure>{compact(4_213_884)}</Figure>
              <div className="mt-1 text-[11.5px] text-ink-label">messages</div>
            </div>
            <div>
              <Figure>{bytes(19_327_352_832)}</Figure>
              <div className="mt-1 text-[11.5px] text-ink-label">on disk</div>
            </div>
          </div>
          <p className="mt-4 text-[12px] leading-[1.6] text-ink-label text-pretty">
            Sans for what nats-lens says, mono for what NATS returned. The two voices never blur,
            which is what keeps a dense screen readable.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}

function Controls() {
  const [tab, setTab] = useState<"auto" | "json" | "protobuf">("auto");
  const [on, setOn] = useState(true);
  const [off, setOff] = useState(false);

  return (
    <div className="grid grid-cols-2 gap-8">
      <div className="flex flex-col gap-6">
        <div>
          <div className="t-label text-ink-subtle">Buttons</div>
          <div className="mt-3 flex flex-wrap items-center gap-2.5">
            <Button variant="primary">Primary</Button>
            <Button variant="outline">Outline</Button>
            <Button variant="destructive">Destructive</Button>
            <Button variant="ghost">Ghost</Button>
            <Button disabled>Disabled</Button>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2.5">
            <Button variant="primary" size="sm">
              Small
            </Button>
            <Button size="xs">Extra small</Button>
            <Button size="icon" aria-label="Settings">
              +
            </Button>
            <Button size="icon-sm" aria-label="Add">
              +
            </Button>
          </div>
        </div>

        <div>
          <div className="t-label text-ink-subtle">Inputs</div>
          <div className="mt-3 flex flex-col gap-2.5">
            <Input font="mono" defaultValue="orders.>" />
            <Input font="mono" placeholder="placeholder" />
            <Input font="mono" defaultValue="orders..new" invalid />
            <Input defaultValue="prod-us-east" />
            <Input defaultValue="disabled" disabled />
            <Textarea rows={2} defaultValue={'{ "id": "ord_8813" }'} />
          </div>
        </div>

        <div>
          <div className="t-label text-ink-subtle">Field, select and toggle</div>
          <div className="mt-3 flex items-end gap-3">
            <Field label="Subject" hint="* one token, > the rest" className="flex-1">
              <Input font="mono" defaultValue="orders.>" />
            </Field>
            <Field label="Encode as" className="w-[180px] flex-none">
              <Select defaultValue="json">
                <option value="json">JSON</option>
                <option value="protobuf">Protobuf</option>
              </Select>
            </Field>
          </div>
          <div className="mt-3">
            <Field label="Queue group" hint="Not a valid subject token" invalid>
              <Input font="mono" defaultValue="bad group" invalid />
            </Field>
          </div>
          <div className="mt-4 flex items-center gap-4">
            <Tabs
              label="Decoder"
              value={tab}
              onChange={setTab}
              tabs={[
                { id: "auto", label: "Auto" },
                { id: "json", label: "JSON" },
                { id: "protobuf", label: "Protobuf" },
              ]}
            />
            <Toggle checked={on} onChange={setOn} label="Follow tail" />
            <Toggle checked={off} onChange={setOff} label="Pause" />
            <Toggle checked={false} onChange={() => {}} label="Disabled toggle" disabled />
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-6">
        <div>
          <div className="t-label text-ink-subtle">Badges</div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Badge tone="healthy">connected</Badge>
            <Badge tone="degraded">reconnecting</Badge>
            <Badge tone="neutral">offline</Badge>
            <Badge tone="primary">protobuf</Badge>
            <Badge tone="healthy">json</Badge>
            <Badge tone="destructive">MAX_DELIVERIES</Badge>
            <Badge tone="idle">idle</Badge>
          </div>
          <div className="mt-2.5 flex flex-wrap items-center gap-2">
            <Badge size="sm" tone="healthy">
              small
            </Badge>
            <Badge size="xs" tone="primary">
              xs
            </Badge>
          </div>
        </div>

        <div>
          <div className="t-label text-ink-subtle">Status dots and subject chips</div>
          <div className="mt-3 flex items-center gap-4">
            <div className="flex items-center gap-2">
              <StatusDot tone="healthy" size={7} label="connected" />
              <span className="text-[12.5px] text-muted-foreground">connected</span>
            </div>
            <div className="flex items-center gap-2">
              <StatusDot tone="degraded" label="reconnecting" />
              <span className="text-[12.5px] text-muted-foreground">reconnecting</span>
            </div>
            <div className="flex items-center gap-2">
              <StatusDot tone="destructive" label="error" />
              <span className="text-[12.5px] text-muted-foreground">error</span>
            </div>
            <div className="flex items-center gap-2">
              <StatusDot tone="idle" size={5} label="disconnected" />
              <span className="text-[12.5px] text-muted-foreground">disconnected</span>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <SubjectChip subject="orders.>" seen={2100} active />
            <SubjectChip subject="billing.charge.*" seen={312} />
            <SubjectChip subject="telemetry.>" tone="degraded" onClick={() => {}} />
          </div>
        </div>

        <div>
          <div className="t-label text-ink-subtle">Meters</div>
          <div className="mt-3 flex flex-col gap-2.5">
            <Meter label="ORDERS" value={0.82} tone="healthy" />
            <Meter label="TELEMETRY" value={0.94} tone="degraded" />
            <Meter label="ORDERS-DLQ" value={0.61} tone="destructive" />
            <Meter
              label="storage"
              value={ratio(316, 2048)}
              caption={`${bytes(316e9)} of ${bytes(2048e9)}`}
            />
          </div>
        </div>

        <div>
          <div className="t-label text-ink-subtle">Radii</div>
          <div className="mt-3 flex items-center gap-3">
            <div className="h-8 w-[46px] rounded-badge border border-border" />
            <div className="h-8 w-[46px] rounded-control border border-border" />
            <div className="h-8 w-[46px] rounded-card border border-border" />
            <span className="text-[11.5px] text-ink-faint">
              5px badges · 6px controls · 10px cards
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

function Provenance() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <div className="t-label text-ink-subtle">Source badges</div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {SOURCES.map((source) => (
            <SourceBadge key={source} source={source} />
          ))}
        </div>
      </div>

      <div>
        <div className="t-label text-ink-subtle">SourcedValue — the only way a number arrives</div>
        <div className="mt-3 grid grid-cols-2 gap-x-8">
          <div>
            <FactRow
              label="Round-trip"
              value={<SourcedValue sourced={known(8.4, "client")} format={millis} />}
            />
            <FactRow
              label="Stored messages"
              value={<SourcedValue sourced={known(4_213_884, "jetstream")} format={count} />}
            />
            <FactRow
              label="Connections"
              value={<SourcedValue sourced={NO_MONITOR} format={count} />}
            />
            <FactRow
              label="Messages / sec"
              value={<SourcedValue sourced={known(1204, "sampled")} format={count} />}
            />
          </div>
          <div>
            <FactRow
              label="Badge suppressed"
              value={
                <SourcedValue sourced={known(316e9, "monitor")} format={bytes} showBadge={false} />
              }
            />
            <FactRow
              label="Missing, with the fix"
              value={<SourcedValue sourced={NO_MONITOR} format={count} fix />}
            />
          </div>
        </div>
      </div>

      <div>
        <div className="t-label text-ink-subtle">Stat cards</div>
        <div className="mt-3 grid grid-cols-4 gap-3.5">
          <StatCard
            label="Servers connected"
            value="3 / 5"
            variant="sans"
            sub="one reconnecting, one offline"
          />
          <StatCard
            label="Streams"
            sourced={known(14, "jetstream")}
            format={count}
            sub="across all connected servers"
          />
          <StatCard
            label="Stored"
            sourced={known(484_000_000_000, "jetstream")}
            format={bytes}
            sub="of 4.1 TB in account limits"
          />
          <StatCard label="Connections" sourced={NO_MONITOR} format={count} />
        </div>
      </div>

      <div>
        <div className="t-label text-ink-subtle">Empty states, and our own limits</div>
        <div className="mt-3 grid grid-cols-2 gap-3.5">
          <EmptyState
            unavailable={NO_MONITOR.unavailable!}
            title="Server-wide counters need a source"
            action={
              <Button size="xs" variant="outline">
                Configure sources
              </Button>
            }
          />
          <NoRows
            title="No streams on this server"
            body="The account is reachable and has no streams. Create one, or point nats-lens at a different account."
            actionLabel="Create a stream"
            action={() => {}}
          />
        </div>
        <div className="mt-3.5 overflow-hidden rounded-card border border-border">
          <DroppedRow count={1204} since={now} />
        </div>
      </div>

      <div>
        <div className="t-label text-ink-subtle">Cards</div>
        <div className="mt-3 grid grid-cols-3 gap-3.5">
          <Card>
            <CardBody>
              <CardHeader
                title="Connection"
                description="From the server INFO block and this client."
                right={<SourceBadge source="client" />}
              />
              <div className="mt-2">
                <FactRow label="Server version" value="2.11.4" />
                <FactRow label="Cluster" value="prod-east" />
                <FactRow
                  label="Round-trip"
                  value={<SourcedValue sourced={known(8.4, "client")} format={millis} showBadge={false} />}
                />
              </div>
            </CardBody>
          </Card>
          <Card tone="healthy">
            <CardBody>
              <CardHeader
                title="Telemetry sources"
                right={<Badge tone="healthy">both</Badge>}
                description="Monitoring port and $SYS are both answering."
              />
            </CardBody>
          </Card>
          <Card tone="degraded">
            <CardBody>
              <CardHeader
                title="Telemetry sources"
                right={<Badge tone="degraded">none</Badge>}
                description="Neither the monitoring port nor a system account is configured."
              />
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Lists() {
  const [selected, setSelected] = useState<string | null>("a");
  const [filter, setFilter] = useState("");

  const columns: Column<Row>[] = [
    {
      key: "name",
      header: "Server",
      width: "1fr",
      cell: (row) => (
        <div className="flex items-center gap-2.5">
          <StatusDot tone="healthy" label="connected" />
          <span className="text-[13.5px] font-medium text-foreground">{row.name}</span>
          <Badge size="sm" tone="healthy">
            connected
          </Badge>
        </div>
      ),
    },
    {
      key: "url",
      header: "Endpoint",
      width: "244px",
      cell: (row) => (
        <Mono size="sm" truncate className="text-muted-foreground">
          {row.url}
        </Mono>
      ),
    },
    {
      key: "msgs",
      header: "Messages",
      width: "122px",
      cell: (row) => (
        <SourcedValue sourced={known(row.msgs, "jetstream")} format={compact} showBadge={false} />
      ),
    },
    {
      key: "rtt",
      header: "RTT",
      width: "68px",
      align: "right",
      cell: (row) => (
        <Mono size="md" className="text-ink-strong">
          {millis(row.rtt)}
        </Mono>
      ),
    },
  ];

  const filtered = ROWS.filter((row) => row.name.includes(filter));

  return (
    <div className="flex flex-col gap-6">
      <div>
        <div className="t-label text-ink-subtle">DataTable</div>
        <div className="mt-3">
          <DataTable
            columns={columns}
            rows={ROWS}
            rowKey={(row) => row.id}
            selectedKey={selected}
            onSelect={(row) => setSelected(row.id)}
            footnote="Throughput is sampled by polling; it needs the monitoring port or a system account. Servers without either show a dash."
          />
        </div>
      </div>

      <div>
        <div className="t-label text-ink-subtle">
          ListPane and the inspector — the split every screen uses
        </div>
        <div className="mt-3 h-[260px] overflow-hidden rounded-card border border-border">
          <Split>
            <ListPane
              title="Streams"
              width={240}
              filter={filter}
              onFilterChange={setFilter}
              placeholder="Filter streams"
              onAdd={() => {}}
              addLabel="Add a stream"
            >
              {filtered.map((row) => (
                <ListRow
                  key={row.id}
                  selected={selected === row.id}
                  onClick={() => setSelected(row.id)}
                >
                  <div className="flex items-center gap-2">
                    <Mono size="lg" className="font-medium text-foreground">
                      {row.name}
                    </Mono>
                    <span className="flex-1" />
                    <Badge size="xs">file</Badge>
                  </div>
                  <Mono size="sm" truncate className="mt-1.5 text-ink-subtle">
                    {row.url}
                  </Mono>
                  <Meter className="mt-2" value={ratio(row.msgs, 5_000_000)} caption={null} />
                </ListRow>
              ))}
              {filtered.length === 0 && (
                <div className="px-[18px] py-3 text-[11.5px] text-ink-faint">
                  Nothing matches “{filter}”.
                </div>
              )}
            </ListPane>
            <SplitMain className="overflow-y-auto p-5">
              <Section title="Selected" right={<SourceBadge source="jetstream" />}>
                <FactRow label="Name" value={selected ?? "—"} />
                <FactRow label="Last message" value={clock(now)} />
              </Section>
            </SplitMain>
          </Split>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ page

export function DesignSystem() {
  return (
    <Shell crumbs={["Design system"]}>
      <Page>
        <PageHeader
          title="Design system"
          description="Every primitive in every state, read from the same tokens the screens use. If something is not on this page, it does not exist yet."
        />

        <div className="mt-11 border-t border-border pt-7">
          <h2 className="t-section m-0">Colour</h2>
          <div className="mt-6">
            <Swatches />
          </div>
        </div>

        <div className="mt-11 border-t border-border pt-7">
          <h2 className="t-section m-0">Type</h2>
          <div className="mt-6">
            <TypeRamp />
          </div>
        </div>

        <div className="mt-11 border-t border-border pt-7">
          <h2 className="t-section m-0">Controls</h2>
          <div className="mt-1.5 text-[12.5px] text-ink-label">
            36px controls, 6px radius on inputs and buttons, 10px on cards, 1px borders, no shadows.
          </div>
          <div className="mt-6">
            <Controls />
          </div>
        </div>

        <div className="mt-11 border-t border-border pt-7">
          <h2 className="t-section m-0">Where a number came from</h2>
          <p className="mt-2 max-w-[720px] text-[12.5px] leading-[1.6] text-muted-foreground text-pretty">
            NATS does not hand every number to every caller. A badge next to a figure says which
            source produced it, so a missing source reads as a missing badge rather than a silent
            zero.
          </p>
          <div className="mt-6">
            <Provenance />
          </div>
        </div>

        <div className="mt-11 border-t border-border pt-7">
          <h2 className="t-section m-0">Lists</h2>
          <div className="mt-6">
            <Lists />
          </div>
        </div>

        <div className="mt-11 border-t border-border pt-7">
          <h2 className="t-section m-0">Rules of the house</h2>
          <div className="mt-4 max-w-[720px]">
            {RULES.map((rule, index) => (
              <div key={rule} className="flex gap-3.5 border-b border-hairline py-2.5">
                <Mono size="sm" className="flex-none text-ink-dim">
                  {String(index + 1).padStart(2, "0")}
                </Mono>
                <span className="text-[12.5px] leading-[1.55] text-muted-foreground text-pretty">
                  {rule}
                </span>
              </div>
            ))}
          </div>
        </div>
      </Page>
    </Shell>
  );
}
