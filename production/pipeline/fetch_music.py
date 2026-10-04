"""Download the CC BY 4.0 music cues (Kevin MacLeod, incompetech.com) listed in assets/music/music_credits.json."""
import json
import urllib.parse
import urllib.request
from pathlib import Path

MUSIC = Path(__file__).resolve().parent.parent / "assets" / "music"

for cue in json.loads((MUSIC / "music_credits.json").read_text()):
    dst = MUSIC / cue["file"]
    if dst.exists():
        continue
    url = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/" + urllib.parse.quote(cue["title"] + ".mp3")
    dst.write_bytes(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=120).read())
    print("downloaded", cue["title"])
