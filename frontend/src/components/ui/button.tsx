import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";

/** Foundations: 36px tall, 6px radius, 1px border, no shadow.
 *
 * Four intents and no more. `primary` is the one accent on the screen, so a page
 * with two primary buttons is a page that has not decided what it is for.
 */
const button = cva(
  "inline-flex flex-none items-center justify-center gap-2 rounded-control font-sans " +
    "whitespace-nowrap transition-[background-color,border-color,color] " +
    "disabled:pointer-events-none disabled:border-transparent disabled:bg-hairline " +
    "disabled:text-ink-faint [&_svg]:pointer-events-none [&_svg]:flex-none",
  {
    variants: {
      variant: {
        primary: "border-0 bg-primary font-medium text-primary-foreground hover:bg-primary-hover",
        outline:
          "border border-border bg-transparent text-ink-quiet hover:bg-control-hover hover:text-foreground",
        destructive:
          "border border-destructive-border bg-transparent text-destructive hover:bg-control-hover",
        ghost: "border-0 bg-transparent text-ink-quiet hover:bg-control-hover hover:text-foreground",
      },
      size: {
        // The design uses three heights: 36 for a form's own action, 32 beside a
        // heading, 30 in a header or toolbar.
        default: "h-9 px-[15px] text-[13px]",
        sm: "h-8 px-3 text-[12.5px]",
        xs: "h-[30px] px-[11px] text-[12px]",
        icon: "size-[30px] p-0",
        "icon-sm": "size-[26px] p-0",
      },
      block: { true: "w-full", false: "" },
    },
    defaultVariants: { variant: "outline", size: "default", block: false },
  },
);

export type ButtonProps = React.ComponentProps<"button"> & VariantProps<typeof button>;

export function Button({ className, variant, size, block, type, ...props }: ButtonProps) {
  return (
    <button
      // A button inside a form defaults to submit, which is rarely what a
      // toolbar means. Say it explicitly.
      type={type ?? "button"}
      className={cn(button({ variant, size, block }), className)}
      {...props}
    />
  );
}

export { button as buttonVariants };
