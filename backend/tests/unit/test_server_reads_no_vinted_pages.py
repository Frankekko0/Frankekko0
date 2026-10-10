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
    "ai/gemini.py": "Google Gemini API",
    "alerts/channels/discord.py": "the user's Discord webhook",
    "alerts/channels/email.py": "the user's SMTP server",
    "alerts/channels/telegram.py": "Telegram bot API",
    "alerts/channels/webpush.py": "browser push services",
    "external/parse.py": "price search results (parsing only)",
    "external/provider.py": "the optional external price search (Serper)",
    "marketplace/feed.py": "an authorized listing feed configured by the user",
    "media/keys.py": "image key parsing only (urllib.parse, no connection)",
}  # photos are never downloaded: the extension uploads them (decision Q3-B), see the test below
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


def test_photos_are_uploaded_by_the_browser_never_downloaded_by_the_server() -> None:
    """Decision Q3-B: no code path fetches a photo from Vinted, and the AI gets bytes, not addresses."""
    source = {str(p.relative_to(APP)): p.read_text(encoding="utf-8") for p in APP.rglob("*.py")}
    for needle in ("fetch_image", "is_public_https_url", "archive_listing_images", "image_archive_hosts"):
        users = [name for name, text in source.items() if needle in text]
        assert not users, f"{needle} is a server-side photo download: still used in {users}"
    sent_as_address = [
        name
        for name, text in source.items()
        if name.startswith(("vision/", "ai/")) and '"type": "url"' in text
    ]
    assert not sent_as_address, (
        f"photos must reach the model as bytes, not Vinted addresses: {sent_as_address}"
    )
    # The one way a photo enters is the extension's upload endpoint.
    extension_api = source["api/v1/extension.py"]
    assert '"/capture/photos/{vinted_id}/{image_key}"' in extension_api
