"""Autopilot shift: the guardian works unattended for hours, replaying the test calls through the real
pipeline between live calls, grading itself, and stopping on its own when it should."""

from __future__ import annotations

import asyncio
import itertools
import time
from typing import TYPE_CHECKING

from .replay import load_scripts, run_script
from .supervisor import sup

if TYPE_CHECKING:
    from .hub import Hub

_task: asyncio.Task | None = None


def running() -> bool:
    return _task is not None and not _task.done()


def start(hub: Hub, minutes: float) -> None:
    global _task
    if running():
        return
    sup.autopilot.update(running=True, started=time.time(), minutes=minutes, calls=0, accuracy=None, stop_reason=None)
    _task = asyncio.create_task(_run(hub, minutes))


async def stop(reason: str = "stopped from the panel") -> None:
    if running():
        _task.cancel()
    if sup.autopilot["running"]:
        sup.autopilot.update(running=False, stop_reason=reason)
        await sup.note("autopilot", "info", f"Autopilot stopped: {reason}")


async def _run(hub: Hub, minutes: float) -> None:
    scripts = list(load_scripts().values())
    sup.autopilot.update(running=True, started=time.time(), minutes=minutes, calls=0, accuracy=None, stop_reason=None)
    await sup.note("autopilot", "info", f"Autopilot shift started for {minutes:g} min. I'll stop myself on budget, errors, or accuracy drift.")
    deadline = time.monotonic() + minutes * 60
    graded = correct = errors = misses_in_a_row = 0
    reason = None
    try:
        for script in itertools.cycle(scripts):
            if time.monotonic() >= deadline:
                reason = f"shift complete ({minutes:g} min)"
                break
            if sup.mode == "rules-only":
                reason = "shift budget spent; staying on live calls only (rules-only), no more practice calls"
                break
            live = hub.session
            if not live.virtual and live.state in ("ringing", "active"):
                await asyncio.sleep(2)  # a real call has the line; practice waits
                continue
            session = hub.new_session(virtual=True)
            try:
                await run_script(session, script, word_delay=0.12, turn_gap=0.8)
                errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                errors += 1
                await sup.note("stuck", "warn", f"Autopilot call failed ({type(e).__name__}); {errors} in a row")
                if errors >= 3:
                    reason = "3 failed calls in a row; stopping instead of burning budget"
                    break
                await asyncio.sleep(5)
                continue
            if hub.session is not session:
                continue  # interrupted by a live call; don't grade
            graded += 1
            ok = session.max_level == script["expected_max_level"] or (
                script["kind"] == "scam" and session.max_level >= 2 and script["expected_max_level"] == 3 and session.ended_by == "agent")
            correct += ok
            misses_in_a_row = 0 if ok else misses_in_a_row + 1
            sup.autopilot.update(calls=graded, accuracy=round(correct / graded, 3))
            if not ok:
                await sup.note("autopilot", "warn", f"Self-check miss on {script['id']}: expected level {script['expected_max_level']}, "
                                                    f"got {session.max_level}", session)
            if misses_in_a_row >= 3:
                reason = "accuracy drift: 3 self-check misses in a row; stopping for a human to look"
                break
            await asyncio.sleep(3)
    except asyncio.CancelledError:
        return
    sup.autopilot.update(running=False, stop_reason=reason)
    await sup.note("stop", "info", f"Autopilot stopped itself: {reason}. {graded} calls, accuracy "
                                   f"{(correct / graded if graded else 0):.0%}, spend ${sup.spend:.4f}.")
