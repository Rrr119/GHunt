import pytest

from ghunt.helpers.masking import (
    MASK_FULL,
    MASK_NONE,
    MASK_PARTIAL,
    REDACTED,
    mask_dict,
    mask_email,
    mask_gaia_id,
    mask_person,
    mask_url,
)


def test_mask_email_partial_and_full():
    assert mask_email("alice.personal@gmail.com", MASK_PARTIAL) == "a***@gmail.com"
    assert mask_email("bob.manager@corp-example.com", MASK_PARTIAL) == "b***@corp-example.com"
    assert mask_email("alice.personal@gmail.com", MASK_FULL) == REDACTED


def test_mask_gaia_id_keeps_only_last_digits():
    assert mask_gaia_id("112233445566778899001", MASK_PARTIAL) == "***9001"
    assert mask_gaia_id("12", MASK_PARTIAL) == "**"
    assert mask_gaia_id("112233445566778899001", MASK_FULL) == REDACTED


def test_mask_url_keeps_host_but_hides_path():
    url = "https://lh3.googleusercontent.com/a-/ALV-UjPhoto=s100"
    masked = mask_url(url, MASK_PARTIAL)
    assert masked == "https://lh3.googleusercontent.com/***"
    assert mask_url(url, MASK_FULL) == REDACTED


@pytest.mark.parametrize("account_name", ["personal", "enterprise", "restricted"])
def test_mask_person_partial_for_all_account_types(account_name, request):
    person = request.getfixturevalue(f"{account_name}_account")
    masked_fields = mask_person(person, MASK_PARTIAL)

    assert person.personId.endswith(tuple(["9001", "1009", "4555"]))
    assert person.personId.startswith("***")
    assert person.emails["PROFILE"].value.endswith(
        ("@gmail.com", "@corp-example.com", "@protonmail.com")
    )
    assert "***" in person.emails["PROFILE"].value
    assert person.profilePhotos["PROFILE"].url.startswith("https://")
    assert person.profilePhotos["PROFILE"].url.endswith("/***")
    assert person.profilePhotos["PROFILE"].flathash == REDACTED
    assert any(path.endswith("personId") for path in masked_fields)
    assert any("emails.PROFILE.value" in path for path in masked_fields)


def test_full_masking_redacts_every_sensitive_value(account_types):
    for person in account_types.values():
        masked_fields = mask_person(person, MASK_FULL)
        assert person.personId == REDACTED
        assert person.emails["PROFILE"].value == REDACTED
        assert person.profilePhotos["PROFILE"].url == REDACTED
        assert person.coverPhotos["PROFILE"].url == REDACTED
        assert person.profilePhotos["PROFILE"].flathash == REDACTED
        assert masked_fields


def test_enterprise_customer_id_is_masked(enterprise_account):
    mask_person(enterprise_account, MASK_PARTIAL)
    assert enterprise_account.extendedData.dynamiteData.customerId == "***-789-abcd" or \
        enterprise_account.extendedData.dynamiteData.customerId.startswith("***")
    mask_person(enterprise_account, MASK_FULL)
    assert enterprise_account.extendedData.dynamiteData.customerId == REDACTED


def test_mask_none_is_a_noop(personal_account):
    original_id = personal_account.personId
    masked_fields = mask_person(personal_account, MASK_NONE)
    assert masked_fields == []
    assert personal_account.personId == original_id


def test_extra_fields_are_fully_redacted(personal_account):
    masked_fields = mask_person(personal_account, MASK_PARTIAL, extra_fields=["extendedData.gplusData"])
    assert personal_account.extendedData.gplusData == REDACTED
    assert any(path.startswith("extendedData.gplusData") for path in masked_fields)
    assert personal_account.personId.startswith("***")


def test_mask_dict_does_not_mutate_input_by_default(snapshot):
    masked_copy, masked_fields = mask_dict(snapshot, MASK_PARTIAL)
    assert snapshot["personId"] == "112233445566778899001"
    assert masked_copy["personId"] == "***9001"
    assert masked_copy["emails"]["PROFILE"]["value"] == "a***@gmail.com"
    assert masked_fields


def test_invalid_masking_level_raises(personal_account):
    with pytest.raises(ValueError):
        mask_person(personal_account, "bogus")
    with pytest.raises(ValueError):
        mask_dict({}, "bogus")
