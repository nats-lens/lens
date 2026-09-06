/** Registering a server: the long form, and the two probes that check it.
 *
 * Reference: the NewConnection artboard.
 *
 * The form's one genuinely interesting behaviour is that the client endpoint and
 * the monitoring endpoint are probed *independently* and reported separately.
 * They are different protocols on different ports and either can fail while the
 * other works -- collapsing them into a single "connection OK" would hide
 * exactly the case the rest of the product is built to explain.
 *
 * The monitoring URL is derived from the client host as a suggestion, not a
 * promise: NATS does not advertise its monitoring port, so the only way to know
 * is to ask, which is what Probe does.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "@tanstack/react-router";
import { api, apiPath, apiQuery } from "@/lib/api";
import { millis } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ErrorPanel,
  FactRow,
  Field,
  Input,
  Mono,
  Page,
  PageHeader,
  Select,
  Shell,
  Tabs,
  Toggle,
  type Tab,
} from "@/components";
import { useServerScope } from "@/lib/useServerScope";
import type { components } from "@/lib/api.d";

type AuthMode = components["schemas"]["AuthMode"];
type ProbeResult = components["schemas"]["ProbeResult"];
type SecretInput = components["schemas"]["SecretInput"];

const AUTH_TABS: readonly Tab<AuthMode>[] = [
  { id: "none", label: "None" },
  { id: "userpass", label: "User & password" },
  { id: "token", label: "Token" },
  { id: "creds", label: "Credentials" },
  { id: "nkey", label: "NKey" },
];

/** The label colours a server can take, from the Foundations artboard.
 *
 * Deliberately fixed rather than themed: a chosen colour is stored against the
 * server and identifies it in the switcher, so it has to mean the same thing
 * whichever theme is on. These are mid-tones that read against both grounds.
 */
const SWATCHES = ["#a6b1ee", "#74c39c", "#cba97a", "#d3a0dc", "#e2938c", "#8b8f99"] as const;

const AUTH_NOTES: Record<AuthMode, string> = {
  none: "Anonymous. Fine for a local server, and nothing else.",
  userpass: "A user and password checked by the server's own account config.",
  token: "One shared secret for the whole server. Fine for a lab, weak for production.",
  creds:
    "A .creds file carries a user JWT and its NKey seed. nats-lens holds the contents in memory and passes them to nats-py at connect time; the file is never written to disk here.",
  nkey: "The seed signs a server-issued nonce. The seed itself is never transmitted.",
};

/** The monitoring port is conventionally 8222 on the same host. A suggestion only. */
function deriveMonitoringUrl(clientUrl: string): string {
  const trimmed = clientUrl.trim();
  if (!trimmed) return "";
  try {
    const url = new URL(trimmed.includes("://") ? trimmed : `nats://${trimmed}`);
    return url.hostname ? `http://${url.hostname}:8222` : "";
  } catch {
    return "";
  }
}

function ProbeCard({ result }: { result: ProbeResult }) {
  return (
    <Card tone={result.ok ? "healthy" : "degraded"}>
      <CardBody>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div
              className={`text-[12.5px] font-medium ${result.ok ? "text-healthy" : "text-degraded"}`}
            >
              {result.title}
            </div>
            <p className="mt-1.5 text-[11.5px] leading-[1.55] text-muted-foreground text-pretty">
              {result.body}
            </p>
            {result.detail && (
              <p className="mt-1.5 text-[11.5px] leading-[1.55] text-ink-dim text-pretty">
                {result.detail}
              </p>
            )}
          </div>
          {result.latency_ms != null && (
            <Mono className="flex-none text-ink-dim">{millis(result.latency_ms)}</Mono>
          )}
        </div>
      </CardBody>
    </Card>
  );
}

export function AddServerScreen() {
  return <ServerForm />;
}

export function EditServerScreen() {
  const { serverId } = useParams({ from: "/servers/$serverId/edit" });
  return <ServerForm serverId={serverId} />;
}

/** The form, in either mode.
 *
 * Editing reuses the whole thing rather than duplicating twenty fields: the only
 * differences are where the initial values come from, whether saving POSTs or
 * PATCHes, and that stored secrets arrive as `SecretRef` -- proof one exists and
 * nothing more. A blank secret field on an edit therefore means "leave it alone",
 * not "clear it", which is the only safe reading when the value cannot be shown.
 */
export function ServerForm({ serverId }: { serverId?: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { selectServer } = useServerScope();
  const editing = Boolean(serverId);

  const config = useQuery({
    ...apiQuery("/api/servers/{server_id}/config", { path: { server_id: serverId ?? "" } }),
    enabled: editing,
  });

  const [name, setName] = useState("");
  const [group, setGroup] = useState("");
  const [colour, setColour] = useState<string>(SWATCHES[0]);
  const [urls, setUrls] = useState<string[]>([""]);

  const [monitoringUrl, setMonitoringUrl] = useState("");
  const [monitoringTouched, setMonitoringTouched] = useState(false);
  const [pollSeconds, setPollSeconds] = useState(5);

  const [authMode, setAuthMode] = useState<AuthMode>("none");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [credsPath, setCredsPath] = useState("");
  const [credsInline, setCredsInline] = useState("");
  const [nkeySeed, setNkeySeed] = useState("");
  const [userJwt, setUserJwt] = useState("");

  const [tlsEnabled, setTlsEnabled] = useState(false);
  const [tlsVerify, setTlsVerify] = useState(true);
  const [caPath, setCaPath] = useState("");
  const [certPath, setCertPath] = useState("");
  const [keyPath, setKeyPath] = useState("");

  const [systemEnabled, setSystemEnabled] = useState(false);
  const [systemUsername, setSystemUsername] = useState("");
  const [systemPassword, setSystemPassword] = useState("");

  const [clientName, setClientName] = useState("nats-lens");
  const [inboxPrefix, setInboxPrefix] = useState("_INBOX");
  const [jsDomain, setJsDomain] = useState("");
  const [maxReconnects, setMaxReconnects] = useState(-1);
  const [connectOnStartup, setConnectOnStartup] = useState(true);

  // Loaded once, keyed on the row's identity: re-running this on every refetch
  // would overwrite whatever the user is in the middle of typing.
  const [loadedId, setLoadedId] = useState<string | null>(null);
  useEffect(() => {
    const saved = config.data;
    if (!saved || loadedId === saved.id) return;
    setLoadedId(saved.id);
    setName(saved.name);
    setGroup(saved.group ?? "");
    setColour(saved.colour);
    setUrls(saved.urls.length ? [...saved.urls] : [""]);
    setMonitoringUrl(saved.monitoring_url ?? "");
    setMonitoringTouched(true);
    setPollSeconds(saved.monitoring_poll_seconds);
    setAuthMode(saved.auth_mode);
    setUsername(saved.username ?? "");
    setCredsPath(saved.creds_path ?? "");
    setTlsEnabled(saved.tls.enabled ?? false);
    setTlsVerify(saved.tls.verify ?? true);
    setCaPath(saved.tls.ca_path ?? "");
    setCertPath(saved.tls.cert_path ?? "");
    setKeyPath(saved.tls.key_path ?? "");
    setSystemEnabled(saved.system_account_enabled ?? false);
    setSystemUsername(saved.system_username ?? "");
    setClientName(saved.advanced.client_name ?? "nats-lens");
    setInboxPrefix(saved.advanced.inbox_prefix ?? "_INBOX");
    setJsDomain(saved.advanced.jetstream_domain ?? "");
    setMaxReconnects(saved.advanced.max_reconnect_attempts ?? -1);
    setConnectOnStartup(saved.connect_on_startup ?? false);
  }, [config.data, loadedId]);

  /** What is already stored for a kind, so the field can say so without showing it. */
  const storedSecret = (kind: string) =>
    config.data?.secrets.find((s) => s.kind === kind && s.is_set) ?? null;

  const cleanUrls = urls.map((u) => u.trim()).filter(Boolean);
  const derived = deriveMonitoringUrl(urls[0] ?? "");
  const effectiveMonitoring = monitoringTouched ? monitoringUrl : derived;

  const secrets = useMemo<SecretInput[]>(() => {
    const out: SecretInput[] = [];
    if (authMode === "userpass" && password) out.push({ kind: "password", value: password });
    if (authMode === "token" && token) out.push({ kind: "token", value: token });
    if (authMode === "creds" && credsInline) out.push({ kind: "creds", value: credsInline });
    if (authMode === "nkey") {
      if (nkeySeed) out.push({ kind: "nkey_seed", value: nkeySeed });
      if (userJwt) out.push({ kind: "jwt", value: userJwt });
    }
    return out;
  }, [authMode, password, token, credsInline, nkeySeed, userJwt]);

  const tls = {
    enabled: tlsEnabled,
    verify: tlsVerify,
    ca_path: caPath || null,
    cert_path: certPath || null,
    key_path: keyPath || null,
  };

  const probe = useMutation({
    mutationFn: async () =>
      api.post("/api/servers/probe", {
        body: {
          urls: cleanUrls,
          monitoring_url: effectiveMonitoring || null,
          auth_mode: authMode,
          username: username || null,
          creds_path: credsPath || null,
          secrets,
          tls,
        },
      }),
  });

  const save = useMutation({
    mutationFn: async () => {
      const body = {
          name: name.trim(),
          urls: cleanUrls,
          group: group.trim() || null,
          colour,
          auth_mode: authMode,
          username: username || null,
          creds_path: credsPath || null,
          secrets,
          tls,
          monitoring_url: effectiveMonitoring || null,
          monitoring_poll_seconds: pollSeconds,
          system_account_enabled: systemEnabled,
          system_username: systemUsername || null,
          system_creds_path: null,
          advanced: {
            client_name: clientName,
            inbox_prefix: inboxPrefix,
            jetstream_domain: jsDomain || null,
            max_reconnect_attempts: maxReconnects,
          },
        connect_on_startup: connectOnStartup,
      };
      if (serverId) {
        // A partial update: every field here is one the form actually shows, and
        // `secrets` carries only what was retyped -- an untouched password field
        // sends nothing, so the stored one survives.
        return api.patch("/api/servers/{server_id}", {
          path: { server_id: serverId },
          body,
        });
      }
      return api.post("/api/servers", { body });
    },
    onSuccess: (server) => {
      selectServer(server.id);
      void queryClient.invalidateQueries({ queryKey: apiPath("/api/servers") });
      void navigate({ to: "/" });
    },
  });

  const canSave = name.trim().length > 0 && cleanUrls.length > 0 && !save.isPending;

  /** Hint under a secret field: what is stored, and that leaving it blank keeps it. */
  const secretHint = (kind: string) => {
    const stored = storedSecret(kind);
    if (!stored) return undefined;
    return `A ${kind.replace("_", " ")} is stored${stored.hint ? ` (${stored.hint})` : ""}. Leave blank to keep it.`;
  };

  return (
    <Shell
      crumbs={["Servers", editing ? name || "Edit" : "Add a server"]}
    >
      <Page>
        <PageHeader
          title={editing ? `Edit ${name || "server"}` : "Add a server"}
          // The design's artboard says credentials stay in the OS keychain. They
          // do not: a container has no keychain, so this states where they
          // actually go. Being wrong about that would undercut the whole product.
          description="Credentials are encrypted in the nats-lens database with the key in NATS_LENS_SECRET_KEY. They are decrypted only to open a connection, and are never sent to the browser."
        />

        <div className="mt-5 grid grid-cols-[1fr_340px] items-start gap-6">
          <div className="flex flex-col gap-3.5">
            <Card>
              <CardBody>
                <CardHeader
                  title="Identity"
                  description="How this server appears in the switcher and across every screen."
                />
                <div className="mt-3 flex gap-3">
                  <Field label="Display name" className="flex-1">
                    <Input
                      placeholder="prod-us-east"
                      value={name}
                      onChange={(e) => setName(e.currentTarget.value)}
                    />
                  </Field>
                  <Field label="Group" className="w-[200px] flex-none">
                    <Input
                      placeholder="Production"
                      value={group}
                      onChange={(e) => setGroup(e.currentTarget.value)}
                    />
                  </Field>
                </div>
                <div className="mt-3">
                  <div className="t-label text-ink-dim">Label colour</div>
                  <div className="mt-2 flex gap-2.5">
                    {SWATCHES.map((c) => (
                      <button
                        key={c}
                        type="button"
                        aria-label={`Label colour ${c}`}
                        onClick={() => setColour(c)}
                        style={{ background: c }}
                        className={`size-[26px] rounded-md ${
                          colour === c ? "ring-2 ring-foreground" : ""
                        }`}
                      />
                    ))}
                  </div>
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardBody>
                <CardHeader
                  title="Client endpoint"
                  description="Where nats-py connects. Extra URLs are failover seeds, tried in order."
                />
                <div className="mt-3 flex flex-col gap-2">
                  {urls.map((url, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <Input
                        className="flex-1 font-mono"
                        placeholder="nats://nats-1.prod.us-east:4222"
                        value={url}
                        onChange={(e) => {
                          const next = [...urls];
                          next[i] = e.currentTarget.value;
                          setUrls(next);
                        }}
                      />
                      {urls.length > 1 && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setUrls(urls.filter((_, j) => j !== i))}
                        >
                          Remove
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
                <div className="mt-2.5 flex items-center gap-3">
                  <Button variant="outline" size="xs" onClick={() => setUrls([...urls, ""])}>
                    Add URL
                  </Button>
                  <span className="text-[11.5px] text-ink-dim">
                    nats:// · tls:// · ws:// · wss://
                  </span>
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardBody>
                <CardHeader
                  title="Monitoring endpoint"
                  description="Server-wide counters, connections, routes and health are HTTP only. NATS does not advertise this port, so the URL below is a guess from the client host until it is probed."
                />
                <div className="mt-3 flex gap-3">
                  <Field label="Monitoring URL" className="flex-1">
                    <Input
                      className="font-mono"
                      placeholder={derived || "http://host:8222"}
                      value={effectiveMonitoring}
                      onChange={(e) => {
                        setMonitoringTouched(true);
                        setMonitoringUrl(e.currentTarget.value);
                      }}
                    />
                  </Field>
                  <Field label="Poll every" className="w-[140px] flex-none">
                    <Select
                      value={String(pollSeconds)}
                      onChange={(e) => setPollSeconds(Number(e.currentTarget.value))}
                    >
                      {[2, 5, 10, 30, 60].map((s) => (
                        <option key={s} value={s}>
                          {s}s
                        </option>
                      ))}
                    </Select>
                  </Field>
                </div>
                <div className="mt-3 flex items-center gap-3">
                  <Toggle checked={systemEnabled} onChange={setSystemEnabled} label="Use a system account" />
                  <div>
                    <div className="text-[12.5px] text-foreground">Use a system account</div>
                    <div className="text-[11.5px] text-ink-dim">
                      $SYS gives connect and disconnect events as they happen, rather than on the
                      next poll.
                    </div>
                  </div>
                </div>
                {systemEnabled && (
                  <div className="mt-3 flex gap-3">
                    <Field label="$SYS user" className="flex-1">
                      <Input
                        value={systemUsername}
                        onChange={(e) => setSystemUsername(e.currentTarget.value)}
                      />
                    </Field>
                    <Field label="$SYS password" className="flex-1">
                      <Input
                        type="password"
                        value={systemPassword}
                        onChange={(e) => setSystemPassword(e.currentTarget.value)}
                      />
                    </Field>
                  </div>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardBody>
                <CardHeader title="Authentication" />
                <Tabs className="mt-3" tabs={AUTH_TABS} value={authMode} onChange={setAuthMode} />
                <div className="mt-3.5">
                  {authMode === "userpass" && (
                    <div className="flex gap-3">
                      <Field label="User" className="flex-1">
                        <Input value={username} onChange={(e) => setUsername(e.currentTarget.value)} />
                      </Field>
                      <Field label="Password" className="flex-1" hint={secretHint("password")}>
                        <Input
                          type="password"
                          placeholder={storedSecret("password") ? "unchanged" : ""}
                          value={password}
                          onChange={(e) => setPassword(e.currentTarget.value)}
                        />
                      </Field>
                    </div>
                  )}
                  {authMode === "token" && (
                    <Field label="Token" hint={secretHint("token")}>
                      <Input
                        type="password"
                        placeholder={storedSecret("token") ? "unchanged" : ""}
                        value={token}
                        onChange={(e) => setToken(e.currentTarget.value)}
                      />
                    </Field>
                  )}
                  {authMode === "creds" && (
                    <div className="flex flex-col gap-3">
                      <Field
                        label="Credentials file path"
                        hint="Mounted into the container. Leave blank to paste the contents instead."
                      >
                        <Input
                          className="font-mono"
                          placeholder="/creds/orders-console.creds"
                          value={credsPath}
                          onChange={(e) => setCredsPath(e.currentTarget.value)}
                        />
                      </Field>
                      <Field label="Or paste the .creds contents" hint={secretHint("creds")}>
                        <textarea
                          rows={4}
                          className="w-full rounded-input border border-input bg-card px-2.5 py-2 font-mono text-[12px] text-foreground"
                          placeholder="-----BEGIN NATS USER JWT-----"
                          value={credsInline}
                          onChange={(e) => setCredsInline(e.currentTarget.value)}
                        />
                      </Field>
                    </div>
                  )}
                  {authMode === "nkey" && (
                    <div className="flex flex-col gap-3">
                      <Field label="NKey seed" hint={secretHint("nkey_seed")}>
                        <Input
                          type="password"
                          className="font-mono"
                          placeholder={storedSecret("nkey_seed") ? "unchanged" : "SUAF..."}
                          value={nkeySeed}
                          onChange={(e) => setNkeySeed(e.currentTarget.value)}
                        />
                      </Field>
                      <Field label="User JWT" hint="Optional — leave blank for seed-only auth.">
                        <Input
                          className="font-mono"
                          value={userJwt}
                          onChange={(e) => setUserJwt(e.currentTarget.value)}
                        />
                      </Field>
                    </div>
                  )}
                  <p className="mt-3 text-[11.5px] leading-[1.55] text-muted-foreground text-pretty">
                    {AUTH_NOTES[authMode]}
                  </p>
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardBody>
                <CardHeader title="Transport security" />
                <div className="mt-3 flex items-center gap-3">
                  <Toggle checked={tlsEnabled} onChange={setTlsEnabled} label="Enable TLS" />
                  <div className="text-[12.5px] text-foreground">
                    {tlsEnabled ? "TLS" : "Plain TCP — credentials would travel unencrypted"}
                  </div>
                </div>
                {tlsEnabled && (
                  <>
                    <div className="mt-3 flex items-center gap-3">
                      <Toggle checked={tlsVerify} onChange={setTlsVerify} label="Verify the server certificate" />
                      <div className="text-[12.5px] text-foreground">
                        Verify the server certificate
                      </div>
                    </div>
                    <div className="mt-3 flex flex-col gap-3">
                      <Field label="CA certificate">
                        <Input
                          className="font-mono"
                          value={caPath}
                          onChange={(e) => setCaPath(e.currentTarget.value)}
                        />
                      </Field>
                      <div className="flex gap-3">
                        <Field label="Client certificate" className="flex-1">
                          <Input
                            className="font-mono"
                            value={certPath}
                            onChange={(e) => setCertPath(e.currentTarget.value)}
                          />
                        </Field>
                        <Field label="Client key" className="flex-1">
                          <Input
                            className="font-mono"
                            value={keyPath}
                            onChange={(e) => setKeyPath(e.currentTarget.value)}
                          />
                        </Field>
                      </div>
                    </div>
                  </>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardBody>
                <CardHeader title="Advanced" />
                <div className="mt-3 grid grid-cols-2 gap-3">
                  <Field label="Client name">
                    <Input value={clientName} onChange={(e) => setClientName(e.currentTarget.value)} />
                  </Field>
                  <Field label="Inbox prefix">
                    <Input
                      className="font-mono"
                      value={inboxPrefix}
                      onChange={(e) => setInboxPrefix(e.currentTarget.value)}
                    />
                  </Field>
                  <Field label="JetStream domain" hint="Leave blank unless this is a leaf node.">
                    <Input value={jsDomain} onChange={(e) => setJsDomain(e.currentTarget.value)} />
                  </Field>
                  <Field label="Max reconnects" hint="-1 is unlimited.">
                    <Input
                      type="number"
                      value={String(maxReconnects)}
                      onChange={(e) => setMaxReconnects(Number(e.currentTarget.value))}
                    />
                  </Field>
                </div>
                <div className="mt-3 flex items-center gap-3">
                  <Toggle checked={connectOnStartup} onChange={setConnectOnStartup} label="Connect when nats-lens starts" />
                  <div className="text-[12.5px] text-foreground">Connect when nats-lens starts</div>
                </div>
              </CardBody>
            </Card>
          </div>

          <div className="sticky top-6 flex flex-col gap-3.5">
            <Card>
              <CardBody>
                <CardHeader title="Summary" />
                <div className="mt-1">
                  <FactRow label="Endpoint" value={<Mono>{cleanUrls[0] ?? "not set"}</Mono>} />
                  <FactRow
                    label="Failover seeds"
                    value={cleanUrls.length > 1 ? `${cleanUrls.length - 1} more` : "none"}
                  />
                  <FactRow label="Authentication" value={authMode} />
                  <FactRow
                    label="Transport"
                    value={
                      <Badge tone={tlsEnabled ? "healthy" : "degraded"} size="sm">
                        {tlsEnabled ? (tlsVerify ? "TLS · verified" : "TLS · unverified") : "plaintext"}
                      </Badge>
                    }
                  />
                  <FactRow
                    label="Monitoring"
                    value={<Mono>{effectiveMonitoring || "not set"}</Mono>}
                  />
                  <FactRow label="System account" value={systemEnabled ? "enabled" : "not set"} />
                </div>

                <div className="mt-4 flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={cleanUrls.length === 0 || probe.isPending}
                    onClick={() => probe.mutate()}
                  >
                    {probe.isPending ? "Probing…" : "Probe"}
                  </Button>
                  <Button size="sm" disabled={!canSave} onClick={() => save.mutate()}>
                    {save.isPending ? "Saving…" : editing ? "Save changes" : "Save server"}
                  </Button>
                </div>
                {save.isError && <ErrorPanel className="mt-3" error={save.error} />}
              </CardBody>
            </Card>

            {probe.isError && <ErrorPanel error={probe.error} onRetry={() => probe.mutate()} />}
            {probe.data && (
              <>
                <ProbeCard result={probe.data.client} />
                <ProbeCard result={probe.data.monitoring} />
              </>
            )}
          </div>
        </div>
      </Page>
    </Shell>
  );
}
