/** How numbers are written on screen.
 *
 * Every one of these returns a string for a mono slot, so they are all
 * fixed-shape: no locale that reorders digits, no unit that changes width
 * halfway down a column. The design's own examples are the test cases --
 * `4.2M`, `18 GB`, `316 GB of 2 TB`, `1,204 /s`, `8.4 ms`, `12:04:11.933`.
 *
 * Nothing here decides whether a number should be shown. That is `Sourced`'s
 * job; these only shape a value that already exists.
 */

const GROUPED = new Intl.NumberFormat("en-US");

/** 1204 -> "1,204". Exact, for counts a person may want to read digit by digit. */
export function count(n: number): string {
  return GROUPED.format(Math.round(n));
}

/** 4_200_000 -> "4.2M". For headline figures where the magnitude is the point. */
export function compact(n: number): string {
  const abs = Math.abs(n);
  if (abs < 1000) return String(Math.round(n));
  for (const [limit, suffix] of [
    [1e12, "T"],
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "k"],
  ] as const) {
    if (abs >= limit) {
      const scaled = n / limit;
      // One decimal below 10 ("4.2M"), none above ("18M") -- keeps the column
      // to a predictable width.
      return `${Math.abs(scaled) < 10 ? scaled.toFixed(1) : Math.round(scaled)}${suffix}`;
    }
  }
  return String(Math.round(n));
}

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

/** 19327352832 -> "18 GB". Binary steps, decimal-looking units, as NATS reports. */
export function bytes(n: number): string {
  if (!Number.isFinite(n)) return "—";
  let value = Math.abs(n);
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const shown = unit === 0 ? Math.round(value) : value < 10 ? value.toFixed(1) : Math.round(value);
  return `${n < 0 ? "-" : ""}${shown} ${BYTE_UNITS[unit]}`;
}

/** "316 GB of 2 TB" -- a used/limit pair. A limit of 0 in NATS means unlimited. */
export function bytesOf(used: number, limit: number): string {
  return limit > 0 ? `${bytes(used)} of ${bytes(limit)}` : `${bytes(used)} of unlimited`;
}

/** 1204 -> "1,204 /s". The space before the slash is the design's. */
export function rate(perSecond: number): string {
  return `${count(perSecond)} /s`;
}

/** 8.42 -> "8.4 ms". Sub-millisecond keeps two decimals rather than reading 0. */
export function millis(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  if (ms < 1) return `${ms.toFixed(2)} ms`;
  if (ms < 1000) return `${ms.toFixed(1)} ms`;
  return duration(ms / 1000);
}

/** 93784 seconds -> "1d 2h". Uptime, age, retention -- two units, never three. */
export function duration(seconds: number): string {
  if (!Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const parts: [number, string][] = [
    [Math.floor(s / 86400), "d"],
    [Math.floor((s % 86400) / 3600), "h"],
    [Math.floor((s % 3600) / 60), "m"],
    [s % 60, "s"],
  ];
  const shown = parts.filter(([v]) => v > 0).slice(0, 2);
  return shown.map(([v, u]) => `${v}${u}`).join(" ");
}

/** 0.94 -> "94%". Takes a fraction, not a percentage, so there is one convention. */
export function percent(fraction: number, digits = 0): string {
  if (!Number.isFinite(fraction)) return "—";
  return `${(fraction * 100).toFixed(digits)}%`;
}

/** used/limit as a fraction, clamped, with 0 for an unlimited or unknown limit. */
export function ratio(used: number, limit: number): number {
  if (!Number.isFinite(used) || !Number.isFinite(limit) || limit <= 0) return 0;
  return Math.min(1, Math.max(0, used / limit));
}

function parse(at: string | number | Date): Date {
  return at instanceof Date ? at : new Date(at);
}

/** "12:04:11.933" -- the transcript's clock. Milliseconds are load-bearing there. */
export function clock(at: string | number | Date): string {
  const d = parse(at);
  if (Number.isNaN(d.getTime())) return "—";
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`;
}

/** "2026-09-03 16:41:08" -- an absolute instant, for anything older than today. */
export function timestamp(at: string | number | Date): string {
  const d = parse(at);
  if (Number.isNaN(d.getTime())) return "—";
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}

/** "4m ago", "just now". For last-seen lines, where the gap is the information. */
export function since(at: string | number | Date, now: number = Date.now()): string {
  const d = parse(at);
  if (Number.isNaN(d.getTime())) return "—";
  const seconds = (now - d.getTime()) / 1000;
  if (seconds < 0) return "in the future";
  if (seconds < 5) return "just now";
  return `${duration(seconds)} ago`;
}

/** Bytes as the inspector's hex dump: 16 per line, offset, hex, ASCII gutter. */
export function hexdump(data: Uint8Array, maxBytes = 512): string {
  const lines: string[] = [];
  const end = Math.min(data.length, maxBytes);
  for (let i = 0; i < end; i += 16) {
    const slice = data.subarray(i, Math.min(i + 16, end));
    const hex = Array.from(slice, (b) => b.toString(16).padStart(2, "0"));
    const ascii = Array.from(slice, (b) => (b >= 0x20 && b < 0x7f ? String.fromCharCode(b) : "."));
    lines.push(
      `${i.toString(16).padStart(8, "0")}  ${hex.join(" ").padEnd(47)}  ${ascii.join("")}`,
    );
  }
  if (data.length > end) lines.push(`… ${count(data.length - end)} more bytes`);
  return lines.join("\n");
}

/** Base64 from the API to bytes, for the payload inspector. */
export function fromBase64(b64: string): Uint8Array {
  const binary = atob(b64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
  return out;
}

/** Bytes to base64, for a publish body. */
export function toBase64(data: Uint8Array): string {
  let binary = "";
  for (const byte of data) binary += String.fromCharCode(byte);
  return btoa(binary);
}
