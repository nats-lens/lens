/** The right-hand panel of the Servers screen.
 *
 * Three cards when nats-lens is actually connected -- Connection, JetStream
 * account, Telemetry sources -- or one "Not connected" card with the exact
 * error nats-py raised. Which of those two panels renders is decided by one
 * thing: whether `detail.client` has a value, never by `state` alone, because
 * `state` and "can I read anything from this server" are not always the same
 * moment (a server can be `reconnecting` and still show its last-known facts).
 */
import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiKey, apiQuery, ApiError } from "@/lib/api";
import type { components } from "@/lib/api.d";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  FactRow,
  Mono,
  SourceBadge,
  StatusDot,
  hasValue,
  toneForState,
} from "@/components";
import { useServerScope } from "@/lib/useServerScope";

type ServerDetail = components["schemas"]["ServerDetail"];
type TelemetrySource = components["schemas"]["TelemetrySource"];

const CHECK_PATH = "M3.4 8.4L6.4 11.4L12.6 4.6";
const CROSS_PATH = "M4.6 4.6l6.8 6.8M11.4 4.6l-6.8 6.8";

function SourceRow({ source }: { source: TelemetrySource }) {
  const tone = !source.configured ? "idle" : source.reachable ? "healthy" : "degraded";
  const ink =
    tone === "healthy" ? "text-healthy" : tone === "degraded" ? "text-degraded" : "text-ink-dim";
  const path = tone === "healthy" ? CHECK_PATH : CROSS_PATH;
  return (
    <div className="flex items-center gap-2.5 border-b border-hairline py-2.5 last:border-b-0">
      <svg
        aria-hidden
        viewBox="0 0 16 16"
        width={13}
        height={13}
        className={`flex-none ${ink}`}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d={path} />
      </svg>
      <div className="min-w-0 flex-1">
        <div className="text-[12.5px] text-card-foreground">{source.label}</div>
        <Mono size="sm" truncate className="mt-[3px] text-ink-subtle">
          {source.detail}
        </Mono>
      </div>
    </div>
  );
}

function sourcesCta(sources: components["schemas"]["TelemetrySources"]): string {
  if (sources.tag === "none") return "Configure sources";
  return sources.system_account.configured ? "Manage sources" : "Add system account";
}

/** Two clicks, not one, for the one action here that cannot be undone. */
function ForgetButton({ onConfirm, pending }: { onConfirm: () => void; pending: boolean }) {
  const [confirming, setConfirming] = useState(false);
  if (!confirming) {
    return (
      <Button variant="ghost" size="xs" className="text-destructive" onClick={() => setConfirming(true)}>
        Forget this server
      </Button>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <span className="text-[11.5px] text-muted-foreground">Delete the registration and its credentials?</span>
      <Button variant="destructive" size="xs" disabled={pending} onClick={onConfirm}>
        {pending ? "Forgetting…" : "Forget"}
      </Button>
      <Button variant="ghost" size="xs" onClick={() => setConfirming(false)}>
        Cancel
      </Button>
    </div>
  );
}

export function DetailPanel({ serverId }: { serverId: string }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { selectServer } = useServerScope();
  const detailQuery = useQuery(apiQuery("/api/servers/{server_id}", { path: { server_id: serverId } }));

  function invalidate() {
    queryClient.invalidateQueries({ queryKey: apiKey("/api/servers") });
    queryClient.invalidateQueries({ queryKey: apiKey("/api/servers/{server_id}", { path: { server_id: serverId } }) });
  }

  const connect = useMutation({
    mutationFn: () => api.post("/api/servers/{server_id}/connect", { path: { server_id: serverId } }),
    onSuccess: invalidate,
  });
  const disconnect = useMutation({
    mutationFn: () => api.post("/api/servers/{server_id}/disconnect", { path: { server_id: serverId } }),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: () => api.delete("/api/servers/{server_id}", { path: { server_id: serverId } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: apiKey("/api/servers") });
    },
  });

  if (detailQuery.isLoading) {
    return <div className="p-5 text-[12.5px] text-ink-subtle">Loading…</div>;
  }
  if (detailQuery.isError || !detailQuery.data) {
    const message =
      detailQuery.error instanceof ApiError
        ? (detailQuery.error.problem?.detail ?? detailQuery.error.message)
        : "Could not load this server.";
    return (
      <Card tone="destructive" className="p-4">
        <div className="text-[12.5px] font-medium text-destructive">Request failed</div>
        <p className="mt-1.5 text-[11.5px] leading-[1.5] text-muted-foreground">{message}</p>
      </Card>
    );
  }

  const detail: ServerDetail = detailQuery.data;
  const online = hasValue(detail.client);
  const extraUrls = detail.urls.length - 1;

  return (
    <div>
      <div className="flex items-center gap-2.5">
        <StatusDot tone={toneForState(detail.state)} size={7} label={detail.state} />
        <span className="truncate text-[17px] font-semibold tracking-[-0.02em] text-foreground">
          {detail.name}
        </span>
        <span className="flex-1" />
        {online ? (
          <>
            <Button
              size="sm"
              onClick={() => {
                selectServer(detail.id);
                void navigate({ to: "/core" });
              }}
            >
              Open console
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={disconnect.isPending}
              onClick={() => disconnect.mutate()}
            >
              {disconnect.isPending ? "Disconnecting…" : "Disconnect"}
            </Button>
          </>
        ) : (
          <Button size="sm" disabled={connect.isPending} onClick={() => connect.mutate()}>
            {connect.isPending ? "Connecting…" : "Connect"}
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          onClick={() => navigate({ to: "/servers/$serverId/edit", params: { serverId: detail.id } })}
        >
          Edit
        </Button>
      </div>

      <Mono size="sm" className="mt-1.5 block break-all text-ink-subtle">
        {detail.urls[0]}
        {extraUrls > 0 && <span className="text-ink-faint"> +{extraUrls} more</span>}
      </Mono>

      {connect.isError && (
        <p className="mt-2 text-[11.5px] text-destructive">
          {connect.error instanceof ApiError ? connect.error.problem?.detail : "Could not connect."}
        </p>
      )}

      {online ? (
        <div className="mt-4 flex flex-col gap-3.5">
          <Card>
            <CardBody>
              <CardHeader
                title="Connection"
                description="From the server INFO block and this client."
                right={<SourceBadge source="client" />}
              />
              <div className="mt-1">
                {detail.connection_rows.map((row) => (
                  <FactRow key={row.k} label={row.k} value={<Mono>{row.v}</Mono>} />
                ))}
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardBody>
              <CardHeader
                title="JetStream account"
                description="jsm.account_info() — always available to a client with JetStream permissions."
                right={<SourceBadge source="jetstream" />}
              />
              <div className="mt-1">
                {hasValue(detail.jetstream) ? (
                  detail.jetstream_rows.map((row) => (
                    <FactRow key={row.k} label={row.k} value={<Mono>{row.v}</Mono>} />
                  ))
                ) : (
                  <EmptyState className="mt-2" unavailable={detail.jetstream.unavailable!} />
                )}
              </div>
            </CardBody>
          </Card>

          <Card tone={detail.sources.tag === "none" ? "degraded" : "healthy"}>
            <CardBody>
              <CardHeader
                title="Telemetry sources"
                right={<Badge tone={detail.sources.tag === "none" ? "degraded" : "healthy"}>{detail.sources.tag}</Badge>}
              />
              <div className="mt-1">
                <SourceRow source={detail.sources.monitoring} />
                <SourceRow source={detail.sources.system_account} />
              </div>
              <p className="mt-3 text-[11.5px] leading-[1.55] text-ink-label text-pretty">
                {detail.sources.note}
              </p>
              <Button
                variant="outline"
                size="sm"
                block
                className="mt-3"
                onClick={() =>
                  navigate({ to: "/servers/$serverId/edit", params: { serverId: detail.id } })
                }
              >
                {sourcesCta(detail.sources)}
              </Button>
            </CardBody>
          </Card>
        </div>
      ) : (
        <div className="mt-4">
          <Card className="p-[18px]">
            <div className="text-[14px] font-semibold tracking-[-0.01em] text-foreground">Not connected</div>
            <p className="mt-2.5 text-[12.5px] leading-[1.55] text-muted-foreground text-pretty">
              Saved subjects, subscriptions and schema mappings are kept and come back with the
              server. Nothing on this server can be read until nats-lens opens a connection to it.
            </p>
            {detail.last_error && (
              <div className="mt-3.5 break-all rounded-control bg-background px-3 py-2.5 font-mono text-[11.5px] text-destructive">
                {detail.last_error}
              </div>
            )}
          </Card>
        </div>
      )}

      <div className="mt-4">
        <ForgetButton pending={remove.isPending} onConfirm={() => remove.mutate()} />
      </div>
    </div>
  );
}
