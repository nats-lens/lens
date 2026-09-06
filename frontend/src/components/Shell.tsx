import { useEffect, useRef, useState } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { cn } from "@/lib/cn";
import { useQuery } from "@tanstack/react-query";
import { apiQuery } from "@/lib/api";
import { useServerScope } from "@/lib/useServerScope";
import { useTheme } from "@/lib/theme";
import { StatusDot, toneForState } from "./ui/status-dot";
import type { components } from "@/lib/api.d";

/** The shell: 240px sidebar, 56px header, the rest is the screen.
 *
 * The measurements are the design canvas's, not approximations of it -- a 32px
 * nav item, a 46px switcher, an 8px gutter either side of the nav. They are
 * written out here so the next four agents inherit the proportions rather than
 * re-deriving them per screen.
 */

/** The design's own icon paths, so the sidebar is the sidebar that was drawn. */
const ICONS = {
  servers: "M2.6 3.4h10.8v3.4H2.6zM2.6 9.2h10.8v3.4H2.6zM4.6 5.1h.01M4.6 10.9h.01",
  core: "M2.5 5.5h9L9 3M13.5 10.5h-9L7 13",
  jetstream: "M8 2L14 5L8 8L2 5zM2 8.5L8 11.5L14 8.5M2 11.5L8 14.5L14 11.5",
  kv: "M9.5 6.5a2.6 2.6 0 1 0-2.6 2.6L6 10l-1 1 1 1 1-1 1 1 1.4-1.4-1-1 1.2-1.2z",
  obj: "M8 2L14 5.2v5.6L8 14L2 10.8V5.2zM2 5.2L8 8.4L14 5.2M8 8.4V14",
  schemas:
    "M6.2 2.5H3.6v11h8.8V6.2L8.7 2.5H6.2zM8.7 2.5v3.7h3.7M5.6 9h4.8M5.6 11.2h3.2",
  advisory:
    "M8 2.4a3.9 3.9 0 0 0-3.9 3.9v2.9l-1.3 2.1h10.4l-1.3-2.1V6.3A3.9 3.9 0 0 0 8 2.4zM6.6 13.4a1.5 1.5 0 0 0 2.8 0",
  monitor: "M1.5 8.5h3L6 4.5L8.5 12L10.5 8.5h4",
  settings:
    "M8 10.2a2.2 2.2 0 1 0 0-4.4a2.2 2.2 0 0 0 0 4.4zM8 1.6v1.6M8 12.8v1.6M14.4 8h-1.6M3.2 8H1.6M12.5 3.5l-1.1 1.1M4.6 11.4l-1.1 1.1M12.5 12.5l-1.1-1.1M4.6 4.6L3.5 3.5",
} as const;

type IconKey = keyof typeof ICONS;

function Icon({ name, className }: { name: IconKey; className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 16 16"
      className={cn("size-[15px] flex-none", className)}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={ICONS[name]} />
    </svg>
  );
}

/** The routes the shell links to. Kept as a literal union so a typo is a build
 * error and `Link` can check the target. */
export type NavPath =
  | "/"
  | "/core"
  | "/jetstream"
  | "/kv"
  | "/objects"
  | "/schemas"
  | "/advisories"
  | "/monitor";

export type NavKey =
  | "servers"
  | "core"
  | "jetstream"
  | "kv"
  | "obj"
  | "schemas"
  | "advisory"
  | "monitor";

/** Every nav item is a real destination. A nav that can render a control which
 * does nothing is a nav that will grow another one. */
type NavItem = { key: NavKey; to: NavPath; label: string; icon: IconKey };

const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "Platform",
    items: [
      { key: "servers", to: "/", label: "Servers", icon: "servers" },
      { key: "core", to: "/core", label: "Core", icon: "core" },
      { key: "jetstream", to: "/jetstream", label: "JetStream", icon: "jetstream" },
      { key: "kv", to: "/kv", label: "Key–Value", icon: "kv" },
      { key: "obj", to: "/objects", label: "Object store", icon: "obj" },
    ],
  },
  {
    group: "Tools",
    items: [
      { key: "schemas", to: "/schemas", label: "Schemas", icon: "schemas" },
      { key: "advisory", to: "/advisories", label: "Advisories", icon: "advisory" },
      { key: "monitor", to: "/monitor", label: "Monitor", icon: "monitor" },
    ],
  },
];

/** What the switcher needs to know. A server that is registered but not
 * connected still appears here -- with its real state, not a hopeful dot. */
export type ShellServer = {
  id: string;
  name: string;
  url: string;
  state: components["schemas"]["ConnectionState"];
};

const NAV_ITEM = "mx-2 mb-[2px] flex h-8 items-center gap-2.5 rounded-control px-2.5 text-[13px]";

function Wordmark() {
  return (
    <div className="flex h-14 flex-none items-center gap-[9px] border-b border-hairline px-4">
      {/* The same file the favicon and the manifest use, so the tab, the
          installed icon and the sidebar cannot drift apart. It carries its own
          ground, so it needs no tinting in either theme. */}
      <img
        src="/logo.png"
        alt=""
        width={20}
        height={20}
        className="size-[20px] flex-none rounded-[5px]"
      />
      <span className="text-[14px] font-semibold tracking-[-0.015em]">nats-lens</span>
    </div>
  );
}

/** The server switcher: a real menu, not a shortcut.
 *
 * It reads the registry itself rather than taking it from every screen, because
 * every screen was already calling `useServerScope` only to hand the same two
 * values back down. TanStack Query dedupes the read, so this costs one request
 * for the whole app.
 *
 * With no server registered it is not an empty control: it says so and offers
 * the one action that fixes it -- the same rule the source badges follow.
 */
function ServerSwitcher() {
  const { servers, serverId, selectServer, shellServer } = useServerScope();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  // A menu that stays open when you click past it is a menu you have to fight.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const shape =
    "flex h-[46px] w-full items-center gap-2.5 rounded-switcher border border-border px-2.5 text-left hover:bg-control-hover";

  return (
    <div ref={root} className="relative flex-none px-3 pb-1.5 pt-3">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={shape}
      >
        <StatusDot
          tone={shellServer ? toneForState(shellServer.state) : "idle"}
          size={7}
          label={shellServer ? shellServer.state : "no server selected"}
        />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-medium text-foreground">
            {shellServer ? shellServer.name : "No server selected"}
          </div>
          <div className="mt-[2px] truncate font-mono text-[10.5px] text-ink-subtle">
            {shellServer ? shellServer.url : "Add a server to begin"}
          </div>
        </div>
        <svg
          aria-hidden
          viewBox="0 0 16 16"
          className="size-[13px] flex-none text-ink-subtle"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
        >
          <path d="M5 6.5L8 3.5L11 6.5M5 9.5L8 12.5L11 9.5" />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          className="absolute left-3 right-3 top-[calc(100%-2px)] z-50 overflow-hidden rounded-switcher border border-border bg-card"
        >
          {/* First, always: the way out of an empty or wrong registry. */}
          <Link
            to="/servers/new"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="flex h-10 items-center gap-2.5 border-b border-hairline px-2.5 text-[13px] text-muted-foreground hover:bg-control-hover hover:text-foreground"
          >
            <svg
              aria-hidden
              viewBox="0 0 16 16"
              className="size-[13px] flex-none"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            >
              <path d="M8 3.5v9M3.5 8h9" />
            </svg>
            Add a server
          </Link>

          {servers.length === 0 ? (
            <div className="px-2.5 py-3 text-[11.5px] text-ink-subtle">
              No servers registered yet.
            </div>
          ) : (
            <div className="max-h-[280px] overflow-y-auto py-1">
              {servers.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    selectServer(s.id);
                    setOpen(false);
                  }}
                  className={cn(
                    "flex w-full items-center gap-2.5 px-2.5 py-1.5 text-left hover:bg-control-hover",
                    s.id === serverId && "bg-muted",
                  )}
                >
                  <StatusDot tone={toneForState(s.state)} size={7} label={s.state} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[12.5px] text-foreground">{s.name}</div>
                    <div className="truncate font-mono text-[10.5px] text-ink-subtle">
                      {s.primary_url}
                    </div>
                  </div>
                  {s.id === serverId && (
                    <svg
                      aria-hidden
                      viewBox="0 0 16 16"
                      className="size-[12px] flex-none text-primary"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M3.4 8.4L6.4 11.4L12.6 4.6" />
                    </svg>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function Sidebar() {
  // Selected rather than taken whole: the bare hook re-renders the shell on
  // every router state change, and the sidebar only cares about the path.
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const health = useQuery(apiQuery("/api/health"));
  const version = health.data?.version;
  // Read here rather than handed down by a screen: the sidebar outlives every
  // screen now, and TanStack Query dedupes this with the switcher's own read.
  const { servers } = useServerScope();
  const counts: Partial<Record<NavKey, number | null>> = { servers: servers.length || null };

  return (
    <aside className="flex w-sidebar flex-none flex-col border-r border-hairline bg-sidebar">
      <Wordmark />
      <ServerSwitcher />

      <nav className="min-h-0 flex-1 overflow-y-auto">
        {NAV.map((section) => (
          <div key={section.group}>
            <div className="px-[18px] pb-1.5 pt-3 text-[11px] font-medium text-ink-subtle">
              {section.group}
            </div>
            {section.items.map((item) => {
              // "/" would otherwise match every route.
              const active =
                item.to === "/" ? pathname === "/" : pathname.startsWith(item.to);
              const count = counts[item.key];
              return (
                <Link
                  key={item.key}
                  to={item.to}
                  className={cn(
                    NAV_ITEM,
                    active
                      ? "bg-control-hover font-medium text-foreground [&>svg]:text-primary"
                      : "text-ink-quiet hover:bg-muted [&>svg]:text-ink-subtle",
                  )}
                >
                  <Icon name={item.icon} />
                  <span className="flex-1">{item.label}</span>
                  {count !== null && count !== undefined && (
                    <span className="font-mono text-[10.5px] tabular-nums text-ink-faint">
                      {count}
                    </span>
                  )}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="flex-none border-t border-hairline px-2 pb-3 pt-2.5">
        <Link
          to="/settings"
          className={cn(
            NAV_ITEM,
            "mx-0",
            pathname === "/settings"
              ? "bg-muted text-foreground"
              : "text-muted-foreground hover:bg-control-hover hover:text-foreground",
          )}
        >
          <Icon name="settings" />
          <span className="flex-1">Settings</span>
        </Link>
        {/* Read from the backend rather than written here: the previous footer
            claimed "nats-py 2.12" long after it had become 2.15. */}
        <div className="px-[18px] pt-2 font-mono text-[10.5px] text-ink-dim">
          {version ? `nats-lens ${version}` : "nats-lens"}
        </div>
      </div>
    </aside>
  );
}

function Breadcrumb({ crumbs }: { crumbs: readonly React.ReactNode[] }) {
  return (
    <div className="flex min-w-0 items-center gap-[9px]">
      {crumbs.map((crumb, index) => (
        <span key={index} className="flex items-center gap-[9px]">
          {index > 0 && <span className="text-ink-dimmest">/</span>}
          <span
            className={cn(
              "text-[13px]",
              index === crumbs.length - 1 ? "text-foreground" : "text-ink-quiet",
            )}
          >
            {crumb}
          </span>
        </span>
      ))}
    </div>
  );
}

/** Cycle the appearance from the header.
 *
 * A shortcut, not the setting: it moves between light and dark and leaves
 * `system` alone, because silently unsticking "follow the system" from a header
 * click would be surprising. Settings is where the three-way choice lives.
 */
function ThemeToggle() {
  const { setting, appearance, choose } = useTheme();
  const next = appearance === "dark" ? "light" : "dark";

  return (
    <button
      type="button"
      onClick={() => choose(next)}
      title={
        setting === "system"
          ? `Following the system (${appearance}). Switch to ${next}.`
          : `Switch to ${next}`
      }
      aria-label={`Switch to ${next} theme`}
      className="flex size-[30px] flex-none items-center justify-center rounded-control border border-border text-ink-subtle hover:bg-control-hover hover:text-foreground"
    >
      <svg
        viewBox="0 0 16 16"
        className="size-[14px]"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        {appearance === "dark" ? (
          // Showing dark: offer the sun.
          <>
            <circle cx="8" cy="8" r="3.1" />
            <path d="M8 1.5v1.6M8 12.9v1.6M14.5 8h-1.6M3.1 8H1.5M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1M12.6 12.6l-1.1-1.1M4.5 4.5L3.4 3.4" />
          </>
        ) : (
          // Showing light: offer the moon.
          <path d="M13.5 9.4A5.9 5.9 0 0 1 6.6 2.5a5.9 5.9 0 1 0 6.9 6.9z" />
        )}
      </svg>
    </button>
  );
}

export function Header({
  crumbs,
  actions,
}: {
  crumbs: readonly React.ReactNode[];
  actions?: React.ReactNode;
}) {
  return (
    <header className="flex h-14 flex-none items-center gap-3 border-b border-hairline pl-6 pr-5">
      <Breadcrumb crumbs={crumbs} />
      <div className="flex-1" />
      {actions}
      <ThemeToggle />
      {/* A disabled search box and a disabled settings gear used to sit here.
          Neither was ever wired to anything, and a control that looks live and
          does nothing is the one thing this product cannot afford. Settings has
          a real home in the sidebar. */}
    </header>
  );
}

/** One screen's chrome: its header, and the screen itself.
 *
 * Deliberately *not* the sidebar. That lives in the root route (`AppFrame`) and
 * survives navigation -- when every screen rendered its own, moving between two
 * of them unmounted the whole frame and remounted it, which read as the page
 * reloading and threw away the registry and health reads on every click.
 */
export function Shell({
  crumbs,
  actions,
  children,
}: {
  crumbs: readonly React.ReactNode[];
  /** Screen-level buttons, at the right of the header. */
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <>
      <Header crumbs={crumbs} actions={actions} />
      {children}
    </>
  );
}

/** The frame the root route renders once: sidebar, then whichever screen is up.
 *
 * The `Suspense` sits here rather than around each route so a lazy chunk blanks
 * only the content column. Around the route it blanked the sidebar too, which is
 * what made a click feel like a reload.
 */
export function AppFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">{children}</div>
    </div>
  );
}
