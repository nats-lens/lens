import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";

/** A 5px chip: 1px border in the dim companion hue, ink in the signal hue.
 *
 * Design rule 02 -- green, amber and rose carry state only. A badge that is
 * merely labelling something takes `neutral`.
 */
const badge = cva("inline-flex flex-none items-center gap-1.5 border font-sans font-medium", {
  variants: {
    tone: {
      healthy: "border-healthy-border text-healthy",
      degraded: "border-degraded-border text-degraded",
      destructive: "border-destructive-border text-destructive",
      primary: "border-primary-border text-primary",
      neutral: "border-idle-border text-ink-label",
      idle: "border-idle-border text-ink-faint",
    },
    size: {
      default: "rounded-badge px-2 py-[2px] text-[11px]",
      sm: "rounded-badge px-[6px] py-[1px] text-[10.5px]",
      xs: "rounded px-[5px] py-[1px] text-[10px]",
    },
  },
  defaultVariants: { tone: "neutral", size: "default" },
});

export type BadgeTone = NonNullable<VariantProps<typeof badge>["tone"]>;

export type BadgeProps = React.ComponentProps<"span"> & VariantProps<typeof badge>;

export function Badge({ className, tone, size, ...props }: BadgeProps) {
  return <span className={cn(badge({ tone, size }), className)} {...props} />;
}

export { badge as badgeVariants };
