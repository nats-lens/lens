import { cn } from "@/lib/cn";

/** A padded, scrolling page body. The design's measure is 26/32/30.
 *
 * Screens that are a split (Core, JetStream, KV, objects, advisories) do not use
 * this -- they fill the shell edge to edge and scroll inside their panes.
 */
export function Page({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("min-h-0 flex-1 overflow-y-auto px-8 pb-[30px] pt-[26px]", className)}
      {...props}
    />
  );
}

/** Title, one paragraph of why the screen exists, and the screen's own action.
 *
 * The description is not decoration: on every artboard it is where the product
 * says what a number on this screen can and cannot mean.
 */
export function PageHeader({
  title,
  description,
  actions,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-6", className)}>
      <div className="min-w-0">
        <h1 className="t-page-title m-0 text-foreground">{title}</h1>
        {description && (
          <p className="mt-2 max-w-[620px] text-[13.5px] leading-[1.5] text-muted-foreground text-pretty">
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex flex-none items-center gap-2">{actions}</div>}
    </div>
  );
}

/** A heading with an optional right-hand slot -- usually a source badge. */
export function Section({
  title,
  right,
  children,
  className,
}: {
  title: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("min-w-0", className)}>
      <div className="flex items-center justify-between gap-3">
        <span className="t-card-title text-foreground">{title}</span>
        {right}
      </div>
      <div className="mt-3.5">{children}</div>
    </section>
  );
}
