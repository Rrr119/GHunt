import time
from datetime import datetime

import pytest

from ghunt.objects.profile_cache import ProfileCache


def test_first_fetch_creates_initial_snapshot(tmp_path, snapshot):
    cache = ProfileCache(tmp_path)
    entry = cache.save("alice.personal@gmail.com", snapshot)

    assert entry["schema_version"] == ProfileCache.SCHEMA_VERSION
    assert len(entry["snapshots"]) == 1
    assert entry["history"][0]["change_count"] == 1
    assert entry["snapshots"][0]["changes"][0]["change"] == "initial"
    assert (tmp_path / "alice.personal@gmail.com.json").is_file()


def test_incremental_merge_keeps_unchanged_and_reports_diff(tmp_path, snapshot):
    cache = ProfileCache(tmp_path)
    cache.save("alice.personal@gmail.com", snapshot)

    updated = {
        "profilePhotos": {
            "PROFILE": {
                "url": "https://lh3.googleusercontent.com/a-/NEW-PHOTO=s100",
                "isDefault": False,
                "flathash": "1111222233334444",
            }
        }
    }
    entry = cache.save("alice.personal@gmail.com", updated)

    assert len(entry["snapshots"]) == 2
    latest = entry["snapshots"][-1]
    changed_fields = {change["field"] for change in latest["changes"]}
    assert "profilePhotos.PROFILE.url" in changed_fields
    assert "profilePhotos.PROFILE.flathash" in changed_fields
    assert "personId" not in changed_fields

    merged = latest["data"]
    assert merged["profilePhotos"]["PROFILE"]["url"] == "https://lh3.googleusercontent.com/a-/NEW-PHOTO=s100"
    assert merged["personId"] == snapshot["personId"]
    assert merged["emails"]["PROFILE"]["value"] == snapshot["emails"]["PROFILE"]["value"]
    assert entry["history"][-1]["changed_fields"] == sorted(changed_fields)


def test_merge_preserves_fields_absent_from_new_fetch(tmp_path, snapshot):
    cache = ProfileCache(tmp_path)
    cache.save("alice.personal@gmail.com", snapshot)

    entry = cache.save("alice.personal@gmail.com", {"personId": snapshot["personId"]})
    merged = entry["snapshots"][-1]["data"]
    assert merged["emails"]["PROFILE"]["value"] == snapshot["emails"]["PROFILE"]["value"]
    assert merged["profilePhotos"]["PROFILE"]["url"] == snapshot["profilePhotos"]["PROFILE"]["url"]
    assert entry["snapshots"][-1]["changes"] == []


def test_removed_and_added_fields_are_tracked(tmp_path, snapshot):
    cache = ProfileCache(tmp_path)
    cache.save("alice.personal@gmail.com", snapshot)

    # An explicit None marks a field as intentionally removed; incremental
    # merge otherwise keeps fields absent from the new fetch.
    entry = cache.save(
        "alice.personal@gmail.com",
        {"personId": snapshot["personId"], "emails": None, "newField": "surprise"},
    )
    changes = {change["field"]: change["change"] for change in entry["snapshots"][-1]["changes"]}
    assert changes["newField"] == "added"
    assert changes["emails"] == "removed"


def test_ttl_freshness_and_cache_reuse(tmp_path, snapshot):
    cache = ProfileCache(tmp_path)
    cache.save("alice.personal@gmail.com", snapshot)

    assert cache.is_fresh("alice.personal@gmail.com", ttl=3600) is True
    time.sleep(1.1)
    assert cache.is_fresh("alice.personal@gmail.com", ttl=1) is False
    assert cache.is_fresh("alice.personal@gmail.com", ttl=None) is False
    assert cache.is_fresh("unknown@nowhere.com", ttl=3600) is False


def test_targets_are_cached_independently(tmp_path, snapshot, account_types):
    cache = ProfileCache(tmp_path)
    for name, person in account_types.items():
        data = _to_json(person)
        cache.save(data["emails"]["PROFILE"]["value"], data)

    assert len(list(tmp_path.glob("*.json"))) == 3
    data, _ = cache.get_latest("alice.personal@gmail.com")
    assert data["personId"] == account_types["personal"].personId
    assert cache.load("BOB.MANAGER@corp-example.com")["target"] == "bob.manager@corp-example.com"


def test_corrupted_cache_is_handled_gracefully(tmp_path, snapshot):
    cache = ProfileCache(tmp_path)
    cache.save("alice.personal@gmail.com", snapshot)
    (tmp_path / "alice.personal@gmail.com.json").write_text("{not json")
    assert cache.load("alice.personal@gmail.com") is None
    data, entry = cache.get_latest("alice.personal@gmail.com")
    assert data is None and entry is None


def test_clear_single_target_and_whole_cache(tmp_path, account_types):
    cache = ProfileCache(tmp_path)
    for person in account_types.values():
        data = _to_json(person)
        cache.save(data["emails"]["PROFILE"]["value"], data)

    assert cache.clear("alice.personal@gmail.com") == 1
    assert cache.load("alice.personal@gmail.com") is None
    assert cache.clear("alice.personal@gmail.com") == 0
    assert cache.clear() == 2
    assert list(tmp_path.glob("*.json")) == []


def test_datetime_values_are_serialized_in_cache(tmp_path):
    cache = ProfileCache(tmp_path)
    entry = cache.save(
        "target@gmail.com",
        {"sourceIds": {"PROFILE": {"lastUpdated": datetime(2026, 1, 2, 3, 4, 5)}}},
    )
    reloaded = cache.load("target@gmail.com")
    assert reloaded["snapshots"][-1]["data"] == {"sourceIds": {"PROFILE": {"lastUpdated": "2026/01/02 03:04:05 (UTC)"}}}
    assert "(UTC)" in reloaded["snapshots"][-1]["data"]["sourceIds"]["PROFILE"]["lastUpdated"]


def _to_json(person):
    import json
    from ghunt.objects.encoders import GHuntEncoder
    return json.loads(json.dumps(person, cls=GHuntEncoder))
