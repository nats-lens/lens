/** Preferences, and what this build actually is.
 *
 * The sidebar has had a disabled "Settings" item since the shell was built. It
 * is a real screen now, and small on purpose: nats-lens keeps almost nothing of
 * its own -- connection settings live on each server, and everything else is
 * read live from NATS. What is left is how the app looks and what it is.
 *
 * The version and connection counts come from `/api/health` rather than being
 * written into the page, because a hardcoded version is a version that will
 * eventually be wrong. The footer used to claim "nats-py 2.12"; it had been
 * 2.15 for some time.
 */
import { useQuery } from "@tanstack/react-query";
import { apiQuery } from "@/lib/api";
import { useTheme, type ThemeSetting } from "@/lib/theme";
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  FactRow,
  Mono,
  Page,
  PageHeader,
  Shell,
} from "@/components";

const THEMES: { id: ThemeSetting; label: string; description: string }[] = [
  { id: "system", label: "System", description: "Follow the operating system, and keep following it." },
  { id: "light", label: "Light", description: "Always light, whatever the system says." },
  { id: "dark", label: "Dark", description: "Always dark. The design's own setting." },
];

/** A miniature of the app's own surfaces, drawn in the theme it names.
 *
 * More useful than a colour swatch: what matters is whether the signal colours
 * still read as signals against that ground, which a flat chip cannot show.
 */
function ThemePreview({ appearance }: { appearance: "light" | "dark" }) {
  const ground = appearance === "light" ? "#faf9f7" : "#0c0b0a";
  const card = appearance === "light" ? "#ffffff" : "#131211";
  const border = appearance === "light" ? "#e0dcd5" : "#272522";
  const text = appearance === "light" ? "#1c1a17" : "#f6f4f0";
  const muted = appearance === "light" ? "#6b645b" : "#918a80";
  const signals =
    appearance === "light"
      ? ["#2f7250", "#8a5f18", "#b0413a"]
      : ["#74c39c", "#cba97a", "#e2938c"];

  return (
    <div
      className="rounded-card border p-2.5"
      style={{ background: ground, borderColor: border }}
      aria-hidden
    >
      <div className="rounded border p-2" style={{ background: card, borderColor: border }}>
        <div className="h-1.5 w-14 rounded-full" style={{ background: text }} />
        <div className="mt-1.5 h-1 w-20 rounded-full" style={{ background: muted }} />
        <div className="mt-2.5 flex gap-1.5">
          {signals.map((c) => (
            <span key={c} className="size-2 rounded-full" style={{ background: c }} />
          ))}
        </div>
      </div>
    </div>
  );
}

export function SettingsScreen() {
  const { setting, appearance, choose } = useTheme();
  const health = useQuery(apiQuery("/api/health"));

  return (
    <Shell crumbs={["Settings"]}>
      <Page>
        <PageHeader
          title="Settings"
          description="nats-lens keeps very little of its own. Connection settings live on each server; everything else on these screens is read live from NATS."
        />

        <div className="mt-5 grid max-w-[760px] grid-cols-1 gap-3.5">
          <Card>
            <CardBody>
              <CardHeader
                title="Appearance"
                description="Stored in this browser only — it is not part of a server's configuration."
              />
              <div className="mt-4 grid grid-cols-3 gap-3">
                {THEMES.map((option) => {
                  const active = option.id === setting;
                  const preview =
                    option.id === "system" ? appearance : (option.id as "light" | "dark");
                  return (
                    <button
                      key={option.id}
                      type="button"
                      aria-pressed={active}
                      onClick={() => choose(option.id)}
                      className={
                        "rounded-card border p-3 text-left transition-colors " +
                        (active
                          ? "border-primary bg-muted"
                          : "border-border hover:bg-control-hover")
                      }
                    >
                      <ThemePreview appearance={preview} />
                      <div className="mt-2.5 flex items-center gap-2">
                        <span className="text-[12.5px] font-medium text-foreground">
                          {option.label}
                        </span>
                        {active && (
                          <Badge tone="primary" size="xs">
                            in use
                          </Badge>
                        )}
                      </div>
                      <p className="mt-1 text-[11.5px] leading-[1.45] text-muted-foreground text-pretty">
                        {option.description}
                      </p>
                    </button>
                  );
                })}
              </div>
              {setting === "system" && (
                <p className="mt-3 text-[11.5px] text-ink-dim">
                  Currently showing {appearance}. This follows the system as it changes, including
                  a scheduled switch at sunset.
                </p>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardBody>
              <CardHeader
                title="This build"
                description="Read from the running backend, so it cannot drift out of date."
              />
              <div className="mt-1">
                <FactRow
                  label="nats-lens"
                  value={<Mono size="sm">{health.data?.version ?? "…"}</Mono>}
                />
                <FactRow
                  label="Servers registered"
                  value={health.data ? String(health.data.servers_registered) : "…"}
                />
                <FactRow
                  label="Servers connected"
                  value={health.data ? String(health.data.servers_connected) : "…"}
                />
                <FactRow
                  label="Registry"
                  value={
                    health.data ? (
                      <Badge tone={health.data.database ? "healthy" : "destructive"} size="sm">
                        {health.data.database ? "reachable" : "unreachable"}
                      </Badge>
                    ) : (
                      "…"
                    )
                  }
                />
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardBody>
              <CardHeader
                title="What nats-lens stores"
                description="Worth knowing before you point it at a production cluster."
              />
              <p className="mt-1 text-[12px] leading-[1.6] text-muted-foreground text-pretty">
                Registered servers, their encrypted credentials, protobuf descriptors and subject
                rules live in a SQLite file on the machine running nats-lens. Nothing else is kept:
                no message history, no advisory log, no metrics. Every figure on every other screen
                is read live and labelled with where it came from.
              </p>
              <p className="mt-2.5 text-[12px] leading-[1.6] text-muted-foreground text-pretty">
                Credentials are encrypted with the key in NATS_LENS_SECRET_KEY, decrypted only to
                open a connection, and never sent to this browser.
              </p>
            </CardBody>
          </Card>
        </div>
      </Page>
    </Shell>
  );
}
