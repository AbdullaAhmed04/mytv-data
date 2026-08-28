from __future__ import annotations

import json
import ssl
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .constants import FREE_TV_PLAYLIST_URL, UPSTREAM_URLS

MAX_UPSTREAM_BYTES = 120 * 1024 * 1024
MAX_UPSTREAM_TEXT_BYTES = 16 * 1024 * 1024


def fetch_json(url: str, timeout: int = 45):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"Unsafe upstream URL: {url}")
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "MYTV-Pipeline/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https":
            raise ValueError("Upstream redirected away from HTTPS")
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > MAX_UPSTREAM_BYTES:
            raise ValueError("Upstream response is too large")
        raw = response.read(MAX_UPSTREAM_BYTES + 1)
        if len(raw) > MAX_UPSTREAM_BYTES:
            raise ValueError("Upstream response is too large")
    return json.loads(raw.decode("utf-8"))


def load_upstream(upstream_dir: Path | None = None) -> dict[str, list]:
    result: dict[str, list] = {}
    for name, url in UPSTREAM_URLS.items():
        if upstream_dir:
            result[name] = json.loads((upstream_dir / f"{name}.json").read_text(encoding="utf-8"))
        else:
            result[name] = fetch_json(url)
        if not isinstance(result[name], list):
            raise ValueError(f"{name}.json must contain an array")
    return result


def fetch_text(url: str, timeout: int = 45) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"Unsafe upstream URL: {url}")
    request = urllib.request.Request(
        url,
        headers={"Accept": "text/plain, application/vnd.apple.mpegurl, */*;q=0.8", "User-Agent": "MYTV-Pipeline/1.3.7"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https":
            raise ValueError("Upstream redirected away from HTTPS")
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > MAX_UPSTREAM_TEXT_BYTES:
            raise ValueError("Upstream text response is too large")
        raw = response.read(MAX_UPSTREAM_TEXT_BYTES + 1)
        if len(raw) > MAX_UPSTREAM_TEXT_BYTES:
            raise ValueError("Upstream text response is too large")
    return raw.decode("utf-8-sig")


def load_free_tv_playlist(upstream_dir: Path | None = None) -> str:
    if upstream_dir:
        fixture = upstream_dir / "free-tv-playlist.m3u8"
        return fixture.read_text(encoding="utf-8") if fixture.exists() else "#EXTM3U\n"
    return fetch_text(FREE_TV_PLAYLIST_URL)
