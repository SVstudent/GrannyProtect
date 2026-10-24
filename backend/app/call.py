"""One phone call: audio relay, the PII-guard delay line, transcripts, decisions, and the call log.

Flow per caller turn:  final transcript -> regex fast path -> policy (capped at caution)
                                        -> decision model (async, serialized) -> policy -> on-enter actions + agent
Flow per granny partial: regex -> PII guard (mute + drop buffered audio before digits reach the caller)
"""

from __future__ import annotations

import array
import asyncio
import difflib
import time
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any

from . import memory, store, verify
from .agent import runner
from .agent.tools import run_tool
from .config import settings
from .events import PanelEvent
from .policy.engine import LEVEL_NAMES, POLICY, Decision, PolicyState, evaluate, needs_verification
from .signals import regex_fast
from .signals.model import SIGNAL_NAMES, TurnSignals, model_signals, regex_signals
from .stt import refine
from .supervisor import sup
from .tts import whispers

if TYPE_CHECKING:
    from .hub import Hub

SAMPLE_RATE = 16000
BYTES_PER_SEC = SAMPLE_RATE * 2


def _rms(pcm: bytes) -> float:
    samples = array.array("h", pcm[: len(pcm) // 2 * 2])[::8]
    return (sum(x * x for x in samples) / len(samples)) ** 0.5 if samples else 0.0


class CallSession:
    def __init__(self, hub: Hub, virtual: bool = False):
        self.hub = hub
        self.virtual = virtual
        self.id = uuid.uuid4().hex[:8]
        self.state = "idle"
        self.started = time.time()
        self.policy = PolicyState()
        self.max_level = 0
        self.challenge_mode = settings.challenge_mode
        self.seq = 0
        self.events: list[dict] = []
        self.lines: list[dict] = []  # {speaker, text, utt, source}
        self.caller_turns: list[str] = []
        self.utt = {"caller": 0, "granny": 0}
        self.turn = 0
        self.last_signals: TurnSignals | None = None
        self.peak = {n: 0.0 for n in SIGNAL_NAMES} | {"scam_score": 0.0}
        self.actions: list[dict] = []
        self.credential_requested = False
        self.claimed_org: str | None = None
        self.safe_step: str = regex_fast.DEFAULT_STEP
        self.memory_matches: list[dict] = []
        self.scam_confirmed = False
        self.private_utt: int | None = None
        self.level_at_request: int | None = None
        self.alerts_sent: set[str] = set()  # which alert kinds granny has already heard
        self.redaction: dict | None = None  # the number currently being silenced
        self.cuts: list[list[float | None]] = []  # [start, end) capture-time windows the caller never hears
        self.credential_ask = ""
        self.verification: dict | None = None  # the identity check in progress (or its result)  # what the caller asked for ("verify your account number"), for card vs account
        self.family_notified = False
        self.family_alerts: set[str] = set()
        self.outcome = "in progress"
        self.ended_by: str | None = None
        self.report: dict | None = None
        # PII guard
        self.muted = False
        self.pii_trigger_utt: int | None = None
        self.last_pii_mono = 0.0
        self._unmute_task: asyncio.Task | None = None
        # whisper ducking: whisper id -> monotonic expiry
        self.pending_whispers: dict[str, float] = {}
        self.whisper_texts: dict[str, str] = {}
        self.echo_windows: dict[str, float] = {}  # un-ducked whispers: still filtered from transcripts
        self._wid = 0
        # granny -> caller delay line: (monotonic ts, kind, payload)
        self.delay_q: deque[tuple[float, str, Any]] = deque()
        self._last_fast: dict[str, str] = {}
        # hybrid STT audio per speaker since the last utterance cut
        self.audio_buf = {"caller": bytearray(), "granny": bytearray()}
        self._tasks: set[asyncio.Task] = set()
        self._decisions: asyncio.Queue = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._pump: asyncio.Task | None = None
        self._finalized = asyncio.Event()
        # supervisor bookkeeping
        self.agent_suspended_level: int | None = None
        self.agent_blocks: dict[str, int] = {}
        self.tool_seen: dict = {}
        self.whisper_times: list[float] = []
        now = time.monotonic()
        self.answered_mono = now
        self.last_speech = {"caller": 0.0, "granny": 0.0}
        self.voiced_at = {"caller": 0.0, "granny": 0.0}
        self.stt_restart_at = {"caller": 0.0, "granny": 0.0}
        self.lost_at: dict[str, float] = {}
        # server-side transcription fallback (when a phone's browser speech recognition stalls)
        self.server_stt = {"caller": False, "granny": False}
        self.seg = {"caller": bytearray(), "granny": bytearray()}
        self.seg_voiced = {"caller": 0.0, "granny": 0.0}

    # ---------- plumbing ----------
    @staticmethod
    def now_wall() -> float:
        return time.time()

    def spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def emit(self, type_: str, **data) -> None:
        self.seq += 1
        ev = PanelEvent(type=type_, seq=self.seq, call_id=self.id, data=data).model_dump()
        self.events.append(ev)
        store.add_event(self.id, ev["seq"], ev["ts"], type_, data)
        if self.hub.session is self:  # a replaced (cancelled) call must not leak events onto the panel
            await self.hub.broadcast(ev)

    async def send_caller(self, msg: dict) -> None:
        if not self.virtual:
            await self.hub.send_json("caller", msg)

    async def send_granny(self, msg: dict) -> None:
        if not self.virtual:
            await self.hub.send_json("granny", msg)

    def state_msg(self) -> dict:
        return {"type": "state", "call": self.state, "call_id": self.id, "stt_mode": settings.stt_provider,
                "audio_frame_ms": settings.audio_frame_ms}

    async def set_state(self, state: str, by: str | None = None) -> None:
        self.state = state
        await self.send_caller(self.state_msg())
        await self.send_granny(self.state_msg())
        await self.emit("call_state", state=state, by=by)

    # ---------- lifecycle ----------
    async def dial(self) -> None:
        if self.state != "idle":
            return
        store.start_call(self.id, self.started, self.virtual)
        await self.set_state("ringing", by="caller")

    async def answer(self) -> None:
        if self.state not in ("ringing", "idle"):
            return
        if self.state == "idle":
            store.start_call(self.id, self.started, self.virtual)
        self.started = time.time()
        self.answered_mono = time.monotonic()
        self._worker = self.spawn(self._decision_worker())
        if not self.virtual:
            self._pump = self.spawn(self._delay_pump())
        await self.set_state("active", by="granny")
        await self.emit("info", message=f"Providers: STT={settings.stt_provider}, signals={settings.signals_provider}, "
                                        f"agent={settings.agent_provider}, TTS={settings.tts_provider}")

    async def hangup(self, by: str, outcome: str | None = None) -> None:
        if self.state in ("ended",):
            return
        if self.state != "active":
            await self.set_state("ended", by=by)
            return
        self.ended_by = by
        self.outcome = outcome or ("hung up by caller" if by == "caller" else "hung up by granny")
        if self.max_level >= 2:
            self.outcome += " after scam alert"
        await self.send_caller({"type": "call_ended", "by": by, "message": ""})
        await self.send_granny({"type": "call_ended", "by": by, "message": ""})
        await self._end()

    async def terminate(self, reason: str) -> None:
        """Agent-initiated end of call (the terminate_call tool)."""
        if self.state == "ended":
            return
        self.ended_by = "agent"
        self.outcome = "terminated by Granny-Protect-5000"
        self.set_muted(True)
        closing = whispers.LINES["closing"]
        await self.send_caller({"type": "call_ended", "by": "agent", "message": closing,
                                "audio_b64": whispers.cached_audio(closing), "mime": "audio/mpeg"})
        by_request = self.policy.granny_hangup and not self.scam_confirmed
        if by_request:
            self.outcome = "ended at granny's request"
        end_line = whispers.LINES["end_request"] if by_request else whispers.LINES["end"]
        await run_tool(self, "whisper_to_granny", {"text": end_line, "tone": "end"}, actor="policy")
        if not by_request:
            await run_tool(self, "whisper_to_granny", {"text": whispers.safe_step_line(self.safe_step), "tone": "caution"},
                           actor="policy")
        self.spawn(self._granny_end_later())
        await self._end()

    async def _granny_end_later(self) -> None:
        await asyncio.sleep(0 if self.virtual else 5.0)
        await self.send_granny({"type": "call_ended", "by": "agent", "message": whispers.LINES["end"]})

    async def _end(self) -> None:
        self.policy.call_ended = True
        await self.set_state("ended", by=self.ended_by)
        self.spawn(self._finalize())

    async def _finalize(self) -> None:
        # Only wait for queued decisions: draining every task here would deadlock with a replay task awaiting us.
        await self._decisions.join()
        await sup.on_call_end(self)
        if self.scam_confirmed and self.ended_by == "agent" and (not self.virtual or settings.memory_include_virtual):
            store.remember_scam(self.id, time.time(), self.claimed_org, self.caller_turns)
            await sup.note("memory", "info", f"Remembered this scam script ({len(self.caller_turns)} lines) to recognise it next time", self)
        await run_tool(self, "write_incident_report", {}, actor="policy")
        if self.scam_confirmed and self.ended_by == "agent":
            await run_tool(self, "notify_family", {"stage": "ended"}, actor="policy")
        self._finalized.set()

    async def drain(self, include_finalize: bool = True) -> None:
        """Wait until all queued decisions and spawned work are done (used by replay and tests)."""
        while True:
            await self._decisions.join()
            pending = [t for t in self._tasks if t is not asyncio.current_task() and not t.done()
                       and t not in (self._worker, self._pump, self._unmute_task)
                       and (include_finalize or t.get_coro().__name__ != "_finalize")]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)
        if include_finalize and self.state == "ended":
            await self._finalized.wait()

    def close(self) -> None:
        for t in list(self._tasks):
            t.cancel()

    # ---------- audio ----------
    async def on_audio(self, speaker: str, frame: bytes) -> None:
        if self.state != "active":
            return
        if settings.stt_provider == "hybrid":
            buf = self.audio_buf[speaker]
            buf.extend(frame)
            if len(buf) > BYTES_PER_SEC * 30:  # cap one utterance at 30 s
                del buf[: len(buf) - BYTES_PER_SEC * 30]
        samples = array.array("h", frame[: len(frame) // 2 * 2])
        voiced = bool(samples) and max(abs(x) for x in samples[::8]) > 1500  # rough voice activity
        if voiced:
            self.voiced_at[speaker] = time.monotonic()
        if self.server_stt[speaker]:
            self._segment(speaker, frame, voiced)
        if speaker == "caller":
            await self.hub.send_bytes("granny", frame)
        else:
            self.delay_q.append((time.monotonic(), "audio", frame))

    # ---------- server-side transcription fallback ----------
    def browser_stt_alive(self, speaker: str) -> None:
        """The phone's own speech recognition is working again: hand transcription back to it."""
        if self.server_stt[speaker]:
            self.server_stt[speaker] = False
            self.seg[speaker].clear()
            self.spawn(sup.note("recovered", "info", f"{speaker}'s browser speech recognition is back; server transcription off", self))

    def _segment(self, speaker: str, frame: bytes, voiced: bool) -> None:
        """Cut utterances on ~0.7 s of silence and transcribe each one through TokenRouter."""
        seg = self.seg[speaker]
        now = time.monotonic()
        if voiced:
            self.seg_voiced[speaker] = now
        if voiced or seg:
            seg.extend(frame)
        long_pause = seg and not voiced and now - self.seg_voiced[speaker] > 0.7
        if long_pause or len(seg) > BYTES_PER_SEC * 12:
            pcm = bytes(seg)
            seg.clear()
            if len(pcm) > BYTES_PER_SEC * 0.5 and _rms(pcm) > 300:
                self.spawn(self._server_transcribe(speaker, pcm))

    async def _server_transcribe(self, speaker: str, pcm: bytes) -> None:
        text = await refine.transcribe(pcm, SAMPLE_RATE, self.id)
        if text and self.server_stt[speaker]:
            await self.on_partial(speaker, text)
            await self.on_final(speaker, text, source="tokenrouter")

    def ducked(self) -> bool:
        if self.virtual:
            return False
        now = time.monotonic()
        self.pending_whispers = {k: v for k, v in self.pending_whispers.items() if v > now}
        return bool(self.pending_whispers)

    def set_muted(self, value: bool, keep_tail_s: float | None = None) -> int:
        """Cut or restore granny's audio to the caller. On restore, `keep_tail_s` drops buffered audio older
        than that, so the tail of a redacted number still sitting in the delay line never leaks out."""
        self.muted = value
        self.policy.pii_guard_active = value and self.state == "active"
        dropped = 0
        if value:
            dropped = sum(1 for _, kind, _ in self.delay_q if kind == "audio")
            self.delay_q.clear()
        elif keep_tail_s is not None:
            cutoff = time.monotonic() - keep_tail_s
            while self.delay_q and self.delay_q[0][0] < cutoff:
                self.delay_q.popleft()
                dropped += 1
        return dropped

    def in_cut(self, ts: float) -> bool:
        return any(start <= ts and (end is None or ts < end) for start, end in self.cuts)

    def start_cut(self, lead_s: float) -> int:
        """Silence from `lead_s` ago (when the first digit was actually spoken, before STT reported it) onwards.
        Audio from before that still reaches the caller."""
        start = time.monotonic() - lead_s
        self.cuts.append([start, None])
        return sum(1 for ts, kind, _ in self.delay_q if kind == "audio" and ts >= start)

    def end_cut(self, tail_s: float) -> None:
        """Close the open window at `tail_s` ago (the last digit ended before STT saw the next word)."""
        if self.cuts and self.cuts[-1][1] is None:
            self.cuts[-1][1] = max(self.cuts[-1][0], time.monotonic() - tail_s)

    def protecting(self) -> bool:
        """Is this call risky enough that granny's audio needs the protection delay? (Same gate as redaction.)"""
        return self.policy.level >= 1 or self.credential_requested or self.redaction is not None

    async def _delay_pump(self) -> None:
        # An ordinary call has no added delay. The moment it turns risky, the line delay grows to PII_DELAY_MS
        # (a one-off pause, usually while the caller is talking) so redaction can act before digits get out.
        while self.state == "active":
            delay = settings.pii_delay_ms / 1000 if self.protecting() else 0.0
            now = time.monotonic()
            while self.delay_q and self.delay_q[0][0] + delay <= now:
                ts, kind, payload = self.delay_q.popleft()
                if self.in_cut(ts):
                    if kind == "audio":
                        self.hub.audio["cut_frames"] = self.hub.audio.get("cut_frames", 0) + 1
                    continue
                if self.muted or self.ducked():
                    continue
                if kind == "audio":
                    await self.hub.send_bytes("caller", payload)
                else:
                    await self.send_caller({"type": "caption", "speaker": "granny", "text": payload})
            await asyncio.sleep(0.005)  # granny->caller hop: poll often so frames leave within ~5 ms

    # ---------- whispers ----------
    async def whisper(self, text: str, tone: str = "caution", duck: bool = True) -> str:
        """Speak to granny only. `duck` holds her outgoing audio while it plays (so a speakerphone can't leak it);
        short reassurances skip that so the conversation isn't interrupted."""
        self._wid += 1
        wid = f"w{self._wid}"
        start = max([time.monotonic(), *self.pending_whispers.values(), *self.echo_windows.values()])
        until = start + 1.5 + len(text) / 13
        (self.pending_whispers if duck and settings.duck_whispers else self.echo_windows)[wid] = until
        self.whisper_texts[wid] = text.lower()
        await self.send_granny({"type": "whisper", "id": wid, "text": text, "tone": tone,
                                "audio_b64": whispers.cached_audio(text), "mime": "audio/mpeg"})
        await self.send_granny({"type": "subtitle", "speaker": "guardian", "text": text, "final": True, "utt": -self._wid,
                                "private": True, "redacted": False})
        return wid

    async def ask_caller(self, text: str) -> None:
        """The guardian speaks on the call (to the caller; granny hears it in her subtitles)."""
        self._wid += 1
        wid = f"q{self._wid}"
        self.echo_windows[wid] = time.monotonic() + 2.5 + len(text) / 13  # don't transcribe our own voice as the caller
        self.whisper_texts[wid] = text.lower()
        await self.send_caller({"type": "agent_say", "text": text, "audio_b64": whispers.cached_audio(text), "mime": "audio/mpeg"})
        sub = {"type": "subtitle", "speaker": "guardian", "text": text, "final": True, "utt": -self._wid,
               "private": False, "redacted": False}
        await self.send_caller(sub)
        await self.send_granny(sub)

    def whisper_done(self, wid: str) -> None:
        self.pending_whispers.pop(wid, None)

    def is_whisper_echo(self, text: str) -> bool:
        """While a whisper plays, granny's mic may pick it up. Drop speech that sounds like the whisper itself,
        but keep granny's own words (she may keep talking over it)."""
        now = time.monotonic()
        self.echo_windows = {k: v for k, v in self.echo_windows.items() if v > now}
        if not self.ducked() and not self.echo_windows:
            return False
        t = text.lower()
        for wid in [*self.pending_whispers, *self.echo_windows]:
            w = self.whisper_texts.get(wid, "")
            if t in w or difflib.SequenceMatcher(None, t, w[: len(t) + 20]).ratio() > 0.45:
                return True
        return False

    # ---------- transcripts ----------
    async def on_partial(self, speaker: str, text: str) -> None:
        if self.state != "active" or not text.strip():
            return
        utt = self.utt[speaker]
        self.last_speech[speaker] = time.monotonic()
        if self.is_whisper_echo(text):
            return  # a whisper leaking into a mic (e.g. both phones in one room); never act on it
        await self.emit("transcript_partial", speaker=speaker, text=text, utt=utt)
        await self._fast_path(speaker, text, utt)
        await self._subtitle(speaker, text, utt, final=False)

    async def on_final(self, speaker: str, text: str, source: str) -> None:
        if self.state != "active" or not text.strip():
            return
        if self.is_whisper_echo(text):
            await self.emit("info", message=f"Ignored a whisper echoing into the {speaker}'s mic: {text!r}")
            return
        self.last_speech[speaker] = time.monotonic()
        utt = self.utt[speaker]
        self.utt[speaker] += 1
        line = {"speaker": speaker, "text": text, "utt": utt, "source": source}
        self.lines.append(line)
        await self.emit("transcript_final", speaker=speaker, text=text, utt=utt, source=source, refined=False)
        await self._fast_path(speaker, text, utt, final=True)
        await self._subtitle(speaker, text, utt, final=True)
        if settings.stt_provider == "hybrid" and not self.virtual and source == "browser":
            pcm = bytes(self.audio_buf[speaker])
            self.audio_buf[speaker].clear()
            if len(pcm) > BYTES_PER_SEC * 0.4 and _rms(pcm) > 300:  # STT models invent words for silence
                self.spawn(self._refine(line, pcm))
        if speaker == "granny" and utt == self.private_utt:
            await self._answer_private(text)
            return
        if speaker == "caller" and self.verification and self.verification["stage"] == "asked":
            await self._judge_answer(text)
            if self.state != "active":
                return
        if speaker == "caller":
            self.turn += 1
            self.caller_turns.append(text)
            if (co := regex_fast.claimed_org(text)) and self.claimed_org in (None, "your bank", "a family member"):
                self.claimed_org, self.safe_step = co
            if not self.policy.memory_match and (m := memory.match(text, exclude_call=self.id)):
                self.policy.memory_match = True
                self.memory_matches.append(m)
                await self.emit("memory_match", similarity=m["similarity"], past_call_id=m["past_call_id"],
                                past_when_ts=m["past_when_ts"], excerpt=m["excerpt"])
            await self._fast_decision(self.turn)
            self._decisions.put_nowait(self.turn)

    async def _refine(self, line: dict, pcm: bytes) -> None:
        better = await refine.transcribe(pcm, SAMPLE_RATE, self.id)
        if better and line["text"].strip() and len(line["text"].split()) >= 3 and \
                difflib.SequenceMatcher(None, better.lower(), line["text"].lower()).ratio() < 0.35:
            return  # a refinement that shares little with what was said is more likely a mishearing than a fix
        if better and better.strip() and better.strip() != line["text"].strip():
            line["text"], line["source"] = better.strip(), "tokenrouter"
            await self.emit("transcript_final", speaker=line["speaker"], text=line["text"], utt=line["utt"],
                            source="tokenrouter", refined=True)

    async def _subtitle(self, speaker: str, text: str, utt: int, final: bool) -> None:
        """Live subtitles under both phones. Granny's page sees everything; the caller's page never sees her
        private questions to the guardian, and sees her number runs as •••• once the call is risky."""
        msg = {"type": "subtitle", "speaker": speaker, "text": text, "final": final, "utt": utt, "private": False, "redacted": False}
        if speaker == "caller":
            await self.send_caller(msg)
            await self.send_granny(msg)
            return
        private = utt == self.private_utt or bool(regex_fast.WAKE_RE.search(text))
        await self.send_granny({**msg, "private": private})
        if private:
            return
        shown, masked = regex_fast.mask_digits(text) if self.protecting() else (text, False)
        await self.send_caller({**msg, "text": shown, "redacted": masked})

    def transcript_text(self, n: int = 16) -> str:
        return "\n".join(f"{'Caller' if l['speaker'] == 'caller' else 'Granny'}: {l['text']}" for l in self.lines[-n:])

    # ---------- fast path + PII guard ----------
    async def _fast_path(self, speaker: str, text: str, utt: int, final: bool = False) -> None:
        t0 = time.perf_counter()
        hits = regex_fast.scan_granny(text) if speaker == "granny" else regex_fast.scan_caller(text)
        latency = round((time.perf_counter() - t0) * 1000, 3)
        key = f"{speaker}:{utt}"
        sig = repr(sorted((h["kind"], h["match"]) for h in hits.hits))
        if hits.hits and self._last_fast.get(key) != sig:
            self._last_fast[key] = sig
            await self.emit("fast_path", speaker=speaker, hits=hits.hits, digit_run=hits.digit_run, latency_ms=latency)
        if speaker == "caller" and "requests_credentials" in hits.kinds:
            self.credential_requested = True
            self.credential_ask = text
        if speaker == "granny":
            if utt == self.private_utt or regex_fast.WAKE_RE.search(text):
                await self._private_start(utt)
                return  # granny is talking to the guardian, not the caller
            await self._pii_guard(hits, utt, text, final)

    # ---------- granny asks the guardian ----------
    async def _private_start(self, utt: int) -> None:
        self.last_pii_mono = time.monotonic()
        if self.private_utt == utt:
            return
        self.private_utt = utt
        self.policy.pii_guard_active = True
        res = await run_tool(self, "mute_granny_to_caller", {}, actor="policy")
        await self.emit("pii_guard", action="muted", reason="granny is speaking privately to the guardian", matched="guardian",
                        attempt=self.policy.pii_attempts, dropped_frames=res.get("dropped_frames", 0))
        if self._unmute_task is None or self._unmute_task.done():
            self._unmute_task = self.spawn(self._unmute_when_quiet())

    async def _answer_private(self, text: str) -> None:
        question = regex_fast.WAKE_RE.sub("", text).strip(" ,.!?") or text
        level = self.policy.level
        org = self.claimed_org
        if regex_fast.HANGUP_INTENT_RE.search(text):
            self.level_at_request = level
            self.policy.granny_hangup = True
            await self.emit("guardian_ask", question=question, answer=whispers.LINES["ask_hangup"], action="hung_up", level=level)
            sig = self.last_signals or regex_signals(self.caller_turns or [""])
            await self._apply(evaluate(self.policy, sig, fast_path_only=True), self.turn)
            return
        if level >= 2:
            answer = whispers.LINES["ask_scam"]
        elif level == 1:
            answer = whispers.ask_unsure_line(org)
        else:
            answer = whispers.LINES["ask_ok"]
        await self.emit("guardian_ask", question=question, answer=answer, action="answered", level=level)
        await run_tool(self, "whisper_to_granny", {"text": answer, "tone": "alert" if level >= 2 else "caution"}, actor="policy")

    # ---------- number redaction: silence ONLY the account / card digits ----------
    async def _pii_guard(self, hits: regex_fast.FastHits, utt: int, text: str = "", final: bool = False) -> None:
        """While granny reads out an account or card number, the caller hears silence for exactly the digits;
        everything she says before and after goes through, so the conversation carries on normally."""
        if self.state != "active":
            return
        now = time.monotonic()
        run, ended = regex_fast.trailing_digits(text)
        r = self.redaction
        if r is not None:
            if r["utt"] == utt:
                r["digits"] = max(r["digits"], hits.digit_run)
                if run:
                    r["last_digit"] = now
                if ended or final:
                    await self._end_redaction("the number is over" if ended else "she finished speaking")
                return
            await self._end_redaction("she started a new sentence")
        if final or not run or self.muted:
            return  # a final is too late to cut anything; a private question (or hang-up) already has her mic cut
        level = self.policy.level
        if not (level >= 1 or self.credential_requested):
            return  # an ordinary call: numbers (times, prices, a doctor's address) flow freely
        kind = regex_fast.number_kind(text, self.credential_ask)
        asked = self.credential_requested
        if run < (1 if asked or kind != "number" else 3):
            return  # a bare "two" or "four" isn't an account number; three in a row in a risky call is
        new_attempt = self.pii_trigger_utt != utt
        if new_attempt:
            self.policy.pii_attempts += 1
        self.pii_trigger_utt = utt
        self.redaction = {"utt": utt, "kind": kind, "started": now, "last_digit": now, "digits": max(run, hits.digit_run)}
        self.policy.pii_guard_active = True
        res = await run_tool(self, "redact_sensitive_number", {"kind": kind}, actor="policy")
        what = {"card": "card number", "account": "account number"}.get(kind, "number")
        reason = (f"caller asked for it, and granny started reading her {what}" if asked
                  else f"granny started reading out her {what}")
        await self.emit("pii_guard", action="redacting", kind=kind, reason=reason, matched=f"{run} digit(s)",
                        attempt=self.policy.pii_attempts, dropped_frames=res.get("dropped_frames", 0))
        self.spawn(self._redaction_timeout(self.redaction))
        if new_attempt:
            await self._pii_escalate()

    async def _end_redaction(self, why: str) -> None:
        r, self.redaction = self.redaction, None
        if r is None:
            return
        self.policy.pii_guard_active = True  # unlocks end_redaction for this one call
        await run_tool(self, "end_redaction", {}, actor="policy")
        self.policy.pii_guard_active = False
        held = int((time.monotonic() - r["started"]) * 1000)
        await self.emit("pii_guard", action="redacted", kind=r["kind"], reason=why, matched="",
                        digits_hidden=r["digits"], held_ms=held, attempt=self.policy.pii_attempts, dropped_frames=0)
        if self.state == "active":
            line = whispers.LINES.get(f"redact_{r['kind']}", whispers.LINES["redact_number"])
            await run_tool(self, "whisper_to_granny", {"text": line, "tone": "pii", "duck": False}, actor="policy")

    async def _redaction_timeout(self, r: dict) -> None:
        """If she pauses mid-number (no new digit for a moment), give the line back."""
        while self.redaction is r and self.state == "active":
            await asyncio.sleep(0.2)
            if self.redaction is r and time.monotonic() - r["last_digit"] > settings.redact_pause_s:
                await self._end_redaction("she paused")

    async def _pii_escalate(self) -> None:
        if self.policy.pii_attempts >= 2 and self.last_signals is not None:
            await self._apply(evaluate(self.policy, self.last_signals), self.turn)

    async def _unmute_when_quiet(self) -> None:
        if self.virtual:
            return  # virtual calls have no audio to restore; the mute stands for the rest of the replay
        while self.state == "active" and self.muted:
            await asyncio.sleep(0.25)
            if time.monotonic() - self.last_pii_mono >= settings.pii_unmute_after_s and not self.ducked():
                await run_tool(self, "unmute_granny", {}, actor="policy")
                self.policy.pii_guard_active = False
                await self.emit("pii_guard", action="unmuted", reason="no more numbers for a few seconds",
                                matched="", attempt=self.policy.pii_attempts, dropped_frames=0)

    # ---------- decisions ----------
    async def _fast_decision(self, turn: int) -> None:
        sig = regex_signals(self.caller_turns)
        # Regex is the full authority when there is no model (mock) or the supervisor cut spending (rules-only).
        mock = settings.signals_provider == "mock" or not settings.llm_api_key or sup.mode == "rules-only"
        self._note_signals(sig)
        sig.claimed_org = self.claimed_org
        await self.emit("signals", turn=turn, **sig.model_dump())
        if mock:
            await self._maybe_verify(sig)
        await self._apply(evaluate(self.policy, sig, fast_path_only=not mock), turn)

    async def _decision_worker(self) -> None:
        while True:
            turn = await self._decisions.get()
            stale = 0
            while not self._decisions.empty():  # the model fell behind: judge only the latest turn
                self._decisions.task_done()
                turn = self._decisions.get_nowait()
                stale += 1
            try:
                if stale:
                    await sup.note("stuck", "info", f"Decision model fell behind; skipped {stale} stale turn(s) and judged the latest", self)
                if self.state == "active" and settings.signals_provider != "mock":
                    lines = [(l["speaker"], l["text"]) for l in self.lines]
                    sig = await model_signals(lines, self.id)
                    if sig is None:
                        why = "rules-only mode" if sup.mode == "rules-only" else "decision model unavailable"
                        await self.emit("info", message=f"{why}: regex signals promoted to full authority for this turn.")
                        sig = regex_signals(self.caller_turns)
                    if self.state == "active":
                        self._note_signals(sig)
                        sig.claimed_org = self.claimed_org
                        await self.emit("signals", turn=turn, **sig.model_dump())
                        await self._maybe_verify(sig)
                        await self._apply(evaluate(self.policy, sig), turn)
            except Exception as e:  # noqa: BLE001
                await self.emit("info", message=f"decision error: {e!r}")
            finally:
                self._decisions.task_done()

    def _note_signals(self, sig: TurnSignals) -> None:
        self.last_signals = sig
        for n in SIGNAL_NAMES:
            self.peak[n] = max(self.peak[n], sig.p(n))
        self.peak["scam_score"] = max(self.peak["scam_score"], sig.scam_score)
        if sig.p("requests_credentials") >= 0.6:
            self.credential_requested = True

    async def _apply(self, d: Decision, turn: int) -> None:
        if self.state != "active":
            return
        if (self.verification and self.verification["stage"] == "asked" and d.level > max(d.prev_level, 1)
                and d.rule_id not in POLICY["verification"]["hard_rules"]):
            # Asking an identity question: don't sound the alarm on a maybe-real grandchild until they answer.
            held = max(d.prev_level, 1)
            d.description += f" (held at level {held} while verifying identity)"
            d.level, d.capped = held, True
            d.actions = list(POLICY["on_enter"].get(held, [])) if held > d.prev_level else []
        await self.emit("rule_fired", turn=turn, rule_id=d.rule_id, description=d.description, level=d.level,
                        prev_level=d.prev_level, inputs=d.inputs, allowed_tools=d.allowed_tools,
                        actions=[a["tool"] + (f":{a['line']}" if a.get("line") else "") for a in d.actions])
        if not d.escalated:
            # Same level, sharper evidence: once the caller actually asks for details or money, tell granny
            # exactly that, even if a general warning already played.
            if self.policy.level == 2 and self._alert_kind() not in self.alerts_sent and self._alert_kind() != "generic":
                await run_tool(self, "whisper_to_granny", {"text": self._line("alert"), "tone": "alert"}, actor="policy")
            return
        if d.level >= 2 and d.rule_id != "R3d_granny_asked":
            self.scam_confirmed = True
        self.policy.level = d.level
        self.max_level = max(self.max_level, d.level)
        await self.emit("level_change", **{"from": d.prev_level, "to": d.level, "name": LEVEL_NAMES[d.level]})
        agent_writes = any(a.get("agent_writes") for a in d.actions)
        agent_task = self.spawn(self._agent(d, agent_writes)) if d.level < 3 else None
        for a in d.actions:
            tool = a["tool"]
            if tool == "whisper_to_granny":
                if a.get("agent_writes"):
                    continue  # the agent writes this one; _agent falls back to the fixed line
                await run_tool(self, tool, {"text": self._line(a["line"]), "tone": a["line"]}, actor="policy")
            elif tool == "notify_family":
                await run_tool(self, tool, {"summary": self.short_summary(), "stage": "alert"}, actor="policy")
            elif tool == "terminate_call":
                await run_tool(self, tool, {"reason": f"{d.rule_id}: {d.description}"}, actor="policy")
            else:
                await run_tool(self, tool, {}, actor="policy")
        if d.level >= 3:
            await runner.on_level(self, d, write_whisper=False)
        elif agent_task is not None and self.virtual:
            await agent_task

    # ---------- identity verification (only when uncertain) ----------
    async def _maybe_verify(self, sig: TurnSignals) -> None:
        verifying = bool(self.verification and self.verification["stage"] == "asked")
        reason = needs_verification(self.policy, sig, self.claimed_org, verifying or self.verification is not None)
        if not reason:
            return
        family = self.claimed_org == "a family member"
        fact = verify.pick_family_question() if family else None
        if family and not fact:
            return  # no family facts configured: fall back to the normal ladder
        question = verify.family_prompt(fact["question"]) if fact else verify.CALLBACK_QUESTION
        kind = "family" if fact else "callback"
        self.verification = {"kind": kind, "question": question, "accepted": fact["answers"] if fact else [],
                             "stage": "asked", "asked_at": time.monotonic(), "reason": reason}
        await self.emit("agent_thought", provider="policy", text=f"I'm not sure yet: {reason}")
        await self.emit("verification", stage="started", kind=kind, reason=reason)
        await run_tool(self, "whisper_to_granny", {"text": whispers.LINES[f"verify_{kind}"], "tone": "caution", "duck": False},
                       actor="policy")
        await run_tool(self, "verify_caller", {"question": question, "kind": kind}, actor="policy")
        await self.emit("verification", stage="asked", kind=kind, question=question)
        self.spawn(self._verification_timeout(self.verification))

    async def _judge_answer(self, answer: str) -> None:
        v = self.verification
        v["stage"], v["answer"] = "judged", answer
        if v["kind"] == "family":
            passed, match, why = verify.judge_family(answer, v["accepted"])
        else:
            passed, match, why = verify.judge_callback(answer)
        v["match"], v["reason_result"] = match, why
        await self.emit("verification", stage="answered", kind=v["kind"], answer=answer, match=match)
        if passed:
            v["result"] = "passed"
            # Only a family secret earns trust. Agreeing to a call-back is cheap for a scammer to say, so it just
            # sends granny to the official number; the ladder keeps watching.
            self.policy.verified = v["kind"] == "family"
            await self.emit("verification", stage="passed", kind=v["kind"], reason=f"The caller {why}.")
            await self.emit("agent_thought", provider="policy",
                            text=(f"Identity check passed: the caller {why}. Treating them as genuine; only hard rules can escalate now."
                                  if v["kind"] == "family" else
                                  f"The caller {why}. That's what a real institution does, but it's easy to say, so I'm still "
                                  "watching, and I've told her to hang up and call the official number."))
            await run_tool(self, "whisper_to_granny", {"text": whispers.LINES[f"verify_pass_{v['kind']}"], "tone": "caution",
                                                       "duck": False}, actor="policy")
            return
        if passed is None:  # unclear: be careful, but don't call it a scam
            await self._verification_unanswered(v, f"The caller {why}.")
            return
        v["result"] = "failed"
        self.policy.verification_failed = True
        self.scam_confirmed = True
        await self.emit("verification", stage="failed", kind=v["kind"], reason=f"The caller {why}.")
        await self.emit("agent_thought", provider="policy",
                        text=f"Identity check failed: the caller {why}. That settles it: this is a scam. Following the protocol.")
        await run_tool(self, "whisper_to_granny", {"text": whispers.LINES["verify_fail"], "tone": "alert"}, actor="policy")
        sig = self.last_signals or regex_signals(self.caller_turns or [""])
        await self._apply(evaluate(self.policy, sig, fast_path_only=True), self.turn)

    async def _verification_unanswered(self, v: dict, why: str) -> None:
        v["result"] = "timeout"
        v["stage"] = "judged"
        self.policy.verification_unanswered = True
        await self.emit("verification", stage="timeout", kind=v["kind"], reason=why)
        await run_tool(self, "whisper_to_granny", {"text": whispers.LINES["verify_timeout"], "tone": "alert"}, actor="policy")
        sig = self.last_signals or regex_signals(self.caller_turns or [""])
        await self._apply(evaluate(self.policy, sig, fast_path_only=True), self.turn)

    async def _verification_timeout(self, v: dict) -> None:
        await asyncio.sleep(0 if self.virtual else POLICY["verification"]["answer_timeout_s"])
        if self.virtual:
            return  # replays answer on the next scripted caller line
        if self.verification is v and v["stage"] == "asked" and self.state == "active":
            await self._verification_unanswered(v, f"No answer within {POLICY['verification']['answer_timeout_s']} seconds.")

    def _alert_kind(self) -> str:
        sig = self.last_signals
        p = sig.p if sig else (lambda _n: 0.0)
        return "payment" if p("requests_unusual_payment") >= 0.6 else "credentials" if p("requests_credentials") >= 0.6 else "generic"

    def _line(self, name: str) -> str:
        if name != "alert":
            return whispers.LINES[name]
        kind = self._alert_kind()
        self.alerts_sent.add(kind)
        return whispers.alert_line(kind, self.claimed_org)

    async def _agent(self, d: Decision, write_whisper: bool) -> None:
        said = await runner.on_level(self, d, write_whisper)
        if write_whisper and not said and self.state == "active":
            await run_tool(self, "whisper_to_granny", {"text": whispers.LINES["alert"], "tone": "alert"}, actor="policy")

    # ---------- report ----------
    def short_summary(self) -> str:
        sig = self.last_signals
        flags = [n.replace("_", " ") for n in SIGNAL_NAMES if sig and sig.p(n) >= 0.6]
        score = f"scam likelihood {sig.scam_score:.0%}" if sig else "no model reading yet"
        who = f" Caller claims to be {self.claimed_org}." if self.claimed_org else ""
        return (f"Call {self.id}: level {self.max_level} ({LEVEL_NAMES[self.max_level]}), {score}.{who} "
                f"Red flags: {', '.join(flags) or 'none above threshold'}. PII attempts blocked: {self.policy.pii_attempts}. "
                f"Outcome: {self.outcome}.")

    def story(self) -> list[dict]:
        """What happened, in plain English, for a loved one reading on their phone."""
        name = settings.protected_name
        out: list[dict] = []
        said_claim = said_ask = False
        for ev in self.events:
            d, t, ts = ev["data"], ev["type"], ev["ts"]
            line = None
            if t == "signals" and not said_claim and d.get("claimed_org"):
                line, said_claim = f"Caller claimed to be {d['claimed_org']}.", True
            elif t == "signals" and not said_ask and d.get("signals", {}).get("requests_credentials", {}).get("p", 0) >= 0.6:
                line, said_ask = f"Caller asked {name} for private details.", True
            elif t == "level_change":
                line = {1: "Guardian became cautious and quietly warned {n}.", 2: "Guardian is sure it's a scam and alerted you.",
                        3: "Guardian hung up on the caller."}[d["to"]].format(n=name)
            elif t == "pii_guard" and d.get("action") == "redacted":
                what = {"card": "card number", "account": "account number"}.get(d.get("kind"), "number")
                line = f"{name} started reading their {what}. The caller heard silence: {d.get('digits_hidden')} digits hidden."
            elif t == "guardian_ask":
                line = f"{name} privately asked the guardian \"{d['question']}\". The caller didn't hear it."
            elif t == "memory_match":
                line = "Guardian recognised this caller's script from an earlier scam."
            if line:
                out.append({"ts": ts, "text": line})
        return out

    def _snapshot(self, summary: str, recommendations: list[str], live: bool) -> dict:
        kinds = {"rule_fired", "level_change", "pii_guard", "blocked_by_policy", "tool_call", "call_state"}
        timeline = []
        for ev in self.events:
            if ev["type"] not in kinds:
                continue
            d = ev["data"]
            desc = {
                "rule_fired": lambda: f"{d['rule_id']} → level {d['level']}",
                "level_change": lambda: f"Level {d['from']} → {d['to']} ({d['name']})",
                "pii_guard": lambda: f"Mic guard {d['action']}: {d['reason']}",
                "blocked_by_policy": lambda: f"Blocked {d['tool']} ({d['actor']}): {d['reason']}",
                "tool_call": lambda: f"{d['actor']} → {d['tool']}({', '.join(f'{k}={v!r}' for k, v in d['args'].items())})",
                "call_state": lambda: f"Call {d['state']}" + (f" by {d['by']}" if d.get("by") else ""),
            }[ev["type"]]()
            timeline.append({"ts": ev["ts"], "kind": ev["type"], "text": desc})
        flagged = [l for l in self.lines if l["speaker"] == "caller" and regex_fast.scan_caller(l["text"]).hits]
        excerpts = [{"speaker": l["speaker"], "text": l["text"]} for l in (flagged or self.lines)[:8]]
        transcript = [{"speaker": l["speaker"], "text": regex_fast.mask_digits(l["text"])[0] if l["speaker"] == "granny" else l["text"]}
                      for l in self.lines]  # never put granny's digits in a shareable report
        return {
            "call_id": self.id,
            "live": live,
            "started": self.started,
            "ended": None if live else time.time(),
            "outcome": "in progress" if live else self.outcome,
            "max_level": self.max_level,
            "max_level_name": LEVEL_NAMES[self.max_level],
            "pii_attempts_blocked": self.policy.pii_attempts,
            "summary": summary,
            "recommendations": recommendations,
            "story": self.story(),
            "timeline": timeline,
            "signals_peak": {k: round(v, 3) for k, v in self.peak.items()},
            "actions": self.actions,
            "excerpts": excerpts,
            "transcript": transcript,
            "providers": {"stt": settings.stt_provider, "signals": settings.signals_provider,
                          "agent": settings.agent_provider, "tts": settings.tts_provider},
            "virtual": self.virtual,
            "cost_usd": sup.call_cost(self.id),
            "claimed_org": self.claimed_org,
            "safe_next_step": (f"Don't call back the number that called. To check, {self.safe_step}."
                               if self.scam_confirmed else None),
            "memory_matches": self.memory_matches,
            "verification": ({k: self.verification.get(k) for k in ("kind", "question", "answer", "match", "result")}
                             | {"reason": self.verification.get("reason_result") or self.verification.get("reason")}
                             if self.verification else None),
            "supervisor": [{"ts": e["ts"], "kind": e["data"]["kind"], "severity": e["data"]["severity"],
                            "message": e["data"]["message"]} for e in self.events if e["type"] == "supervisor"],
        }

    def live_report(self) -> dict:
        """The report while the call is still happening (for the loved one's link): no AI summary, refreshes live."""
        name = settings.protected_name
        return self._snapshot(self.short_summary(), [
            f"Call {name} as soon as this call ends.",
            "Don't call back the number that called; use the number on the card or official website.",
        ], live=True)

    async def build_report(self) -> dict:
        text = await runner.write_summary(self)
        self.report = self._snapshot(text["summary"], text["recommendations"], live=False)
        store.end_call(self.id, self.report["ended"], self.outcome, self.max_level, self.report)
        await self.emit("report_ready", call_id=self.id, url=f"/report/{self.id}")
        return self.report

    # ---------- mock STT ----------
    async def say(self, speaker: str, text: str, word_delay: float = 0.18) -> None:
        """Mock STT: stream a typed line as word-by-word partials, then a final."""
        words = text.split()
        acc: list[str] = []
        for w in words:
            if self.state != "active":
                return
            acc.append(w)
            if speaker == "granny":
                self.delay_q.append((time.monotonic(), "caption", w))
            await self.on_partial(speaker, " ".join(acc))
            if word_delay:
                await asyncio.sleep(word_delay)
        await self.on_final(speaker, text, source="mock")
