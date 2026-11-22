import os

from ghunt import globals as gb
from ghunt.objects.base import GHuntCreds
from ghunt.apis.peoplepa import PeoplePaHttp
from ghunt.apis.vision import VisionHttp
from ghunt.helpers import gmaps, auth, ia
from ghunt.helpers.masking import MASK_NONE
from ghunt.helpers.knowledge import get_user_type_definition
from ghunt.helpers.profile_service import ProfileService
from ghunt.objects.profile_cache import ProfileCache
from ghunt.helpers.utils import get_httpx_client

import httpx

from typing import *
from pathlib import Path


async def hunt(
    as_client: httpx.AsyncClient,
    gaia_id: str,
    json_file: Path=None,
    use_cache: bool=False,
    cache_ttl: int=None,
    mask_level: str=MASK_NONE,
    mask_fields: List[str]=None,
):
    if not as_client:
        as_client = get_httpx_client()

    ghunt_creds = await auth.load_and_auth(as_client)

    # #gb.rc.print("\n[+] Target found !", style="spring_green3")

    people_pa = PeoplePaHttp(ghunt_creds)
    # # vision_api = VisionHttp(ghunt_creds)
    cache = ProfileCache() if use_cache else None
    if use_cache or mask_level != MASK_NONE:
        service = ProfileService(ghunt_creds, cache=cache, enable_cache=use_cache)
        result = await service.get_by_gaia_id(
            as_client, gaia_id,
            ttl=cache_ttl, mask_level=mask_level, mask_fields=mask_fields,
        )
        is_found, target = result.found, result.person
        from_cache, changes, masked_fields = result.from_cache, result.changes, result.masked_fields
    else:
        is_found, target = await people_pa.people(as_client, gaia_id, params_template="max_details")
        from_cache, changes, masked_fields = False, [], []
    if not is_found:
        exit("[-] The target wasn't found.")

    if cache:
        gb.rc.print("💾 Incremental cache\n", style="bright_cyan")
        if from_cache:
            print("[+] Profile served from cache (fresh TTL, no Google query).")
        elif changes and changes[0].get("change") != "initial":
            print(f"[+] Cache updated, {len(changes)} changed field(s) :")
            for change in changes:
                print(f"- {change['field']} : {change['old']!r} -> {change['new']!r}")
        else:
            print("[+] Profile cached for the first time.")
        print()

    if json_file:
        json_results = {}

    containers = target.sourceIds

    if len(containers) > 1 or not "PROFILE" in containers:
        print("[!] You have this person in these containers :")
        for container in containers:
            print(f"- {container.title()}")

    if not "PROFILE" in containers:
        exit("[-] Given information does not match a public Google Account.")

    container = "PROFILE"
    
    gb.rc.print("🙋 Google Account data\n", style="plum2")

    # if container in target.names:
        # print(f"Name : {target.names[container].fullname}\n")

    if container in target.profilePhotos:
        if target.profilePhotos[container].isDefault:
            print("[-] Default profile picture")
        else:
            print("[+] Custom profile picture !")
            print(f"=> {target.profilePhotos[container].url}")
            
    #         # await ia.detect_face(vision_api, as_client, target.profilePhotos[container].url)
    #         print()

    if container in target.coverPhotos:
        if target.coverPhotos[container].isDefault:
            print("[-] Default cover picture\n")
        else:
            print("[+] Custom cover picture !")
            print(f"=> {target.coverPhotos[container].url}")

    #         # await ia.detect_face(vision_api, as_client, target.coverPhotos[container].url)
            print()

    print(f"Last profile edit : {target.sourceIds[container].lastUpdated.strftime('%Y/%m/%d %H:%M:%S (UTC)')}\n")

    print(f"Gaia ID : {target.personId}\n")

    if container in target.profileInfos:
        print("User types :")
        for user_type in target.profileInfos[container].userTypes:
            definition = get_user_type_definition(user_type)
            gb.rc.print(f"- {user_type} [italic]({definition})[/italic]")

    gb.rc.print(f"\n📞 Google Chat Extended Data\n", style="light_salmon3")

    #print(f"Presence : {target.extendedData.dynamiteData.presence}")
    print(f"Entity Type : {target.extendedData.dynamiteData.entityType}")
    #print(f"DND State : {target.extendedData.dynamiteData.dndState}")
    gb.rc.print(f"Customer ID : {x if (x := target.extendedData.dynamiteData.customerId) else '[italic]Not found.[/italic]'}")

    gb.rc.print(f"\n🌐 Google Plus Extended Data\n", style="cyan")

    print(f"Entreprise User : {target.extendedData.gplusData.isEntrepriseUser}")
    #print(f"Content Restriction : {target.extendedData.gplusData.contentRestriction}")
    
    if container in target.inAppReachability:
        print("\n[+] Activated Google services :")
        for app in target.inAppReachability[container].apps:
            print(f"- {app}")

    gb.rc.print("\n🗺️ Maps data", style="green4")

    err, stats = await gmaps.get_reviews(as_client, gaia_id)
    gmaps.output(err, stats, gaia_id)

    if json_file:
        if container == "PROFILE":
            json_results[f"{container}_CONTAINER"] = {
                "profile": target,
                "maps": {
                    # "photos": photos,
                    # "reviews": reviews,
                    "stats": stats
                }
            }
        else:
            json_results[f"{container}_CONTAINER"] = {
                "profile": target
            }

        if cache:
            json_results["CACHE_METADATA"] = {
                "target": gaia_id.strip(),
                "served_from_cache": from_cache,
                "changes": changes,
                "cache_path": str(cache._entry_path(gaia_id)),
            }
        if masked_fields:
            json_results["MASKING_METADATA"] = {
                "level": mask_level,
                "masked_fields": masked_fields,
            }
    
    if json_file:
        import json
        from ghunt.objects.encoders import GHuntEncoder;
        with open(json_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(json_results, cls=GHuntEncoder, indent=4))
        gb.rc.print(f"\n[+] JSON output wrote to {json_file} !", style="italic")

    await as_client.aclose()
