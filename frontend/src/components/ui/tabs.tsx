import { cn } from "@/lib/cn";

/** The pill tabs from Foundations: a 3px inset track, 28px items, 6px radius.
 *
 * Controlled, and generic over the tab id, so a screen's tab union stays a union
 * instead of decaying to `string`.
 */
export type Tab<T extends string> = { id: T; label: React.ReactNode; disabled?: boolean };

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  className,
  label,
}: {
  tabs: readonly Tab<T>[];
  value: T;
  onChange: (id: T) => void;
  className?: string;
  /** Names the group for a screen reader; there is more than one on some pages. */
  label?: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className={cn("inline-flex flex-none gap-[2px] rounded-[8px] bg-muted p-[3px]", className)}
    >
      {tabs.map((tab) => {
        const active = tab.id === value;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={tab.disabled}
            onClick={() => onChange(tab.id)}
            className={cn(
              "flex h-7 items-center rounded-control px-3 text-[12.5px] whitespace-nowrap",
              active
                ? "bg-tab-active font-medium text-foreground"
                : "text-muted-foreground hover:text-foreground",
              tab.disabled && "cursor-not-allowed text-ink-faint hover:text-ink-faint",
            )}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
