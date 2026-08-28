from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlparse

from .constants import ARABIC_COUNTRY_NAMES, ARAB_COUNTRIES, COUNTRY_ORDER
from .normalize import is_https_url, normalize_stream
from .safety import evaluate_safety

REASON_KEYS = (
    "APPROVED",
    "CANDIDATE_NOT_APPROVED",
    "USER_BLOCKLIST",
    "DMCA",
    "ADULT_REJECTED",
    "SAFETY_QUARANTINED",
    "NO_STREAMS",
    "HTTP_ONLY",
    "NO_HTTPS_STREAM",
    "INSECURE_REFERER",
    "INVALID_USER_AGENT",
    "INVALID_STREAM_METADATA",
    "OTHER_EXCLUDED",
)

HIDDEN_DETAIL_REASONS = {"ADULT_REJECTED", "SAFETY_QUARANTINED"}


def _plain_http_url(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        parsed = urlparse(value)
        return parsed.scheme.casefold() == "http" and bool(parsed.hostname)
    except ValueError:
        return False


def _invalid_user_agent(value: object) -> bool:
    if value is None:
        return False
    return (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 512
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    )


def _stream_exclusion_reason(raw_streams: list[dict]) -> str | None:
    if not raw_streams:
        return "NO_STREAMS"
    if any(normalize_stream(item) is not None for item in raw_streams):
        return None

    urls = [item.get("url") for item in raw_streams]
    if urls and all(_plain_http_url(value) for value in urls):
        return "HTTP_ONLY"
    if not any(is_https_url(value) for value in urls):
        return "NO_HTTPS_STREAM"

    https_streams = [item for item in raw_streams if is_https_url(item.get("url"))]
    if any(item.get("referrer") is not None and not is_https_url(item.get("referrer")) for item in https_streams):
        return "INSECURE_REFERER"
    if any(_invalid_user_agent(item.get("user_agent")) for item in https_streams):
        return "INVALID_USER_AGENT"
    return "INVALID_STREAM_METADATA"


def _country_name(code: str, upstream_countries: list[dict]) -> str:
    if code in ARABIC_COUNTRY_NAMES:
        return ARABIC_COUNTRY_NAMES[code]
    for item in upstream_countries:
        if str(item.get("code") or "").upper() == code:
            return str(item.get("name") or code)
    return code


def _safe_detail(channel: dict, reason: str, raw_streams: list[dict]) -> dict:
    return {
        "channelId": str(channel.get("id") or ""),
        "name": str(channel.get("name") or channel.get("id") or "").strip()[:160],
        "reason": reason,
        "rawStreamCount": len(raw_streams),
        "httpsStreamCount": sum(1 for item in raw_streams if is_https_url(item.get("url"))),
        "validStreamCount": sum(1 for item in raw_streams if normalize_stream(item) is not None),
    }


def build_arab_channel_audit(
    upstream: dict[str, list],
    adult_blocklist: dict,
    user_blocklist: dict,
    candidate: dict,
    approved: dict,
    generated_at: str,
) -> dict:
    channels = {str(item.get("id")): item for item in upstream["channels"] if item.get("id")}
    streams_by_channel: dict[str, list[dict]] = defaultdict(list)
    for stream in upstream["streams"]:
        if stream.get("channel"):
            streams_by_channel[str(stream["channel"])].append(stream)

    upstream_nsfw = {
        str(item.get("channel")) for item in upstream["blocklist"]
        if str(item.get("reason") or "").casefold() == "nsfw" and item.get("channel")
    }
    upstream_dmca = {
        str(item.get("channel")) for item in upstream["blocklist"]
        if str(item.get("reason") or "").casefold() == "dmca" and item.get("channel")
    }
    user_blocked = {str(value) for value in user_blocklist.get("channelIds", [])}
    candidate_ids = {str(item.get("id")) for item in candidate.get("channels", []) if item.get("id")}
    approved_enabled_ids = {
        str(item.get("id")) for item in approved.get("channels", [])
        if item.get("id") and item.get("enabled") is not False
    }

    by_country: dict[str, dict] = {}
    for code in COUNTRY_ORDER:
        by_country[code] = {
            "countryCode": code,
            "country": _country_name(code, upstream.get("countries", [])),
            "upstreamChannels": 0,
            "channelsWithAnyStream": 0,
            "channelsWithHttpsStream": 0,
            "channelsWithValidStream": 0,
            "candidateChannels": 0,
            "approvedCurrentChannels": 0,
            "excludedBeforeCandidate": 0,
            "hiddenDetailsCount": 0,
            "reasons": {key: 0 for key in REASON_KEYS},
            "items": [],
        }

    for channel_id in sorted(channels):
        channel = channels[channel_id]
        country_code = str(channel.get("country") or "").upper()
        if country_code not in ARAB_COUNTRIES:
            continue
        country = by_country.setdefault(country_code, {
            "countryCode": country_code,
            "country": _country_name(country_code, upstream.get("countries", [])),
            "upstreamChannels": 0,
            "channelsWithAnyStream": 0,
            "channelsWithHttpsStream": 0,
            "channelsWithValidStream": 0,
            "candidateChannels": 0,
            "approvedCurrentChannels": 0,
            "excludedBeforeCandidate": 0,
            "hiddenDetailsCount": 0,
            "reasons": {key: 0 for key in REASON_KEYS},
            "items": [],
        })
        raw_streams = streams_by_channel.get(channel_id, [])
        country["upstreamChannels"] += 1
        if raw_streams:
            country["channelsWithAnyStream"] += 1
        if any(is_https_url(item.get("url")) for item in raw_streams):
            country["channelsWithHttpsStream"] += 1
        if any(normalize_stream(item) is not None for item in raw_streams):
            country["channelsWithValidStream"] += 1

        safety = evaluate_safety(channel, raw_streams, upstream_nsfw, adult_blocklist)
        if channel_id in candidate_ids:
            country["candidateChannels"] += 1
            if channel_id in approved_enabled_ids:
                reason = "APPROVED"
                country["approvedCurrentChannels"] += 1
            else:
                reason = "CANDIDATE_NOT_APPROVED"
        elif channel_id in user_blocked:
            reason = "USER_BLOCKLIST"
        elif channel_id in upstream_dmca:
            reason = "DMCA"
        elif safety.action == "reject":
            reason = "ADULT_REJECTED"
        elif safety.action == "quarantine":
            reason = "SAFETY_QUARANTINED"
        else:
            reason = _stream_exclusion_reason(raw_streams) or "OTHER_EXCLUDED"

        country["reasons"][reason] += 1
        if reason not in {"APPROVED", "CANDIDATE_NOT_APPROVED"}:
            country["excludedBeforeCandidate"] += 1

        if reason == "APPROVED":
            continue
        if reason in HIDDEN_DETAIL_REASONS or safety.action != "allow":
            country["hiddenDetailsCount"] += 1
            continue
        country["items"].append(_safe_detail(channel, reason, raw_streams))

    for country in by_country.values():
        country["items"].sort(key=lambda item: (item["reason"], item["name"].casefold(), item["channelId"]))

    countries = [by_country[code] for code in COUNTRY_ORDER]
    reason_totals = {key: sum(country["reasons"][key] for country in countries) for key in REASON_KEYS}
    summary = {
        "upstreamChannels": sum(country["upstreamChannels"] for country in countries),
        "channelsWithAnyStream": sum(country["channelsWithAnyStream"] for country in countries),
        "channelsWithHttpsStream": sum(country["channelsWithHttpsStream"] for country in countries),
        "channelsWithValidStream": sum(country["channelsWithValidStream"] for country in countries),
        "candidateChannels": sum(country["candidateChannels"] for country in countries),
        "approvedCurrentChannels": sum(country["approvedCurrentChannels"] for country in countries),
        "excludedBeforeCandidate": sum(country["excludedBeforeCandidate"] for country in countries),
        "hiddenDetailsCount": sum(country["hiddenDetailsCount"] for country in countries),
        "reasons": reason_totals,
    }
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "source": "iptv-org",
        "scope": "ARAB_COUNTRIES",
        "summary": summary,
        "countries": countries,
    }


def render_arab_channel_audit_markdown(audit: dict) -> str:
    summary = audit["summary"]
    lines = [
        "# MYTV Arab channel audit",
        "",
        f"Generated: `{audit['generatedAt']}`",
        "",
        "This report audits Arab-country channels from IPTV-org without exposing adult or safety-quarantined channel identities or stream URLs.",
        "",
        "## Summary",
        "",
        f"- Upstream Arab channels: **{summary['upstreamChannels']}**",
        f"- Eligible candidate channels: **{summary['candidateChannels']}**",
        f"- Currently approved channels from this upstream set: **{summary['approvedCurrentChannels']}**",
        f"- Excluded before candidate generation: **{summary['excludedBeforeCandidate']}**",
        f"- Hidden safety details: **{summary['hiddenDetailsCount']}**",
        "",
        "## Countries",
        "",
        "| Country | Source | Any stream | HTTPS | Valid stream | Candidate | Approved | Excluded | +18 | Safety quarantine | HTTP only | No streams | DMCA | User block |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for country in audit["countries"]:
        reasons = country["reasons"]
        lines.append(
            f"| {country['country']} | {country['upstreamChannels']} | {country['channelsWithAnyStream']} | "
            f"{country['channelsWithHttpsStream']} | {country['channelsWithValidStream']} | {country['candidateChannels']} | "
            f"{country['approvedCurrentChannels']} | {country['excludedBeforeCandidate']} | {reasons['ADULT_REJECTED']} | "
            f"{reasons['SAFETY_QUARANTINED']} | {reasons['HTTP_ONLY']} | {reasons['NO_STREAMS']} | "
            f"{reasons['DMCA']} | {reasons['USER_BLOCKLIST']} |"
        )

    lines.extend(["", "## Safe details by country", ""])
    for country in audit["countries"]:
        if not country["items"] and country["hiddenDetailsCount"] == 0:
            continue
        lines.append(f"### {country['country']} ({country['countryCode']})")
        lines.append("")
        if country["hiddenDetailsCount"]:
            lines.append(
                f"- **{country['hiddenDetailsCount']}** item(s) are intentionally hidden because safety checks did not allow identity disclosure."
            )
        for item in country["items"]:
            lines.append(
                f"- `{item['reason']}` — **{item['name']}** (`{item['channelId']}`); "
                f"streams: {item['rawStreamCount']}, HTTPS: {item['httpsStreamCount']}, valid: {item['validStreamCount']}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
