import { cn } from "@/lib/cn";

/** The 34x20 switch from Foundations.
 *
 * A `role="switch"` button rather than a checkbox: it acts immediately, it is
 * never submitted with a form, and the label is usually to its left already.
 */
export function Toggle({
  checked,
  onChange,
  disabled = false,
  label,
  className,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "flex h-5 w-[34px] flex-none items-center rounded-[10px] p-[2px] transition-colors",
        checked ? "justify-end bg-primary" : "justify-start bg-border",
        disabled && "cursor-not-allowed opacity-60",
        className,
      )}
    >
      <span
        className={cn(
          "size-4 rounded-full",
          checked ? "bg-primary-foreground" : "bg-ink-subtle",
        )}
      />
    </button>
  );
}
