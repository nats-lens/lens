/** `/healthz`, and where to go for the history nats-lens does not keep.
 *
 * Reference: the Monitor artboard, the "health" tab. The important behaviour
 * here is that a non-200 is a *row*, not an error: the endpoint answers 400 when
 * a stream probe names no account, 404 when JetStream does not know the account,
 * and 503 when a consumer is genuinely behind. All three are useful answers, and
 * a monitoring screen that threw on the interesting ones would be no use at all.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiQuery } from "@/lib/api";
import { millis } from "@/lib/format";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ErrorPanel,
  Field,
  Input,
  Mono,
  SourceBadge,
  type BadgeTone,
} from "@/components";

function statusTone(code: number): BadgeTone {
  if (code >= 200 && code < 300) return "healthy";
  if (code >= 500) return "destructive";
  return "degraded";
}

export function HealthTab({ serverId, pollMs }: { serverId: string; pollMs: number | false }) {
  // The stream and consumer probes are the ones worth aiming by hand; the rest
  // of the battery runs on every poll without asking.
  const [stream, setStream] = useState("");
  const [consumer, setConsumer] = useState("");
  const [account, setAccount] = useState("");
  const [applied, setApplied] = useState<{ stream?: string; consumer?: string; account?: string }>({});

  const health = useQuery({
    ...apiQuery("/api/servers/{server_id}/monitor/health", {
      path: { server_id: serverId },
      query: {
        stream: applied.stream || undefined,
        consumer: applied.consumer || undefined,
        account: applied.account || undefined,
      },
    }),
    refetchInterval: pollMs,
  });

  const prometheus = useQuery(
    apiQuery("/api/servers/{server_id}/monitor/prometheus", { path: { server_id: serverId } }),
  );

  if (health.isError) return <ErrorPanel error={health.error} onRetry={() => health.refetch()} />;

  const checks = health.data ?? [];

  return (
    <div className="grid grid-cols-[1fr_360px] gap-6">
      <div>
        <div className="flex items-center justify-between">
          <span className="t-card-title text-foreground">Health checks — /healthz</span>
          <SourceBadge source="monitor" />
        </div>

        <div className="mt-3 flex items-end gap-2">
          <Field label="Stream">
            <Input
              placeholder="ORDERS"
              value={stream}
              onChange={(e) => setStream(e.currentTarget.value)}
            />
          </Field>
          <Field label="Consumer">
            <Input
              placeholder="search-index"
              value={consumer}
              onChange={(e) => setConsumer(e.currentTarget.value)}
            />
          </Field>
          <Field label="Account">
            <Input
              placeholder="APP"
              value={account}
              onChange={(e) => setAccount(e.currentTarget.value)}
            />
          </Field>
          <Button
            variant="outline"
            onClick={() => setApplied({ stream, consumer, account })}
            title="Stream health also needs an account; without one the server answers 400"
          >
            Probe
          </Button>
        </div>

        <div className="mt-4 flex flex-col gap-2">
          {checks.map((check) => (
            <div
              key={check.path}
              className="flex items-start gap-3 rounded-card border border-border bg-card px-3.5 py-3"
            >
              <Badge tone={statusTone(check.status_code)} size="sm">
                {check.status_code}
              </Badge>
              <div className="min-w-0 flex-1">
                <Mono className="block truncate text-card-foreground">{check.path}</Mono>
                <p className="mt-1 text-[11.5px] leading-[1.5] text-muted-foreground text-pretty">
                  {check.error ?? check.label}
                </p>
              </div>
              <Mono className="flex-none text-ink-dim">{millis(check.latency_ms)}</Mono>
            </div>
          ))}
        </div>
      </div>

      <Card>
        <CardBody>
          <CardHeader
            title="History lives elsewhere"
            description="nats-lens polls these endpoints for the live view and keeps no time series, so there is nothing here to look back through."
          />
          {prometheus.data && (
            <div className="mt-1 flex flex-col gap-2.5">
              <div>
                <div className="t-label text-ink-dim">Exporter</div>
                <Mono className="mt-1 block break-all text-[11.5px] text-card-foreground">
                  {prometheus.data.exporter_image}
                </Mono>
              </div>
              <div>
                <div className="t-label text-ink-dim">Scrape</div>
                <Mono className="mt-1 block break-all text-[11.5px] text-card-foreground">
                  {prometheus.data.scrape_url}
                </Mono>
              </div>
              <p className="text-[11.5px] leading-[1.55] text-muted-foreground text-pretty">
                {prometheus.data.surveyor_note}
              </p>
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
