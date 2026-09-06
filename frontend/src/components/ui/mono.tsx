import { cn } from "@/lib/cn";

/** Monospace means the server said it (design rule 01).
 *
 * Having a component for it makes the rule reviewable: a subject rendered in
 * sans is a missing `<Mono>`, not a missing class. Numbers are tabular so a
 * column of them lines up.
 */
export function Mono({
  size = "md",
  truncate = false,
  className,
  children,
  ...props
}: React.ComponentProps<"span"> & {
  size?: "xs" | "sm" | "md" | "lg" | "xl";
  truncate?: boolean;
}) {
  return (
    <span
      className={cn(
        "font-mono tabular-nums",
        size === "xs" && "text-[10.5px]",
        size === "sm" && "text-[11px]",
        size === "md" && "text-[12px]",
        size === "lg" && "text-[12.5px]",
        size === "xl" && "text-[13px]",
        truncate && "block min-w-0 overflow-hidden text-ellipsis whitespace-nowrap",
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
}

/** A headline figure: mono 500 at 20px, the last row of the Foundations ramp. */
export function Figure({ className, children, ...props }: React.ComponentProps<"div">) {
  return (
    <div className={cn("t-figure text-foreground", className)} {...props}>
      {children}
    </div>
  );
}
