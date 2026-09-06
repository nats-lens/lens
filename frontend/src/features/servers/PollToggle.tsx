import { Button } from "@/components";

/** How often a polling screen refetches. `false` means off.
 *
 * Every polling screen in nats-lens shares this list rather than inventing its
 * own, so "5s" means the same thing everywhere a person sees it.
 */
export const POLL_OPTIONS = [
  { label: "Live off", ms: false as const },
  { label: "Poll 5s", ms: 5_000 },
  { label: "Poll 10s", ms: 10_000 },
  { label: "Poll 30s", ms: 30_000 },
] as const;

export type PollMs = (typeof POLL_OPTIONS)[number]["ms"];

/** One button that cycles the interval on click.
 *
 * There is no hidden default poll baked into a query anywhere in this feature --
 * every `refetchInterval` traces back to a `PollMs` a person chose here.
 */
export function PollToggle({
  value,
  onChange,
  className,
}: {
  value: PollMs;
  onChange: (ms: PollMs) => void;
  className?: string;
}) {
  const index = Math.max(
    0,
    POLL_OPTIONS.findIndex((o) => o.ms === value),
  );
  const next = POLL_OPTIONS[(index + 1) % POLL_OPTIONS.length] ?? POLL_OPTIONS[0];
  const current = POLL_OPTIONS[index] ?? POLL_OPTIONS[0];

  return (
    <Button
      variant="outline"
      size="xs"
      className={className}
      onClick={() => onChange(next.ms)}
      title="Click to change how often this screen refreshes itself"
    >
      {current.label}
    </Button>
  );
}
