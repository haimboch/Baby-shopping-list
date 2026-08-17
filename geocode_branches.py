from __future__ import annotations

import argparse
import os
import time
from typing import Any

import requests

from baby_worker.supabase_rest import SupabaseREST

NOMINATIM_URL = os.environ.get(
    "NOMINATIM_URL",
    "https://nominatim.openstreetmap.org/search",
)
USER_AGENT = os.environ.get(
    "NOMINATIM_USER_AGENT",
    "BabyShoppingPilot/0.28 (+https://github.com/haimboch/Baby-shopping-list)",
)
CHAIN_LABELS = {
    "rami_levy": "רמי לוי",
    "yochananof": "יוחננוף",
    "shufersal": "שופרסל",
    "be": "Be",
    "osher_ad": "אושר עד",
}


def clean(value: Any) -> str:
    return " ".join(str(value or "").replace("unknown", "").split()).strip(" ,-")


def valid_israel(lat: float, lon: float) -> bool:
    # Loose geographic envelope including the full country.
    return 29.0 <= lat <= 34.0 and 34.0 <= lon <= 36.5


def query_text(branch: dict[str, Any]) -> str:
    chain = CHAIN_LABELS.get(str(branch.get("chain_id")), str(branch.get("chain_id") or ""))
    name = clean(branch.get("branch_name"))
    address = clean(branch.get("address"))
    parts = [x for x in (chain, name, address, "ישראל") if x]
    return ", ".join(parts)


def geocode(session: requests.Session, query: str) -> tuple[float, float] | None:
    response = session.get(
        NOMINATIM_URL,
        params={
            "format": "jsonv2",
            "q": query,
            "countrycodes": "il",
            "limit": "1",
            "addressdetails": "1",
            "accept-language": "he,en",
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not data:
        return None
    lat = float(data[0]["lat"])
    lon = float(data[0]["lon"])
    if not valid_israel(lat, lon):
        return None
    return lat, lon


def run(limit: int, sleep_seconds: float) -> None:
    db = SupabaseREST()
    chains = [
        x.strip()
        for x in os.environ.get(
            "GEOCODE_CHAIN_IDS",
            "rami_levy,yochananof,shufersal,be,osher_ad",
        ).split(",")
        if x.strip()
    ]

    params = {
        "select": "chain_id,branch_code,branch_name,city,address,latitude,longitude",
        "active": "eq.true",
        "or": "(latitude.is.null,longitude.is.null)",
        "order": "chain_id.asc,branch_code.asc",
        "limit": str(max(limit * 3, limit)),
    }
    rows = db.select("retail_branches", params)
    rows = [r for r in rows if r.get("chain_id") in chains]
    rows = [r for r in rows if clean(r.get("branch_name")) and clean(r.get("address"))][:limit]

    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "he,en;q=0.8",
    })

    ok = 0
    miss = 0
    errors = 0
    started = time.time()

    for i, branch in enumerate(rows, 1):
        q = query_text(branch)
        try:
            coords = geocode(session, q)
            if coords is None:
                miss += 1
                print(f"⚠️ {i}/{len(rows)} no match: {branch['chain_id']} {branch['branch_code']} | {q}")
            else:
                lat, lon = coords
                db.patch(
                    "retail_branches",
                    {
                        "chain_id": str(branch["chain_id"]),
                        "branch_code": str(branch["branch_code"]),
                    },
                    {
                        "latitude": lat,
                        "longitude": lon,
                    },
                )
                ok += 1
                print(f"✅ {i}/{len(rows)} {branch['chain_id']} {branch['branch_code']} -> {lat:.6f},{lon:.6f}")
        except Exception as exc:
            errors += 1
            print(f"❌ {i}/{len(rows)} {branch['chain_id']} {branch['branch_code']}: {type(exc).__name__}: {exc}")

        # OSMF public Nominatim policy: absolute max 1 request/sec.
        if i < len(rows):
            time.sleep(max(1.1, sleep_seconds))

    elapsed = time.time() - started
    print()
    print(f"Done. attempted={len(rows)} geocoded={ok} no_match={miss} errors={errors} elapsed={elapsed:.1f}s")
    print("Coordinates are cached in retail_branches; reruns query only missing coordinates.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="One-time/cached retail branch geocoder")
    parser.add_argument("--limit", type=int, default=int(os.environ.get("GEOCODE_LIMIT", "120")))
    parser.add_argument("--sleep", type=float, default=float(os.environ.get("GEOCODE_SLEEP_SECONDS", "1.1")))
    args = parser.parse_args()
    run(max(1, args.limit), max(1.1, args.sleep))
