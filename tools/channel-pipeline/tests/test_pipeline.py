from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE_ROOT))

from mytv_pipeline.cli import update, validate_repository
from mytv_pipeline.decisions import apply_decisions
from mytv_pipeline.diff import generate_changes
from mytv_pipeline.net import load_upstream
from mytv_pipeline.normalize import normalize, normalize_stream
from mytv_pipeline.safety import evaluate_safety
from mytv_pipeline.validation import validate_channels, validate_links, validate_pending

FIXTURES = Path(__file__).parent / "fixtures"


def channel(channel_id: str, url: str = "https://media.example.org/live.m3u8") -> dict:
    return {
        "id": channel_id,
        "upstreamId": channel_id,
        "name": channel_id,
        "country": "اليمن",
        "countryCode": "YE",
        "categories": ["GENERAL"],
        "languages": ["ara"],
        "logo": None,
        "website": None,
        "enabled": True,
        "order": 0,
        "description": None,
        "safety": {"isNsfw": False, "reviewed": True},
        "streams": [{
            "url": url, "quality": "720p", "userAgent": None, "referer": None,
            "priority": 0, "source": "test",
            "geo": {"label": None, "isGeoBlocked": False, "blockedCountries": [], "allowedCountries": []},
        }],
    }


def channels_doc(items: list[dict], version: int = 1) -> dict:
    return {
        "schemaVersion": 1, "version": version, "generatedAt": "2026-08-20T00:00:00Z",
        "countryOrder": ["YE"], "channels": items,
    }


class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.upstream = load_upstream(FIXTURES)
        self.adult = {"channelIds": [], "domains": [], "categories": [], "keywords": []}

    def test_filters_adult_http_user_blocked_and_unsafe_metadata(self):
        result = normalize(self.upstream, self.adult, {"channelIds": ["UserBlocked.ye"]})
        ids = {item["id"] for item in result.channels}
        self.assertNotIn("AdultXXX.us", ids)
        self.assertNotIn("BlockedAdult.ye", ids)
        self.assertNotIn("HttpOnly.ye", ids)
        self.assertNotIn("MissingSafety.ye", ids)
        self.assertNotIn("UserBlocked.ye", ids)
        self.assertEqual(2, len(result.rejected))
        self.assertEqual(1, len(result.quarantined))

    def test_keeps_arabic_library_and_only_us_uk_english_news_sports(self):
        result = normalize(self.upstream, self.adult, {"channelIds": []})
        ids = {item["id"] for item in result.channels}
        self.assertTrue({"YemenSports.ye", "ArabGeneral.eg", "UsNews.us", "UkSports.uk"} <= ids)
        self.assertNotIn("CanadaSports.ca", ids)

    def test_fallback_order_is_1080_720_480_other_then_us_blocked(self):
        result = normalize(self.upstream, self.adult, {"channelIds": []})
        yemen = next(item for item in result.channels if item["id"] == "YemenSports.ye")
        self.assertEqual(
            ["yemen-1080.m3u8", "yemen-720.m3u8", "yemen-480.m3u8", "yemen-other.m3u8", "yemen-geoblocked.m3u8"],
            [stream["url"].rsplit("/", 1)[-1] for stream in yemen["streams"]],
        )
        self.assertEqual(list(range(5)), [stream["priority"] for stream in yemen["streams"]])

    def test_iptv_org_dmca_blocklist_is_rejected_without_counting_as_adult(self):
        upstream = deepcopy(self.upstream)
        upstream["blocklist"].append({"channel": "ArabGeneral.eg", "reason": "dmca"})
        result = normalize(upstream, self.adult, {"channelIds": []})
        ids = {item["id"] for item in result.channels}
        self.assertNotIn("ArabGeneral.eg", ids)
        self.assertEqual(1, len(result.policy_rejected))
        self.assertEqual(2, len(result.rejected))

    def test_permanent_domain_blocklist_rejects_without_preview(self):
        item = {"id": "SafeName.ye", "name": "اسم عادي", "categories": ["general"], "is_nsfw": False}
        decision = evaluate_safety(
            item,
            [{"url": "https://adult.invalid/live.m3u8"}],
            set(),
            {"channelIds": [], "domains": ["adult.invalid"], "categories": [], "keywords": []},
        )
        self.assertEqual("reject", decision.action)

    def test_rejects_header_control_characters(self):
        self.assertIsNone(normalize_stream({
            "url": "https://media.example.org/live.m3u8",
            "user_agent": "MYTV\r\nX-Injected: yes",
        }))


class DiffTests(unittest.TestCase):
    def test_stream_change_is_stable_and_not_duplicated_daily(self):
        old = channels_doc([channel("One.ye", "https://media.example.org/a.m3u8")])
        new = channels_doc([channel("One.ye", "https://media.example.org/b.m3u8")], version=2)
        first = generate_changes(old, new, detected_at="2026-08-20T00:00:00Z")
        previous = {"changes": first}
        second = generate_changes(old, new, previous, detected_at="2026-08-27T00:00:00Z")
        self.assertEqual([item["id"] for item in first], [item["id"] for item in second])
        self.assertEqual(first[0]["detectedAt"], second[0]["detectedAt"])
        self.assertEqual("CHANGE_STREAMS", first[0]["type"])

    def test_logo_only_is_auto_approved_but_not_directly_published(self):
        old_channel = channel("One.ye")
        new_channel = deepcopy(old_channel)
        new_channel["logo"] = "https://images.example.org/new.png"
        changes = generate_changes(channels_doc([old_channel]), channels_doc([new_channel], 2))
        self.assertEqual(1, len(changes))
        self.assertFalse(changes[0]["requiresApproval"])
        self.assertEqual("AUTO_APPROVED", changes[0]["status"])


class DecisionTests(unittest.TestCase):
    def test_approval_changes_stream_rejection_keeps_old(self):
        old = channels_doc([channel("One.ye", "https://media.example.org/a.m3u8")])
        new = channels_doc([channel("One.ye", "https://media.example.org/b.m3u8")], 2)
        changes = generate_changes(old, new, detected_at="2026-08-20T00:00:00Z")
        pending = {"changes": changes}
        kept = apply_decisions(old, new, pending, {changes[0]["id"]: "REJECT"})
        changed = apply_decisions(old, new, pending, {changes[0]["id"]: "APPROVE"})
        self.assertTrue(kept["channels"][0]["streams"][0]["url"].endswith("a.m3u8"))
        self.assertTrue(changed["channels"][0]["streams"][0]["url"].endswith("b.m3u8"))

    def test_upstream_removal_requires_approval_and_disables(self):
        old = channels_doc([channel("One.ye")])
        new = channels_doc([], 2)
        changes = generate_changes(old, new, detected_at="2026-08-20T00:00:00Z")
        output = apply_decisions(old, new, {"changes": changes}, {changes[0]["id"]: "APPROVE"})
        self.assertFalse(output["channels"][0]["enabled"])


class ValidationTests(unittest.TestCase):
    def test_rejects_http_duplicates_and_malformed_documents(self):
        invalid = channels_doc([channel("One.ye", "http://example.org/a.m3u8")])
        with self.assertRaises(ValueError):
            validate_channels(invalid)
        duplicate = channels_doc([channel("One.ye"), channel("One.ye")])
        with self.assertRaises(ValueError):
            validate_channels(duplicate)
        with self.assertRaises(ValueError):
            validate_links({"schemaVersion": 1, "version": 1, "links": [{"id": "x", "url": "http://x", "type": "AUTO"}]})
        with self.assertRaises(ValueError):
            validate_pending({"schemaVersion": 1, "changes": [{"id": "same"}, {"id": "same"}]})
        invalid_header = channels_doc([channel("Header.ye")])
        invalid_header["channels"][0]["streams"][0]["userAgent"] = "MYTV\nInjected"
        with self.assertRaises(ValueError):
            validate_channels(invalid_header)

    def test_full_fixture_update_and_repository_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("data/approved", "data/blocklists", "data/review"):
                (root / relative).mkdir(parents=True, exist_ok=True)
            (root / "data/approved/channels.json").write_text(
                json.dumps(channels_doc([channel("Existing.ye")])), encoding="utf-8",
            )
            (root / "data/approved/links.json").write_text(
                json.dumps({"schemaVersion": 1, "version": 1, "generatedAt": None, "links": []}),
                encoding="utf-8",
            )
            (root / "data/blocklists/adult-blocklist.json").write_text(
                json.dumps({"channelIds": [], "domains": [], "categories": [], "keywords": []}),
                encoding="utf-8",
            )
            (root / "data/blocklists/user-blocklist.json").write_text(
                json.dumps({"channelIds": []}), encoding="utf-8",
            )
            report = update(root, FIXTURES, "2026-08-27T00:00:00Z")
            validate_repository(root)
            self.assertEqual("SUCCESS", report["status"])
            pending = json.loads((root / "data/review/pending-changes.json").read_text(encoding="utf-8"))
            self.assertGreater(pending["summary"]["totalPending"], 0)
            quarantine = json.loads((root / "data/candidate/quarantined-safety.json").read_text(encoding="utf-8"))
            self.assertNotIn("url", json.dumps(quarantine))


if __name__ == "__main__":
    unittest.main()
