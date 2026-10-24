"""Connection hub: the two phone sockets, the panel sockets, and the current call."""

from __future__ import annotations

import asyncio

from fastapi import WebSocket

from .call import CallSession


class Hub:
    def __init__(self) -> None:
        self.phones: dict[str, WebSocket | None] = {"caller": None, "granny": None}
        self.panels: set[WebSocket] = set()
        self.session = CallSession(self)
        self._send_locks = {"caller": asyncio.Lock(), "granny": asyncio.Lock()}
        # audio health: frames in from each phone, frames out to each phone, send failures
        self.audio = {"in": {"caller": 0, "granny": 0}, "out": {"caller": 0, "granny": 0}, "send_errors": 0, "send_ms_max": 0.0}
        # Outgoing audio goes through a small per-phone queue drained by its own writer task, so a slow phone link
        # never stalls the other phone's receive loop (that stall is what made audio arrive in choppy bursts).
        self._audio_q: dict[str, asyncio.Queue[bytes]] = {}
        self._audio_writers: dict[str, asyncio.Task] = {}

    def new_session(self, virtual: bool = False) -> CallSession:
        self.session.close()
        self.session = CallSession(self, virtual=virtual)
        return self.session

    async def send_json(self, role: str, msg: dict) -> None:
        ws = self.phones.get(role)
        if ws is None:
            return
        try:
            async with self._send_locks[role]:
                await ws.send_json(msg)
        except Exception:  # noqa: BLE001 - a dropped phone must never break the call loop
            pass

    # Frames queued for one phone beyond this (about 1 s of 40 ms frames) mean the link is stalled: drop the oldest
    # instead of letting delay pile up. Shorter bursts go through; the phone absorbs them by playing slightly faster.
    AUDIO_QUEUE_MAX = 25

    async def send_bytes(self, role: str, data: bytes) -> None:
        if self.phones.get(role) is None:
            return
        q = self._audio_q.get(role)
        if q is None:
            q = self._audio_q[role] = asyncio.Queue()
        while q.qsize() >= self.AUDIO_QUEUE_MAX:
            q.get_nowait()
            self.audio["dropped_stale"] = self.audio.get("dropped_stale", 0) + 1
        q.put_nowait(data)
        w = self._audio_writers.get(role)
        if w is None or w.done():
            self._audio_writers[role] = asyncio.create_task(self._audio_writer(role, q))
        await asyncio.sleep(0)  # let the writer drain during a burst instead of only after the receive loop blocks

    async def _audio_writer(self, role: str, q: asyncio.Queue[bytes]) -> None:
        import time

        while True:
            data = await q.get()
            ws = self.phones.get(role)
            if ws is None:
                continue
            t0 = time.perf_counter()
            try:
                async with self._send_locks[role]:
                    await ws.send_bytes(data)
                self.audio["out"][role] += 1
            except Exception:  # noqa: BLE001 - a dropped phone must never break the call loop
                self.audio["send_errors"] += 1
            self.audio["send_ms_max"] = max(self.audio["send_ms_max"], (time.perf_counter() - t0) * 1000)

    async def broadcast(self, event: dict) -> None:
        dead = []
        for ws in list(self.panels):
            try:
                await ws.send_json(event)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.panels.discard(ws)


class NullHub(Hub):
    """For tests and offline replay: no sockets at all."""

    async def send_json(self, role: str, msg: dict) -> None:
        return

    async def send_bytes(self, role: str, data: bytes) -> None:
        return

    async def broadcast(self, event: dict) -> None:
        return
