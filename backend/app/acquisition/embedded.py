"""The item's own data embedded in a Vinted page, read structurally.

A Vinted item page carries several JSON objects in its scripts: the item (with its ``photos``
gallery), its seller, the signed-in user (with *their* profile photo), suggested items (with
their photos)... Searching the page text for ``"full_size_url"`` or ``"favourite_count"`` picks
values from any of them - that is how a profile photo ended up among an item's photos.

Here the item object is located by its id and parsed; only its own fields are read. The same
algorithm runs in the browser extension (``extension/src/parse.js``), tested on the same pages.

The JSON may be nested inside JavaScript strings (Next.js flight chunks
``self.__next_f.push([1,"..."])``), i.e. escaped once or more: the reader decodes the flight
chunks first and otherwise works directly on escaped text, recognising string delimiters by the
number of backslashes before each quote.
"""

from __future__ import annotations

import json
import re
from typing import Any

FLIGHT_CHUNK = re.compile(r'self\.__next_f\.push\(\[\s*\d+\s*,\s*("(?:[^"\\]|\\.)*")\s*\]\)', re.S)
LITERAL = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null")
MAX_BACKSCAN = 400_000


def payload_texts(scripts: list[str]) -> list[str]:
    """Flight chunks decoded and joined (one JSON stream), then every other script as is."""
    flight: list[str] = []
    others: list[str] = []
    for s in scripts:
        chunks = FLIGHT_CHUNK.findall(s)
        if not chunks:
            others.append(s)
            continue
        for c in chunks:
            try:
                flight.append(json.loads(c))
            except ValueError:
                continue
    return (["".join(flight)] if flight else []) + others


class _Reader:
    """Tolerant JSON reader over text escaped ``level`` times (prefix of ``q`` backslashes)."""

    def __init__(self, text: str, q: int) -> None:
        self.t = text
        self.q = q
        self.m = q + 1  # 2**level
        self.decodes = self.m.bit_length()  # level + 1 json decodes per string

    def _run(self, j: int) -> int:
        n = 0
        while j - 1 - n >= 0 and self.t[j - 1 - n] == "\\":
            n += 1
        return n

    def is_delim(self, j: int) -> bool:
        """A quote at ``j`` that opens or closes a string at this escape level."""
        if self.t[j] != '"':
            return False
        r = self._run(j) - self.q
        return r >= 0 and r % self.m == 0 and (r // self.m) % 2 == 0

    def enclosing_object(self, pos: int) -> int | None:
        """Start of the object containing the key that starts at ``pos`` (outside any string)."""
        depth = 0
        in_str = False
        stop = max(0, pos - MAX_BACKSCAN)
        j = pos - 1
        while j >= stop:
            c = self.t[j]
            if c == '"' and self.is_delim(j):
                in_str = not in_str
            elif not in_str:
                if c in "}]":
                    depth += 1
                elif c in "{[":
                    if depth == 0:
                        return j if c == "{" else None
                    depth -= 1
            j -= 1
        return None

    # ---------------------------------------------------------------- forward parsing
    def _ws(self, i: int) -> int:
        t = self.t
        while i < len(t) and t[i] in " \t\r\n":
            i += 1
        return i

    def _string(self, i: int) -> tuple[str, int]:
        # i points at the first backslash of the opening delimiter (or the quote when q == 0)
        start = i + self.q
        if self.t[start] != '"':
            raise ValueError("string expected")
        j = start + 1
        while True:
            j = self.t.find('"', j)
            if j < 0:
                raise ValueError("unterminated string")
            if self.is_delim(j):
                break
            j += 1
        raw = self.t[start + 1 : j - self.q]
        value = raw
        for _ in range(self.decodes):
            try:
                value = json.loads(f'"{value}"')
            except ValueError:
                break
        return value, j + 1

    def _at_string(self, i: int) -> bool:
        return self.t.startswith("\\" * self.q + '"', i)

    def value(self, i: int, depth: int = 0) -> tuple[Any, int]:
        if depth > 60:
            raise ValueError("too deep")
        i = self._ws(i)
        if i >= len(self.t):
            raise ValueError("end of text")
        c = self.t[i]
        if c == "{":
            obj: dict[str, Any] = {}
            i = self._ws(i + 1)
            if self.t.startswith("}", i):
                return obj, i + 1
            while True:
                i = self._ws(i)
                if not self._at_string(i):
                    raise ValueError("key expected")
                key, i = self._string(i)
                i = self._ws(i)
                if not self.t.startswith(":", i):
                    raise ValueError("colon expected")
                obj[key], i = self.value(i + 1, depth + 1)
                i = self._ws(i)
                if self.t.startswith(",", i):
                    i += 1
                elif self.t.startswith("}", i):
                    return obj, i + 1
                else:
                    raise ValueError("comma or brace expected")
        if c == "[":
            arr: list[Any] = []
            i = self._ws(i + 1)
            if self.t.startswith("]", i):
                return arr, i + 1
            while True:
                item, i = self.value(i, depth + 1)
                arr.append(item)
                i = self._ws(i)
                if self.t.startswith(",", i):
                    i += 1
                elif self.t.startswith("]", i):
                    return arr, i + 1
                else:
                    raise ValueError("comma or bracket expected")
        if self._at_string(i):
            return self._string(i)
        m = LITERAL.match(self.t, i)
        if not m:
            raise ValueError("value expected")
        lit = m.group(0)
        if lit in ("true", "false"):
            return lit == "true", m.end()
        if lit == "null":
            return None, m.end()
        return (float(lit) if any(ch in lit for ch in ".eE") else int(lit)), m.end()


def _objects_with_key(text: str, key: str, value_rx: str) -> list[tuple[_Reader, int]]:
    """(reader, object start) for each object having ``"key": <value>`` at any escape level."""
    out = []
    rx = re.compile(r'(\\*)"' + re.escape(key) + r'\1"\s*:\s*' + value_rx)
    for m in rx.finditer(text):
        q = len(m.group(1))
        if (q + 1) & q:  # not 2**n - 1 backslashes: not a key at a consistent escape level
            continue
        reader = _Reader(text, q)
        start = reader.enclosing_object(m.start())
        if start is not None:
            out.append((reader, start))
    return out


def find_item(
    scripts: list[str], vinted_id: str, photos_keys: list[str], markers: list[str]
) -> dict[str, Any] | None:
    """The embedded object of item ``vinted_id``: has that ``id`` and item fields (``markers``)."""
    if not vinted_id or not vinted_id.isdigit():
        return None
    for text in payload_texts(scripts):
        if vinted_id not in text:
            continue
        for reader, start in _objects_with_key(text, "id", r'(?:\\*")?' + vinted_id + r"(?![\d.])"):
            try:
                obj, _ = reader.value(start)
            except (ValueError, IndexError, RecursionError):
                continue
            if not isinstance(obj, dict) or str(obj.get("id")) != vinted_id:
                continue
            if any(k in obj for k in [*photos_keys, *markers]):
                return obj
    return None


def profile_photo_urls(scripts: list[str], photo_keys: list[str], url_keys: list[str]) -> set[str]:
    """URLs of every profile photo in the page (signed-in user, seller, other members): they must
    never be taken for item photos, whatever the parser finds elsewhere."""
    urls: set[str] = set()
    for text in payload_texts(scripts):
        for key in photo_keys:
            rx = re.compile(r'(\\*)"' + re.escape(key) + r'\1"\s*:\s*\{')
            for m in rx.finditer(text):
                q = len(m.group(1))
                if (q + 1) & q:
                    continue
                try:
                    obj, _ = _Reader(text, q).value(m.end() - 1)
                except (ValueError, IndexError, RecursionError):
                    continue
                if isinstance(obj, dict):
                    urls |= _photo_urls(obj, url_keys)
    return urls


def _photo_urls(obj: dict[str, Any], url_keys: list[str]) -> set[str]:
    out: set[str] = set()
    for k in url_keys:
        v = obj.get(k)
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            out.add(v)
    for v in obj.values():  # thumbnails: [{"url": ...}, ...]
        if isinstance(v, list):
            for t in v:
                if isinstance(t, dict):
                    for k in url_keys:
                        u = t.get(k)
                        if isinstance(u, str) and u.startswith(("http://", "https://")):
                            out.add(u)
    return out


def gallery(item: dict[str, Any], photos_keys: list[str], url_keys: list[str]) -> list[str]:
    """The item's photos in gallery order, largest version first available per photo."""
    for k in photos_keys:
        photos = item.get(k)
        if not isinstance(photos, list):
            continue
        urls = []
        for p in photos:
            if isinstance(p, str) and p.startswith(("http://", "https://")):
                urls.append(p)
            elif isinstance(p, dict):
                u = next(
                    (p[k2] for k2 in url_keys if isinstance(p.get(k2), str) and p[k2].startswith("http")),
                    None,
                )
                if u:
                    urls.append(u)
        return list(dict.fromkeys(urls))
    return []


def pick(obj: dict[str, Any] | None, keys: list[str]) -> Any:
    """First present value among ``keys`` (dotted paths allowed: ``service_fee.amount``)."""
    if not obj:
        return None
    for key in keys:
        cur: Any = obj
        for part in key.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is not None and cur != "":
            return cur
    return None
