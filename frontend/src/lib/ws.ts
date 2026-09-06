/** The single websocket.
 *
 * Mirrors `backend/nats_lens/domain/ws.py`. Client frames are tagged with `op`,
 * server frames with `t`. The socket is read-path and control only: a
 * subscription is *created* over HTTP, which returns a channel name, and the
 * socket is only ever asked to *join* it. That is what makes reconnecting
 * blind safe -- replaying the registry cannot re-run a side effect, because
 * joining has none.
 *
 * One socket for the whole app. Channels are multiplexed over it and the local
 * registry is the reconnect plan: whatever is joined now is re-joined on the
 * next open, in one batch.
 */

import type { components } from "./api.d";

type Schemas = components["schemas"];

/** A transcript row.
 *
 * The one type here that is not generated. `TranscriptRow` is reachable only
 * through the websocket union, and websockets are not part of an OpenAPI
 * document, so `openapi-typescript` never sees it. Kept in step with
 * `domain/core/schemas.py::TranscriptRow` by hand; if the socket frames ever
 * gain an HTTP mirror, delete this and read it from `components["schemas"]`.
 */
export interface TranscriptRow {
  capture_id: string;
  seq: number;
  at: string;
  direction: Schemas["Direction"];
  subject: string;
  reply: string | null;
  size: number;
  headers_count: number;
  codec: string;
  /** One line, already decoded, at most 120 characters. */
  preview: string;
  truncated: boolean;
}

// ------------------------------------------------------------------ frames

export type ClientFrame =
  | { op: "join"; channel: string }
  | { op: "leave"; channel: string }
  | { op: "ping"; at: string | null };

export type JoinedFrame = { t: "joined"; channel: string };
export type LeftFrame = { t: "left"; channel: string };
export type MessageFrame = { t: "msg"; channel: string; row: TranscriptRow };
/** nats-lens fell behind and discarded messages. Never swallowed: see `onDropped`. */
export type DroppedFrame = { t: "dropped"; channel: string; count: number; since: string };
export type StatusFrame = {
  t: "status";
  server_id: string;
  state: Schemas["ConnectionState"];
  detail: string | null;
  rtt_ms: number | null;
};
export type AdvisoryFrame = { t: "advisory"; channel: string; event: Schemas["AdvisoryEvent"] };
export type MonitorFrame = {
  t: "monitor";
  channel: string;
  varz: Schemas["VarzSummary"];
  rates: Schemas["RateSample"] | null;
};
export type KvFrame = {
  t: "kv";
  channel: string;
  bucket: string;
  key: string;
  revision: number;
  operation: string;
};
export type ErrorFrame = { t: "error"; detail: string; channel: string | null };
export type PongFrame = { t: "pong"; at: string };

export type ServerFrame =
  | JoinedFrame
  | LeftFrame
  | MessageFrame
  | DroppedFrame
  | StatusFrame
  | AdvisoryFrame
  | MonitorFrame
  | KvFrame
  | ErrorFrame
  | PongFrame;

// ------------------------------------------------------------------ handlers

export type ChannelHandlers = {
  onMessage?: (row: TranscriptRow, frame: MessageFrame) => void;
  onAdvisory?: (frame: AdvisoryFrame) => void;
  onMonitor?: (frame: MonitorFrame) => void;
  onKv?: (frame: KvFrame) => void;
  /** Messages nats-lens discarded to keep up. Show them; never count them out. */
  onDropped?: (frame: DroppedFrame) => void;
  onJoined?: (frame: JoinedFrame) => void;
  onLeft?: (frame: LeftFrame) => void;
  onError?: (frame: ErrorFrame) => void;
  /** Everything for this channel, in arrival order, after the specific hooks. */
  onFrame?: (frame: ServerFrame) => void;
};

export type SocketState = "idle" | "connecting" | "open" | "reconnecting" | "closed";

export type WsOptions = {
  url?: string;
  /** First retry delay in ms; doubles up to `maxBackoffMs`, with full jitter. */
  backoffMs?: number;
  maxBackoffMs?: number;
  /** How often to send `ping`. A missed `pong` is treated as a dead socket. */
  heartbeatMs?: number;
};

function defaultUrl(): string {
  // Same origin as the API: one process serves both in production, and Vite
  // proxies /ws in dev.
  if (typeof window === "undefined") return "ws://localhost:8000/ws";
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/ws`;
}

export class WsClient {
  private socket: WebSocket | null = null;
  private state: SocketState = "idle";

  /** The reconnect plan. Every joined channel and who is listening to it. */
  private readonly channels = new Map<string, Set<ChannelHandlers>>();
  private readonly stateListeners = new Set<(state: SocketState) => void>();
  private readonly statusListeners = new Set<(frame: StatusFrame) => void>();
  private readonly droppedListeners = new Set<(frame: DroppedFrame) => void>();
  private readonly errorListeners = new Set<(frame: ErrorFrame) => void>();

  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private awaitingPong = false;
  /** Set by `close()`, so a deliberate teardown does not reconnect. */
  private stopped = false;

  private readonly url: string;
  private readonly backoffMs: number;
  private readonly maxBackoffMs: number;
  private readonly heartbeatMs: number;

  constructor(options: WsOptions = {}) {
    this.url = options.url ?? defaultUrl();
    this.backoffMs = options.backoffMs ?? 500;
    this.maxBackoffMs = options.maxBackoffMs ?? 15_000;
    this.heartbeatMs = options.heartbeatMs ?? 20_000;

    // A laptop coming out of sleep should not wait out the backoff.
    if (typeof window !== "undefined") {
      window.addEventListener("online", () => {
        if (!this.stopped && this.state !== "open") this.reconnectNow();
      });
    }
  }

  getState(): SocketState {
    return this.state;
  }

  /** Fires on every transition, so the header can show what the socket is doing. */
  onStateChange(listener: (state: SocketState) => void): () => void {
    this.stateListeners.add(listener);
    listener(this.state);
    return () => this.stateListeners.delete(listener);
  }

  /** Server connection state pushed from nats-py's callbacks, not polled. */
  onStatus(listener: (frame: StatusFrame) => void): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  /** A last-resort listener for drops on any channel.
   *
   * The same honesty rule as the source badges, turned on our own limits: if a
   * screen does not claim its drops, this fires so they are still reported
   * somewhere rather than quietly discarded twice. */
  onDropped(listener: (frame: DroppedFrame) => void): () => void {
    this.droppedListeners.add(listener);
    return () => this.droppedListeners.delete(listener);
  }

  onError(listener: (frame: ErrorFrame) => void): () => void {
    this.errorListeners.add(listener);
    return () => this.errorListeners.delete(listener);
  }

  /** Join a channel that was already created over HTTP.
   *
   * Returns the leave. The channel stays joined while anyone still holds a
   * handler for it, so two panels watching one subscription do not fight. */
  join(channel: string, handlers: ChannelHandlers = {}): () => void {
    const existing = this.channels.get(channel);
    if (existing) {
      existing.add(handlers);
    } else {
      this.channels.set(channel, new Set([handlers]));
      this.send({ op: "join", channel });
    }
    this.connect();

    let released = false;
    return () => {
      if (released) return;
      released = true;
      const listeners = this.channels.get(channel);
      if (!listeners) return;
      listeners.delete(handlers);
      if (listeners.size === 0) {
        this.channels.delete(channel);
        this.send({ op: "leave", channel });
      }
    };
  }

  connect(): void {
    this.stopped = false;
    if (this.socket && (this.state === "open" || this.state === "connecting")) return;
    this.open();
  }

  /** Deliberate teardown. The registry is kept, so `connect()` restores the app. */
  close(): void {
    this.stopped = true;
    this.clearTimers();
    this.socket?.close(1000, "client closed");
    this.socket = null;
    this.setState("closed");
  }

  // ---------------------------------------------------------------- internals

  private open(): void {
    this.setState(this.attempt === 0 ? "connecting" : "reconnecting");
    let socket: WebSocket;
    try {
      socket = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.setState("open");
      // The registry is the reconnect plan: re-join everything that was joined
      // before the socket dropped, in one pass.
      for (const channel of this.channels.keys()) this.send({ op: "join", channel });
      this.startHeartbeat();
    };

    socket.onmessage = (event: MessageEvent<string>) => {
      let frame: ServerFrame;
      try {
        frame = JSON.parse(event.data) as ServerFrame;
      } catch {
        // A frame we cannot parse is a bug on one side or the other. Say so
        // rather than dropping it on the floor.
        this.emitError({ t: "error", detail: "unparseable frame from server", channel: null });
        return;
      }
      this.dispatch(frame);
    };

    socket.onerror = () => {
      // `onclose` always follows, and carries the reason; nothing to do here.
    };

    socket.onclose = () => {
      this.clearTimers();
      this.socket = null;
      if (this.stopped) return;
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return;
    // Exponential with full jitter: a server restart brings every open tab back
    // at once otherwise.
    const ceiling = Math.min(this.maxBackoffMs, this.backoffMs * 2 ** this.attempt);
    const delay = Math.random() * ceiling;
    this.attempt += 1;
    this.setState("reconnecting");
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.open();
    }, delay);
  }

  private reconnectNow(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.attempt = 0;
    this.open();
  }

  private startHeartbeat(): void {
    if (this.heartbeatMs <= 0) return;
    this.awaitingPong = false;
    this.heartbeatTimer = setInterval(() => {
      if (this.awaitingPong) {
        // A socket that is open but not answering is worse than a closed one,
        // because nothing else will notice. Force the reconnect.
        this.socket?.close(4000, "no pong");
        return;
      }
      this.awaitingPong = true;
      this.send({ op: "ping", at: new Date().toISOString() });
    }, this.heartbeatMs);
  }

  private clearTimers(): void {
    if (this.heartbeatTimer !== null) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private setState(state: SocketState): void {
    if (this.state === state) return;
    this.state = state;
    for (const listener of this.stateListeners) listener(state);
  }

  /** Fire and forget. A frame sent while the socket is down is not queued: the
   * only frames we send are joins and leaves, and the registry replays those on
   * the next open anyway. */
  private send(frame: ClientFrame): void {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(frame));
    }
  }

  private emitError(frame: ErrorFrame): void {
    for (const listener of this.errorListeners) listener(frame);
  }

  private dispatch(frame: ServerFrame): void {
    if (frame.t === "pong") {
      this.awaitingPong = false;
      return;
    }
    if (frame.t === "status") {
      for (const listener of this.statusListeners) listener(frame);
      return;
    }
    if (frame.t === "error" && frame.channel === null) {
      this.emitError(frame);
      return;
    }

    // Everything left in the union carries a channel; only `error` may null it.
    const channel: string | null = frame.channel;
    const listeners = channel === null ? undefined : this.channels.get(channel);

    if (frame.t === "dropped") {
      // Drops are surfaced even if the channel has gone away: they are the one
      // frame that must never be lost twice.
      let claimed = false;
      for (const handlers of listeners ?? []) {
        if (handlers.onDropped) {
          handlers.onDropped(frame);
          claimed = true;
        }
        handlers.onFrame?.(frame);
      }
      if (!claimed) for (const listener of this.droppedListeners) listener(frame);
      return;
    }

    for (const handlers of listeners ?? []) {
      switch (frame.t) {
        case "msg":
          handlers.onMessage?.(frame.row, frame);
          break;
        case "advisory":
          handlers.onAdvisory?.(frame);
          break;
        case "monitor":
          handlers.onMonitor?.(frame);
          break;
        case "kv":
          handlers.onKv?.(frame);
          break;
        case "joined":
          handlers.onJoined?.(frame);
          break;
        case "left":
          handlers.onLeft?.(frame);
          break;
        case "error":
          handlers.onError?.(frame);
          break;
      }
      handlers.onFrame?.(frame);
    }

    if (frame.t === "error" && !listeners) this.emitError(frame);
  }
}

/** The app's one socket. Screens join channels on it; nobody opens a second. */
export const ws = new WsClient();
