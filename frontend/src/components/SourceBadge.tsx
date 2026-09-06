import { SOURCE_MEANING, type Source } from "@/lib/provenance";
import { cn } from "@/lib/cn";

/** The uppercase mono chip that says where a number came from.
 *
 * Every figure on every screen carries one. It is the smallest visible piece of
 * the product's central claim, so it lives here and nowhere else.
 *
 * Foundations draws it at mono 9.5px, 0.06em tracking, on the muted ground --
 * quiet enough to sit beside a number without competing with it, and identical
 * on every screen so it reads as a system rather than a label.
 */
export function SourceBadge({ source, className }: { source: Source; className?: string }) {
  return (
    <span
      title={SOURCE_MEANING[source]}
      className={cn(
        "inline-flex flex-none items-center rounded px-[5px] py-[2px]",
        "font-mono text-[9.5px] uppercase tracking-[0.06em]",
        "bg-muted text-ink-label",
        className,
      )}
    >
      {source}
    </span>
  );
}
