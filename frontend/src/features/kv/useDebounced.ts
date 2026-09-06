/** Debounces a fast-changing value (a filter input) before it drives a query.
 *
 * `KvKeyPage.note` exists because listing a bucket walks it server-side; the
 * whole point of a filter box here is to cut that work down, which a request
 * per keystroke would undo.
 */
import { useEffect, useState } from "react";

export function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}
