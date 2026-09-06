import { cn } from "@/lib/cn";

/** The list-and-inspector split every screen uses.
 *
 * Design rule 06: every list row fills the same right-hand inspector. Having the
 * split as a component is what stops a screen from inventing a second one.
 */
export function Split({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex min-h-0 flex-1", className)} {...props} />;
}

/** The right-hand side of the split: a column that owns its own scrolling. */
export function SplitMain({ className, ...props }: React.ComponentProps<"div">) {
  return <main className={cn("flex min-w-0 flex-1 flex-col", className)} {...props} />;
}

/** The left-hand list: title, an optional add button, a filter, and the rows. */
export function ListPane({
  title,
  width = 282,
  filter,
  onFilterChange,
  placeholder = "Filter",
  onAdd,
  addLabel,
  children,
  className,
}: {
  title: React.ReactNode;
  width?: number;
  filter?: string;
  onFilterChange?: (next: string) => void;
  placeholder?: string;
  onAdd?: () => void;
  /** Named for a screen reader; the button itself is a 12px plus. */
  addLabel?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      style={{ width }}
      className={cn("flex flex-none flex-col border-r border-hairline", className)}
    >
      <div className="flex-none px-[18px] pb-3 pt-5">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[15px] font-semibold tracking-[-0.015em] text-foreground">
            {title}
          </span>
          {onAdd && (
            <button
              type="button"
              onClick={onAdd}
              aria-label={addLabel ?? "Add"}
              className="flex size-[26px] flex-none items-center justify-center rounded-control border border-border text-ink-quiet hover:bg-control-hover hover:text-foreground"
            >
              <svg viewBox="0 0 16 16" className="size-3" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
                <path d="M8 3v10M3 8h10" />
              </svg>
            </button>
          )}
        </div>

        {onFilterChange && (
          <div className="mt-[11px] flex h-[30px] items-center gap-2 rounded-control border border-border px-[10px] focus-within:border-border-strong">
            <svg viewBox="0 0 16 16" className="size-3 flex-none text-ink-faint" fill="none" stroke="currentColor" strokeWidth="1.4">
              <circle cx="7" cy="7" r="4.4" />
              <path d="M10.4 10.4L14 14" />
            </svg>
            <input
              value={filter ?? ""}
              onChange={(event) => onFilterChange(event.target.value)}
              placeholder={placeholder}
              className="min-w-0 flex-1 bg-transparent text-[11.5px] text-foreground placeholder:text-ink-faint focus-visible:outline-none"
            />
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto pb-3.5">{children}</div>
    </section>
  );
}

/** A row in that list. Selection lifts the ground one step; nothing else moves. */
export function ListRow({
  selected = false,
  onClick,
  className,
  children,
}: {
  selected?: boolean;
  onClick?: () => void;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={selected || undefined}
      className={cn(
        "block w-full border-b border-hairline-soft px-[18px] py-3 text-left",
        selected ? "bg-muted" : "hover:bg-row-hover",
        className,
      )}
    >
      {children}
    </button>
  );
}
