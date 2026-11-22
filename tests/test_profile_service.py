import json

import pytest

from ghunt.helpers.masking import MASK_FULL, MASK_NONE, MASK_PARTIAL, REDACTED
from ghunt.helpers.profile_service import ProfileResult, ProfileService
from ghunt.objects.encoders import GHuntEncoder
from ghunt.objects.profile_cache import ProfileCache
from ghunt.parsers.people import Person


class DummyPeopleApi:
    """In-memory stand-in for PeoplePaHttp: no network, no credentials."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.lookup_calls = 0
        self.people_calls = 0
        self.templates_seen = []

    async def people_lookup(self, as_client, email, params_template="just_gaia_id"):
        self.lookup_calls += 1
        self.templates_seen.append(params_template)
        if self.lookup_calls > len(self._responses):
            return False, Person()
        response = self._responses[self.lookup_calls - 1]
        return response is not None, response

    async def people(self, as_client, gaia_id, params_template="just_name"):
        self.people_calls += 1
        self.templates_seen.append(params_template)
        if self.people_calls > len(self._responses):
            return False, Person()
        response = self._responses[self.people_calls - 1]
        return response is not None, response


def make_service(tmp_path, responses):
    cache = ProfileCache(tmp_path)
    service = ProfileService.__new__(ProfileService)
    service.people_api = DummyPeopleApi(responses)
    service.cache = cache
    return service, service.people_api, cache


@pytest.mark.parametrize("account_name", ["personal", "enterprise", "restricted"])
async def test_lookup_by_email_for_three_account_types(account_name, request, tmp_path):
    person = request.getfixturevalue(f"{account_name}_account")
    service, dummy_api, cache = make_service(tmp_path, [person])

    result = await service.lookup_by_email(None, person.emails["PROFILE"].value)

    assert isinstance(result, ProfileResult)
    assert result.found is True
    assert result.from_cache is False
    assert isinstance(result.person, Person)
    assert result.person.personId == person.personId
    assert dummy_api.lookup_calls == 1
    assert dummy_api.templates_seen == ["max_details"]
    assert cache.load(person.emails["PROFILE"].value.lower()) is not None


async def test_second_fetch_uses_cache_within_ttl_and_skips_google(personal_account, tmp_path):
    service, dummy_api, cache = make_service(tmp_path, [personal_account])
    email = personal_account.emails["PROFILE"].value

    first = await service.lookup_by_email(None, email)
    second = await service.lookup_by_email(None, email, ttl=3600)

    assert dummy_api.lookup_calls == 1
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.person.personId == personal_account.personId


async def test_expired_ttl_triggers_incremental_refresh(personal_account, tmp_path):
    service, dummy_api, cache = make_service(tmp_path, [personal_account, personal_account])
    email = personal_account.emails["PROFILE"].value

    await service.lookup_by_email(None, email)
    refreshed = await service.lookup_by_email(None, email, ttl=0)

    assert dummy_api.lookup_calls == 2
    assert refreshed.from_cache is False


async def test_incremental_changes_are_returned(personal_account, tmp_path):
    import copy

    updated_person = Person()
    updated_person.personId = personal_account.personId
    photo = copy.deepcopy(personal_account.profilePhotos["PROFILE"])
    photo.url = "https://lh3.googleusercontent.com/a-/CHANGED=s100"
    updated_person.profilePhotos = {"PROFILE": photo}

    service, _, _ = make_service(tmp_path, [personal_account, updated_person])
    email = personal_account.emails["PROFILE"].value

    await service.lookup_by_email(None, email)
    result = await service.lookup_by_email(None, email)

    changed_fields = {change["field"] for change in result.changes}
    assert "profilePhotos.PROFILE.url" in changed_fields


async def test_masking_applies_to_live_and_cached_person(enterprise_account, tmp_path):
    service, _, _ = make_service(tmp_path, [enterprise_account])
    email = enterprise_account.emails["PROFILE"].value

    result = await service.lookup_by_email(None, email, mask_level=MASK_PARTIAL)
    assert result.masked_fields
    assert result.person.personId.startswith("***")
    assert "***" in result.person.emails["PROFILE"].value

    cached = await service.lookup_by_email(None, email, ttl=3600, mask_level=MASK_FULL)
    assert cached.from_cache is True
    assert cached.person.personId == REDACTED
    assert cached.person.emails["PROFILE"].value == REDACTED


async def test_get_by_gaia_id_path_and_not_found(restricted_account, tmp_path):
    service, dummy_api, cache = make_service(tmp_path, [restricted_account])

    result = await service.get_by_gaia_id(None, restricted_account.personId)
    assert result.found is True
    assert dummy_api.people_calls == 1
    assert cache.load(restricted_account.personId) is not None

    not_found = await service.get_by_gaia_id(None, "000000000000000000000")
    assert not_found.found is False
    assert isinstance(not_found.person, Person)
    assert not_found.cache_entry is None


async def test_cached_snapshot_roundtrips_through_json_encoder(personal_account, tmp_path):
    service, _, cache = make_service(tmp_path, [personal_account])
    email = personal_account.emails["PROFILE"].value
    await service.lookup_by_email(None, email)

    snapshot, entry = cache.get_latest(email)
    reencoded = json.loads(json.dumps(snapshot, cls=GHuntEncoder))
    assert reencoded["personId"] == personal_account.personId
    assert reencoded["emails"]["PROFILE"]["value"] == email


async def test_default_masking_and_cache_options_preserve_person_api(personal_account, tmp_path):
    """Backward compatibility: defaults behave like a plain PeoplePaHttp call."""
    service, dummy_api, _ = make_service(tmp_path, [personal_account])
    result = await service.lookup_by_email(None, personal_account.emails["PROFILE"].value)

    assert result.person.personId == personal_account.personId
    assert result.person.emails["PROFILE"].value == personal_account.emails["PROFILE"].value
    assert result.masked_fields == []
    assert result.changes[0]["change"] == "initial"
    assert MASK_NONE == "none"
