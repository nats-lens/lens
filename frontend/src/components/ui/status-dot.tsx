import type { components } from "@/lib/api.d";
import { cn } from "@/lib/cn";

export type Tone = "healthy" | "degraded" | "destructive" | "idle" | "primary";

/** The one place a connection state becomes a colour.
 *
 * Every screen shows connection state somewhere -- the switcher, the server
 * list, the monitor header -- and they must all agree on what amber means.
 */
export function toneForState(state: components["schemas"]["ConnectionState"]): Tone {
  switch (state) {
    case "connected":
      return "healthy";
    case "connecting":
    case "reconnecting":
      return "degraded";
    case "error":
      return "destructive";
    case "disconnected":
      return "idle";
  }
}

const FILL: Record<Tone, string> = {
  healthy: "bg-healthy",
  degraded: "bg-degraded",
  destructive: "bg-destructive",
  idle: "bg-idle",
  primary: "bg-primary",
};

/** A 5, 6 or 7px dot. No pulse and no glow -- design rule 05. */
export function StatusDot({
  tone,
  size = 6,
  label,
  className,
}: {
  tone: Tone;
  size?: 5 | 6 | 7;
  /** Read out instead of the colour, which carries the meaning for everyone else. */
  label?: string;
  className?: string;
}) {
  return (
    <span
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      style={{ width: size, height: size }}
      className={cn("flex-none rounded-full", FILL[tone], className)}
    />
  );
}
