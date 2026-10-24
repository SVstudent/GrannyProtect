"""Settings loaded from .env. Every external service has a mock so the demo runs with zero keys.

Real providers all go through TokenRouter (one key): chat completions for the agent and LLM signals,
/api/alpha/decisions for TypeSafe Jev typed signals, audio-capable chat models for STT and TTS.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    # mock (typed lines) | browser (Web Speech only) | hybrid (Web Speech partials + TokenRouter STT finals)
    stt_provider: str = field(default_factory=lambda: _env("STT_PROVIDER", "mock"))
    # mock (regex) | jev (TypeSafe Jev typed decisions) | llm (JSON on a chat model)
    signals_provider: str = field(default_factory=lambda: _env("SIGNALS_PROVIDER", "mock"))
    # mock (canned lines) | llm (tool-calling chat model)
    agent_provider: str = field(default_factory=lambda: _env("AGENT_PROVIDER", "mock"))
    # mock (browser speechSynthesis) | tokenrouter (fixed whispers pre-generated at startup)
    tts_provider: str = field(default_factory=lambda: _env("TTS_PROVIDER", "mock"))

    llm_api_key: str = field(default_factory=lambda: _env("TOKENROUTER_API_KEY") or _env("OPENAI_API_KEY"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "https://api.tokenrouter.com/v1"))
    agent_model: str = field(default_factory=lambda: _env("AGENT_MODEL", "openai/gpt-4o-mini"))
    signals_llm_model: str = field(default_factory=lambda: _env("SIGNALS_LLM_MODEL", "openai/gpt-4o-mini"))
    stt_model: str = field(default_factory=lambda: _env("STT_MODEL", "mistralai/voxtral-small-24b-2507"))
    tts_model: str = field(default_factory=lambda: _env("TTS_MODEL", "openai/gpt-audio-mini"))
    tts_voice: str = field(default_factory=lambda: _env("TTS_VOICE", "shimmer"))
    jev_url: str = field(default_factory=lambda: _env("JEV_URL", "https://api.tokenrouter.com/api/alpha/decisions"))
    jev_model: str = field(default_factory=lambda: _env("JEV_MODEL", "typesafe/jev-1.14"))
    discord_webhook_url: str = field(default_factory=lambda: _env("DISCORD_WEBHOOK_URL"))
    # Trusted-person alerts via a Telegram bot (create with @BotFather). Chat id is auto-discovered if empty.
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))
    protected_name: str = field(default_factory=lambda: _env("PROTECTED_NAME", "Grandma"))
    public_url: str = field(default_factory=lambda: _env("PUBLIC_URL"))  # for report links in alerts

    # Granny→caller audio is held this long so the PII guard can act on STT partials before digits leak.
    pii_delay_ms: float = field(default_factory=lambda: _float("PII_DELAY_MS", 700))
    # Mic chunk size sent to the phones: 40 ms is low-latency on Wi-Fi; raise to 100 for slow tunnels.
    audio_frame_ms: float = field(default_factory=lambda: _float("AUDIO_FRAME_MS", 40))
    # Number redaction: give the line back after this long without a new digit, and on restore drop buffered
    # audio older than REDACT_TAIL_MS so the number's last digit can't slip out of the delay line.
    redact_pause_s: float = field(default_factory=lambda: _float("REDACT_PAUSE_S", 1.5))
    redact_tail_ms: float = field(default_factory=lambda: _float("REDACT_TAIL_MS", 350))
    # How far back the cut starts: speech recognition reports a digit ~0.3-0.6 s after it's spoken.
    redact_lead_ms: float = field(default_factory=lambda: _float("REDACT_LEAD_MS", 600))
    pii_unmute_after_s: float = field(default_factory=lambda: _float("PII_UNMUTE_AFTER_S", 3.0))
    agent_timeout_s: float = field(default_factory=lambda: _float("AGENT_TIMEOUT_S", 8.0))
    signals_timeout_s: float = field(default_factory=lambda: _float("SIGNALS_TIMEOUT_S", 10.0))
    # Supervisor: budgets and stop rules
    call_budget_usd: float = field(default_factory=lambda: _float("CALL_BUDGET_USD", 0.05))
    shift_budget_usd: float = field(default_factory=lambda: _float("SHIFT_BUDGET_USD", 2.0))
    max_silence_s: float = field(default_factory=lambda: _float("MAX_SILENCE_S", 180))
    max_call_s: float = field(default_factory=lambda: _float("MAX_CALL_S", 1800))
    # Remember scams stopped on replayed/autopilot calls too (off: only real calls teach the memory)
    memory_include_virtual: bool = field(default_factory=lambda: _env("MEMORY_INCLUDE_VIRTUAL", "false").lower() == "true")
    # Hold granny's outgoing audio while a whisper plays (protects against a loud speakerphone leaking the warning).
    # Off by default: the conversation keeps flowing, browser echo cancellation + the transcript echo filter cover it.
    duck_whispers: bool = field(default_factory=lambda: _env("DUCK_WHISPERS", "false").lower() == "true")
    challenge_mode: bool = field(default_factory=lambda: _env("CHALLENGE_MODE", "false").lower() == "true")
    db_path: str = field(default_factory=lambda: _env("DB_PATH", str(ROOT / "backend" / "granny.db")))
    cache_dir: Path = field(default_factory=lambda: ROOT / "backend" / ".cache")


settings = Settings()
