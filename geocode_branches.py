from __future__ import annotations

import argparse
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from baby_worker.supabase_rest import SupabaseREST


NOMINATIM_URL = os.environ.get(
    "NOMINATIM_URL",
    "https://nominatim.openstreetmap.org/search",
)

USER_AGENT = os.environ.get(
    "NOMINATIM_USER_AGENT",
    "BabyShoppingPilot/0.28.1 (+https://github.com/haimboch/Baby-shopping-list)",
)

CHAIN_LABELS = {
    "rami_levy": "רמי לוי",
    "yochananof": "יוחננוף",
    "shufersal": "שופרסל",
    "be": "Be",
    "osher_ad": "אושר עד",
}

CHAIN_NOISE = (
    "רמי לוי",
    "שופרסל",
    "שופרסל דיל",
    "שופרסל שלי",
    "שופרסל אקספרס",
    "יוניברס",
    "יוניברס אקסטרה",
    "אושר עד",
    "יוחננוף",
    "BE",
    "Be",
    "be",
)

UNKNOWN_VALUES = {"", "unknown", "null", "none", "0"}


def clean(value: Any) -> str:
    text = " ".join(str(value or "").replace("\u200f", " ").split()).strip(" ,-")
    if text.lower() in UNKNOWN_VALUES:
        return ""
    return text


def normalize_branch_name(value: Any) -> str:
    text = clean(value)
    for token in CHAIN_NOISE:
        text = re.sub(
            rf"(^|\s){re.escape(token)}(\s|$)",
            " ",
            text,
            flags=re.I,
        )
    return " ".join(text.split()).strip(" ,-")


def valid_israel(lat: float, lon: float) -> bool:
    return 29.0 <= lat <= 34.0 and 34.0 <= lon <= 36.5


@dataclass
class SearchResult:
    lat: float
    lon: float
    display_name: str
    query: str
    level: str


class RequestLimiter:
    def __init__(self, interval_seconds: float) -> None:
        self.interval = max(1.1, float(interval_seconds))
        self.last_request_at = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_request_at
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)

    def mark(self) -> None:
        self.last_request_at = time.monotonic()


def unique_queries(branch: dict[str, Any]) -> list[tuple[str, str]]:
    chain = CHAIN_LABELS.get(
        str(branch.get("chain_id") or ""),
        clean(branch.get("chain_id")),
    )
    name = clean(branch.get("branch_name"))
    short_name = normalize_branch_name(name)
    address = clean(branch.get("address"))

    candidates: list[tuple[str, str]] = []

    def add(level: str, parts: list[str]) -> None:
        q = ", ".join([clean(x) for x in parts if clean(x)])
        if q and q not in [existing for _, existing in candidates]:
            candidates.append((level, q))

    add("chain_name_address", [chain, name, address, "ישראל"])
    add("name_address", [name, address, "ישראל"])
    add("short_name_address", [short_name, address, "ישראל"])
    add("address_short_name", [address, short_name, "ישראל"])
    add("short_name_only", [short_name, "ישראל"])
    add("name_only", [name, "ישראל"])

    return candidates


def fetch_candidates(
    session: requests.Session,
    limiter: RequestLimiter,
    query: str,
) -> list[dict[str, Any]]:
    limiter.wait()
    try:
        response = session.get(
            NOMINATIM_URL,
            params={
                "format": "jsonv2",
                "q": query,
                "countrycodes": "il",
                "limit": "3",
                "addressdetails": "1",
                "accept-language": "he,en",
            },
            timeout=30,
        )
    finally:
        limiter.mark()

    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


def pick_candidate(
    candidates: list[dict[str, Any]],
    query: str,
    level: str,
) -> SearchResult | None:
    for item in candidates:
        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue

        if not valid_israel(lat, lon):
            continue

        address = item.get("address") or {}
        country_code = clean(address.get("country_code")).lower()
        if country_code and country_code != "il":
            continue

        return SearchResult(
            lat=lat,
            lon=lon,
            display_name=clean(item.get("display_name")),
            query=query,
            level=level,
        )

    return None


def geocode_branch(
    session: requests.Session,
    limiter: RequestLimiter,
    branch: dict[str, Any],
) -> tuple[SearchResult | None, list[str]]:
    attempts: list[str] = []

    for level, query in unique_queries(branch):
        attempts.append(f"{level}: {query}")
        candidates = fetch_candidates(session, limiter, query)
        result = pick_candidate(candidates, query, level)
        if result is not None:
            return result, attempts

    return None, attempts


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
        "select": (
            "chain_id,branch_code,branch_name,city,address,"
            "latitude,longitude"
        ),
        "active": "eq.true",
        "or": "(latitude.is.null,longitude.is.null)",
        "order": "chain_id.asc,branch_code.asc",
        "limit": str(max(limit * 3, limit)),
    }

    rows = db.select("retail_branches", params)

    rows = [
        row
        for row in rows
        if row.get("chain_id") in chains
        and clean(row.get("branch_name"))
    ][:limit]

    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "he,en;q=0.8",
    })

    limiter = RequestLimiter(sleep_seconds)

    ok = 0
    miss = 0
    errors = 0
    started = time.time()

    for i, branch in enumerate(rows, 1):
        chain_id = str(branch.get("chain_id") or "")
        branch_code = str(branch.get("branch_code") or "")
        label = (
            f"{chain_id} {branch_code} "
            f"{clean(branch.get('branch_name'))}"
        )

        try:
            result, attempts = geocode_branch(
                session,
                limiter,
                branch,
            )

            if result is None:
                miss += 1
                print(f"⚠️ {i}/{len(rows)} no match: {label}")
                for attempt in attempts:
                    print(f"   ↳ {attempt}")
                continue

            db.patch(
                "retail_branches",
                {
                    "chain_id": chain_id,
                    "branch_code": branch_code,
                },
                {
                    "latitude": result.lat,
                    "longitude": result.lon,
                },
            )

            ok += 1
            print(
                f"✅ {i}/{len(rows)} {label}"
                f" -> {result.lat:.6f},{result.lon:.6f}"
                f" [{result.level}]"
            )
            if result.display_name:
                print(f"   ↳ {result.display_name}")

        except Exception as exc:
            errors += 1
            print(
                f"❌ {i}/{len(rows)} {label}: "
                f"{type(exc).__name__}: {exc}"
            )

    elapsed = time.time() - started

    print()
    print(
        "Done. "
        f"attempted={len(rows)} "
        f"geocoded={ok} "
        f"no_match={miss} "
        f"errors={errors} "
        f"elapsed={elapsed:.1f}s"
    )
    print(
        "Coordinates are cached in retail_branches; "
        "reruns query only missing coordinates."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fallback geocoder for retail branches (v0.28.1)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.environ.get("GEOCODE_LIMIT", "60")),
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=float(os.environ.get("GEOCODE_SLEEP_SECONDS", "1.1")),
    )

    args = parser.parse_args()

    run(
        max(1, args.limit),
        max(1.1, args.sleep),
    )
