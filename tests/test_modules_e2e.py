import json
from pathlib import Path

import pytest

from ghunt.helpers import auth
from ghunt.helpers import calendar as gcalendar
from ghunt.helpers import gmaps, playgames
from ghunt.helpers import knowledge
from ghunt.modules import email as email_module
from ghunt.modules import gaia as gaia_module
from ghunt.objects.base import GHuntCreds
from ghunt.objects.profile_cache import ProfileCache


@pytest.fixture
def stubbed_ecosystem(monkeypatch):
    """Replaces every network-touching helper used by the email/gaia modules."""
    async def fake_load_and_auth(as_client):
        creds = GHuntCreds(creds_path=str(Path("/tmp/ghunt-fake-creds.m")))
        creds.cookies = {"SAPISID": "fake"}
        creds.osids = {"people": "fake"}
        creds.android.master_token = "fake"
        return creds

    async def fake_search_player(creds, as_client, email_address):
        return []

    async def fake_reviews(as_client, gaia_id):
        return "", {"reviews": 0, "photos": 0, "stars_avg": 0.0}

    async def fake_calendar(creds, as_client, email_address):
        class _Events:
            items = []
        return False, None, _Events()

    monkeypatch.setattr(auth, "load_and_auth", fake_load_and_auth)
    monkeypatch.setattr(email_module.auth, "load_and_auth", fake_load_and_auth)
    monkeypatch.setattr(gaia_module.auth, "load_and_auth", fake_load_and_auth)
    monkeypatch.setattr(playgames, "search_player", fake_search_player)
    monkeypatch.setattr(email_module.playgames, "search_player", fake_search_player)
    monkeypatch.setattr(gmaps, "get_reviews", fake_reviews)
    monkeypatch.setattr(email_module.gmaps, "get_reviews", fake_reviews)
    monkeypatch.setattr(gaia_module.gmaps, "get_reviews", fake_reviews)
    monkeypatch.setattr(gcalendar, "fetch_all", fake_calendar)
    monkeypatch.setattr(email_module.gcalendar, "fetch_all", fake_calendar)
    monkeypatch.setattr(knowledge, "get_user_type_definition", lambda user_type: f"definition of {user_type}")
    monkeypatch.setattr(email_module, "get_user_type_definition", lambda user_type: f"definition of {user_type}")
    monkeypatch.setattr(gaia_module, "get_user_type_definition", lambda user_type: f"definition of {user_type}")


@pytest.mark.parametrize("account_name", ["personal", "enterprise", "restricted"])
async def test_email_module_json_export_is_compatible_and_masked(
    account_name, request, tmp_path, monkeypatch, stubbed_ecosystem
):
    person = request.getfixturevalue(f"{account_name}_account")

    async def fake_lookup(self, as_client, email, params_template="max_details"):
        return True, person

    monkeypatch.setattr(
        "ghunt.apis.peoplepa.PeoplePaHttp.people_lookup", fake_lookup
    )

    json_path = tmp_path / "out.json"
    await email_module.hunt(
        None, person.emails["PROFILE"].value, json_path,
        mask_level="partial",
    )

    data = json.loads(json_path.read_text())
    assert "PROFILE_CONTAINER" in data
    container = data["PROFILE_CONTAINER"]
    assert set(container) >= {"profile", "play_games", "maps", "calendar"}
    assert container["profile"]["personId"].startswith("***")
    assert "***" in container["profile"]["emails"]["PROFILE"]["value"]
    assert data["MASKING_METADATA"]["level"] == "partial"
    assert data["MASKING_METADATA"]["masked_fields"]
    assert "CACHE_METADATA" not in data


async def test_email_module_incremental_cache_runs_without_refetch(
    personal_account, tmp_path, monkeypatch, stubbed_ecosystem
):
    call_count = {"n": 0}

    async def fake_lookup(self, as_client, email, params_template="max_details"):
        call_count["n"] += 1
        return True, personal_account

    monkeypatch.setattr(
        "ghunt.apis.peoplepa.PeoplePaHttp.people_lookup", fake_lookup
    )
    monkeypatch.setattr(
        "ghunt.objects.profile_cache.ProfileCache.__init__",
        lambda self, cache_dir=None: _tmp_init(self, tmp_path),
    )

    json_path = tmp_path / "out.json"
    await email_module.hunt(None, personal_account.emails["PROFILE"].value, json_path, use_cache=True)
    await email_module.hunt(
        None, personal_account.emails["PROFILE"].value, json_path,
        use_cache=True, cache_ttl=3600,
    )

    assert call_count["n"] == 1
    cache_files = list(tmp_path.glob("*.json"))
    assert any(personal_account.emails["PROFILE"].value in f.name for f in cache_files)
    data = json.loads(json_path.read_text())
    assert data["CACHE_METADATA"]["served_from_cache"] is True


async def test_gaia_module_supports_cache_and_masking(
    enterprise_account, tmp_path, monkeypatch, stubbed_ecosystem
):
    async def fake_people(self, as_client, gaia_id, params_template="max_details"):
        return True, enterprise_account

    monkeypatch.setattr("ghunt.apis.peoplepa.PeoplePaHttp.people", fake_people)
    monkeypatch.setattr(
        "ghunt.objects.profile_cache.ProfileCache.__init__",
        lambda self, cache_dir=None: _tmp_init(self, tmp_path),
    )

    json_path = tmp_path / "gaia.json"
    await gaia_module.hunt(
        None, enterprise_account.personId, json_path,
        use_cache=True, mask_level="full",
    )

    data = json.loads(json_path.read_text())
    profile = data["PROFILE_CONTAINER"]["profile"]
    assert profile["personId"] == "***REDACTED***"
    assert profile["emails"]["PROFILE"]["value"] == "***REDACTED***"
    assert data["CACHE_METADATA"]["served_from_cache"] is False
    assert data["MASKING_METADATA"]["level"] == "full"


def _tmp_init(self, tmp_path):
    from pathlib import Path as _Path
    self.cache_dir = _Path(tmp_path)
