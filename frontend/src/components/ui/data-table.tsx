import { cn } from "@/lib/cn";

/** One column. `width` is a CSS grid track, so a table can pin the numeric
 * columns and let the name column take the slack -- which is how every table in
 * the design is laid out. */
export type Column<T> = {
  key: string;
  header: React.ReactNode;
  cell: (row: T) => React.ReactNode;
  /** Grid track, e.g. "1fr", "244px", "minmax(0, 1fr)". Defaults to "1fr". */
  width?: string;
  align?: "left" | "right";
};

/** The design's table: a grid, not a `<table>`.
 *
 * Rows are one grid template shared with the header, which keeps a 60px row with
 * a sparkline in it aligned without a layout pass. Selection is a one-step
 * ground lift and nothing else (design rule 05).
 *
 * Not virtualised on purpose. Registries, streams and buckets are tens of rows;
 * the one genuinely unbounded list is the transcript, which brings its own
 * windowing.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  selectedKey,
  onSelect,
  rowHeight = 60,
  empty,
  footnote,
  className,
}: {
  columns: readonly Column<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string;
  selectedKey?: string | null;
  onSelect?: (row: T) => void;
  rowHeight?: number;
  /** Shown when the server returned no rows. A *missing source* is not this --
   * that is an `EmptyState`, which names the fix. */
  empty?: React.ReactNode;
  footnote?: React.ReactNode;
  className?: string;
}) {
  const template = columns.map((c) => c.width ?? "1fr").join(" ");

  return (
    <div className={cn("min-w-0", className)}>
      <div
        role="table"
        aria-rowcount={rows.length}
        className="min-w-0"
      >
        <div
          role="row"
          className="grid gap-4 border-b border-border px-3 pb-[10px] text-[11.5px] font-medium text-ink-subtle"
          style={{ gridTemplateColumns: template }}
        >
          {columns.map((column) => (
            <div
              key={column.key}
              role="columnheader"
              className={cn("min-w-0 truncate", column.align === "right" && "text-right")}
            >
              {column.header}
            </div>
          ))}
        </div>

        {rows.map((row) => {
          const key = rowKey(row);
          const selected = selectedKey === key;
          return (
            <div
              key={key}
              role="row"
              aria-selected={onSelect ? selected : undefined}
              tabIndex={onSelect ? 0 : undefined}
              onClick={onSelect ? () => onSelect(row) : undefined}
              onKeyDown={
                onSelect
                  ? (event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onSelect(row);
                      }
                    }
                  : undefined
              }
              className={cn(
                "-mx-3 grid items-center gap-4 rounded-control border-b border-hairline px-3",
                onSelect && "cursor-pointer hover:bg-row-hover",
                selected && "bg-muted",
              )}
              style={{ gridTemplateColumns: template, height: rowHeight }}
            >
              {columns.map((column) => (
                <div
                  key={column.key}
                  role="cell"
                  className={cn("min-w-0", column.align === "right" && "text-right")}
                >
                  {column.cell(row)}
                </div>
              ))}
            </div>
          );
        })}
      </div>

      {rows.length === 0 && empty && <div className="py-6">{empty}</div>}
      {footnote && <div className="mt-3.5 t-caption text-ink-faint text-pretty">{footnote}</div>}
    </div>
  );
}
