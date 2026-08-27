from __future__ import annotations

from copy import deepcopy


def apply_decisions(approved: dict, candidate: dict, pending: dict, decisions: dict[str, str]) -> dict:
    """Pure reference implementation used by tests and the Windows review logic.

    A channel only switches to its candidate form when every required change for that channel is approved.
    An approved upstream removal disables rather than deletes the existing record.
    """
    old_by_id = {item["id"]: deepcopy(item) for item in approved.get("channels", [])}
    new_by_id = {item["id"]: deepcopy(item) for item in candidate.get("channels", [])}
    changes_by_channel: dict[str, list[dict]] = {}
    for change in pending.get("changes", []):
        changes_by_channel.setdefault(change["channelId"], []).append(change)

    result: list[dict] = []
    for channel_id in sorted(old_by_id.keys() | new_by_id.keys()):
        changes = changes_by_channel.get(channel_id, [])
        if any(decisions.get(item["id"]) == "BLOCK" for item in changes):
            continue
        required = [item for item in changes if item.get("requiresApproval")]
        approved_all = bool(required) and all(decisions.get(item["id"]) == "APPROVE" for item in required)
        if channel_id not in old_by_id:
            if approved_all and channel_id in new_by_id:
                result.append(new_by_id[channel_id])
            continue
        if channel_id not in new_by_id:
            kept = old_by_id[channel_id]
            if approved_all:
                kept["enabled"] = False
            result.append(kept)
            continue
        if approved_all:
            result.append(new_by_id[channel_id])
        else:
            kept = old_by_id[channel_id]
            # Logo-only updates are safe to carry forward even while other changes await approval.
            auto_logo = any(item["type"] == "LOGO_ONLY" and not item["requiresApproval"] for item in changes)
            if auto_logo:
                kept["logo"] = new_by_id[channel_id].get("logo")
            result.append(kept)

    result.sort(key=lambda item: (item.get("order", 10000), item["id"]))
    output = deepcopy(approved)
    output["version"] = max(int(approved.get("version", 1)) + 1, int(candidate.get("version", 1)))
    output["channels"] = result
    return output
