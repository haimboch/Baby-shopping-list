from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests

from .classifier import (
    classify_need,
    infer_brand,
    parse_dimension,
    parse_package_quantity,
)
from .promotions import normalize_promotion_terms, parse_bundle_description
from .product_types import SUPPORTED_PRODUCT_TYPES


SP_SHOP_BASE_URL = "https://shop.super-pharm.co.il"
SP_ONLINE_BRANCH = "online"
SP_CATEGORY_PATHS = {
    "diapers": "/infants-and-toddlers/diapering/0-2-diapers-new-born/c/25111600",
    "wipes": "/infants-and-toddlers/diapering/wet-wipes/c/25112000",
    "formula": "/infants-and-toddlers/nursing-and-feeding/baby-formulas/c/25122800",
}
SP_DISCOVERY_ROOTS = (
    "/infants-and-toddlers/c/25110000",
)

# These official product pages keep the fallback useful when category pages are
# temporarily challenged. Category discovery expands beyond this small seed.
SP_SEED_PRODUCTS = (
    ("formula", "/infants-and-toddlers/nursing-and-feeding/baby-formulas/תרכובת-מזון-לתינוקות-שלב-1/p/319709"),
    ("formula", "/infants-and-toddlers/nursing-and-feeding/baby-formulas/מהדרין-תרכובת-מזון-לתינוקות-שלב-1/p/319712"),
    ("formula", "/infants-and-toddlers/nursing-and-feeding/baby-formulas/product/p/648060"),
    ("wipes", "/infants-and-toddlers/diapering/wet-wipes/מגבונים-לחים-לתינוק-ללא-בישום/p/263254"),
    ("wipes", "/infants-and-toddlers/diapering/wet-wipes/מגבונים-רכים-ועדינים-לתינוק/p/536257"),
    ("wipes", "/infants-and-toddlers/baby-wash/baby-bathing-oil/מגבונים-לחים-לתינוק-ללא-תוספת-בישום/p/710695"),
    ("diapers", "/infants-and-toddlers/diapering/0-2-diapers-new-born/חיתולים-מידה-2-4-8-קג/p/625472"),
)

_PRODUCT_PATH_RE = re.compile(r"/[^\"'<>\s]*?/p/(?P<product_id>\d{4,12})(?:[?\"'<>\s]|$)", re.I)
_MONEY_RE = re.compile(r"(?<![\d.])(?P<amount>\d{1,5}(?:[.,]\d{2}))(?![\d.])")
_DATE_RE = re.compile(r"(?P<day>\d{1,2})[./-](?P<month>\d{1,2})[./-](?P<year>20\d{2})")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _money(value: Any) -> float | None:
    if value in (None, ""):
        return None
    match = re.search(r"\d{1,5}(?:[.,]\d{1,2})?", str(value).replace("₪", ""))
    if not match:
        return None
    try:
        amount = float(match.group(0).replace(",", "."))
    except ValueError:
        return None
    return amount if 0 < amount < 100_000 else None


def _barcode(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if 8 <= len(digits) <= 14 else None


def _iter_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_json(child)


def _json_payloads(page_html: str) -> list[Any]:
    payloads: list[Any] = []
    for match in re.finditer(r"<script\b[^>]*>(.*?)</script>", page_html or "", re.I | re.S):
        raw = html.unescape(match.group(1)).strip()
        if not raw or raw[0] not in "[{":
            continue
        try:
            payloads.append(json.loads(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return payloads


def _first_json_value(objects: Iterable[dict[str, Any]], keys: Iterable[str]) -> Any:
    wanted = {key.lower() for key in keys}
    for obj in objects:
        for key, value in obj.items():
            if key.lower() in wanted and value not in (None, "", [], {}):
                return value
    return None


def _all_json_money(objects: Iterable[dict[str, Any]], keys: Iterable[str]) -> list[float]:
    wanted = {key.lower() for key in keys}
    out: list[float] = []
    for obj in objects:
        for key, value in obj.items():
            if key.lower() not in wanted:
                continue
            amount = _money(value)
            if amount is not None and amount not in out:
                out.append(amount)
    return out


def _meta_content(page_html: str, names: Iterable[str]) -> str | None:
    wanted = {name.lower() for name in names}
    for tag in re.findall(r"<meta\b[^>]*>", page_html or "", re.I):
        attrs = {
            key.lower(): html.unescape(value)
            for key, _, value in re.findall(r"([\w:-]+)\s*=\s*([\"'])(.*?)\2", tag, re.I | re.S)
        }
        marker = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
        if marker in wanted and attrs.get("content"):
            return attrs["content"].strip()
    return None


def _brand_value(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("brand")
    brand = _clean_text(value)
    return brand or None


def _iso_end_date(value: str | None) -> str | None:
    if not value:
        return None
    match = _DATE_RE.search(value)
    if not match:
        return None
    try:
        parsed = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            23,
            59,
            59,
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None
    return parsed.isoformat()


def _visible_money_candidates(text: str) -> list[float]:
    date_spans = [match.span() for match in _DATE_RE.finditer(text)]
    out: list[float] = []
    for match in _MONEY_RE.finditer(text):
        if any(start <= match.start() < end for start, end in date_spans):
            continue
        context = text[max(0, match.start() - 16):match.end() + 28]
        if re.search(r"ל[-\s]?(?:100|יחידה|גרם|מ\"ל)", context, re.I):
            continue
        amount = _money(match.group("amount"))
        if amount is not None and amount not in out:
            out.append(amount)
    return out


def extract_superpharm_product_urls(page_html: str) -> list[str]:
    """Return official Super-Pharm product URLs from links or hydration text."""
    text = html.unescape(page_html or "")
    paths: list[str] = []
    for match in _PRODUCT_PATH_RE.finditer(text):
        raw = match.group(0).rstrip("?\"'<> \t\r\n")
        parsed = urlparse(urljoin(SP_SHOP_BASE_URL, raw))
        if parsed.netloc.lower() != urlparse(SP_SHOP_BASE_URL).netloc:
            continue
        canonical = f"{SP_SHOP_BASE_URL}{parsed.path}"
        if canonical not in paths:
            paths.append(canonical)
    return paths


def extract_superpharm_category_urls(page_html: str) -> list[tuple[str, str]]:
    """Discover supported baby categories from official navigation anchors."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    anchor_re = re.compile(
        r"<a\b[^>]*href=[\"'](?P<href>[^\"']+/c/\d+[^\"']*)[\"'][^>]*>"
        r"(?P<label>.*?)</a>",
        re.I | re.S,
    )
    for match in anchor_re.finditer(page_html or ""):
        label = _clean_text(match.group("label"))
        need_key = classify_need(label)
        if need_key not in SUPPORTED_PRODUCT_TYPES:
            continue
        parsed = urlparse(urljoin(SP_SHOP_BASE_URL, html.unescape(match.group("href"))))
        if parsed.netloc.lower() != urlparse(SP_SHOP_BASE_URL).netloc:
            continue
        canonical = f"{SP_SHOP_BASE_URL}{parsed.path}"
        if canonical in seen:
            continue
        seen.add(canonical)
        found.append((need_key, canonical))
    return found


def parse_superpharm_product_html(
    page_html: str,
    product_url: str,
    *,
    expected_need: str | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any] | None:
    """Parse one official online product page and require barcode plus price."""
    fetched_at = fetched_at or utcnow()
    payloads = _json_payloads(page_html)
    objects = [obj for payload in payloads for obj in _iter_json(payload)]
    product_objects = [
        obj for obj in objects
        if str(obj.get("@type") or "").lower() == "product"
        or any(key in obj for key in ("gtin13", "gtin", "barcode"))
    ]
    scope = product_objects or objects

    name = _clean_text(_first_json_value(scope, ("name", "productName", "title")))
    if not name:
        h1 = re.search(r"<h1\b[^>]*>(.*?)</h1>", page_html or "", re.I | re.S)
        name = _clean_text(h1.group(1)) if h1 else ""
    title = _clean_text(_meta_content(page_html, ("og:title", "twitter:title", "title")))
    if not name:
        name = title
    if not name:
        return None

    barcode = _barcode(_first_json_value(scope, ("gtin13", "gtin14", "gtin", "barcode", "ean")))
    plain = _clean_text(page_html)
    if not barcode:
        match = re.search(r"ברקוד\s+מוצר\s*[:\-]?\s*(\d{8,14})", plain, re.I)
        barcode = _barcode(match.group(1)) if match else None
    if not barcode:
        return None

    need_key = classify_need(f"{name} {title}")
    if expected_need and need_key != expected_need:
        return None
    need_key = expected_need or need_key
    if need_key not in SUPPORTED_PRODUCT_TYPES:
        return None

    offers: list[dict[str, Any]] = []
    for obj in scope:
        value = obj.get("offers")
        if isinstance(value, dict):
            offers.extend(_iter_json(value))
        elif isinstance(value, list):
            for offer in value:
                offers.extend(_iter_json(offer))
    price_scope = offers or scope

    current_candidates = _all_json_money(
        price_scope,
        ("salePrice", "specialPrice", "discountPrice", "currentPrice", "price", "lowPrice"),
    )
    regular_candidates = _all_json_money(
        scope,
        ("regularPrice", "listPrice", "oldPrice", "originalPrice", "highPrice"),
    )
    meta_price = _money(_meta_content(page_html, ("product:price:amount", "og:price:amount", "price")))
    if meta_price is not None:
        current_candidates.insert(0, meta_price)

    h1_pos = plain.find(name)
    barcode_pos = plain.find(barcode)
    visible_scope = plain[h1_pos:barcode_pos] if h1_pos >= 0 and barcode_pos > h1_pos else plain[:5000]
    visible_prices = _visible_money_candidates(visible_scope)
    all_prices = [*current_candidates, *regular_candidates, *visible_prices]
    all_prices = [value for index, value in enumerate(all_prices) if value not in all_prices[:index]]
    if not all_prices:
        return None

    current_price = current_candidates[0] if current_candidates else min(all_prices)
    regular_price = max([value for value in all_prices if value >= current_price] or [current_price])

    multi = parse_bundle_description(visible_scope)
    promo_min_quantity = 1
    promo_total_price: float | None = None
    promo_price: float | None = None
    promo_description = ""
    if multi:
        promo_min_quantity = int(multi["quantity"])
        if multi.get("kind") == "fixed_total":
            promo_total_price = float(multi["total_price"])
        elif multi.get("kind") == "buy_get_free":
            promo_total_price = regular_price * int(multi["paid_quantity"])
        if promo_total_price and promo_min_quantity > 0:
            unit_price = promo_total_price / promo_min_quantity
            if unit_price < regular_price:
                promo_price = unit_price
                promo_description = (
                    f"{promo_min_quantity} אריזות ב-₪{promo_total_price:.2f}"
                )
    elif regular_price > current_price * 1.001:
        promo_price = current_price
        promo_total_price = current_price
        promo_description = f"מחיר אונליין ירד מ-₪{regular_price:.2f} ל-₪{current_price:.2f}"

    valid_match = re.search(r"(?:המחיר\s+)?בתוקף\s+עד\s+([0-9./-]+)", visible_scope, re.I)
    promo_end_at = _iso_end_date(valid_match.group(1)) if valid_match else None
    requires_club = bool(re.search(r"חברי?\s+מועדון|לייף\s*סטייל", visible_scope, re.I))

    brand = _brand_value(_first_json_value(scope, ("brand", "manufacturer", "manufacturerName")))
    brand = brand or infer_brand(f"{title} {name}", None)
    dimension_type, dimension_value = parse_dimension(name, need_key)
    package_quantity, package_unit = parse_package_quantity(name, need_key, None, None)
    product_id_match = re.search(r"/p/(\d{4,12})", product_url)
    product_id = product_id_match.group(1) if product_id_match else None

    price_row = {
        "source_name": "SUPER_PHARM",
        "subchain_id": None,
        "branch_code": SP_ONLINE_BRANCH,
        "barcode": barcode,
        "need_key": need_key,
        "dimension_type": dimension_type,
        "dimension_value": dimension_value,
        "brand": brand,
        "product_name": name,
        "package_quantity": package_quantity,
        "package_unit": package_unit,
        "regular_price": regular_price,
        "source_updated_at": fetched_at,
        "raw_source": {
            "source": "super_pharm_official_online_product_page",
            "product_id": product_id,
            "item_url": product_url,
            "current_price": current_price,
            "list_price": regular_price,
        },
    }

    promo_row = None
    if promo_price is not None:
        candidate = {
            "source_name": "SUPER_PHARM",
            "subchain_id": None,
            "branch_code": SP_ONLINE_BRANCH,
            "barcode": barcode,
            "promo_price": promo_price,
            "promo_description": promo_description or "מבצע באתר סופר-פארם",
            "promo_start_at": fetched_at,
            "promo_end_at": promo_end_at,
            "promo_min_quantity": promo_min_quantity,
            "promo_total_price": promo_total_price,
            "requires_club": requires_club,
            "raw_promo": {
                "source": "super_pharm_official_online_product_page",
                "product_id": product_id,
                "item_url": product_url,
            },
        }
        promo_row = normalize_promotion_terms(candidate, regular_price)

    return {
        "price_row": price_row,
        "promo_row": promo_row,
        "item_url": product_url,
        "product_id": product_id,
    }


def _looks_blocked(response: requests.Response) -> bool:
    text = response.text[:4000].lower()
    return (
        response.status_code not in (200, 304)
        or "kramericaindustries" in text
        or "window.rbzns" in text
        or "access denied" in text
    )


def _get(session: requests.Session, url: str, *, params: dict[str, Any] | None = None) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            response = session.get(url, params=params, timeout=45, allow_redirects=True)
            if not _looks_blocked(response):
                return response
            last_error = RuntimeError(f"blocked HTTP {response.status_code} from {response.url}")
        except Exception as exc:  # pragma: no cover - network behavior
            last_error = exc
        if attempt < 2:
            import time

            time.sleep(1.25)
    assert last_error is not None
    raise last_error


def collect_superpharm_online(
    *,
    max_items: int = 90,
    category_pages: int = 2,
    session: requests.Session | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fallback to Super-Pharm's official online catalog when feeds are blocked."""
    owns_session = session is None
    session = session or requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "he-IL,he;q=0.9,en;q=0.6",
        "Referer": f"{SP_SHOP_BASE_URL}/",
    })

    url_need: dict[str, str | None] = {}
    errors: list[str] = []
    category_pages_attempted = 0
    category_pages_fetched = 0
    category_urls: dict[str, str] = {
        urljoin(SP_SHOP_BASE_URL, path): need_key
        for need_key, path in SP_CATEGORY_PATHS.items()
    }

    def add_url(raw_url: str, need_key: str | None) -> None:
        parsed = urlparse(urljoin(SP_SHOP_BASE_URL, raw_url))
        if parsed.netloc.lower() != urlparse(SP_SHOP_BASE_URL).netloc:
            return
        match = re.search(r"/p/(\d{4,12})", parsed.path)
        if not match:
            return
        canonical = f"{SP_SHOP_BASE_URL}{parsed.path}"
        if canonical not in url_need and len(url_need) < max_items:
            url_need[canonical] = need_key

    for need_key, seed_url in SP_SEED_PRODUCTS:
        add_url(seed_url, need_key)

    for root in SP_DISCOVERY_ROOTS:
        category_pages_attempted += 1
        try:
            response = _get(session, urljoin(SP_SHOP_BASE_URL, root))
            category_pages_fetched += 1
            for need_key, category_url in extract_superpharm_category_urls(response.text):
                category_urls.setdefault(category_url, need_key)
        except Exception as exc:  # pragma: no cover - network behavior
            errors.append(f"category discovery root: {type(exc).__name__}: {exc}")

    for category_url, need_key in category_urls.items():
        for page in range(max(1, category_pages)):
            if len(url_need) >= max_items:
                break
            category_pages_attempted += 1
            try:
                response = _get(session, category_url, params={"page": page})
                category_pages_fetched += 1
                found = extract_superpharm_product_urls(response.text)
                for item_url in found:
                    add_url(item_url, need_key)
                if page > 0 and not found:
                    break
            except Exception as exc:  # pragma: no cover - network behavior
                errors.append(f"category {need_key} page {page}: {type(exc).__name__}: {exc}")
                break

    price_rows: list[dict[str, Any]] = []
    promo_rows: list[dict[str, Any]] = []
    product_pages_attempted = 0
    product_pages_fetched = 0
    product_pages_parsed = 0
    for product_url, expected_need in list(url_need.items())[:max_items]:
        product_pages_attempted += 1
        try:
            response = _get(session, product_url)
            product_pages_fetched += 1
            parsed = parse_superpharm_product_html(
                response.text,
                product_url,
                expected_need=expected_need,
            )
            if not parsed:
                continue
            product_pages_parsed += 1
            price_rows.append(parsed["price_row"])
            if parsed.get("promo_row"):
                promo_rows.append(parsed["promo_row"])
        except Exception as exc:  # pragma: no cover - network behavior
            errors.append(f"{product_url}: {type(exc).__name__}: {exc}")

    price_by_barcode = {str(row["barcode"]): row for row in price_rows}
    promo_by_barcode = {str(row["barcode"]): row for row in promo_rows}
    price_rows = list(price_by_barcode.values())
    promo_rows = [promo_by_barcode[key] for key in price_by_barcode if key in promo_by_barcode]

    result = {
        "pass_name": "super_pharm_official_online_fallback",
        "file_types": ["OFFICIAL_ONLINE_PRODUCT_PAGES"],
        "limit": max_items,
        "files_seen": product_pages_fetched,
        "price_rows": price_rows,
        "promo_rows": promo_rows,
        "store_rows": ([{
            "source_name": "SUPER_PHARM",
            "branch_code": SP_ONLINE_BRANCH,
            "subchain_id": None,
            "branch_name": "סופר-פארם אונליין",
            "city": None,
            "address": "משלוח או איסוף לפי זמינות באתר",
        }] if price_rows else []),
        "errors": errors[:40],
        "kind_counts": {"official_online_product_page": product_pages_fetched},
        "sample_files": list(url_need.keys())[:12],
        "price_schema_diagnostics": [],
    }
    stats = {
        "source": SP_SHOP_BASE_URL,
        "fallback_used": True,
        "category_pages_attempted": category_pages_attempted,
        "category_pages_fetched": category_pages_fetched,
        "category_urls_discovered": len(category_urls),
        "category_needs_discovered": sorted(set(category_urls.values())),
        "product_urls_discovered": len(url_need),
        "product_pages_attempted": product_pages_attempted,
        "product_pages_fetched": product_pages_fetched,
        "product_pages_parsed": product_pages_parsed,
        "baby_prices": len(price_rows),
        "active_promotions": len(promo_rows),
        "blocked": bool(errors) and product_pages_fetched == 0,
        "errors": errors[:12],
    }

    if owns_session:
        session.close()
    return result, stats
