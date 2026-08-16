from __future__ import annotations
import argparse
import asyncio
import gzip
import html
import json
import os
import re
import sys
import traceback
from urllib.parse import unquote, urlparse, urljoin
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import requests

from .xmlfeeds import file_kind, parse_price_rows, parse_promotions, parse_stores, timestamp_from_filename, price_file_diagnostics
from .supabase_rest import SupabaseREST
from .enrichment import MetadataEnricher

SCRAPER_TO_DB = {
    "SUPER_PHARM": "super_pharm",
    "RAMI_LEVY": "rami_levy",
    "YOHANANOF": "yochananof",
    "SHUFERSAL": "shufersal",  # split to Be later by subchain
}
CHAIN_TO_SCRAPER = {
    "super_pharm": "SUPER_PHARM",
    "rami_levy": "RAMI_LEVY",
    "yochananof": "YOHANANOF",
    "shufersal": "SHUFERSAL",
    "be": "SHUFERSAL",
}

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

def csv_env(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]

def be_subchains() -> set[str]:
    return {x.lstrip("0") or "0" for x in csv_env("BE_SUBCHAIN_IDS", "005,5")}

def resolved_chain(source_scraper: str, subchain_id: str | None, product_or_store_name: str | None = None) -> str:
    if source_scraper != "SHUFERSAL":
        return SCRAPER_TO_DB[source_scraper]
    normalized = (str(subchain_id).lstrip("0") or "0") if subchain_id not in (None, "") else None
    if normalized and normalized in be_subchains():
        return "be"
    if product_or_store_name and str(product_or_store_name).strip().lower().startswith("be "):
        return "be"
    return "shufersal"

def normalize_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    s = str(value).strip()
    # Keep valid-ish ISO as-is. DB can parse it.
    if "T" in s:
        return s
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M", "%Y%m%d%H%M%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    return None

def freshness_dt(row: dict[str, Any]) -> datetime | None:
    """Best available publication time for a normalized price row."""
    value = normalize_timestamp(row.get("source_updated_at"))
    if value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    raw = row.get("raw_source") or {}
    file_name = raw.get("file_name") if isinstance(raw, dict) else None
    value = timestamp_from_filename(str(file_name or ""))
    if value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return None


def metadata_score(row: dict[str, Any]) -> int:
    return sum(
        1 for value in (
            row.get("brand"), row.get("dimension_value"),
            row.get("package_quantity"), row.get("package_unit")
        ) if value not in (None, "")
    )


def collapse_latest_prices(prices: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """One row per chain+branch+barcode, choosing the newest source snapshot."""
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in prices:
        key = (row["chain_id"], row["branch_code"], row["barcode"])
        current = latest.get(key)
        if current is None:
            latest[key] = row
            continue

        cur_dt = freshness_dt(current)
        new_dt = freshness_dt(row)
        replace = False
        if cur_dt is None and new_dt is not None:
            replace = True
        elif cur_dt is not None and new_dt is not None and new_dt > cur_dt:
            replace = True
        elif cur_dt == new_dt and metadata_score(row) > metadata_score(current):
            replace = True
        elif cur_dt is None and new_dt is None and metadata_score(row) > metadata_score(current):
            replace = True

        if replace:
            latest[key] = row

    return list(latest.values()), len(prices) - len(latest)


def filter_stale_against_db(db: SupabaseREST, prices: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Do not let an older retailer snapshot overwrite a newer DB row.

    Same timestamps are allowed, which lets a parser upgrade repair metadata.
    """
    if not prices:
        return prices, 0

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in prices:
        groups[(row["chain_id"], row["branch_code"])].append(row)

    existing: dict[tuple[str, str, str], dict[str, Any]] = {}
    for chain_id, branch_code in groups:
        found = db.select(
            "baby_retail_prices",
            {
                "select": "chain_id,branch_code,barcode,source_updated_at,raw_source",
                "chain_id": f"eq.{chain_id}",
                "branch_code": f"eq.{branch_code}",
            },
        )
        for row in found:
            existing[(str(row.get("chain_id")), str(row.get("branch_code")), str(row.get("barcode")))] = row

    kept: list[dict[str, Any]] = []
    stale = 0
    for row in prices:
        key = (row["chain_id"], row["branch_code"], row["barcode"])
        old = existing.get(key)
        if not old:
            kept.append(row)
            continue
        old_dt = freshness_dt(old)
        new_dt = freshness_dt(row)
        if old_dt is not None and (new_dt is None or new_dt < old_dt):
            stale += 1
            continue
        kept.append(row)

    return kept, stale


def merge_prices_and_promos(price_rows: list[dict[str, Any]], promos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pmap = {(r["source_name"], r["branch_code"], r["barcode"]): r for r in promos}
    out = []
    for r in price_rows:
        promo = pmap.get((r["source_name"], r["branch_code"], r["barcode"]))
        row = dict(r)
        if promo:
            row.update({
                "promo_price": promo.get("promo_price"),
                "promo_description": promo.get("promo_description"),
                "promo_start_at": normalize_timestamp(promo.get("promo_start_at")),
                "promo_end_at": normalize_timestamp(promo.get("promo_end_at")),
                "requires_club": bool(promo.get("requires_club")),
            })
            raw = dict(row.get("raw_source") or {})
            raw["promo"] = promo.get("raw_promo")
            row["raw_source"] = raw
        promo_price = row.get("promo_price")
        regular = float(row["regular_price"])
        row["effective_price"] = float(promo_price) if promo_price and 0 < float(promo_price) < regular else regular
        row["source_updated_at"] = normalize_timestamp(row.get("source_updated_at"))
        row["last_seen_at"] = utcnow()
        out.append(row)
    return out

def to_db_price(row: dict[str, Any]) -> dict[str, Any]:
    chain_id = resolved_chain(row["source_name"], row.get("subchain_id"), row.get("product_name"))
    return {
        "chain_id": chain_id,
        "branch_code": str(row["branch_code"]),
        "barcode": str(row["barcode"]),
        "need_key": row["need_key"],
        "dimension_type": row["dimension_type"],
        "dimension_value": row.get("dimension_value"),
        "brand": row.get("brand"),
        "product_name": row.get("product_name"),
        "package_quantity": row.get("package_quantity"),
        "package_unit": row.get("package_unit"),
        "regular_price": row["regular_price"],
        "promo_price": row.get("promo_price"),
        # effective_price is a GENERATED ALWAYS column in Supabase/Postgres.
        # Do not send it on INSERT/UPSERT; the database calculates it automatically.
        "promo_description": row.get("promo_description"),
        "promo_start_at": row.get("promo_start_at"),
        "promo_end_at": row.get("promo_end_at"),
        "requires_club": bool(row.get("requires_club")),
        "source_updated_at": row.get("source_updated_at"),
        "last_seen_at": row.get("last_seen_at") or utcnow(),
        "raw_source": row.get("raw_source"),
    }

def to_db_branch(row: dict[str, Any]) -> dict[str, Any]:
    chain_id = resolved_chain(row["source_name"], row.get("subchain_id"), row.get("branch_name"))
    return {
        "chain_id": chain_id,
        "branch_code": str(row["branch_code"]),
        "branch_name": row.get("branch_name"),
        "city": row.get("city"),
        "address": row.get("address"),
        "active": True,
        "last_seen_at": utcnow(),
    }



SP_BASE_URL = "https://prices.super-pharm.co.il/"
SP_DOWNLOAD_BUCKET = "sp_transparency_output_prod_v2"
SP_CHAIN_EAN = "7290172900007"

# Match filenames anywhere in the returned HTML/text. This is deliberately
# independent of <a href=...>, because the GitHub runner may receive markup
# where the filename is visible but the download URL is not represented as a
# simple href.
SP_FILENAME_RE = re.compile(
    r"(?P<filename>"
    r"(?P<kind>PriceFull|Price|Stores)"
    r"7290172900007-000-"
    r"(?:(?P<branch>\d+)-)?"
    r"(?P<timestamp>\d{8}-\d{6}|\d{8}-\d+)"
    r"\.gz"
    r")",
    re.I,
)


def _sp_kind(kind_raw: str) -> str:
    return {
        "pricefull": "price_full",
        "price": "price",
        "stores": "stores",
    }[kind_raw.lower()]


def _sp_direct_download_url(file_name: str) -> str:
    # This endpoint shape is exposed by the official site.
    return (
        f"{SP_BASE_URL}Download/{file_name}"
        f"?bucketName={SP_DOWNLOAD_BUCKET}"
    )


def _sp_candidates_from_html(page_html: str) -> list[dict[str, str]]:
    """Extract SP files from hrefs AND from raw page text.

    Href parsing remains useful when available. Filename scanning is the robust
    fallback and is the primary v0.20 change.
    """
    text = html.unescape(page_html or "")
    out_by_filename: dict[str, dict[str, str]] = {}

    # 1) Parse filenames anywhere in page text.
    for m in SP_FILENAME_RE.finditer(text):
        file_name = m.group("filename")
        out_by_filename[file_name] = {
            "url": _sp_direct_download_url(file_name),
            "file_name": file_name,
            "kind": _sp_kind(m.group("kind")),
            "branch_code": m.group("branch") or "",
            "timestamp": m.group("timestamp"),
            "discovery": "text",
        }

    # 2) If the actual href is present, prefer it. This preserves any official
    # query string/signature shape while still keeping the text fallback.
    href_pattern = r'href\s*=\s*["\']([^"\']+)["\']'
    for href in re.findall(href_pattern, text, re.I):
        href = html.unescape(href)
        decoded_path = unquote(urlparse(href).path)
        file_name = decoded_path.rsplit("/", 1)[-1]
        m = SP_FILENAME_RE.fullmatch(file_name)
        if not m:
            continue

        url = urljoin(SP_BASE_URL, href)
        if "bucketName=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}bucketName={SP_DOWNLOAD_BUCKET}"

        out_by_filename[file_name] = {
            "url": url,
            "file_name": file_name,
            "kind": _sp_kind(m.group("kind")),
            "branch_code": m.group("branch") or "",
            "timestamp": m.group("timestamp"),
            "discovery": "href",
        }

    return list(out_by_filename.values())


# Backwards-compatible name used by the v0.19 offline tests / helpers.
_sp_links_from_html = _sp_candidates_from_html


def _sp_newest_per_branch(
    items: list[dict[str, str]],
) -> list[dict[str, str]]:
    latest: dict[str, dict[str, str]] = {}
    for item in items:
        branch = item.get("branch_code") or "__stores__"
        current = latest.get(branch)
        if current is None or item["timestamp"] > current["timestamp"]:
            latest[branch] = item
    return sorted(
        latest.values(),
        key=lambda x: (
            x.get("branch_code") or "",
            x["timestamp"],
        ),
    )


def _sp_get(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 35,
    attempts: int = 3,
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = session.get(
                url,
                params=params,
                timeout=timeout,
                allow_redirects=True,
            )
            if 200 <= response.status_code < 300:
                return response
            last_exc = RuntimeError(
                f"HTTP {response.status_code} from {response.url}"
            )
        except Exception as exc:
            last_exc = exc

        if attempt < attempts:
            import time
            time.sleep(1.25 * attempt)

    assert last_exc is not None
    raise last_exc


def _sp_page_diagnostic(
    response: requests.Response,
    page_html: str,
    *,
    filtered: bool,
    requested_kind: str,
    page: int,
) -> dict[str, Any]:
    """Small, safe diagnostic describing what the runner actually received."""
    text = page_html or ""
    candidates = _sp_candidates_from_html(text)
    hrefs = re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        text,
        re.I,
    )
    title_match = re.search(
        r"<title[^>]*>(.*?)</title>",
        text,
        re.I | re.S,
    )
    title = ""
    if title_match:
        title = re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip()

    # Do not persist the full HTML. Keep a short whitespace-normalized prefix
    # and filenames only, enough to distinguish a normal index from a block/
    # challenge page.
    prefix = re.sub(r"\s+", " ", html.unescape(text[:1200])).strip()

    return {
        "filtered": filtered,
        "requested_kind": requested_kind,
        "page": page,
        "status_code": getattr(response, "status_code", None),
        "final_url": getattr(response, "url", None),
        "html_length": len(text),
        "title": title[:200],
        "href_count": len(hrefs),
        "filenames_found": len(candidates),
        "filename_samples": [
            x["file_name"] for x in candidates[:8]
        ],
        "html_prefix": prefix[:700],
    }


def _scan_sp_index(
    session: requests.Session,
    *,
    wanted_kind: str,
    target_count: int,
    max_pages: int,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Scan the official index with robust filename discovery.

    Strategy:
      A) filtered query (`type=PriceFull`, etc.) with `date=` included,
      B) broad-index fallback only when necessary.

    We search filenames in the entire HTML, not merely href attributes.
    """
    type_value = {
        "price_full": "PriceFull",
        "price": "Price",
        "stores": "Stores",
    }[wanted_kind]

    found: list[dict[str, str]] = []
    pages_scanned = 0
    errors: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    seen_files: set[str] = set()

    for filtered in (True, False):
        if len(_sp_newest_per_branch(found)) >= target_count:
            break

        consecutive_empty = 0

        for page in range(1, max_pages + 1):
            try:
                params: dict[str, Any] = {
                    "date": "",
                    "page": page,
                }
                if filtered:
                    params["type"] = type_value
                else:
                    params["type"] = ""

                response = _sp_get(
                    session,
                    SP_BASE_URL,
                    params=params,
                )
                page_html = response.text or ""
                pages_scanned += 1

                candidates = _sp_candidates_from_html(page_html)
                matching = [
                    x for x in candidates
                    if x["kind"] == wanted_kind
                ]

                if len(diagnostics) < 6:
                    diagnostics.append(
                        _sp_page_diagnostic(
                            response,
                            page_html,
                            filtered=filtered,
                            requested_kind=wanted_kind,
                            page=page,
                        )
                    )

                added = 0
                for item in matching:
                    if item["file_name"] in seen_files:
                        continue
                    seen_files.add(item["file_name"])
                    found.append(item)
                    added += 1

                newest = _sp_newest_per_branch(found)
                if len(newest) >= target_count:
                    break

                # Stop a dead scan quickly, but only after we have enough
                # evidence that the response contains no filenames at all.
                if not candidates:
                    consecutive_empty += 1
                else:
                    consecutive_empty = 0

                if consecutive_empty >= 5:
                    break

                # If filtered mode is clearly returning files of other types
                # but never the requested type, don't burn the whole page
                # budget. Fall back to the broad scan.
                if (
                    filtered
                    and page >= 12
                    and not newest
                    and candidates
                ):
                    break

            except Exception as exc:
                errors.append(
                    f"{'filtered' if filtered else 'broad'} "
                    f"page {page}: {type(exc).__name__}: {exc}"
                )
                if len(errors) >= 8:
                    break

        if len(errors) >= 8:
            break

    newest = _sp_newest_per_branch(found)
    return newest[:target_count], {
        "wanted_kind": wanted_kind,
        "pages_scanned": pages_scanned,
        "links_found": len(newest),
        "files_discovered_total": len(found),
        "errors": errors[:8],
        "diagnostics": diagnostics,
    }


def _empty_sp_pass(
    pass_name: str,
    file_type: str,
    limit: int,
) -> dict[str, Any]:
    return {
        "pass_name": pass_name,
        "file_types": [file_type],
        "limit": limit,
        "files_seen": 0,
        "price_rows": [],
        "promo_rows": [],
        "store_rows": [],
        "errors": [],
        "kind_counts": {},
        "sample_files": [],
        "price_schema_diagnostics": [],
    }


def _download_sp_pass(
    session: requests.Session,
    links: list[dict[str, str]],
    *,
    pass_name: str,
    file_type: str,
    limit: int,
) -> dict[str, Any]:
    result = _empty_sp_pass(
        pass_name,
        file_type,
        limit,
    )

    for item in links[:limit]:
        try:
            response = _sp_get(
                session,
                item["url"],
                timeout=60,
                attempts=3,
            )
            payload = response.content

            if payload[:2] == b"\x1f\x8b":
                payload = gzip.decompress(payload)

            xml_name = item["file_name"][:-3] + ".xml"

            if item["kind"] == "stores":
                parsed_stores = parse_stores(
                    payload,
                    "SUPER_PHARM",
                    xml_name,
                )
                result["store_rows"].extend(parsed_stores)
            else:
                parsed = parse_price_rows(
                    payload,
                    "SUPER_PHARM",
                    xml_name,
                )
                result["price_rows"].extend(parsed)

                if (
                    not parsed
                    and len(
                        result["price_schema_diagnostics"]
                    ) < 3
                ):
                    result[
                        "price_schema_diagnostics"
                    ].append(
                        price_file_diagnostics(
                            payload,
                            xml_name,
                        )
                    )

            result["files_seen"] += 1
            result["kind_counts"][item["kind"]] = (
                result["kind_counts"].get(
                    item["kind"],
                    0,
                ) + 1
            )

            if len(result["sample_files"]) < 12:
                result["sample_files"].append(
                    xml_name
                )

        except Exception as exc:
            result["errors"].append(
                f'{item["file_name"]}: '
                f'{type(exc).__name__}: {exc}'
            )

    print(
        f"🔎 SUPER_PHARM/{pass_name}: "
        f"files={result['files_seen']} "
        f"baby_rows={len(result['price_rows'])} "
        f"stores={len(result['store_rows'])}"
    )
    return result


def _collect_superpharm_official(
    *,
    full_limit: int,
    incremental_limit: int,
    max_pages: int,
    session: requests.Session | None = None,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Official HTTPS bootstrap for Super-Pharm."""
    owns_session = session is None
    session = session or requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": (
            "he-IL,he;q=0.9,en;q=0.7"
        ),
        "Referer": SP_BASE_URL,
        "Connection": "keep-alive",
    })

    try:
        full_links, full_scan = _scan_sp_index(
            session,
            wanted_kind="price_full",
            target_count=full_limit,
            max_pages=max_pages,
        )
        price_links, price_scan = _scan_sp_index(
            session,
            wanted_kind="price",
            target_count=incremental_limit,
            max_pages=max_pages,
        )
        store_links, store_scan = _scan_sp_index(
            session,
            wanted_kind="stores",
            target_count=1,
            max_pages=max_pages,
        )

        stores_pass = _download_sp_pass(
            session,
            store_links,
            pass_name="stores",
            file_type="STORE_FILE",
            limit=1,
        )
        full_pass = _download_sp_pass(
            session,
            full_links,
            pass_name="full_bootstrap",
            file_type="PRICE_FULL_FILE",
            limit=full_limit,
        )
        incremental_pass = _download_sp_pass(
            session,
            price_links,
            pass_name="incremental",
            file_type="PRICE_FILE",
            limit=incremental_limit,
        )

        scan_errors = (
            full_scan["errors"]
            + price_scan["errors"]
            + store_scan["errors"]
        )
        stores_pass["errors"] = (
            scan_errors
            + stores_pass["errors"]
        )

        stats = {
            "source": SP_BASE_URL,
            "https_direct": True,
            "filename_scan": True,
            "full_scan": full_scan,
            "incremental_scan": price_scan,
            "stores_scan": store_scan,
            "full_files_seen": full_pass[
                "files_seen"
            ],
            "full_baby_rows": len(
                full_pass["price_rows"]
            ),
            "incremental_files_seen": (
                incremental_pass["files_seen"]
            ),
            "incremental_baby_rows": len(
                incremental_pass["price_rows"]
            ),
            "stores_files_seen": stores_pass[
                "files_seen"
            ],
            "stores_rows": len(
                stores_pass["store_rows"]
            ),
            "full_snapshot_found": (
                full_pass["files_seen"] > 0
            ),
        }

        return [
            stores_pass,
            full_pass,
            incremental_pass,
        ], stats

    finally:
        if owns_session:
            session.close()


BE_INDEX_URL = "https://prices.shufersal.co.il/FileObject/UpdateCategory"
BE_SUBCHAIN = "005"
BE_FILE_RE = re.compile(
    r"^(?P<kind>PriceFull|Price)7290027600007-005-(?P<branch>\d+)-"
    r"(?P<timestamp>\d{8}-\d{6})\.gz$",
    re.I,
)


def _be_links_from_html(page_html: str) -> list[dict[str, str]]:
    """Extract official Be price links from a Shufersal transparency index page."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    href_pattern = r"href\s*=\s*[\"']([^\"']+)[\"']"

    for href in re.findall(href_pattern, page_html or "", re.I):
        url = html.unescape(href)
        path = unquote(urlparse(url).path)
        file_name = path.rsplit("/", 1)[-1]
        m = BE_FILE_RE.match(file_name)
        if not m or url in seen:
            continue
        seen.add(url)
        out.append({
            "url": url,
            "file_name": file_name,
            "kind": "price_full" if m.group("kind").lower() == "pricefull" else "price",
            "branch_code": m.group("branch"),
            "timestamp": m.group("timestamp"),
        })
    return out


def _newest_per_branch(items: list[dict[str, str]]) -> list[dict[str, str]]:
    """One newest link per Be branch for one file kind."""
    latest: dict[str, dict[str, str]] = {}
    for item in items:
        branch = item["branch_code"]
        current = latest.get(branch)
        if current is None or item["timestamp"] > current["timestamp"]:
            latest[branch] = item
    return sorted(latest.values(), key=lambda x: (x["branch_code"], x["timestamp"]))


def _empty_direct_pass(pass_name: str, file_type: str, limit: int) -> dict[str, Any]:
    return {
        "pass_name": pass_name,
        "file_types": [file_type],
        "limit": limit,
        "files_seen": 0,
        "price_rows": [],
        "promo_rows": [],
        "store_rows": [],
        "errors": [],
        "kind_counts": {},
        "sample_files": [],
        "price_schema_diagnostics": [],
    }


def _download_be_price_pass(
    session: requests.Session,
    links: list[dict[str, str]],
    *,
    pass_name: str,
    file_type: str,
    limit: int,
) -> dict[str, Any]:
    result = _empty_direct_pass(pass_name, file_type, limit)
    selected = _newest_per_branch(links)[:limit]

    for item in selected:
        try:
            response = session.get(item["url"], timeout=45)
            response.raise_for_status()
            payload = response.content
            if payload[:2] == b"\x1f\x8b":
                payload = gzip.decompress(payload)

            xml_name = item["file_name"][:-3] + ".xml"
            parsed = parse_price_rows(payload, "SHUFERSAL", xml_name)
            for row in parsed:
                row["subchain_id"] = BE_SUBCHAIN

            result["price_rows"].extend(parsed)
            result["files_seen"] += 1
            result["kind_counts"][item["kind"]] = result["kind_counts"].get(item["kind"], 0) + 1
            if len(result["sample_files"]) < 12:
                result["sample_files"].append(xml_name)

            if not parsed and len(result["price_schema_diagnostics"]) < 3:
                result["price_schema_diagnostics"].append(price_file_diagnostics(payload, xml_name))
        except Exception as exc:
            result["errors"].append(f'{item["file_name"]}: {type(exc).__name__}: {exc}')

    print(
        f"🔎 SHUFERSAL/{pass_name} official Be files: "
        f"{result['files_seen']} | baby_rows={len(result['price_rows'])}"
    )
    return result


def _collect_be_official(
    *,
    full_limit: int,
    incremental_limit: int,
    max_pages: int,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Find and download Be (subchain 005) files directly from the official index."""
    owns_session = session is None
    session = session or requests.Session()
    session.headers.update({
        "User-Agent": "baby-price-worker/0.20 (+price-transparency)",
        "Accept": "text/html,application/xhtml+xml,*/*",
    })

    full_links: list[dict[str, str]] = []
    incremental_links: list[dict[str, str]] = []
    scan_errors: list[str] = []
    pages_scanned = 0

    try:
        for page in range(1, max_pages + 1):
            try:
                response = session.get(
                    BE_INDEX_URL,
                    params={
                        "catID": 0,
                        "page": page,
                        "sort": "Branch",
                        "sortdir": "ASC",
                        "storeId": 0,
                    },
                    timeout=30,
                )
                response.raise_for_status()
                page_links = _be_links_from_html(response.text)
                pages_scanned += 1

                for item in page_links:
                    if item["kind"] == "price_full":
                        full_links.append(item)
                    elif item["kind"] == "price":
                        incremental_links.append(item)

                if (
                    len(_newest_per_branch(full_links)) >= full_limit
                    and len(_newest_per_branch(incremental_links)) >= incremental_limit
                ):
                    break
            except Exception as exc:
                scan_errors.append(f"index page {page}: {type(exc).__name__}: {exc}")
                if len(scan_errors) >= 5:
                    break

        full_pass = _download_be_price_pass(
            session,
            full_links,
            pass_name="be_full_bootstrap",
            file_type="PRICE_FULL_FILE",
            limit=full_limit,
        )
        incremental_pass = _download_be_price_pass(
            session,
            incremental_links,
            pass_name="be_incremental",
            file_type="PRICE_FILE",
            limit=incremental_limit,
        )
        full_pass["errors"] = scan_errors + full_pass["errors"]

        stats = {
            "subchain_id": BE_SUBCHAIN,
            "pages_scanned": pages_scanned,
            "index_errors": scan_errors[:5],
            "full_links_found": len(_newest_per_branch(full_links)),
            "incremental_links_found": len(_newest_per_branch(incremental_links)),
            "full_files_seen": full_pass["files_seen"],
            "full_baby_rows": len(full_pass["price_rows"]),
            "incremental_files_seen": incremental_pass["files_seen"],
            "incremental_baby_rows": len(incremental_pass["price_rows"]),
            "full_snapshot_found": full_pass["files_seen"] > 0,
        }
        return [full_pass, incremental_pass], stats
    finally:
        if owns_session:
            session.close()


async def _collect_scraper_pass(
    scraper_name: str,
    *,
    file_types: list[str],
    limit: int | None,
    pass_name: str,
    collect_schema_diagnostics: bool = False,
) -> dict[str, Any]:
    """Run one explicit scraper pass and normalize its queue output."""
    try:
        from il_supermarket_scarper import ScarpingTask
        from il_supermarket_scarper.utils import _now, Logger
    except ImportError as e:
        raise RuntimeError(
            "Missing il-supermarket-scraper. Run: pip install -r requirements.txt"
        ) from e

    Logger.set_logging_level(os.environ.get("SCRAPER_LOG_LEVEL", "WARNING"))

    scraper = ScarpingTask(
        output_configuration={"output_mode": "queue", "queue_type": "memory"},
        status_configuration={"database_type": "json", "base_path": "status_logs"},
        multiprocessing=1,
        enabled_scrapers=[scraper_name],
        files_types=file_types,
        max_size=50_000_000 if "PRICE_FULL_FILE" in file_types else None,
    )

    scraper.start(limit=limit, when_date=_now())

    price_rows: list[dict[str, Any]] = []
    promo_rows: list[dict[str, Any]] = []
    store_rows: list[dict[str, Any]] = []
    files_seen = 0
    errors: list[str] = []
    kind_counts: dict[str, int] = defaultdict(int)
    sample_files: list[str] = []
    price_schema_diagnostics: list[dict[str, Any]] = []

    try:
        for name, file_output in scraper.consume().items():
            async for msg in file_output.queue_handler.get_all_messages():
                file_name = msg.get("file_name") or ""
                content = msg.get("file_content") or b""

                if not file_name or not content:
                    errors.append(
                        f"queue message missing file_name/file_content; keys={list(msg.keys())}"
                    )
                    continue

                files_seen += 1
                kind = file_kind(file_name)
                kind_counts[kind] += 1
                if len(sample_files) < 12:
                    sample_files.append(file_name)

                try:
                    if kind == "stores":
                        store_rows.extend(parse_stores(content, scraper_name, file_name))
                    elif kind in ("price_full", "price"):
                        parsed_rows = parse_price_rows(content, scraper_name, file_name)
                        price_rows.extend(parsed_rows)
                        if (
                            collect_schema_diagnostics
                            and not parsed_rows
                            and len(price_schema_diagnostics) < 3
                        ):
                            price_schema_diagnostics.append(
                                price_file_diagnostics(content, file_name)
                            )
                    elif kind in ("promo_full", "promo"):
                        promo_rows.extend(
                            parse_promotions(content, scraper_name, file_name)
                        )
                    else:
                        errors.append(f"unrecognized file kind: {file_name}")
                except Exception as exc:
                    errors.append(
                        f"{file_name}: {type(exc).__name__}: {exc}"
                    )
    finally:
        try:
            scraper.stop()
        except Exception:
            pass
        scraper.join()

    print(
        f"🔎 {scraper_name}/{pass_name} file kinds: {dict(kind_counts)} | "
        f"files={files_seen} | baby_rows={len(price_rows)}"
    )

    return {
        "pass_name": pass_name,
        "file_types": list(file_types),
        "limit": limit,
        "files_seen": files_seen,
        "price_rows": price_rows,
        "promo_rows": promo_rows,
        "store_rows": store_rows,
        "errors": errors,
        "kind_counts": dict(kind_counts),
        "sample_files": sample_files,
        "price_schema_diagnostics": price_schema_diagnostics,
    }


def _merge_source_passes(
    scraper_name: str,
    requested_file_limit: int | None,
    passes: list[dict[str, Any]],
) -> dict[str, Any]:
    price_rows: list[dict[str, Any]] = []
    promo_rows: list[dict[str, Any]] = []
    store_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    kind_counts: dict[str, int] = defaultdict(int)
    sample_files: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    pass_summaries: list[dict[str, Any]] = []

    for p in passes:
        price_rows.extend(p["price_rows"])
        promo_rows.extend(p["promo_rows"])
        store_rows.extend(p["store_rows"])
        errors.extend(p["errors"])

        for kind, count in p["kind_counts"].items():
            kind_counts[kind] += count

        for file_name in p["sample_files"]:
            if len(sample_files) < 12:
                sample_files.append(file_name)

        for diag in p["price_schema_diagnostics"]:
            if len(diagnostics) < 3:
                diagnostics.append(diag)

        pass_summaries.append({
            "pass_name": p["pass_name"],
            "file_types": p["file_types"],
            "limit": p["limit"],
            "files_seen": p["files_seen"],
            "baby_rows": len(p["price_rows"]),
            "file_kind_counts": p["kind_counts"],
            "sample_files": p["sample_files"][:5],
            "errors": p["errors"][:5],
        })

    return {
        "files_seen": sum(p["files_seen"] for p in passes),
        "price_rows": price_rows,
        "promo_rows": promo_rows,
        "store_rows": store_rows,
        "errors": errors,
        "kind_counts": dict(kind_counts),
        "sample_files": sample_files,
        "requested_file_limit": requested_file_limit,
        "scrape_file_limit": None,
        "price_schema_diagnostics": diagnostics,
        "source_passes": pass_summaries,
    }


async def collect_source(
    scraper_name: str, file_limit: int | None
) -> dict[str, Any]:
    """Collect retailer data with a chain-specific source strategy."""

    if scraper_name == "SUPER_PHARM":
        full_default = file_limit if file_limit is not None else 20
        full_limit = int(
            os.environ.get(
                "SUPER_PHARM_FULL_FILE_LIMIT",
                str(max(1, full_default)),
            )
        )
        inc_default = file_limit if file_limit is not None else 20
        incremental_limit = int(
            os.environ.get(
                "SUPER_PHARM_INCREMENTAL_FILE_LIMIT",
                str(max(1, inc_default)),
            )
        )
        max_pages = int(
            os.environ.get("SUPER_PHARM_INDEX_MAX_PAGES", "120")
        )

        passes, stats = _collect_superpharm_official(
            full_limit=full_limit,
            incremental_limit=incremental_limit,
            max_pages=max_pages,
        )
        data = _merge_source_passes(
            scraper_name,
            file_limit,
            passes,
        )
        data["scrape_file_limit"] = {
            "stores": 1,
            "full": full_limit,
            "incremental": incremental_limit,
            "index_max_pages": max_pages,
        }
        data["super_pharm_bootstrap"] = stats
        return data

    if scraper_name == "YOHANANOF":
        full_limit_default = file_limit if file_limit is not None else 20
        full_limit = int(
            os.environ.get(
                "YOHANANOF_FULL_FILE_LIMIT",
                str(max(1, full_limit_default)),
            )
        )
        incremental_limit_default = file_limit if file_limit is not None else 20
        incremental_limit = int(
            os.environ.get(
                "YOHANANOF_INCREMENTAL_FILE_LIMIT",
                str(max(1, incremental_limit_default)),
            )
        )

        full_pass = await _collect_scraper_pass(
            scraper_name,
            file_types=["PRICE_FULL_FILE"],
            limit=full_limit,
            pass_name="full_bootstrap",
            collect_schema_diagnostics=True,
        )

        incremental_pass = await _collect_scraper_pass(
            scraper_name,
            file_types=["PRICE_FILE"],
            limit=incremental_limit,
            pass_name="incremental",
            collect_schema_diagnostics=not bool(full_pass["price_rows"]),
        )

        data = _merge_source_passes(
            scraper_name,
            file_limit,
            [full_pass, incremental_pass],
        )
        data["scrape_file_limit"] = {
            "full": full_limit,
            "incremental": incremental_limit,
        }
        data["yohananof_bootstrap"] = {
            "full_files_seen": full_pass["files_seen"],
            "full_baby_rows": len(full_pass["price_rows"]),
            "incremental_files_seen": incremental_pass["files_seen"],
            "incremental_baby_rows": len(incremental_pass["price_rows"]),
            "full_snapshot_found": full_pass["files_seen"] > 0,
        }
        return data

    if scraper_name == "SHUFERSAL":
        stores_pass = await _collect_scraper_pass(
            scraper_name,
            file_types=["STORE_FILE"],
            limit=1,
            pass_name="stores",
        )

        full_limit_default = file_limit if file_limit is not None else 20
        full_limit = int(os.environ.get("SHUFERSAL_FULL_FILE_LIMIT", str(max(1, full_limit_default))))
        incremental_limit_default = file_limit if file_limit is not None else 20
        incremental_limit = int(os.environ.get("SHUFERSAL_INCREMENTAL_FILE_LIMIT", str(max(1, incremental_limit_default))))

        full_pass = await _collect_scraper_pass(
            scraper_name,
            file_types=["PRICE_FULL_FILE"],
            limit=full_limit,
            pass_name="full_bootstrap",
            collect_schema_diagnostics=True,
        )
        incremental_pass = await _collect_scraper_pass(
            scraper_name,
            file_types=["PRICE_FILE"],
            limit=incremental_limit,
            pass_name="incremental",
            collect_schema_diagnostics=not bool(full_pass["price_rows"]),
        )

        be_full_default = file_limit if file_limit is not None else 20
        be_full_limit = int(os.environ.get("BE_FULL_FILE_LIMIT", str(max(1, be_full_default))))
        be_inc_default = file_limit if file_limit is not None else 20
        be_incremental_limit = int(os.environ.get("BE_INCREMENTAL_FILE_LIMIT", str(max(1, be_inc_default))))
        be_max_pages = int(os.environ.get("BE_INDEX_MAX_PAGES", "100"))

        be_passes, be_stats = _collect_be_official(
            full_limit=be_full_limit,
            incremental_limit=be_incremental_limit,
            max_pages=be_max_pages,
        )

        all_passes = [stores_pass, full_pass, incremental_pass, *be_passes]
        data = _merge_source_passes(scraper_name, file_limit, all_passes)
        data["scrape_file_limit"] = {
            "stores": 1,
            "shufersal_full": full_limit,
            "shufersal_incremental": incremental_limit,
            "be_full": be_full_limit,
            "be_incremental": be_incremental_limit,
        }
        data["shufersal_bootstrap"] = {
            "full_files_seen": full_pass["files_seen"],
            "full_baby_rows": len(full_pass["price_rows"]),
            "incremental_files_seen": incremental_pass["files_seen"],
            "incremental_baby_rows": len(incremental_pass["price_rows"]),
            "full_snapshot_found": full_pass["files_seen"] > 0,
        }
        data["be_bootstrap"] = be_stats
        return data

    if scraper_name == "RAMI_LEVY":
        base_limit = file_limit
        if file_limit is not None:
            min_files = int(
                os.environ.get("RAMI_LEVY_MIN_SOURCE_FILES", "100")
            )
            base_limit = max(file_limit, min_files)

        mixed_pass = await _collect_scraper_pass(
            scraper_name,
            file_types=[
                "STORE_FILE",
                "PRICE_FILE",
                "PRICE_FULL_FILE",
                "PROMO_FILE",
                "PROMO_FULL_FILE",
            ],
            limit=base_limit,
            pass_name="mixed",
        )
        data = _merge_source_passes(
            scraper_name, file_limit, [mixed_pass]
        )
        data["scrape_file_limit"] = base_limit
        return data

    broad_pass = await _collect_scraper_pass(
        scraper_name,
        file_types=[
            "STORE_FILE",
            "PRICE_FILE",
            "PRICE_FULL_FILE",
            "PROMO_FILE",
            "PROMO_FULL_FILE",
        ],
        limit=file_limit,
        pass_name="mixed",
    )
    data = _merge_source_passes(
        scraper_name, file_limit, [broad_pass]
    )
    data["scrape_file_limit"] = file_limit
    return data

async def run_chain(scraper_name: str, db: SupabaseREST | None, dry_run: bool, file_limit: int | None, enricher: MetadataEnricher | None = None):
    source_db_name = SCRAPER_TO_DB[scraper_name]
    run_id = None
    if db and not dry_run:
        run = db.insert_returning("feed_ingestion_runs", {
            "chain_id": source_db_name,
            "status": "running",
            "details": {"scraper": scraper_name},
        })
        run_id = run.get("id")

    try:
        data = await collect_source(scraper_name, file_limit)
        merged = merge_prices_and_promos(data["price_rows"], data["promo_rows"])
        prices = [to_db_price(x) for x in merged]

        prices, duplicate_price_rows = collapse_latest_prices(prices)

        stale_rows_skipped = 0
        if db and not dry_run and prices:
            prices, stale_rows_skipped = filter_stale_against_db(db, prices)

        # One-time metadata enrichment for previously unknown barcodes.
        # Prices still come from the retailer transparency files. Cheapersal is
        # used only to fill persistent catalog metadata such as full name,
        # brand, size/stage and package quantity.
        enrichment_stats = {"attempted": 0, "catalog_saved": 0, "skipped": 0, "errors": []}
        if enricher and db and not dry_run and prices:
            enrichment_stats = enricher.enrich_missing_catalog(prices)

        branches = [to_db_branch(x) for x in data["store_rows"]]
        branch_map = {
            (b["chain_id"], b["branch_code"]): b
            for b in branches
        }
        branches = list(branch_map.values())

        # Ensure a branch FK target exists even if a Stores file wasn't present in this run.
        known = {(b["chain_id"], b["branch_code"]) for b in branches}
        for p in prices:
            key = (p["chain_id"], p["branch_code"])
            if key not in known:
                branches.append({
                    "chain_id": p["chain_id"],
                    "branch_code": p["branch_code"],
                    "branch_name": None, "city": None, "address": None,
                    "active": True, "last_seen_at": utcnow(),
                })
                known.add(key)

        if dry_run:
            print(json.dumps({
                "scraper": scraper_name,
                "files_seen": data["files_seen"],
                "branches": len(branches),
                "baby_prices": len(prices),
                "examples": prices[:5],
                "parse_errors": data["errors"][:5],
            }, ensure_ascii=False, indent=2))
        elif db:
            db.upsert("retail_branches", branches, "chain_id,branch_code")
            db.upsert("baby_retail_prices", prices, "chain_id,branch_code,barcode")
            # Since SHUFERSAL supplies both, mark both successful if rows were actually seen.
            touched = sorted({p["chain_id"] for p in prices} | {b["chain_id"] for b in branches})
            for chain_id in touched:
                db.patch("retail_chains", {"id": chain_id}, {"last_success_at": utcnow()})
            if run_id:
                db.patch("feed_ingestion_runs", {"id": run_id}, {
                    "status": "success",
                    "finished_at": utcnow(),
                    "files_seen": data["files_seen"],
                    "products_seen": len(data["price_rows"]),
                    "baby_products_saved": len(prices),
                    "details": {
                        "scraper": scraper_name,
                        "branches_saved": len(branches),
                        "promo_rows_seen": len(data["promo_rows"]),
                        "duplicate_price_rows_collapsed": duplicate_price_rows,
                        "stale_rows_skipped": stale_rows_skipped,
                        "requested_file_limit": data.get("requested_file_limit"),
                        "scrape_file_limit": data.get("scrape_file_limit"),
                        "price_schema_diagnostics": data.get("price_schema_diagnostics", [])[:3],
                        "source_passes": data.get("source_passes", []),
                        "yohananof_bootstrap": data.get("yohananof_bootstrap"),
                        "shufersal_bootstrap": data.get("shufersal_bootstrap"),
                        "be_bootstrap": data.get("be_bootstrap"),
                        "super_pharm_bootstrap": data.get("super_pharm_bootstrap"),
                        "metadata_enrichment": enrichment_stats,
                        "file_kind_counts": data.get("kind_counts", {}),
                        "sample_files": data.get("sample_files", [])[:12],
                        "parse_errors": data["errors"][:25],
                    },
                })
        print(
            f"✅ {scraper_name}: {len(prices)} unique baby price rows "
            f"from {data['files_seen']} files "
            f"({duplicate_price_rows} duplicate rows collapsed, "
            f"{stale_rows_skipped} stale rows skipped)"
        )
    except Exception as exc:
        if db and run_id and not dry_run:
            try:
                db.patch("feed_ingestion_runs", {"id": run_id}, {
                    "status": "failed",
                    "finished_at": utcnow(),
                    "error_message": f"{type(exc).__name__}: {exc}"[:1500],
                    "details": {"traceback": traceback.format_exc()[-5000:]},
                })
            except Exception:
                pass
        print(f"❌ {scraper_name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

async def async_main(args):
    requested = [x.strip() for x in args.chains.split(",") if x.strip()]
    unsupported = [x for x in requested if x == "hiper_cohen"]
    if unsupported:
        print("⚠️ hiper_cohen is pending exact official feed verification and will be skipped.", file=sys.stderr)

    scraper_names = []
    for chain in requested:
        if chain == "hiper_cohen":
            continue
        scr = CHAIN_TO_SCRAPER.get(chain)
        if not scr:
            raise SystemExit(f"Unknown chain: {chain}")
        if scr not in scraper_names:
            scraper_names.append(scr)

    db = None if args.dry_run else SupabaseREST()
    enricher = None
    if db and not args.dry_run:
        enricher = MetadataEnricher(
            db=db,
            api_key=os.environ.get("CHEAPERSAL_API_KEY", ""),
            limit=int(os.environ.get("ENRICHMENT_LIMIT", "8")),
        )
    for scraper_name in scraper_names:
        await run_chain(scraper_name, db, args.dry_run, args.file_limit, enricher)

def main():
    parser = argparse.ArgumentParser(description="Baby price-transparency worker")
    parser.add_argument(
        "--chains",
        default=os.environ.get("ENABLED_CHAINS", "super_pharm,shufersal,be,rami_levy,yochananof"),
        help="Comma-separated DB chain ids",
    )
    raw_limit = os.environ.get("WORKER_FILE_LIMIT", "").strip()
    parser.add_argument("--file-limit", type=int, default=int(raw_limit) if raw_limit else None)
    parser.add_argument("--dry-run", action="store_true", default=os.environ.get("DRY_RUN") == "1")
    args = parser.parse_args()
    asyncio.run(async_main(args))

if __name__ == "__main__":
    main()
