"""The server reads no Vinted page (decision Q1d, prompt v3 section 2).

Only the browser extension, on the pages the user opens, brings Vinted data in. The server may talk
to the services below and nothing else; adding a network client means adding it here on purpose.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

# Modules allowed to open network connections, and to whom.
ALLOWED_NETWORK_CLIENTS = {
    "ai/llm.py": "Anthropic API",
    "alerts/channels/discord.py": "the user's Discord webhook",
    "alerts/channels/email.py": "the user's SMTP server",
    "alerts/channels/telegram.py": "Telegram bot API",
    "alerts/channels/webpush.py": "browser push services",
    "external/parse.py": "price search results (parsing only)",
    "external/provider.py": "the optional external price search (Serper)",
    "marketplace/feed.py": "an authorized listing feed configured by the user",
    # Photo copies are still downloaded from Vinted's image CDN by the server. Decision Q3-B replaces
    # this with photos uploaded by the extension; until then it is the one known exception.
    "media/archive.py": "Vinted image CDN (known exception, to be replaced by Q3-B)",
    "media/keys.py": "image key parsing only",
    "vision/analyzer.py": "Anthropic API",
}
NETWORK_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(httpx|aiohttp|requests|urllib|socket|aiosmtplib|anthropic|pywebpush)\b", re.M
)


def test_only_known_modules_open_network_connections() -> None:
    users = {
        str(p.relative_to(APP)): m.group(1)
        for p in APP.rglob("*.py")
        if (m := NETWORK_IMPORT.search(p.read_text(encoding="utf-8")))
    }
    unknown = sorted(set(users) - set(ALLOWED_NETWORK_CLIENTS))
    assert not unknown, f"new network client(s) {unknown}: add them to ALLOWED_NETWORK_CLIENTS with a reason"
    gone = sorted(set(ALLOWED_NETWORK_CLIENTS) - set(users))
    assert not gone, f"{gone} no longer use the network: remove them from the list"


def test_the_page_reader_and_the_automatic_modes_are_gone() -> None:
    assert not (APP / "acquisition" / "public_fetch.py").exists()
    assert not (APP / "acquisition" / "vinted_actions.py").exists()
    settings = (APP / "core" / "config.py").read_text(encoding="utf-8")
    assert "vinted_public_fetch" not in settings
    tasks = (APP / "workers" / "tasks.py").read_text(encoding="utf-8")
    assert "refresh_tracked_public" not in tasks
