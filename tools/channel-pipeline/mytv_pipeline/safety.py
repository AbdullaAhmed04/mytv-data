from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .constants import ADULT_CATEGORY_WORDS, ADULT_NAME_WORDS


@dataclass(frozen=True)
class SafetyDecision:
    action: str  # allow, reject, quarantine
    reason: str


def _tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9+]+", value.casefold()) if token}


def evaluate_safety(
    channel: dict,
    streams: list[dict],
    upstream_nsfw_ids: set[str],
    adult_blocklist: dict,
) -> SafetyDecision:
    channel_id = str(channel.get("id") or "")
    nsfw = channel.get("is_nsfw")
    if nsfw is True:
        return SafetyDecision("reject", "IPTV-org is_nsfw=true")
    if nsfw is not False:
        return SafetyDecision("quarantine", "Missing or invalid is_nsfw safety metadata")

    if channel_id in upstream_nsfw_ids:
        return SafetyDecision("reject", "IPTV-org NSFW blocklist")
    if channel_id in set(adult_blocklist.get("channelIds", [])):
        return SafetyDecision("reject", "Permanent adult channel blocklist")

    category_tokens = {str(category).casefold() for category in channel.get("categories", [])}
    configured_categories = {str(value).casefold() for value in adult_blocklist.get("categories", [])}
    if category_tokens & (ADULT_CATEGORY_WORDS | configured_categories):
        return SafetyDecision("reject", "Explicit adult category")

    name_tokens = _tokens(" ".join([str(channel.get("name") or ""), channel_id]))
    configured_keywords = {str(value).casefold() for value in adult_blocklist.get("keywords", [])}
    if name_tokens & (ADULT_NAME_WORDS | configured_keywords):
        return SafetyDecision("reject", "Explicit adult identity keyword")

    blocked_domains = {str(value).casefold() for value in adult_blocklist.get("domains", [])}
    for stream in streams:
        host = (urlparse(str(stream.get("url") or "")).hostname or "").casefold()
        if any(host == domain or host.endswith(f".{domain}") for domain in blocked_domains):
            return SafetyDecision("reject", "Permanent adult domain blocklist")
    return SafetyDecision("allow", "Passed layered safety checks")
