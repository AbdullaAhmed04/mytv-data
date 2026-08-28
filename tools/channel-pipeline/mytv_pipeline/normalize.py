from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urlparse

from .constants import (
    ARABIC_COUNTRY_NAMES,
    ARAB_COUNTRIES,
    CATEGORY_MAP,
    COUNTRY_ORDER,
    OUTPUT_CATEGORY_ORDER,
    US_UK,
)
from .safety import evaluate_safety


@dataclass
class NormalizationResult:
    channels: list[dict]
    rejected: list[dict]
    policy_rejected: list[dict]
    quarantined: list[dict]


def is_https_url(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        parsed = urlparse(value)
        return parsed.scheme.casefold() == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def quality_rank(value: object) -> int:
    quality = str(value or "").casefold()
    if quality in {"1080p", "1080i"}:
        return 0
    if quality in {"720p", "720i", "hd"}:
        return 1
    if quality in {"480p", "480i", "sd"}:
        return 2
    return 3


def normalize_category(values: object) -> list[str]:
    source = values if isinstance(values, list) else []
    mapped = {CATEGORY_MAP.get(str(value).casefold(), "OTHER") for value in source}
    if not mapped:
        mapped = {"OTHER"}
    return [value for value in OUTPUT_CATEGORY_ORDER if value in mapped]


def _geo(label_value: object) -> dict:
    label = str(label_value).strip() if label_value else None
    lowered = (label or "").casefold()
    blocked: list[str] = []
    allowed: list[str] = []
    if re.search(r"(?:not available|blocked|unavailable).*(?:us|usa|united states)", lowered):
        blocked.append("US")
    if re.search(r"(?:us|usa|united states)[ -]?(?:only|exclusive)", lowered):
        allowed.append("US")
    is_blocked = bool(label and any(word in lowered for word in ("geo", "block", "only", "unavailable")))
    return {
        "label": label,
        "isGeoBlocked": is_blocked,
        "blockedCountries": blocked,
        "allowedCountries": allowed,
    }


def _stream_priority(stream: dict) -> int:
    geo = _geo(stream.get("label"))
    us_penalty = 500 if "US" in geo["blockedCountries"] else 0
    unknown_geo_penalty = 100 if geo["isGeoBlocked"] and not geo["allowedCountries"] else 0
    return quality_rank(stream.get("quality")) * 10 + us_penalty + unknown_geo_penalty


def normalize_stream(stream: dict) -> dict | None:
    url = stream.get("url")
    if not is_https_url(url):
        return None
    referer = stream.get("referrer")
    if referer is not None and not is_https_url(referer):
        return None
    user_agent = stream.get("user_agent")
    if user_agent is not None:
        if (
            not isinstance(user_agent, str)
            or not user_agent.strip()
            or len(user_agent) > 512
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in user_agent)
        ):
            return None
    return {
        "url": url,
        "quality": str(stream["quality"]).strip() if stream.get("quality") else None,
        "userAgent": user_agent.strip() if isinstance(user_agent, str) else None,
        "referer": referer,
        "priority": _stream_priority(stream),
        "source": "iptv-org",
        "geo": _geo(stream.get("label")),
    }


def _choose_logo(logos: list[dict]) -> str | None:
    valid = [item for item in logos if is_https_url(item.get("url"))]
    if not valid:
        return None
    def rank(item: dict):
        tags = {str(tag).casefold() for tag in item.get("tags", [])}
        return (
            0 if item.get("in_use") is True else 1,
            0 if "horizontal" in tags else 1,
            0 if str(item.get("format") or "").upper() in {"PNG", "WEBP", "SVG"} else 1,
            -int(item.get("width") or 0),
            str(item.get("url")),
        )
    return min(valid, key=rank)["url"]


def _language_map(feeds: list[dict], countries: list[dict]):
    by_channel: dict[str, set[str]] = defaultdict(set)
    by_feed: dict[tuple[str, str], set[str]] = defaultdict(set)
    for feed in feeds:
        channel_id = feed.get("channel")
        if not channel_id:
            continue
        values = {str(value).casefold() for value in feed.get("languages", []) if value}
        by_channel[str(channel_id)].update(values)
        if feed.get("id"):
            by_feed[(str(channel_id), str(feed["id"]))].update(values)
    country_languages = {
        str(country.get("code") or "").upper(): {
            str(value).casefold() for value in country.get("languages", []) if value
        }
        for country in countries
    }
    return by_channel, by_feed, country_languages


def _country_names(countries: list[dict]) -> dict[str, str]:
    names = {str(item.get("code") or "").upper(): str(item.get("name") or "") for item in countries}
    names.update(ARABIC_COUNTRY_NAMES)
    return names


def normalize(upstream: dict[str, list], adult_blocklist: dict, user_blocklist: dict) -> NormalizationResult:
    channels = {str(item.get("id")): item for item in upstream["channels"] if item.get("id")}
    streams_by_channel: dict[str, list[dict]] = defaultdict(list)
    for stream in upstream["streams"]:
        if stream.get("channel"):
            streams_by_channel[str(stream["channel"])].append(stream)
    logos_by_channel: dict[str, list[dict]] = defaultdict(list)
    for logo in upstream["logos"]:
        if logo.get("channel"):
            logos_by_channel[str(logo["channel"])].append(logo)

    upstream_nsfw = {
        str(item.get("channel")) for item in upstream["blocklist"]
        if str(item.get("reason") or "").casefold() == "nsfw" and item.get("channel")
    }
    upstream_dmca = {
        str(item.get("channel")) for item in upstream["blocklist"]
        if str(item.get("reason") or "").casefold() == "dmca" and item.get("channel")
    }
    user_blocked = {str(value) for value in user_blocklist.get("channelIds", [])}
    language_by_channel, language_by_feed, country_languages = _language_map(
        upstream["feeds"], upstream["countries"],
    )
    country_names = _country_names(upstream["countries"])

    normalized: list[dict] = []
    rejected: list[dict] = []
    policy_rejected: list[dict] = []
    quarantined: list[dict] = []
    for channel_id in sorted(channels):
        channel = channels[channel_id]
        if channel_id in user_blocked:
            continue
        if channel_id in upstream_dmca:
            policy_rejected.append({"channelId": channel_id, "reason": "IPTV-org DMCA blocklist"})
            continue
        raw_streams = streams_by_channel.get(channel_id, [])
        safety = evaluate_safety(channel, raw_streams, upstream_nsfw, adult_blocklist)
        if safety.action != "allow":
            summary = {"channelId": channel_id, "reason": safety.reason}
            (rejected if safety.action == "reject" else quarantined).append(summary)
            continue

        valid_streams: list[dict] = []
        seen_urls: set[str] = set()
        languages = set(language_by_channel.get(channel_id, set()))
        for raw_stream in raw_streams:
            feed_id = raw_stream.get("feed")
            if feed_id:
                languages.update(language_by_feed.get((channel_id, str(feed_id)), set()))
            candidate = normalize_stream(raw_stream)
            if candidate and candidate["url"] not in seen_urls:
                seen_urls.add(candidate["url"])
                valid_streams.append(candidate)
        if not valid_streams:
            continue

        country_code = str(channel.get("country") or "").upper()
        if not languages:
            languages.update(country_languages.get(country_code, set()))
        categories = normalize_category(channel.get("categories"))
        is_arabic = country_code in ARAB_COUNTRIES or "ara" in languages or "ar" in languages
        is_english_special = (
            country_code in US_UK
            and ("eng" in languages or "en" in languages)
            and bool({"NEWS", "SPORTS"} & set(categories))
        )
        if not (is_arabic or is_english_special):
            continue

        valid_streams.sort(key=lambda item: (item["priority"], item["url"]))
        for priority, item in enumerate(valid_streams[:8]):
            # Preserve quality/geo preference while guaranteeing unique bounded fallback order.
            item["priority"] = priority
        website = channel.get("website") if is_https_url(channel.get("website")) else None
        normalized.append({
            "id": channel_id,
            "upstreamId": channel_id,
            "name": str(channel.get("name") or channel_id).strip()[:160],
            "country": country_names.get(country_code) or country_code,
            "countryCode": country_code,
            "categories": categories,
            "languages": sorted(languages),
            "logo": _choose_logo(logos_by_channel.get(channel_id, [])),
            "website": website,
            "enabled": True,
            "order": 0,
            "description": None,
            "safety": {"isNsfw": False, "reviewed": True},
            "streams": valid_streams[:8],
        })

    priority_map = {code: index for index, code in enumerate(COUNTRY_ORDER)}
    normalized.sort(key=lambda item: (
        priority_map.get(item["countryCode"], len(COUNTRY_ORDER) + (0 if item["countryCode"] in US_UK else 1)),
        item["name"].casefold(),
        item["id"],
    ))
    for index, item in enumerate(normalized):
        item["order"] = index
    return NormalizationResult(normalized, rejected, policy_rejected, quarantined)
