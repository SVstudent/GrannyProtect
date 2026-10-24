import os
import tempfile

# Tests always run fully offline on the mock providers, whatever .env says.
os.environ.update({
    "STT_PROVIDER": "mock", "SIGNALS_PROVIDER": "mock", "AGENT_PROVIDER": "mock", "TTS_PROVIDER": "mock",
    "DB_PATH": os.path.join(tempfile.mkdtemp(), "test.db"), "CHALLENGE_MODE": "false",
    # never message a real loved one from tests
    "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "", "DISCORD_WEBHOOK_URL": "", "PUBLIC_URL": "",
})

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_scam_memory():
    """Each test starts with an empty scam memory (real-call tests teach it scripts)."""
    from app import store

    store.forget_scams()
    yield
    store.forget_scams()
