import json
from datetime import datetime

import pytest

from ghunt.objects.encoders import GHuntEncoder
from ghunt.parsers.people import (
    Person,
    PersonEmail,
    PersonExtendedData,
    PersonDynamiteExtendedData,
    PersonGplusExtendedData,
    PersonInAppReachability,
    PersonPhoto,
    PersonProfileInfo,
    PersonSourceIds,
)


def build_person(
    gaia_id: str,
    email: str,
    *,
    custom_photo: bool = True,
    customer_id: str = "",
    enterprise: bool = False,
    user_types=None,
    apps=None,
    last_updated: datetime = None,
) -> Person:
    """Builds a realistic in-memory Person without any network call."""
    person = Person()
    person.personId = gaia_id

    person_email = PersonEmail()
    person_email.value = email
    person.emails["PROFILE"] = person_email

    source_ids = PersonSourceIds()
    source_ids.lastUpdated = last_updated or datetime(2026, 11, 1, 10, 0, 0)
    person.sourceIds["PROFILE"] = source_ids

    photo = PersonPhoto()
    photo.url = f"https://lh3.googleusercontent.com/a-/ALV-Uj{gaia_id}=s100"
    photo.isDefault = not custom_photo
    photo.flathash = "ff00ff00ff00ff00" if custom_photo else "000018183c3c0000"
    person.profilePhotos["PROFILE"] = photo

    cover = PersonPhoto()
    cover.url = f"https://lh3.googleusercontent.com/-abc/cover-{gaia_id}"
    cover.isDefault = not custom_photo
    person.coverPhotos["PROFILE"] = cover

    profile_info = PersonProfileInfo()
    profile_info.userTypes = list(user_types or ["GOOGLE_USER"])
    person.profileInfos["PROFILE"] = profile_info

    reachability = PersonInAppReachability()
    reachability.apps = list(apps or ["Gmail", "Chat", "Meet"])
    person.inAppReachability["PROFILE"] = reachability

    dynamite = PersonDynamiteExtendedData()
    dynamite.entityType = "ENTERPRISE_USER" if enterprise else "CONSUMER_USER"
    dynamite.customerId = customer_id
    person.extendedData = PersonExtendedData()
    person.extendedData.dynamiteData = dynamite

    gplus = PersonGplusExtendedData()
    gplus.isEntrepriseUser = enterprise
    person.extendedData.gplusData = gplus
    return person


@pytest.fixture
def personal_account() -> Person:
    """Type 1: regular consumer Google account with a custom picture."""
    return build_person(
        "112233445566778899001",
        "alice.personal@gmail.com",
        custom_photo=True,
        user_types=["GOOGLE_USER"],
        apps=["Gmail", "Chat", "Meet", "Photos"],
    )


@pytest.fixture
def enterprise_account() -> Person:
    """Type 2: Workspace enterprise account with a Chat customer ID."""
    return build_person(
        "998877665544332211009",
        "bob.manager@corp-example.com",
        custom_photo=True,
        customer_id="C0123456789-abcd",
        enterprise=True,
        user_types=["GOOGLE_USER", "WORKSPACE_USER"],
        apps=["Gmail", "Chat", "Meet", "Drive", "Calendar"],
    )


@pytest.fixture
def restricted_account() -> Person:
    """Type 3: minimal / privacy-restricted account (default picture, no apps)."""
    person = build_person(
        "555000111222333444555",
        "quiet.user@protonmail.com",
        custom_photo=False,
        user_types=["GOOGLE_USER"],
        apps=[],
    )
    person.coverPhotos["PROFILE"].isDefault = True
    return person


@pytest.fixture
def account_types(personal_account, enterprise_account, restricted_account):
    """The three required account test profiles, keyed by a readable name."""
    return {
        "personal": personal_account,
        "enterprise": enterprise_account,
        "restricted": restricted_account,
    }


@pytest.fixture
def snapshot(personal_account):
    """JSON-ready representation of a Person, as produced by GHuntEncoder."""
    return json.loads(json.dumps(personal_account, cls=GHuntEncoder))
