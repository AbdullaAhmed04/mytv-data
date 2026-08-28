from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urlparse

from .constants import ARABIC_COUNTRY_NAMES, ARAB_COUNTRIES, COUNTRY_ORDER, US_UK
from .normalize import is_https_url, normalize_category, quality_rank
from .safety import evaluate_safety

FREE_TV_MARKERS = {"Ⓢ", "Ⓖ", "Ⓨ", "Ⓣ"}
DIRECT_MEDIA_SUFFIXES = (".m3u8", ".mpd")
SAFE_CHANNEL_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
ATTRIBUTE_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')

# Conservative, reviewed aliases for Free-TV names that refer to an existing IPTV-org identity
# but are spelled differently enough that normalized exact-name matching cannot safely infer it.
FREE_TV_IDENTITY_ALIASES = {
    ("IQ", "aliraqiya"): "AlIraqia.iq",
    ("AE", "dubairacing1"): "DubaiRacing.ae",
    ("QA", "qatartelevisiontheholyquran"): "QatarTVTheHolyQuran.qa",
}


def _id_country_suffix(value: str | None) -> str | None:
    text = str(value or "").strip()
    match = re.search(r"\.([A-Za-z]{2})$", text)
    return match.group(1).upper() if match else None


def _id_country_matches(value: str | None, country_code: str) -> bool:
    suffix = _id_country_suffix(value)
    return suffix is None or suffix == str(country_code or "").upper()


@dataclass(frozen=True)
class FreeTvEntry:
    name: str
    url: str
    country_code: str
    tvg_id: str | None
    logo: str | None
    is_sd: bool
    is_geo_blocked: bool


@dataclass
class FreeTvMergeResult:
    channels: list[dict]
    rejected: list[dict]
    policy_rejected: list[dict]
    quarantined: list[dict]
    stats: dict[str, int]


def _clean_name(value: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    for marker in FREE_TV_MARKERS:
        text = text.replace(marker, " ")
    return " ".join(text.split()).strip()


def _name_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", _clean_name(value).casefold())
    return "".join(character for character in text if character.isalnum())


def _parse_extinf(line: str) -> tuple[dict[str, str], str]:
    attrs = {key.casefold(): value for key, value in ATTRIBUTE_RE.findall(line)}
    raw_name = line.split(",", 1)[1] if "," in line else attrs.get("tvg-name", "")
    attrs["__is_sd"] = "1" if "Ⓢ" in raw_name else "0"
    attrs["__is_geo"] = "1" if "Ⓖ" in raw_name else "0"
    return attrs, _clean_name(raw_name or attrs.get("tvg-name", ""))


def parse_free_tv_playlist(text: str) -> list[FreeTvEntry]:
    if not isinstance(text, str):
        raise ValueError("Free-TV playlist must be text")
    entries: list[FreeTvEntry] = []
    pending: tuple[dict[str, str], str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            pending = _parse_extinf(line)
            continue
        if line.startswith("#"):
            continue
        if pending is None:
            continue
        attrs, name = pending
        pending = None
        country_code = str(attrs.get("tvg-country") or "").upper().strip()
        if not country_code:
            # Free-TV also exposes group-title, but country codes are much safer for automatic ingestion.
            continue
        tvg_id = str(attrs.get("tvg-id") or "").strip() or None
        logo = str(attrs.get("tvg-logo") or "").strip() or None
        if not name:
            continue
        entries.append(FreeTvEntry(
            name=name,
            url=str(line).strip(),
            country_code=country_code,
            tvg_id=tvg_id,
            logo=logo,
            is_sd=attrs.get("__is_sd") == "1",
            is_geo_blocked=attrs.get("__is_geo") == "1",
        ))
    return entries


def _direct_media_url(value: object) -> bool:
    if not is_https_url(value):
        return False
    parsed = urlparse(str(value))
    path = parsed.path.casefold()
    return path.endswith(DIRECT_MEDIA_SUFFIXES)


def _free_tv_stream(entry: FreeTvEntry) -> dict | None:
    if not _direct_media_url(entry.url):
        return None
    geo_penalty = 100 if entry.is_geo_blocked else 0
    quality = "SD" if entry.is_sd else "HD"
    return {
        "url": entry.url,
        "quality": quality,
        "userAgent": None,
        "referer": None,
        "priority": quality_rank(quality) * 10 + geo_penalty,
        "source": "free-tv",
        "geo": {
            "label": "Free-TV GeoIP" if entry.is_geo_blocked else None,
            "isGeoBlocked": entry.is_geo_blocked,
            "blockedCountries": [],
            "allowedCountries": [entry.country_code] if entry.is_geo_blocked else [],
        },
    }


def _stable_synthetic_id(entry: FreeTvEntry) -> str:
    if entry.tvg_id and SAFE_CHANNEL_ID.fullmatch(entry.tvg_id):
        return entry.tvg_id
    digest = hashlib.sha256(
        f"{entry.country_code}\0{_name_key(entry.name)}".encode("utf-8")
    ).hexdigest()[:20]
    return f"free-tv.{entry.country_code.lower()}.{digest}"


def _source_rank(value: str) -> int:
    # Free-TV is used as a supplemental curated source; it wins ties only.
    return {"free-tv": 0, "iptv-org": 1}.get(value, 2)


def _stream_sort_key(stream: dict):
    geo = stream.get("geo") if isinstance(stream.get("geo"), dict) else {}
    unknown_geo_penalty = 100 if geo.get("isGeoBlocked") and not geo.get("allowedCountries") else 0
    us_penalty = 500 if "US" in set(geo.get("blockedCountries") or []) else 0
    return (
        quality_rank(stream.get("quality")),
        us_penalty,
        unknown_geo_penalty,
        _source_rank(str(stream.get("source") or "")),
        str(stream.get("url") or ""),
    )


def _reorder_streams(channel: dict) -> None:
    by_url: dict[str, dict] = {}
    for stream in channel.get("streams", []):
        url = str(stream.get("url") or "")
        if url and url not in by_url:
            by_url[url] = stream
    ordered = sorted(by_url.values(), key=_stream_sort_key)[:8]
    for index, stream in enumerate(ordered):
        stream["priority"] = index
    channel["streams"] = ordered


def _channel_from_iptv_org(
    channel: dict,
    entry: FreeTvEntry,
    stream: dict,
    upstream: dict[str, list],
) -> dict:
    country_code = str(channel.get("country") or entry.country_code).upper()
    country_name = ARABIC_COUNTRY_NAMES.get(country_code, country_code)
    logo = entry.logo if is_https_url(entry.logo) else None
    website = channel.get("website") if is_https_url(channel.get("website")) else None
    languages = []
    for feed in upstream.get("feeds", []):
        if str(feed.get("channel") or "") == str(channel.get("id") or ""):
            languages.extend(str(value).casefold() for value in feed.get("languages", []) if value)
    if not languages and country_code in ARAB_COUNTRIES:
        languages = ["ara"]
    return {
        "id": str(channel["id"]),
        "upstreamId": str(channel["id"]),
        "name": str(channel.get("name") or entry.name or channel["id"]).strip()[:160],
        "country": country_name,
        "countryCode": country_code,
        "categories": normalize_category(channel.get("categories")),
        "languages": sorted(set(languages)),
        "logo": logo,
        "website": website,
        "enabled": True,
        "order": 0,
        "description": None,
        "safety": {"isNsfw": False, "reviewed": True},
        "streams": [stream],
    }


def _english_special_allowed(channel: dict, upstream: dict[str, list]) -> bool:
    country_code = str(channel.get("country") or "").upper()
    if country_code not in US_UK:
        return False
    categories = set(normalize_category(channel.get("categories")))
    if not ({"NEWS", "SPORTS"} & categories):
        return False
    languages: set[str] = set()
    channel_id = str(channel.get("id") or "")
    for feed in upstream.get("feeds", []):
        if str(feed.get("channel") or "") == channel_id:
            languages.update(str(value).casefold() for value in feed.get("languages", []) if value)
    if not languages:
        for country in upstream.get("countries", []):
            if str(country.get("code") or "").upper() == country_code:
                languages.update(str(value).casefold() for value in country.get("languages", []) if value)
                break
    return "eng" in languages or "en" in languages


def _channel_from_free_tv(entry: FreeTvEntry, stream: dict) -> dict:
    channel_id = _stable_synthetic_id(entry)
    return {
        "id": channel_id,
        "upstreamId": channel_id,
        "name": entry.name[:160],
        "country": ARABIC_COUNTRY_NAMES.get(entry.country_code, entry.country_code),
        "countryCode": entry.country_code,
        "categories": ["OTHER"],
        "languages": ["ara"] if entry.country_code in ARAB_COUNTRIES else [],
        "logo": entry.logo if is_https_url(entry.logo) else None,
        "website": None,
        "enabled": True,
        "order": 0,
        "description": None,
        "safety": {"isNsfw": False, "reviewed": True},
        "streams": [stream],
    }


def merge_free_tv(
    base_channels: list[dict],
    entries: list[FreeTvEntry],
    upstream: dict[str, list],
    adult_blocklist: dict,
    user_blocklist: dict,
    base_rejected: list[dict],
    base_policy_rejected: list[dict],
    base_quarantined: list[dict],
) -> FreeTvMergeResult:
    stats = {
        "freeTvPlaylistEntries": len(entries),
        "freeTvArabEntries": 0,
        "freeTvUsUkEntries": 0,
        "freeTvDirectHttpsEntries": 0,
        "freeTvMatchedById": 0,
        "freeTvMatchedByName": 0,
        "freeTvMatchedByAlias": 0,
        "freeTvIdCountryMismatchSkipped": 0,
        "freeTvNewChannels": 0,
        "freeTvMergedStreams": 0,
        "freeTvDuplicateEntriesSkipped": 0,
        "freeTvAmbiguousEntriesSkipped": 0,
        "freeTvNonDirectEntriesSkipped": 0,
        "freeTvUnsafeEntriesRejected": 0,
        "freeTvPolicyEntriesRejected": 0,
        "freeTvUserBlockedSkipped": 0,
        "freeTvOutOfScopeEntriesSkipped": 0,
    }

    channels_by_id = {
        str(item.get("id")): dict(item)
        for item in base_channels
        if item.get("id")
    }
    for item in channels_by_id.values():
        item["streams"] = [dict(stream) for stream in item.get("streams", [])]

    upstream_channels = {
        str(item.get("id")): item
        for item in upstream.get("channels", [])
        if item.get("id")
    }
    upstream_by_casefold_id = {
        key.casefold(): key for key in upstream_channels
    }
    upstream_names: dict[tuple[str, str], list[str]] = defaultdict(list)
    for channel_id, channel in upstream_channels.items():
        key = (str(channel.get("country") or "").upper(), _name_key(str(channel.get("name") or "")))
        if key[0] and key[1]:
            upstream_names[key].append(channel_id)

    candidate_names: dict[tuple[str, str], list[str]] = defaultdict(list)
    for channel_id, channel in channels_by_id.items():
        key = (str(channel.get("countryCode") or "").upper(), _name_key(str(channel.get("name") or "")))
        if key[0] and key[1]:
            candidate_names[key].append(channel_id)

    streams_by_channel: dict[str, list[dict]] = defaultdict(list)
    for raw in upstream.get("streams", []):
        if raw.get("channel"):
            streams_by_channel[str(raw["channel"])].append(raw)

    upstream_nsfw = {
        str(item.get("channel")) for item in upstream.get("blocklist", [])
        if str(item.get("reason") or "").casefold() == "nsfw" and item.get("channel")
    }
    upstream_dmca = {
        str(item.get("channel")) for item in upstream.get("blocklist", [])
        if str(item.get("reason") or "").casefold() == "dmca" and item.get("channel")
    }
    user_blocked = {str(value) for value in user_blocklist.get("channelIds", [])}
    base_rejected_ids = {str(item.get("channelId")) for item in base_rejected}
    base_policy_ids = {str(item.get("channelId")) for item in base_policy_rejected}
    base_quarantined_ids = {str(item.get("channelId")) for item in base_quarantined}

    rejected = list(base_rejected)
    policy_rejected = list(base_policy_rejected)
    quarantined = list(base_quarantined)
    rejected_ids = set(base_rejected_ids)

    # Exact URL ownership is another strong dedupe signal.
    url_owner: dict[str, str] = {}
    for channel_id, channel in channels_by_id.items():
        for stream in channel.get("streams", []):
            if stream.get("url"):
                url_owner[str(stream["url"])] = channel_id

    for entry in entries:
        if entry.country_code in ARAB_COUNTRIES:
            stats["freeTvArabEntries"] += 1
        elif entry.country_code in US_UK:
            stats["freeTvUsUkEntries"] += 1
        else:
            stats["freeTvOutOfScopeEntriesSkipped"] += 1
            continue
        stream = _free_tv_stream(entry)
        if stream is None:
            stats["freeTvNonDirectEntriesSkipped"] += 1
            continue
        stats["freeTvDirectHttpsEntries"] += 1

        matched_id: str | None = None
        matched_reason: str | None = None
        if entry.tvg_id:
            candidate = upstream_by_casefold_id.get(entry.tvg_id.casefold())
            if candidate:
                candidate_country = str(upstream_channels[candidate].get("country") or "").upper()
                if candidate_country == entry.country_code:
                    matched_id = candidate
                    matched_reason = "id"
        key = (entry.country_code, _name_key(entry.name))
        name_matches = upstream_names.get(key, [])
        alias_id = FREE_TV_IDENTITY_ALIASES.get(key)
        if alias_id is not None:
            alias_channel = upstream_channels.get(alias_id)
            alias_country = str((alias_channel or {}).get("country") or "").upper()
            if alias_channel is None or alias_country != entry.country_code:
                stats["freeTvAmbiguousEntriesSkipped"] += 1
                continue
            if matched_id is not None and matched_id != alias_id:
                stats["freeTvAmbiguousEntriesSkipped"] += 1
                continue
            matched_id = alias_id
            matched_reason = "alias"
        if matched_id is not None and len(name_matches) == 1 and name_matches[0] != matched_id:
            stats["freeTvAmbiguousEntriesSkipped"] += 1
            continue
        if matched_id is None and len(name_matches) == 1:
            matched_id = name_matches[0]
            matched_reason = "name"
        elif matched_id is None and len(name_matches) > 1:
            stats["freeTvAmbiguousEntriesSkipped"] += 1
            continue

        if matched_id:
            if entry.country_code in US_UK and not _english_special_allowed(upstream_channels[matched_id], upstream):
                stats["freeTvOutOfScopeEntriesSkipped"] += 1
                continue
            if matched_reason == "id":
                stats["freeTvMatchedById"] += 1
            elif matched_reason == "alias":
                stats["freeTvMatchedByAlias"] += 1
            else:
                stats["freeTvMatchedByName"] += 1
            if matched_id in user_blocked:
                stats["freeTvUserBlockedSkipped"] += 1
                continue
            if matched_id in upstream_dmca or matched_id in base_policy_ids:
                stats["freeTvPolicyEntriesRejected"] += 1
                continue
            if matched_id in base_rejected_ids or matched_id in base_quarantined_ids:
                stats["freeTvUnsafeEntriesRejected"] += 1
                continue
            raw_channel = upstream_channels[matched_id]
            raw_streams = streams_by_channel.get(matched_id, [])
            safety = evaluate_safety(raw_channel, raw_streams + [{"url": entry.url}], upstream_nsfw, adult_blocklist)
            if safety.action != "allow":
                stats["freeTvUnsafeEntriesRejected"] += 1
                continue

            target = channels_by_id.get(matched_id)
            if target is None:
                target = _channel_from_iptv_org(raw_channel, entry, stream, upstream)
                channels_by_id[matched_id] = target
                candidate_names[key].append(matched_id)
                stats["freeTvNewChannels"] += 1
            else:
                if entry.logo and target.get("logo") is None and is_https_url(entry.logo):
                    target["logo"] = entry.logo
                if entry.url in {str(item.get("url")) for item in target.get("streams", [])}:
                    stats["freeTvDuplicateEntriesSkipped"] += 1
                    continue
                owner = url_owner.get(entry.url)
                if owner and owner != matched_id:
                    stats["freeTvDuplicateEntriesSkipped"] += 1
                    continue
                target["streams"].append(stream)
                stats["freeTvMergedStreams"] += 1
            url_owner[entry.url] = matched_id
            _reorder_streams(target)
            continue

        # No IPTV-org identity. Match an already-normalized channel by exact country+name before creating a new one.
        existing_candidates = candidate_names.get(key, [])
        if len(existing_candidates) == 1:
            target_id = existing_candidates[0]
            target = channels_by_id[target_id]
            if entry.url in {str(item.get("url")) for item in target.get("streams", [])}:
                stats["freeTvDuplicateEntriesSkipped"] += 1
                continue
            owner = url_owner.get(entry.url)
            if owner and owner != target_id:
                stats["freeTvDuplicateEntriesSkipped"] += 1
                continue
            target["streams"].append(stream)
            if entry.logo and target.get("logo") is None and is_https_url(entry.logo):
                target["logo"] = entry.logo
            url_owner[entry.url] = target_id
            _reorder_streams(target)
            stats["freeTvMatchedByName"] += 1
            stats["freeTvMergedStreams"] += 1
            continue
        if len(existing_candidates) > 1:
            stats["freeTvAmbiguousEntriesSkipped"] += 1
            continue

        if entry.country_code in US_UK:
            # Preserve MYTV's original rule: Free-TV cannot introduce unclassified US/UK channels.
            # It may only supplement an identity that IPTV-org metadata already classifies as English NEWS/SPORTS.
            stats["freeTvOutOfScopeEntriesSkipped"] += 1
            continue

        # Never auto-create a new channel from a tvg-id whose country suffix contradicts
        # the playlist country. A wrong-country id is a strong signal that identity metadata
        # needs manual review (for example SomaliCableTV.uk tagged as SO).
        if entry.tvg_id and not _id_country_matches(entry.tvg_id, entry.country_code):
            stats["freeTvIdCountryMismatchSkipped"] += 1
            continue

        synthetic_id = _stable_synthetic_id(entry)
        if synthetic_id in user_blocked:
            stats["freeTvUserBlockedSkipped"] += 1
            continue
        # Apply the same adult identity/domain rules even though Free-TV's own policy already excludes adult channels.
        synthetic_safety_channel = {
            "id": synthetic_id,
            "name": entry.name,
            "country": entry.country_code,
            "categories": [],
            "is_nsfw": False,
        }
        safety = evaluate_safety(synthetic_safety_channel, [{"url": entry.url}], set(), adult_blocklist)
        if safety.action != "allow":
            stats["freeTvUnsafeEntriesRejected"] += 1
            if safety.action == "reject" and synthetic_id not in rejected_ids:
                rejected.append({"channelId": synthetic_id, "reason": safety.reason})
                rejected_ids.add(synthetic_id)
            continue
        owner = url_owner.get(entry.url)
        if owner:
            stats["freeTvDuplicateEntriesSkipped"] += 1
            continue
        if synthetic_id in channels_by_id:
            stats["freeTvDuplicateEntriesSkipped"] += 1
            continue

        target = _channel_from_free_tv(entry, stream)
        channels_by_id[synthetic_id] = target
        candidate_names[key].append(synthetic_id)
        url_owner[entry.url] = synthetic_id
        stats["freeTvNewChannels"] += 1
        _reorder_streams(target)

    priority_map = {code: index for index, code in enumerate(COUNTRY_ORDER)}
    channels = list(channels_by_id.values())
    channels.sort(key=lambda item: (
        priority_map.get(str(item.get("countryCode") or ""), len(COUNTRY_ORDER) + 1),
        str(item.get("name") or "").casefold(),
        str(item.get("id") or ""),
    ))
    for index, channel in enumerate(channels):
        _reorder_streams(channel)
        channel["order"] = index

    return FreeTvMergeResult(channels, rejected, policy_rejected, quarantined, stats)
