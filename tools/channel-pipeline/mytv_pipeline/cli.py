from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .audit import build_arab_channel_audit, render_arab_channel_audit_markdown
from .constants import COUNTRY_ORDER, FREE_TV_PLAYLIST_URL, UPSTREAM_URLS
from .diff import canonical, generate_changes, summary
from .free_tv import merge_free_tv, parse_free_tv_playlist
from .net import load_free_tv_playlist, load_upstream
from .normalize import normalize
from .validation import validate_arab_audit, validate_channels, validate_links, validate_pending


def read_json(path: Path, default=None):
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def utc_now(value: str | None = None) -> str:
    if value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def update(repo_root: Path, upstream_dir: Path | None, now: str | None) -> dict:
    checked_at = utc_now(now)
    approved_path = repo_root / "data/approved/channels.json"
    approved = read_json(approved_path)
    validate_channels(approved)
    adult_blocklist = read_json(repo_root / "data/blocklists/adult-blocklist.json", {"channelIds": []})
    user_blocklist = read_json(repo_root / "data/blocklists/user-blocklist.json", {"channelIds": []})
    upstream = load_upstream(upstream_dir)
    result = normalize(upstream, adult_blocklist, user_blocklist)
    free_tv_entries = parse_free_tv_playlist(load_free_tv_playlist(upstream_dir))
    free_tv = merge_free_tv(
        result.channels,
        free_tv_entries,
        upstream,
        adult_blocklist,
        user_blocklist,
        result.rejected,
        result.policy_rejected,
        result.quarantined,
    )
    result.channels = free_tv.channels
    result.rejected = free_tv.rejected
    result.policy_rejected = free_tv.policy_rejected
    result.quarantined = free_tv.quarantined

    content_changed = canonical(result.channels) != canonical(approved.get("channels", []))
    candidate_version = int(approved["version"]) + (1 if content_changed else 0)
    candidate = {
        "schemaVersion": 1,
        "version": candidate_version,
        "generatedAt": checked_at,
        "countryOrder": COUNTRY_ORDER,
        "channels": result.channels,
    }
    # A successful source check producing no eligible channels is treated as suspicious, not as a wipe.
    validate_channels(candidate)

    previous_pending = read_json(
        repo_root / "data/review/pending-changes.json",
        {"schemaVersion": 1, "changes": []},
    )
    changes = generate_changes(approved, candidate, previous_pending, checked_at)
    pending = {
        "schemaVersion": 1,
        "generatedAt": checked_at,
        "approvedVersion": approved["version"],
        "candidateVersion": candidate["version"],
        "summary": summary(changes, len(result.rejected), len(result.quarantined)),
        "changes": changes,
    }
    validate_pending(pending)

    rejected_summary = {
        "schemaVersion": 1,
        "generatedAt": checked_at,
        "adultRejectedCount": len(result.rejected),
        "items": result.rejected,
    }
    quarantine = {
        "schemaVersion": 1,
        "generatedAt": checked_at,
        "count": len(result.quarantined),
        # Deliberately contains no names, URLs, logos, or previews.
        "items": result.quarantined,
    }
    arab_audit = build_arab_channel_audit(
        upstream, adult_blocklist, user_blocklist, candidate, approved, checked_at,
    )
    validate_arab_audit(arab_audit)

    source_check = {
        "schemaVersion": 1,
        "checkedAt": checked_at,
        "status": "SUCCESS",
        "sources": list(UPSTREAM_URLS.values()) + [FREE_TV_PLAYLIST_URL],
        "upstreamCounts": {
            **{key: len(value) for key, value in sorted(upstream.items())},
            **free_tv.stats,
        },
        "candidateChannels": len(candidate["channels"]),
        "pendingChanges": len(changes),
        "adultRejectedCount": len(result.rejected),
        "policyRejectedCount": len(result.policy_rejected),
        "safetyQuarantinedCount": len(result.quarantined),
    }
    write_json_atomic(repo_root / "data/candidate/channels-candidate.json", candidate)
    write_json_atomic(repo_root / "data/candidate/rejected-adult-summary.json", rejected_summary)
    write_json_atomic(repo_root / "data/candidate/quarantined-safety.json", quarantine)
    write_json_atomic(repo_root / "data/review/pending-changes.json", pending)
    write_json_atomic(repo_root / "data/metadata/source-check.json", source_check)
    write_json_atomic(repo_root / "data/metadata/arab-channel-audit.json", arab_audit)
    audit_md = repo_root / "data/metadata/arab-channel-audit.md"
    audit_md.parent.mkdir(parents=True, exist_ok=True)
    audit_md.write_text(render_arab_channel_audit_markdown(arab_audit), encoding="utf-8")
    return source_check


def validate_repository(repo_root: Path) -> None:
    validate_channels(read_json(repo_root / "data/approved/channels.json"))
    validate_links(read_json(repo_root / "data/approved/links.json"))
    candidate_path = repo_root / "data/candidate/channels-candidate.json"
    if candidate_path.exists():
        validate_channels(read_json(candidate_path))
    pending_path = repo_root / "data/review/pending-changes.json"
    if pending_path.exists():
        validate_pending(read_json(pending_path))
    audit_path = repo_root / "data/metadata/arab-channel-audit.json"
    if audit_path.exists():
        validate_arab_audit(read_json(audit_path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MYTV multi-source review pipeline (IPTV-org + Free-TV)")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
    subparsers = parser.add_subparsers(dest="command", required=True)
    update_parser = subparsers.add_parser("update", help="fetch, normalize, and generate review files")
    update_parser.add_argument("--upstream-dir", type=Path)
    update_parser.add_argument("--now", help="fixed ISO timestamp for reproducible tests")
    subparsers.add_parser("validate", help="validate MYTV repository data")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.repo_root.resolve()
    if args.command == "update":
        report = update(root, args.upstream_dir, args.now)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        validate_repository(root)
        print("MYTV data validation passed")
    return 0
