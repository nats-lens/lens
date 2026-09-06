/** The live transcript's buffer.
 *
 * Messages arrive faster than React should re-render: the backend rate-caps a
 * subscription at 200/s by default, and several can be open at once. So frames
 * land in a plain array held in a ref and the component is told about them once
 * per animation frame, rather than once per message.
 *
 * The buffer is bounded. When it is full the oldest row goes -- the same
 * drop-oldest rule the server applies to its own queue, for the same reason, and
 * the count is shown rather than swallowed.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { DroppedFrame, TranscriptRow } from "@/lib/ws";

export const MAX_ROWS = 2000;

/** One line of the transcript: a message, or the gap where messages were lost.
 *
 * Drops are entries rather than a counter at the top of the screen, because
 * *when* the gap happened is the useful part -- a total tells you something went
 * wrong, a marker in position tells you what you are missing between which two
 * messages.
 */
export type Entry =
  | { kind: "msg"; key: string; row: TranscriptRow }
  | { kind: "drop"; key: string; count: number; since: string };

export type TranscriptState = {
  entries: Entry[];
  /** Discarded by nats-lens, server-side and client-side combined. */
  dropped: number;
};

export function useTranscript(paused: boolean) {
  const [state, setState] = useState<TranscriptState>({ entries: [], dropped: 0 });

  // Everything the socket has handed us since the last paint.
  const pending = useRef<Entry[]>([]);
  const pendingDrops = useRef(0);
  const seq = useRef(0);
  const frame = useRef<number | null>(null);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  const flush = useCallback(() => {
    frame.current = null;
    const incoming = pending.current;
    const drops = pendingDrops.current;
    if (incoming.length === 0 && drops === 0) return;
    pending.current = [];
    pendingDrops.current = 0;

    setState((prev) => {
      // Newest first: the design reads top-down, and a live tail growing
      // downward keeps the interesting row off-screen.
      const merged = [...incoming.reverse(), ...prev.entries];
      const overflow = Math.max(0, merged.length - MAX_ROWS);
      return {
        entries: overflow ? merged.slice(0, MAX_ROWS) : merged,
        // Rows pushed out of the buffer are lost to the reader just as surely
        // as ones the server discarded, so they are counted the same way.
        dropped: prev.dropped + drops + overflow,
      };
    });
  }, []);

  const schedule = useCallback(() => {
    if (frame.current !== null) return;
    frame.current = requestAnimationFrame(flush);
  }, [flush]);

  const onMessage = useCallback(
    (row: TranscriptRow) => {
      // Paused means "stop the screen moving", not "stop listening": the
      // subscription stays open server-side, so unpausing does not lose the
      // interval. What arrives while paused is simply not shown.
      if (pausedRef.current) return;
      pending.current.push({ kind: "msg", key: row.capture_id, row });
      schedule();
    },
    [schedule],
  );

  const onDropped = useCallback(
    (frameIn: DroppedFrame) => {
      pendingDrops.current += frameIn.count;
      // Placed in the stream, not just counted: the marker sits between the
      // messages either side of the gap.
      seq.current += 1;
      pending.current.push({
        kind: "drop",
        key: `drop-${seq.current}`,
        count: frameIn.count,
        since: frameIn.since,
      });
      schedule();
    },
    [schedule],
  );

  const clear = useCallback(() => {
    pending.current = [];
    pendingDrops.current = 0;
    setState({ entries: [], dropped: 0 });
  }, []);

  useEffect(
    () => () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    },
    [],
  );

  return { ...state, onMessage, onDropped, clear };
}
