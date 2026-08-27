from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _change_id(channel_id: str, change_type: str, field: str, old, new) -> str:
    digest = hashlib.sha256(
        f"{channel_id}\0{change_type}\0{field}\0{canonical(old)}\0{canonical(new)}".encode("utf-8")
    ).hexdigest()[:24]
    return f"chg-{digest}"


def _record(channel_id: str, change_type: str, field: str, old, new, detected_at: str,
            requires_approval: bool = True) -> dict:
    return {
        "id": _change_id(channel_id, change_type, field, old, new),
        "channelId": channel_id,
        "type": change_type,
        "field": field,
        "status": "PENDING" if requires_approval else "AUTO_APPROVED",
        "requiresApproval": requires_approval,
        "detectedAt": detected_at,
        "old": old,
        "new": new,
    }


def generate_changes(approved: dict, candidate: dict, previous_pending: dict | None = None,
                     detected_at: str | None = None) -> list[dict]:
    detected_at = detected_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    previous_dates = {
        item.get("id"): item.get("detectedAt")
        for item in (previous_pending or {}).get("changes", [])
        if item.get("id") and item.get("detectedAt")
    }
    old_by_id = {item["id"]: item for item in approved.get("channels", [])}
    new_by_id = {item["id"]: item for item in candidate.get("channels", [])}
    changes: list[dict] = []
    for channel_id in sorted(old_by_id.keys() | new_by_id.keys()):
        old = old_by_id.get(channel_id)
        new = new_by_id.get(channel_id)
        if old is None:
            changes.append(_record(channel_id, "ADD_CHANNEL", "channel", None, new, detected_at))
            continue
        if new is None:
            changes.append(_record(channel_id, "UPSTREAM_MISSING", "channel", old, None, detected_at))
            continue

        simple_fields = [
            ("name", "CHANGE_IDENTITY", True),
            ("country", "CHANGE_COUNTRY", True),
            ("countryCode", "CHANGE_COUNTRY", True),
            ("categories", "CHANGE_CATEGORIES", True),
            ("languages", "CHANGE_LANGUAGES", True),
            ("enabled", "CHANGE_ENABLED", True),
            ("logo", "LOGO_ONLY", False),
            ("website", "CHANGE_METADATA", True),
            ("description", "CHANGE_METADATA", True),
        ]
        for field, change_type, approval in simple_fields:
            if old.get(field) != new.get(field):
                changes.append(_record(channel_id, change_type, field, old.get(field), new.get(field),
                                       detected_at, approval))

        old_streams = old.get("streams", [])
        new_streams = new.get("streams", [])
        old_urls = [item.get("url") for item in old_streams]
        new_urls = [item.get("url") for item in new_streams]
        if old_urls != new_urls:
            changes.append(_record(channel_id, "CHANGE_STREAMS", "streams", old_streams, new_streams,
                                   detected_at))
        else:
            comparisons = [
                ("quality", "CHANGE_QUALITY"),
                ("userAgent", "CHANGE_STREAM_HEADERS"),
                ("referer", "CHANGE_STREAM_HEADERS"),
                ("geo", "CHANGE_GEO"),
                ("priority", "CHANGE_STREAM_ORDER"),
            ]
            for field, change_type in comparisons:
                old_values = [item.get(field) for item in old_streams]
                new_values = [item.get(field) for item in new_streams]
                if old_values != new_values:
                    changes.append(_record(channel_id, change_type, f"streams.{field}", old_values,
                                           new_values, detected_at))

    for item in changes:
        item["detectedAt"] = previous_dates.get(item["id"], item["detectedAt"])
    return sorted(changes, key=lambda item: (item["channelId"], item["type"], item["field"]))


def summary(changes: list[dict], rejected_count: int, quarantined_count: int) -> dict:
    counts: dict[str, int] = {}
    for item in changes:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    return {
        "totalPending": sum(1 for item in changes if item["requiresApproval"]),
        "byType": dict(sorted(counts.items())),
        "adultRejectedCount": rejected_count,
        "safetyQuarantinedCount": quarantined_count,
    }
