from __future__ import annotations

import argparse
import math
import os
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import requests

from baby_worker.supabase_rest import SupabaseREST

VERSION = "0.28.2"
NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
USER_AGENT = os.environ.get(
    "NOMINATIM_USER_AGENT",
    "BabyShoppingPilot/0.28.2 (+https://github.com/haimboch/Baby-shopping-list)",
)

CHAIN_ORDER = ["rami_levy", "yochananof", "shufersal", "be", "osher_ad"]
CHAIN_LABELS = {
    "rami_levy": "רמי לוי",
    "yochananof": "יוחננוף",
    "shufersal": "שופרסל",
    "be": "Be",
    "osher_ad": "אושר עד",
}
CHAIN_NOISE = (
    "רמי לוי","שופרסל","שופרסל דיל","שופרסל שלי","שופרסל אקספרס",
    "דיל","שלי","אקספרס","יוניברס","יוניברס אקסטרה","אושר עד","יוחננוף",
    "BE","Be","be",
)
UNKNOWN_VALUES = {"", "unknown", "null", "none", "0"}
ADMIN_TYPES = {
    "city","town","village","municipality","county","state","state_district",
    "administrative","suburb","neighbourhood","quarter","district","borough",
    "hamlet","residential",
}
PRECISE_ADDRESS_TYPES = {
    "house","building","shop","supermarket","mall","commercial","retail",
    "office","amenity",
}
PRECISE_CATEGORIES = {"shop","building","amenity","office"}
STOPWORDS = {
    "ישראל","סניף","מרכז","מסחרי","קניון","מתחם","אזור","תעשיה","התעשיה",
    "רחוב","רח","פינת",
}


def clean(value: Any) -> str:
    text = " ".join(str(value or "").replace("\u200f", " ").replace("״", '"').replace("׳", "'").split()).strip(" ,-")
    return "" if text.lower() in UNKNOWN_VALUES else text


def normalized(value: Any) -> str:
    text = clean(value).lower()
    text = re.sub(r'["\'.,():;/\\\-]+', " ", text)
    return " ".join(text.split())


def tokens(value: Any) -> set[str]:
    return {t for t in normalized(value).split() if len(t) >= 2 and t not in STOPWORDS}


def house_numbers(value: Any) -> set[str]:
    return set(re.findall(r"\b\d{1,4}\b", normalized(value)))


def normalize_branch_name(value: Any) -> str:
    text = clean(value)
    for token in CHAIN_NOISE:
        text = re.sub(rf"(^|\s){re.escape(token)}(\s|$)", " ", text, flags=re.I)
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
    score: int
    category: str
    addresstype: str


class RequestLimiter:
    def __init__(self, interval_seconds: float):
        self.interval = max(1.1, float(interval_seconds))
        self.last_request_at = 0.0

    def wait(self):
        elapsed = time.monotonic() - self.last_request_at
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)

    def mark(self):
        self.last_request_at = time.monotonic()


def unique_queries(branch: dict[str, Any]) -> list[tuple[str, str]]:
    chain = CHAIN_LABELS.get(str(branch.get("chain_id") or ""), clean(branch.get("chain_id")))
    name = clean(branch.get("branch_name"))
    short_name = normalize_branch_name(name)
    address = clean(branch.get("address"))
    queries = []

    def add(level: str, parts: list[str]):
        q = ", ".join(clean(x) for x in parts if clean(x))
        if q and q not in [existing for _, existing in queries]:
            queries.append((level, q))

    if address:
        add("chain_name_address", [chain, name, address, "ישראל"])
        add("name_address", [name, address, "ישראל"])
        add("short_name_address", [short_name, address, "ישראל"])
        add("address_short_name", [address, short_name, "ישראל"])
        add("address_only", [address, "ישראל"])

    add("chain_name_poi", [chain, short_name or name, "ישראל"])
    add("name_poi", [name, "ישראל"])
    return queries


def fetch_candidates(session, limiter, query):
    limiter.wait()
    try:
        r = session.get(
            NOMINATIM_URL,
            params={
                "format":"jsonv2","q":query,"countrycodes":"il","limit":"5",
                "addressdetails":"1","extratags":"1","namedetails":"1",
                "accept-language":"he,en",
            },
            timeout=30,
        )
    finally:
        limiter.mark()
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def candidate_score(item, branch, level):
    reasons = []
    category = normalized(item.get("category"))
    addresstype = normalized(item.get("addresstype") or item.get("type"))
    address = item.get("address") or {}

    if addresstype in ADMIN_TYPES:
        return -100, [f"administrative_result:{addresstype}"]

    country_code = normalized(address.get("country_code"))
    if country_code and country_code != "il":
        return -100, ["outside_israel"]

    try:
        lat = float(item["lat"]); lon = float(item["lon"])
    except (KeyError, TypeError, ValueError):
        return -100, ["bad_coordinates"]

    if not valid_israel(lat, lon):
        return -100, ["outside_israel_bbox"]

    source_name = clean(branch.get("branch_name"))
    source_short = normalize_branch_name(source_name)
    source_address = clean(branch.get("address"))
    source_chain = CHAIN_LABELS.get(str(branch.get("chain_id") or ""), "")

    display = clean(item.get("display_name"))
    namedetails = " ".join(str(v) for v in (item.get("namedetails") or {}).values() if v)
    extra = " ".join(str(v) for v in (item.get("extratags") or {}).values() if v)
    result_text = " ".join([
        display, namedetails, extra, clean(address.get("road")),
        clean(address.get("house_number")), clean(address.get("shop")),
        clean(address.get("amenity")),
    ])

    src_name_tokens = tokens(source_short or source_name)
    src_addr_tokens = tokens(source_address)
    result_tokens = tokens(result_text)

    score = 0
    precise_type = addresstype in PRECISE_ADDRESS_TYPES or category in PRECISE_CATEGORIES
    if precise_type:
        score += 4; reasons.append(f"precise_type:{category}/{addresstype}")

    road = clean(address.get("road"))
    if road:
        score += 2; reasons.append("has_road")

    result_house = clean(address.get("house_number"))
    if result_house:
        score += 2; reasons.append("has_house_number")

    name_overlap = src_name_tokens & result_tokens
    if name_overlap:
        score += min(4, len(name_overlap) * 2)
        reasons.append("name_overlap:" + ",".join(sorted(name_overlap)))

    address_overlap = src_addr_tokens & result_tokens
    if address_overlap:
        score += min(5, len(address_overlap) * 2)
        reasons.append("address_overlap:" + ",".join(sorted(address_overlap)))

    source_nums = house_numbers(source_address)
    result_nums = house_numbers(" ".join([result_house, display]))
    if source_nums:
        if source_nums & result_nums:
            score += 5; reasons.append("house_number_match")
        elif result_nums:
            score -= 5; reasons.append("house_number_conflict")
        else:
            score -= 1; reasons.append("house_number_missing_in_result")

    chain_tokens = tokens(source_chain)
    if chain_tokens and chain_tokens & result_tokens:
        score += 3; reasons.append("chain_match")

    if level.endswith("_poi") and not precise_type:
        return -100, ["poi_query_returned_non_poi"]

    if source_address and not precise_type and not road:
        score -= 5; reasons.append("address_query_without_road_or_poi")

    return score, reasons


def pick_candidate(candidates, branch, query, level, min_score):
    ranked = []
    audit = []
    for item in candidates:
        score, reasons = candidate_score(item, branch, level)
        ranked.append((score, item, reasons))
    ranked.sort(key=lambda x: x[0], reverse=True)

    for score, item, reasons in ranked:
        audit.append(
            f"score={score} type={item.get('category')}/{item.get('addresstype') or item.get('type')} "
            f"name={clean(item.get('display_name'))} reasons={'|'.join(reasons)}"
        )
        if score < min_score:
            continue
        try:
            lat = float(item["lat"]); lon = float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        return SearchResult(
            lat=lat, lon=lon, display_name=clean(item.get("display_name")),
            query=query, level=level, score=score,
            category=clean(item.get("category")),
            addresstype=clean(item.get("addresstype") or item.get("type")),
        ), audit
    return None, audit


def geocode_branch(session, limiter, branch, min_score):
    attempts = []
    for level, query in unique_queries(branch):
        candidates = fetch_candidates(session, limiter, query)
        result, audit = pick_candidate(candidates, branch, query, level, min_score)
        attempts.append(f"{level}: {query}")
        attempts.extend("  " + line for line in audit[:3])
        if result:
            return result, attempts
    return None, attempts


def low_detail_address(value):
    text = normalized(value)
    return (not text) or (not house_numbers(text) and len(text.split()) <= 3)


def repair_suspicious_duplicates(db, dry_run: bool):
    rows = db.select("retail_branches", {
        "select":"chain_id,branch_code,branch_name,address,latitude,longitude",
        "active":"eq.true","latitude":"not.is.null","longitude":"not.is.null","limit":"5000",
    })
    groups = defaultdict(list)
    for row in rows:
        try:
            key = (round(float(row["latitude"]), 6), round(float(row["longitude"]), 6))
        except (TypeError, ValueError, KeyError):
            continue
        groups[key].append(row)

    suspects = {}
    for key, items in groups.items():
        if len(items) < 2:
            continue
        addresses = {normalized(x.get("address")) for x in items}
        addresses.discard("")
        all_same_detailed = (
            len(addresses) == 1
            and all(not low_detail_address(x.get("address")) for x in items)
        )
        if all_same_detailed:
            continue
        if len(addresses) > 1 or any(low_detail_address(x.get("address")) for x in items):
            print(f"⚠️ suspicious coordinate {key[0]},{key[1]} branches={len(items)}")
            for item in items:
                print(f"   ↳ {item.get('chain_id')} {item.get('branch_code')} "
                      f"{clean(item.get('branch_name'))} | {clean(item.get('address'))}")
                suspects[(str(item.get("chain_id")), str(item.get("branch_code")))] = item

    if dry_run:
        return len(suspects), 0

    cleared = 0
    for chain_id, branch_code in suspects:
        db.patch("retail_branches",
                 {"chain_id":chain_id,"branch_code":branch_code},
                 {"latitude":None,"longitude":None})
        cleared += 1
    return len(suspects), cleared


def household_city(db):
    try:
        rows = db.select("households", {"select":"city","city":"not.is.null","limit":"1"})
        if rows:
            return clean(rows[0].get("city"))
    except Exception:
        pass
    return ""


def branch_priority(row, local_city):
    if not local_city:
        return 1
    ct = tokens(local_city)
    rt = tokens(" ".join([clean(row.get("branch_name")), clean(row.get("address"))]))
    return 0 if ct and ct & rt else 1


def round_robin_rows(rows, limit, local_city):
    buckets = {}
    for chain_id in CHAIN_ORDER:
        chain_rows = [r for r in rows if r.get("chain_id") == chain_id]
        chain_rows.sort(key=lambda r: (
            branch_priority(r, local_city),
            str(r.get("branch_code") or ""),
        ))
        buckets[chain_id] = deque(chain_rows)

    selected = []
    while len(selected) < limit and any(buckets.values()):
        for chain_id in CHAIN_ORDER:
            if buckets[chain_id]:
                selected.append(buckets[chain_id].popleft())
                if len(selected) >= limit:
                    break
    return selected


def run(limit, sleep_seconds, min_score, repair, repair_dry_run):
    db = SupabaseREST()

    if repair:
        flagged, cleared = repair_suspicious_duplicates(db, dry_run=repair_dry_run)
        print(f"Repair audit: flagged={flagged} cleared={cleared} dry_run={repair_dry_run}\n")

    chains = [x.strip() for x in os.environ.get("GEOCODE_CHAIN_IDS", ",".join(CHAIN_ORDER)).split(",") if x.strip()]
    rows = db.select("retail_branches", {
        "select":"chain_id,branch_code,branch_name,city,address,latitude,longitude",
        "active":"eq.true","or":"(latitude.is.null,longitude.is.null)","limit":"5000",
    })
    rows = [r for r in rows if r.get("chain_id") in chains and clean(r.get("branch_name"))]
    local_city = household_city(db)
    rows = round_robin_rows(rows, limit, local_city)

    session = requests.Session()
    session.headers.update({
        "User-Agent":USER_AGENT,"Accept":"application/json","Accept-Language":"he,en;q=0.8",
    })
    limiter = RequestLimiter(sleep_seconds)

    ok = miss = errors = 0
    started = time.time()

    for i, branch in enumerate(rows, 1):
        chain_id = str(branch.get("chain_id") or "")
        branch_code = str(branch.get("branch_code") or "")
        label = f"{chain_id} {branch_code} {clean(branch.get('branch_name'))}"
        try:
            result, attempts = geocode_branch(session, limiter, branch, min_score)
            if result is None:
                miss += 1
                print(f"⚠️ {i}/{len(rows)} no precise match: {label}")
                for attempt in attempts:
                    print(f"   ↳ {attempt}")
                continue

            db.patch("retail_branches",
                     {"chain_id":chain_id,"branch_code":branch_code},
                     {"latitude":result.lat,"longitude":result.lon})
            ok += 1
            print(f"✅ {i}/{len(rows)} {label} -> {result.lat:.6f},{result.lon:.6f} "
                  f"score={result.score} [{result.level}] type={result.category}/{result.addresstype}")
            if result.display_name:
                print(f"   ↳ {result.display_name}")
        except Exception as exc:
            errors += 1
            print(f"❌ {i}/{len(rows)} {label}: {type(exc).__name__}: {exc}")

    elapsed = time.time() - started
    print(f"\nv{VERSION} done. attempted={len(rows)} geocoded={ok} "
          f"no_precise_match={miss} errors={errors} elapsed={elapsed:.1f}s")
    print("Only precise matches are cached. Rejected city/town/admin results remain NULL.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Precision retail branch geocoder v0.28.2")
    p.add_argument("--limit", type=int, default=int(os.environ.get("GEOCODE_LIMIT", "40")))
    p.add_argument("--sleep", type=float, default=float(os.environ.get("GEOCODE_SLEEP_SECONDS", "1.1")))
    p.add_argument("--min-score", type=int, default=int(os.environ.get("GEOCODE_MIN_SCORE", "7")))
    p.add_argument("--repair-suspicious", action="store_true")
    p.add_argument("--repair-dry-run", action="store_true")
    a = p.parse_args()
    run(max(1,a.limit), max(1.1,a.sleep), max(1,a.min_score),
        a.repair_suspicious, a.repair_dry_run)
