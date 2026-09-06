import { cn } from "@/lib/cn";

/** The design's field with a chevron.
 *
 * A native `<select>` on purpose: it is keyboard- and screen-reader-correct for
 * free, and the option list is the one piece of chrome the OS should own.
 */
export type SelectProps = Omit<React.ComponentProps<"select">, "size"> & {
  font?: "sans" | "mono";
};

export function Select({ className, font = "sans", children, ...props }: SelectProps) {
  return (
    <div className="relative min-w-0">
      <select
        className={cn(
          "h-9 w-full appearance-none rounded-control border border-border bg-card",
          "pl-[11px] pr-[30px] text-[13px] text-card-foreground",
          "hover:border-border-strong focus-visible:border-primary focus-visible:outline-none",
          "disabled:cursor-not-allowed disabled:text-ink-faint",
          font === "mono" ? "font-mono" : "font-sans",
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <svg
        aria-hidden
        viewBox="0 0 16 16"
        className="pointer-events-none absolute right-[10px] top-1/2 size-[11px] -translate-y-1/2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        style={{ color: "var(--color-ink-subtle)" }}
      >
        <path d="M4 6.5L8 10.5L12 6.5" />
      </svg>
    </div>
  );
}
