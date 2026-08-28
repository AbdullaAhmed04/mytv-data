from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_CATEGORIES = {
    "NEWS", "SPORTS", "MOVIES", "SERIES", "KIDS", "MUSIC", "RELIGIOUS",
    "DOCUMENTARY", "CULTURE", "GENERAL", "GOVERNMENT", "OTHER",
}


def _https(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        parsed = urlparse(value)
        return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def validate_channels(document: dict, allow_empty: bool = False) -> None:
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise ValueError("Invalid channel schemaVersion")
    if not isinstance(document.get("version"), int) or document["version"] < 1:
        raise ValueError("Invalid channel version")
    channels = document.get("channels")
    if not isinstance(channels, list) or (not channels and not allow_empty) or len(channels) > 8000:
        raise ValueError("Invalid channels array")
    ids: set[str] = set()
    for channel in channels:
        if not isinstance(channel, dict) or not isinstance(channel.get("id"), str) or not channel["id"]:
            raise ValueError("Invalid channel id")
        if channel["id"] in ids:
            raise ValueError("Duplicate channel id")
        ids.add(channel["id"])
        if not isinstance(channel.get("name"), str) or not channel["name"].strip():
            raise ValueError("Invalid channel name")
        if not isinstance(channel.get("country"), str) or not channel["country"].strip():
            raise ValueError("Invalid channel country")
        if channel.get("safety") != {"isNsfw": False, "reviewed": True}:
            raise ValueError("Unsafe or unreviewed channel")
        categories = channel.get("categories")
        if not isinstance(categories, list) or not categories or not set(categories) <= ALLOWED_CATEGORIES:
            raise ValueError("Invalid categories")
        logo = channel.get("logo")
        website = channel.get("website")
        if logo is not None and not _https(logo):
            raise ValueError("Invalid logo")
        if website is not None and not _https(website):
            raise ValueError("Invalid website")
        streams = channel.get("streams")
        if not isinstance(streams, list) or not 1 <= len(streams) <= 8:
            raise ValueError("Invalid streams")
        urls: set[str] = set()
        for stream in streams:
            if not isinstance(stream, dict) or not _https(stream.get("url")):
                raise ValueError("Invalid stream URL")
            if stream["url"] in urls:
                raise ValueError("Duplicate stream URL")
            urls.add(stream["url"])
            if stream.get("referer") is not None and not _https(stream["referer"]):
                raise ValueError("Invalid stream referer")
            user_agent = stream.get("userAgent")
            if user_agent is not None and (
                not isinstance(user_agent, str)
                or not 1 <= len(user_agent) <= 512
                or any(ord(character) < 0x20 or ord(character) == 0x7F for character in user_agent)
            ):
                raise ValueError("Invalid stream user agent")


def validate_links(document: dict) -> None:
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise ValueError("Invalid links schemaVersion")
    if not isinstance(document.get("version"), int) or document["version"] < 1:
        raise ValueError("Invalid links version")
    links = document.get("links")
    if not isinstance(links, list) or len(links) > 2000:
        raise ValueError("Invalid links array")
    ids: set[str] = set()
    for link in links:
        if not isinstance(link, dict) or not isinstance(link.get("id"), str) or not link["id"]:
            raise ValueError("Invalid link id")
        if link["id"] in ids:
            raise ValueError("Duplicate link id")
        ids.add(link["id"])
        if not _https(link.get("url")):
            raise ValueError("Invalid link URL")
        if link.get("type") not in {"DIRECT_STREAM", "WEB_PAGE", "AUTO"}:
            raise ValueError("Invalid link type")
        image = link.get("image")
        if image is not None and not _https(image):
            raise ValueError("Invalid link image")


def validate_pending(document: dict) -> None:
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise ValueError("Invalid pending schemaVersion")
    changes = document.get("changes")
    if not isinstance(changes, list):
        raise ValueError("Invalid changes")
    ids = [item.get("id") for item in changes if isinstance(item, dict)]
    if len(ids) != len(changes) or len(ids) != len(set(ids)):
        raise ValueError("Invalid or duplicate change IDs")

AUDIT_REASON_KEYS = {
    "APPROVED", "CANDIDATE_NOT_APPROVED", "USER_BLOCKLIST", "DMCA",
    "ADULT_REJECTED", "SAFETY_QUARANTINED", "NO_STREAMS", "HTTP_ONLY",
    "NO_HTTPS_STREAM", "INSECURE_REFERER", "INVALID_USER_AGENT",
    "INVALID_STREAM_METADATA", "OTHER_EXCLUDED",
}


def validate_arab_audit(document: dict) -> None:
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise ValueError("Invalid audit schemaVersion")
    if document.get("source") != "iptv-org" or document.get("scope") != "ARAB_COUNTRIES":
        raise ValueError("Invalid audit source or scope")
    if not isinstance(document.get("generatedAt"), str) or not document["generatedAt"]:
        raise ValueError("Invalid audit generatedAt")
    summary = document.get("summary")
    countries = document.get("countries")
    if not isinstance(summary, dict) or not isinstance(countries, list):
        raise ValueError("Invalid audit structure")
    codes: set[str] = set()
    totals = {
        "upstreamChannels": 0,
        "channelsWithAnyStream": 0,
        "channelsWithHttpsStream": 0,
        "channelsWithValidStream": 0,
        "candidateChannels": 0,
        "approvedCurrentChannels": 0,
        "excludedBeforeCandidate": 0,
        "hiddenDetailsCount": 0,
    }
    reason_totals = {key: 0 for key in AUDIT_REASON_KEYS}
    for country in countries:
        if not isinstance(country, dict):
            raise ValueError("Invalid audit country")
        code = country.get("countryCode")
        if not isinstance(code, str) or not code or code in codes:
            raise ValueError("Invalid or duplicate audit countryCode")
        codes.add(code)
        if not isinstance(country.get("country"), str) or not country["country"]:
            raise ValueError("Invalid audit country name")
        for key in totals:
            value = country.get(key)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"Invalid audit count: {key}")
            totals[key] += value
        reasons = country.get("reasons")
        if not isinstance(reasons, dict) or set(reasons) != AUDIT_REASON_KEYS:
            raise ValueError("Invalid audit reasons")
        for key, value in reasons.items():
            if not isinstance(value, int) or value < 0:
                raise ValueError("Invalid audit reason count")
            reason_totals[key] += value
        if sum(reasons.values()) != country["upstreamChannels"]:
            raise ValueError("Audit country accounting mismatch")
        if reasons["APPROVED"] + reasons["CANDIDATE_NOT_APPROVED"] != country["candidateChannels"]:
            raise ValueError("Audit candidate accounting mismatch")
        if country["upstreamChannels"] - country["candidateChannels"] != country["excludedBeforeCandidate"]:
            raise ValueError("Audit exclusion accounting mismatch")
        if reasons["APPROVED"] != country["approvedCurrentChannels"]:
            raise ValueError("Audit approved accounting mismatch")
        items = country.get("items")
        if not isinstance(items, list):
            raise ValueError("Invalid audit items")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Invalid audit item")
            if item.get("reason") not in AUDIT_REASON_KEYS - {"APPROVED", "ADULT_REJECTED", "SAFETY_QUARANTINED"}:
                raise ValueError("Unsafe or invalid audit item reason")
            if not isinstance(item.get("channelId"), str) or not item["channelId"]:
                raise ValueError("Invalid audit item channelId")
            if not isinstance(item.get("name"), str) or not item["name"]:
                raise ValueError("Invalid audit item name")
            for count_key in ("rawStreamCount", "httpsStreamCount", "validStreamCount"):
                if not isinstance(item.get(count_key), int) or item[count_key] < 0:
                    raise ValueError("Invalid audit stream count")
            if any(key in item for key in ("url", "logo", "website", "referer", "userAgent")):
                raise ValueError("Audit item leaked media metadata")
    for key, value in totals.items():
        if summary.get(key) != value:
            raise ValueError(f"Audit summary mismatch: {key}")
    reasons = summary.get("reasons")
    if not isinstance(reasons, dict) or set(reasons) != AUDIT_REASON_KEYS:
        raise ValueError("Invalid audit summary reasons")
    if reasons != reason_totals:
        raise ValueError("Audit reason totals mismatch")
    if sum(reason_totals.values()) != summary["upstreamChannels"]:
        raise ValueError("Audit summary accounting mismatch")
