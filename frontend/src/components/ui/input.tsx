import { cn } from "@/lib/cn";

/** A 36px field.
 *
 * `font="mono"` for anything the server will read back verbatim -- subjects,
 * URLs, keys, durations. Sans for a display name or a note, which is interface
 * copy that happens to be typed by a person (design rule 01).
 *
 * `invalid` is a state, not a colour choice: it paints the failing border and
 * ink from Foundations, and is what a subject validator should set.
 */
export type InputProps = Omit<React.ComponentProps<"input">, "size"> & {
  font?: "sans" | "mono";
  invalid?: boolean;
};

export function Input({ className, font = "sans", invalid = false, ...props }: InputProps) {
  return (
    <input
      aria-invalid={invalid || undefined}
      className={cn(
        "h-9 w-full rounded-control border bg-card px-[11px] text-[13px] text-foreground",
        "placeholder:text-ink-faint focus-visible:outline-none",
        font === "mono" ? "font-mono tabular-nums" : "font-sans",
        invalid
          ? "border-border-invalid text-destructive focus-visible:border-destructive"
          : "border-border hover:border-border-strong focus-visible:border-primary",
        "disabled:cursor-not-allowed disabled:bg-muted disabled:text-ink-faint",
        className,
      )}
      {...props}
    />
  );
}

/** The same field, taller, for payloads and .proto sources. */
export function Textarea({
  className,
  font = "mono",
  invalid = false,
  ...props
}: Omit<React.ComponentProps<"textarea">, "size"> & { font?: "sans" | "mono"; invalid?: boolean }) {
  return (
    <textarea
      aria-invalid={invalid || undefined}
      className={cn(
        "w-full rounded-control border bg-card px-[11px] py-[9px] text-[13px] leading-[1.55]",
        "text-foreground placeholder:text-ink-faint focus-visible:outline-none",
        font === "mono" ? "font-mono" : "font-sans",
        invalid
          ? "border-border-invalid text-destructive focus-visible:border-destructive"
          : "border-border hover:border-border-strong focus-visible:border-primary",
        className,
      )}
      {...props}
    />
  );
}

/** Label, control, hint. The hint carries the reason a value is rejected, so it
 * takes the failing ink when the field does. */
export function Field({
  label,
  hint,
  invalid = false,
  htmlFor,
  className,
  children,
}: {
  label: string;
  hint?: React.ReactNode;
  invalid?: boolean;
  htmlFor?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      <label htmlFor={htmlFor} className="t-label mb-[7px] block text-muted-foreground">
        {label}
      </label>
      {children}
      {hint && (
        <div className={cn("t-caption mt-[7px]", invalid ? "text-destructive" : "text-ink-faint")}>
          {hint}
        </div>
      )}
    </div>
  );
}
