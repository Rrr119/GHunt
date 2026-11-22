from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from ghunt.apis.peoplepa import PeoplePaHttp
from ghunt.helpers.masking import MASK_NONE, mask_person
from ghunt.objects.base import GHuntCreds
from ghunt.objects.encoders import GHuntEncoder
from ghunt.objects.profile_cache import ProfileCache
from ghunt.parsers.people import (
    Person,
    PersonContainers,
    PersonEmail,
    PersonExtendedData,
    PersonDynamiteExtendedData,
    PersonGplusExtendedData,
    PersonInAppReachability,
    PersonName,
    PersonPhoto,
    PersonProfileInfo,
    PersonSourceIds,
)
from datetime import datetime

import json


@dataclass
class ProfileResult:
    """
    Result of a profile fetch.

    ``person`` stays a plain ghunt.parsers.people.Person so every existing
    caller (modules, examples, third-party code) keeps working unchanged.
    """

    found: bool
    person: Person
    from_cache: bool = False
    changes: List[Dict[str, Any]] = field(default_factory=list)
    masked_fields: List[str] = field(default_factory=list)
    cache_entry: Optional[Dict[str, Any]] = None


class ProfileService:
    """
    Incrementally cached and optionally masked Google account profiles.

    The service is a thin layer over PeoplePaHttp: it does not change any
    existing API method signature or return type, it only adds caching and
    masking on top of the same Person object.
    """

    def __init__(
        self,
        creds: GHuntCreds,
        cache: Optional[ProfileCache] = None,
        cache_dir: Optional[Path] = None,
        enable_cache: bool = True,
    ):
        self.people_api = PeoplePaHttp(creds)
        self.enable_cache = enable_cache
        self.cache = cache or (ProfileCache(cache_dir) if enable_cache else None)

    async def lookup_by_email(
        self,
        as_client: httpx.AsyncClient,
        email: str,
        params_template: str = "max_details",
        ttl: Optional[int] = None,
        mask_level: str = MASK_NONE,
        mask_fields: Optional[List[str]] = None,
    ) -> ProfileResult:
        """Cached equivalent of PeoplePaHttp.people_lookup()."""
        return await self._fetch(
            as_client, "people_lookup", email.lower().strip(),
            email, params_template, ttl, mask_level, mask_fields,
        )

    async def get_by_gaia_id(
        self,
        as_client: httpx.AsyncClient,
        gaia_id: str,
        params_template: str = "max_details",
        ttl: Optional[int] = None,
        mask_level: str = MASK_NONE,
        mask_fields: Optional[List[str]] = None,
    ) -> ProfileResult:
        """Cached equivalent of PeoplePaHttp.people()."""
        return await self._fetch(
            as_client, "people", gaia_id.strip(),
            gaia_id, params_template, ttl, mask_level, mask_fields,
        )

    async def _fetch(
        self,
        as_client: httpx.AsyncClient,
        source: str,
        cache_key: str,
        identifier: str,
        params_template: str,
        ttl: Optional[int],
        mask_level: str,
        mask_fields: Optional[List[str]],
    ) -> ProfileResult:
        if self.cache and ttl and self.cache.is_fresh(cache_key, ttl):
            snapshot, entry = self.cache.get_latest(cache_key)
            if snapshot is not None:
                person = self._person_from_snapshot(snapshot)
                masked = mask_person(person, mask_level, mask_fields) if mask_level != MASK_NONE else []
                return ProfileResult(
                    found=True,
                    person=person,
                    from_cache=True,
                    changes=[],
                    masked_fields=masked,
                    cache_entry=entry,
                )

        if source == "people_lookup":
            found, person = await self.people_api.people_lookup(as_client, identifier, params_template)
        else:
            found, person = await self.people_api.people(as_client, identifier, params_template)

        if not found:
            return ProfileResult(found=False, person=person)

        entry = None
        changes = []
        if self.cache:
            profile_data = json.loads(json.dumps(person, cls=GHuntEncoder))
            entry = self.cache.save(
                cache_key,
                profile_data,
                source=source,
                metadata={"params_template": params_template, "identifier": identifier},
            )
            changes = entry["snapshots"][-1]["changes"]

        masked = mask_person(person, mask_level, mask_fields) if mask_level != MASK_NONE else []
        return ProfileResult(
            found=True,
            person=person,
            from_cache=False,
            changes=changes,
            masked_fields=masked,
            cache_entry=entry,
        )

    @staticmethod
    def _person_from_snapshot(snapshot: Dict[str, Any]) -> Person:
        """Rebuilds a fully-typed Person from a cached JSON snapshot."""
        person = Person()
        person.personId = snapshot.get("personId", "")

        person.emails = PersonContainers()
        for container, data in snapshot.get("emails", {}).items():
            email = PersonEmail()
            email.value = data.get("value", "")
            person.emails[container] = email

        person.names = PersonContainers()
        for container, data in snapshot.get("names", {}).items():
            name = PersonName()
            name.fullname = data.get("fullname", "")
            name.firstName = data.get("firstName", "")
            name.lastName = data.get("lastName", "")
            person.names[container] = name

        person.sourceIds = PersonContainers()
        for container, data in snapshot.get("sourceIds", {}).items():
            source_ids = PersonSourceIds()
            raw_date = data.get("lastUpdated")
            if isinstance(raw_date, str):
                try:
                    source_ids.lastUpdated = datetime.strptime(raw_date, "%Y/%m/%d %H:%M:%S (UTC)")
                except ValueError:
                    source_ids.lastUpdated = None
            else:
                source_ids.lastUpdated = raw_date
            person.sourceIds[container] = source_ids

        person.profileInfos = PersonContainers()
        for container, data in snapshot.get("profileInfos", {}).items():
            info = PersonProfileInfo()
            info.userTypes = data.get("userTypes", [])
            person.profileInfos[container] = info

        for attr in ("profilePhotos", "coverPhotos"):
            containers = PersonContainers()
            for container, data in snapshot.get(attr, {}).items():
                photo = PersonPhoto()
                photo.url = data.get("url", "")
                photo.isDefault = data.get("isDefault", False)
                photo.flathash = data.get("flathash")
                containers[container] = photo
            setattr(person, attr, containers)

        person.inAppReachability = PersonContainers()
        for container, data in snapshot.get("inAppReachability", {}).items():
            reachability = PersonInAppReachability()
            reachability.apps = data.get("apps", [])
            person.inAppReachability[container] = reachability

        extended = PersonExtendedData()
        ext_data = snapshot.get("extendedData", {})
        if isinstance(ext_data, dict):
            dynamite = PersonDynamiteExtendedData()
            for key, value in ext_data.get("dynamiteData", {}).items():
                setattr(dynamite, key, value)
            gplus = PersonGplusExtendedData()
            for key, value in ext_data.get("gplusData", {}).items():
                setattr(gplus, key, value)
            extended.dynamiteData = dynamite
            extended.gplusData = gplus
        person.extendedData = extended
        return person
