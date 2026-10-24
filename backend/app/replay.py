"""Script replay: run a test call through the real pipeline with no audio.

Used three ways: pytest (zero delays), the panel's replay buttons (a demo backup), and the CLI:
    uv run python -m app.replay scam_bank
"""

import asyncio
import sys
from pathlib import Path

import yaml

from .call import CallSession

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def load_scripts() -> dict[str, dict]:
    out = {}
    for f in sorted(SCRIPTS_DIR.glob("*.yaml")):
        s = yaml.safe_load(f.read_text())
        out[s["id"]] = s
    return out


def script_index() -> list[dict]:
    return [{"id": s["id"], "title": s["title"], "kind": s["kind"], "expected_max_level": s["expected_max_level"]}
            for s in load_scripts().values()]


async def run_script(session: CallSession, script: dict, word_delay: float = 0.0, turn_gap: float = 0.0) -> CallSession:
    await session.dial()
    await session.answer()
    for turn in script["turns"]:
        if session.state != "active" or session.hub.session is not session:
            break  # call over, or a live call took the line
        speaker, text = next(iter(turn.items()))
        await session.say(speaker, text, word_delay=word_delay)
        await session.drain(include_finalize=False)
        if turn_gap:
            await asyncio.sleep(turn_gap)
    if session.state == "active" and session.hub.session is session:
        await session.hangup("caller")
    await session.drain()
    return session


async def _cli(script_id: str) -> None:
    from .hub import NullHub

    hub = NullHub()
    session = hub.new_session(virtual=True)
    orig = session.emit

    async def printing_emit(type_, **data):
        await orig(type_, **data)
        if type_ in ("transcript_final", "signals", "rule_fired", "level_change", "tool_call", "blocked_by_policy",
                     "pii_guard", "agent_thought"):
            brief = {k: v for k, v in data.items() if k not in ("signals",)}
            if type_ == "signals":
                brief["ps"] = {k: v["p"] for k, v in data["signals"].items()}
            print(f"{type_:>18}  {brief}")

    session.emit = printing_emit
    await run_script(session, load_scripts()[script_id])
    print(f"\nmax level {session.max_level}, outcome: {session.outcome}")


if __name__ == "__main__":
    asyncio.run(_cli(sys.argv[1] if len(sys.argv) > 1 else "scam_bank"))
