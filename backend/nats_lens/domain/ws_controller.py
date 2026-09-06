"""The one websocket for the whole app. OWNER: agent B5-messaging.

Read-path and control only, by design (`domain/ws.py`): a client sends `join`,
`leave` or `ping`; the server answers with whatever a joined channel publishes,
plus `joined` / `left` / `pong` / `error`. Every mutation -- subscribing,
publishing, requesting -- is HTTP, so replaying a `join` on reconnect can never
re-run a side effect.

This handler does not know what a channel *means*. `core:<server>:<uid>` carries
transcript rows from `conn.multiplex`; `advisories:<server>` carries advisory
events; `servers` (or a future `monitor:<server>` / `kv:<server>:<bucket>`)
carries whatever another domain publishes. All of that is `ChannelsPlugin`'s
fan-out -- this file only ever forwards raw bytes for whichever channels the
socket in front of it has joined, which is what lets one generic handler serve
every screen.

A raw `@websocket` handler rather than `websocket_listener`: the socket needs to
receive control frames and forward channel traffic at the same time, and
`websocket_listener` only drives one side of that.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

import msgspec
from litestar import WebSocket, websocket
from litestar.channels import ChannelsPlugin
from litestar.channels.subscriber import Subscriber
from litestar.di import NamedDependency
from litestar.exceptions import WebSocketDisconnect

from nats_lens.domain.ws import ClientFrame, Join, Joined, Leave, Left, Ping, Pong, WsError

_decode_client_frame = msgspec.json.Decoder(type=ClientFrame).decode


class _Session:
    """One socket's joined channels and the tasks pumping them.

    Scoped to a single connection's lifetime -- there is no state here that
    outlives `core_websocket`, so a reconnect starts clean and the client's own
    replay of its `join` registry is the only recovery plan, exactly as
    `domain/ws.py` promises.
    """

    def __init__(self, socket: WebSocket, channels: ChannelsPlugin) -> None:
        self._socket = socket
        self._channels = channels
        self._send_lock = asyncio.Lock()
        self._subscribers: dict[str, Subscriber] = {}
        self._pumps: dict[str, asyncio.Task[None]] = {}

    async def send(self, frame: msgspec.Struct) -> None:
        async with self._send_lock:
            with contextlib.suppress(WebSocketDisconnect):
                await self._socket.send_text(msgspec.json.encode(frame).decode())

    async def join(self, channel: str) -> None:
        if channel not in self._subscribers:
            subscriber = await self._channels.subscribe(channel)
            self._subscribers[channel] = subscriber
            self._pumps[channel] = asyncio.create_task(self._pump(channel, subscriber))
        await self.send(Joined(channel=channel))

    async def leave(self, channel: str) -> None:
        await self._drop(channel)
        await self.send(Left(channel=channel))

    async def close(self) -> None:
        for channel in list(self._subscribers):
            await self._drop(channel)

    async def _drop(self, channel: str) -> None:
        task = self._pumps.pop(channel, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        subscriber = self._subscribers.pop(channel, None)
        if subscriber is not None:
            with contextlib.suppress(Exception):
                await self._channels.unsubscribe(subscriber, channel)

    async def _pump(self, channel: str, subscriber: Subscriber) -> None:
        """Forward whatever `ChannelsPlugin` hands this channel's subscriber.

        Frames are already fully encoded `ServerFrame` JSON by the time they reach
        the backend (see `conn.multiplex._emit`), so this never decodes them --
        it just relays bytes, which is what lets this handler stay ignorant of
        every other domain's frame shapes.
        """
        async for raw in subscriber.iter_events():
            async with self._send_lock:
                with contextlib.suppress(WebSocketDisconnect):
                    await self._socket.send_text(raw.decode("utf-8"))


@websocket("/ws")
async def core_websocket(socket: WebSocket, channels: NamedDependency[ChannelsPlugin]) -> None:
    await socket.accept()
    session = _Session(socket, channels)
    try:
        while True:
            try:
                raw = await socket.receive_data(mode="text")
            except WebSocketDisconnect:
                break

            try:
                frame = _decode_client_frame(raw)
            except msgspec.DecodeError as exc:
                await session.send(WsError(detail=f"unreadable frame: {exc}", channel=None))
                continue

            if isinstance(frame, Join):
                await session.join(frame.channel)
            elif isinstance(frame, Leave):
                await session.leave(frame.channel)
            elif isinstance(frame, Ping):
                await session.send(Pong(at=datetime.now(UTC)))
    finally:
        await session.close()
