from __future__ import annotations

import html
import json
import os
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


KSP_BASE_URL = "https://ksp.co.il"
KSP_API_BASE = f"{KSP_BASE_URL}/m_action/api"
KSP_ONLINE_BRANCH = "online"
KSP_CATEGORY_PATHS = {
    "diapers": "/mob/cat/7043",
    "wipes": "/mob/cat/13763",
    "formula": "/mob/cat/56308",
}

# Direct official product pages are tried before category/search discovery.
# This gives the worker a useful starting point when KSP blocks list/search
# endpoints from a GitHub-hosted IP. Successful pages are persisted and become
# self-refreshing through ksp_known_items on later runs.
KSP_SEED_ITEMS = (
    {"item_id": "440079", "need_key": "diapers"},
    {"item_id": "440064", "need_key": "diapers"},
    {"item_id": "150487", "need_key": "diapers"},
    {"item_id": "70286", "need_key": "wipes"},
    {"item_id": "456164", "need_key": "wipes"},
)

_ITEM_URL_RE = re.compile(
    r"(?:https?://(?:www\.)?ksp\.co\.il)?/"
    r"(?:(?:mob|web)/item/(?P<plain>\d+)|item/(?:\d+-)?(?P<legacy>\d+))",
    re.I,
)
_JSON_ITEM_ID_RE = re.compile(
    r'\"(?:itemId|item_id|productId|product_id)\"\s*:\s*\"?(\d{3,9})\"?',
    re.I,
)
_MONEY_RE = re.compile(r"-?\d+(?:[.,]\d{1,2})?")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _money(value: Any) -> float | None:
    if value in (None, ""):
        return None
    match = _MONEY_RE.search(str(value).replace("₪", "").replace(",", "."))
    if not match:
        return None
    try:
        amount = float(match.group(0).replace(",", "."))
    except ValueError:
        return None
    return amount if 0 < amount < 100_000 else None


def _barcode(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if 8 <= len(digits) <= 14:
        return digits
    return None


def _iter_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_json(child)


def _find_api_barcode(payload: Any) -> str | None:
    """Find a GTIN in KSP's API response, including specification rows."""
    for obj in _iter_json(payload):
        for key, value in obj.items():
            normalized = str(key).lower().replace("_", "")
            if normalized in {"barcode", "barCode".lower(), "gtin", "gtin13", "ean", "ean13"}:
                found = _barcode(value)
                if found:
                    return found
        label = _clean_text(obj.get("name") or obj.get("title") or obj.get("label")).lower()
        if "ברקוד" in label or "barcode" in label or "gtin" in label:
            found = _barcode(obj.get("value") or obj.get("val") or obj.get("text"))
            if found:
                return found
    return None


def _api_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    result = payload.get("result")
    return result if isinstance(result, dict) else payload


def _api_items(payload: Any) -> list[dict[str, Any]]:
    result = _api_result(payload)
    items = result.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    data = result.get("data")
    return [data] if isinstance(data, dict) else []


def parse_ksp_api_product(
    payload: dict[str, Any],
    *,
    item_id: str | None = None,
    expected_barcode: str | None = None,
    expected_need: str | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any] | None:
    """Normalize one KSP internal JSON API response into retail price rows.

    Barcode search results do not always repeat the GTIN in every item object.
    In that narrow case the exact barcode used for the search is accepted,
    provided the returned product still classifies to the expected baby need.
    """
    fetched_at = fetched_at or utcnow()
    result = _api_result(payload)
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    if not isinstance(data, dict):
        return None

    name = _clean_text(data.get("name") or data.get("productName") or data.get("title"))
    if not name:
        return None
    detected_need = classify_need(name)
    if expected_need and detected_need != expected_need:
        return None
    need_key = expected_need or detected_need
    if need_key not in SUPPORTED_PRODUCT_TYPES:
        return None

    barcode = _find_api_barcode(payload) or _barcode(expected_barcode)
    if not barcode:
        return None

    price = _money(data.get("price") or data.get("current_price") or data.get("regularPrice"))
    club_price = _money(data.get("min_price") or data.get("bms_price") or data.get("clubPrice"))
    if price is None and club_price is None:
        return None
    regular_price = max(value for value in (price, club_price) if value is not None)
    promo_price = club_price if club_price is not None and club_price < regular_price else None

    resolved_item_id = _clean_text(data.get("uin") or data.get("id") or item_id) or None
    brand = _brand_value(data.get("brandName") or data.get("brand")) or infer_brand(name, None)
    dimension_type, dimension_value = parse_dimension(name, need_key)
    package_quantity, package_unit = parse_package_quantity(name, need_key, None, None)
    labels = data.get("labels") if isinstance(data.get("labels"), list) else []
    label_text = " | ".join(
        _clean_text(label.get("msg") if isinstance(label, dict) else label)
        for label in labels
        if _clean_text(label.get("msg") if isinstance(label, dict) else label)
    )
    description = label_text or ("מחיר מועדון KSP" if promo_price is not None else "")
    item_url = f"{KSP_BASE_URL}/mob/item/{resolved_item_id}" if resolved_item_id else None

    price_row = {
        "source_name": "KSP",
        "subchain_id": None,
        "branch_code": KSP_ONLINE_BRANCH,
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
            "source": "ksp_internal_json_api",
            "item_id": resolved_item_id,
            "item_url": item_url,
            "current_price": price,
            "club_price": club_price,
        },
    }
    promo_row = None
    if promo_price is not None or parse_bundle_description(description):
        candidate = {
            "source_name": "KSP",
            "subchain_id": None,
            "branch_code": KSP_ONLINE_BRANCH,
            "barcode": barcode,
            "promo_price": promo_price,
            "promo_description": description or "מחיר מועדון KSP",
            "promo_start_at": fetched_at,
            "promo_end_at": None,
            "promo_min_quantity": 1,
            "promo_total_price": promo_price,
            "requires_club": promo_price is not None,
            "raw_promo": {
                "source": "ksp_internal_json_api",
                "item_id": resolved_item_id,
                "item_url": item_url,
            },
        }
        candidate = normalize_promotion_terms(candidate, regular_price)
        if candidate.get("promo_price") is not None:
            promo_row = candidate
    return {
        "price_row": price_row,
        "promo_row": promo_row,
        "item_url": item_url,
        "item_id": resolved_item_id,
    }


def _json_payloads(page_html: str) -> list[Any]:
    payloads: list[Any] = []
    for match in re.finditer(
        r"<script\b[^>]*(?:type=[\"']application/(?:ld\+json|json)[\"'])?[^>]*>(.*?)</script>",
        page_html or "",
        re.I | re.S,
    ):
        raw = html.unescape(match.group(1)).strip()
        if not raw or raw[0] not in "[{":
            continue
        try:
            payloads.append(json.loads(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return payloads


def extract_ksp_item_urls(page_html: str) -> list[str]:
    """Extract canonical official KSP product URLs from HTML or hydration JSON."""
    ids: list[str] = []
    for match in _ITEM_URL_RE.finditer(html.unescape(page_html or "")):
        item_id = match.group("plain") or match.group("legacy")
        if item_id and item_id not in ids:
            ids.append(item_id)
    for item_id in _JSON_ITEM_ID_RE.findall(page_html or ""):
        if item_id not in ids:
            ids.append(item_id)
    return [f"{KSP_BASE_URL}/mob/item/{item_id}" for item_id in ids]


def _meta_content(page_html: str, names: Iterable[str]) -> str | None:
    wanted = {name.lower() for name in names}
    for tag in re.findall(r"<meta\b[^>]*>", page_html or "", re.I):
        attrs = {
            key.lower(): html.unescape(value)
            for key, _, value in re.findall(
                r"([\w:-]+)\s*=\s*([\"'])(.*?)\2",
                tag,
                re.I | re.S,
            )
        }
        marker = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
        if marker in wanted and attrs.get("content"):
            return attrs["content"].strip()
    return None


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


def _brand_value(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("brand")
    text = _clean_text(value)
    return text or None


def parse_ksp_product_html(
    page_html: str,
    product_url: str,
    *,
    expected_need: str | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any] | None:
    """Parse one official KSP product page without relying on a private API.

    The parser prefers Product JSON-LD and official price meta tags, then falls
    back to hydration JSON/visible labels. It rejects pages without both a
    retail price and a real barcode, preventing SEO/category text from entering
    the product catalog.
    """
    fetched_at = fetched_at or utcnow()
    payloads = _json_payloads(page_html)
    objects = [obj for payload in payloads for obj in _iter_json(payload)]
    product_objects = [
        obj
        for obj in objects
        if str(obj.get("@type") or "").lower() == "product"
        or any(key in obj for key in ("gtin13", "gtin", "barcode"))
    ]
    scope = product_objects or objects

    name = _clean_text(_first_json_value(scope, ("name", "productName", "title")))
    if not name:
        name = _clean_text(_meta_content(page_html, ("og:title", "twitter:title")))
    if not name:
        h1 = re.search(r"<h1\b[^>]*>(.*?)</h1>", page_html or "", re.I | re.S)
        name = _clean_text(h1.group(1)) if h1 else ""
    if not name:
        printed_name = re.search(r"שם\s+המוצר\s*[:\-]?\s*(.+?)(?:מספר\s+מוצר|תאריך\s+תוקף)", _clean_text(page_html), re.I)
        name = _clean_text(printed_name.group(1)) if printed_name else ""
    if not name:
        return None

    raw_barcode = _first_json_value(
        scope,
        ("gtin13", "gtin14", "gtin12", "gtin8", "gtin", "barcode", "barCode"),
    )
    barcode = _barcode(raw_barcode)
    plain = _clean_text(page_html)
    if not barcode:
        match = re.search(r"ברקוד\s*[:\-]?\s*(\d{8,14})", plain, re.I)
        barcode = _barcode(match.group(1)) if match else None
    if not barcode:
        return None

    detected_need = classify_need(name)
    if expected_need and detected_need != expected_need:
        return None
    need_key = expected_need or detected_need
    if need_key not in SUPPORTED_PRODUCT_TYPES:
        return None

    offer_objects: list[dict[str, Any]] = []
    for obj in scope:
        offers = obj.get("offers")
        if isinstance(offers, dict):
            offer_objects.extend(_iter_json(offers))
        elif isinstance(offers, list):
            for offer in offers:
                offer_objects.extend(_iter_json(offer))
    price_scope = offer_objects or scope

    current_candidates = _all_json_money(
        price_scope,
        ("salePrice", "specialPrice", "finalPrice", "discountPrice", "currentPrice", "price", "lowPrice"),
    )
    meta_price = _money(
        _meta_content(
            page_html,
            ("product:price:amount", "og:price:amount", "price"),
        )
    )
    if meta_price is not None:
        current_candidates.insert(0, meta_price)

    # KSP's print/mobile variants do not always expose JSON-LD. Keep the
    # fallback narrowly anchored to official price labels.
    plain_price = re.search(
        r"(?:מחיר\s+אשראי|מחיר\s+המוצר|מחיר\s+באתר)\s*[:\-]?\s*(\d+(?:[.,]\d{1,2})?)\s*₪?",
        plain,
        re.I,
    )
    visible_price = _money(plain_price.group(1)) if plain_price else None
    if visible_price is not None:
        current_candidates.append(visible_price)

    regular_candidates = _all_json_money(
        scope,
        ("regularPrice", "listPrice", "oldPrice", "originalPrice", "beforePrice", "priceBeforeDiscount"),
    )
    current_price = current_candidates[0] if current_candidates else None
    if current_price is None:
        return None

    valid_regular = [amount for amount in regular_candidates if amount >= current_price]
    regular_price = max(valid_regular) if valid_regular else current_price
    promo_price = current_price if regular_price > current_price * 1.001 else None

    brand_raw = _first_json_value(scope, ("brand", "manufacturer", "manufacturerName"))
    # KSP is the retailer, not a fallback manufacturer. Unknown brands remain
    # null so they cannot contaminate the shared catalog.
    brand = _brand_value(brand_raw) or infer_brand(name, None)
    dimension_type, dimension_value = parse_dimension(name, need_key)
    package_quantity, package_unit = parse_package_quantity(name, need_key, None, None)

    description = _clean_text(
        _first_json_value(scope, ("promotionDescription", "promoDescription", "saleText", "discountText"))
    )
    if promo_price is not None and not description:
        description = f"מחיר KSP ירד מ-₪{regular_price:.2f} ל-₪{promo_price:.2f}"

    promo_end = _first_json_value(
        price_scope,
        ("priceValidUntil", "validThrough", "promotionEndDate", "endDate"),
    )
    item_match = _ITEM_URL_RE.search(product_url)
    item_id = (item_match.group("plain") or item_match.group("legacy")) if item_match else None

    price_row = {
        "source_name": "KSP",
        "subchain_id": None,
        "branch_code": KSP_ONLINE_BRANCH,
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
            "source": "ksp_official_product_page",
            "item_id": item_id,
            "item_url": product_url,
            "current_price": current_price,
            "list_price": regular_price,
        },
    }

    promo_row = None
    if promo_price is not None or parse_bundle_description(description):
        candidate = {
            "source_name": "KSP",
            "subchain_id": None,
            "branch_code": KSP_ONLINE_BRANCH,
            "barcode": barcode,
            "promo_price": promo_price,
            "promo_description": description or "מחיר מבצע באתר KSP",
            "promo_start_at": fetched_at,
            "promo_end_at": str(promo_end).strip() if promo_end else None,
            "promo_min_quantity": 1,
            "promo_total_price": promo_price,
            "requires_club": False,
            "raw_promo": {
                "source": "ksp_official_product_page",
                "item_id": item_id,
                "item_url": product_url,
            },
        }
        candidate = normalize_promotion_terms(candidate, regular_price)
        if candidate.get("promo_price") is not None:
            promo_row = candidate

    return {
        "price_row": price_row,
        "promo_row": promo_row,
        "item_url": product_url,
        "item_id": item_id,
    }


def _get(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 35,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, 3):
        try:
            response = session.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
            )
            challenge = response.text[:4000].lower() if "text" in response.headers.get("Content-Type", "").lower() else ""
            blocked = (
                response.status_code in (403, 429)
                or "kramericaindustries" in challenge
                or "window.rbzns" in challenge
                or "access denied" in challenge
            )
            if response.ok and not blocked:
                return response
            detail = ""
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    error = str(payload.get("error") or "").strip()
                    upstream_status = payload.get("upstream_status")
                    if response.status_code == 401 and error.lower() == "unauthorized" and upstream_status is None:
                        detail = "relay authorization rejected; compare Cloudflare RELAY_TOKEN with GitHub KSP_RELAY_TOKEN"
                    elif upstream_status is not None:
                        detail = f"KSP upstream status={upstream_status}; {error}".strip("; ")
                    elif error:
                        detail = error
            except (TypeError, ValueError):
                detail = ""
            suffix = f" ({detail[:260]})" if detail else ""
            last_error = RuntimeError(
                f"HTTP {response.status_code} from {response.url}{suffix}"
            )
        except Exception as exc:  # pragma: no cover - network behavior
            last_error = exc
        # Retrying the same WAF denial only makes a run slower and noisier.
        if isinstance(last_error, RuntimeError) and any(
            marker in str(last_error) for marker in ("HTTP 401", "HTTP 403")
        ):
            break
        if attempt < 2:
            import time

            time.sleep(1.1 * attempt)
    assert last_error is not None
    raise last_error


def _get_json(
    session: requests.Session,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    relay_url: str = "",
    relay_token: str = "",
    timeout: int = 35,
) -> dict[str, Any]:
    relay_url = relay_url.strip().rstrip("/")
    relay_token = relay_token.strip()
    headers = None
    if relay_url:
        if not relay_token:
            raise RuntimeError("KSP relay is configured without KSP_RELAY_TOKEN")
        normalized = path.strip("/")
        if normalized == "category":
            url = f"{relay_url}/ksp/category"
        elif re.fullmatch(r"item/\d{3,9}", normalized):
            url = f"{relay_url}/ksp/{normalized}"
        else:
            raise RuntimeError(f"unsupported KSP relay path: {normalized}")
        headers = {"Authorization": f"Bearer {relay_token}", "Accept": "application/json"}
    else:
        url = urljoin(f"{KSP_API_BASE}/", path.lstrip("/"))
    response = _get(session, url, params=params, headers=headers, timeout=timeout)
    content_type = response.headers.get("Content-Type", "").lower()
    if "json" not in content_type and not response.text.lstrip().startswith(("{", "[")):
        raise RuntimeError(f"non-JSON response from {response.url}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected JSON response from {response.url}")
    return payload


def collect_ksp_official(
    *,
    catalog_targets: list[dict[str, str]] | None = None,
    known_items: list[dict[str, str]] | None = None,
    max_items: int = 120,
    category_pages: int = 3,
    discovery_limit: int = 80,
    relay_url: str | None = None,
    relay_token: str | None = None,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collect KSP baby offers from public official category/search/product pages."""
    owns_session = session is None
    session = session or requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36 "
                "Baby-Smart-List/0.49"
            ),
            "Accept": "application/json,text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "he-IL,he;q=0.9,en;q=0.6",
            "Referer": f"{KSP_BASE_URL}/web/",
            "Origin": KSP_BASE_URL,
        }
    )

    targets = catalog_targets or []
    known = known_items or []
    relay_url = (relay_url if relay_url is not None else os.environ.get("KSP_RELAY_URL", "")).strip()
    relay_token = (relay_token if relay_token is not None else os.environ.get("KSP_RELAY_TOKEN", "")).strip()
    url_need: dict[str, str | None] = {}
    errors: list[str] = []
    search_requests = 0
    category_requests = 0
    item_page_attempts = 0
    api_search_requests = 0
    api_item_requests = 0
    api_responses = 0
    api_parsed = 0

    def add_url(raw_url: str, need_key: str | None) -> None:
        parsed = urlparse(urljoin(KSP_BASE_URL, raw_url))
        match = _ITEM_URL_RE.search(parsed.path)
        if not match:
            return
        item_id = match.group("plain") or match.group("legacy")
        canonical = f"{KSP_BASE_URL}/mob/item/{item_id}"
        if canonical not in url_need and len(url_need) < max_items:
            url_need[canonical] = need_key
        elif canonical in url_need and not url_need[canonical] and need_key:
            url_need[canonical] = need_key

    for item in known:
        if item.get("item_url"):
            add_url(str(item["item_url"]), item.get("need_key"))
        elif item.get("item_id"):
            add_url(f"/mob/item/{item['item_id']}", item.get("need_key"))

    for item in KSP_SEED_ITEMS:
        add_url(f"/mob/item/{item['item_id']}", item.get("need_key"))

    # KSP's React storefront uses a JSON endpoint that is less likely to be
    # challenged than the rendered HTML. Exact GTIN searches are attempted
    # first so the requested barcode remains the identity anchor.
    api_price_rows: list[dict[str, Any]] = []
    api_promo_rows: list[dict[str, Any]] = []
    api_item_ids: set[str] = set()
    for target in targets:
        if api_search_requests >= discovery_limit or len(api_price_rows) >= max_items:
            break
        barcode = _barcode(target.get("barcode"))
        need_key = str(target.get("need_key") or "").strip() or None
        if not barcode or need_key not in SUPPORTED_PRODUCT_TYPES:
            continue
        api_search_requests += 1
        try:
            payload = _get_json(
                session,
                "category/",
                params={"search": barcode},
                relay_url=relay_url,
                relay_token=relay_token,
            )
            api_responses += 1
            candidates = _api_items(payload)
            for item in candidates:
                item_barcode = _find_api_barcode(item)
                if item_barcode and item_barcode != barcode:
                    continue
                if not item_barcode and len(candidates) != 1:
                    continue
                item_id = _clean_text(item.get("uin") or item.get("id")) or None
                parsed = parse_ksp_api_product(
                    {"result": item},
                    item_id=item_id,
                    expected_barcode=barcode,
                    expected_need=need_key,
                )
                if not parsed:
                    continue
                api_price_rows.append(parsed["price_row"])
                if parsed.get("promo_row"):
                    api_promo_rows.append(parsed["promo_row"])
                if item_id:
                    api_item_ids.add(item_id)
                    add_url(f"/mob/item/{item_id}", need_key)
                api_parsed += 1
                break
        except Exception as exc:  # pragma: no cover - network behavior
            errors.append(f"api search {barcode}: {type(exc).__name__}: {exc}")

    # Enrich known/seeded items through the product JSON endpoint. These rows
    # are accepted only when the API itself contains a real barcode.
    known_api_targets: list[tuple[str, str | None]] = []
    for product_url, need_key in list(url_need.items()):
        match = _ITEM_URL_RE.search(product_url)
        item_id = (match.group("plain") or match.group("legacy")) if match else None
        if item_id and item_id not in api_item_ids:
            known_api_targets.append((item_id, need_key))
    for item_id, need_key in known_api_targets:
        if api_item_requests >= max_items or len(api_price_rows) >= max_items:
            break
        api_item_requests += 1
        try:
            payload = _get_json(
                session,
                f"item/{item_id}",
                relay_url=relay_url,
                relay_token=relay_token,
            )
            api_responses += 1
            parsed = parse_ksp_api_product(
                payload,
                item_id=item_id,
                expected_need=need_key,
            )
            if not parsed:
                continue
            api_price_rows.append(parsed["price_row"])
            if parsed.get("promo_row"):
                api_promo_rows.append(parsed["promo_row"])
            api_parsed += 1
        except Exception as exc:  # pragma: no cover - network behavior
            errors.append(f"api item {item_id}: {type(exc).__name__}: {exc}")

    # Category discovery is a small fixed number of requests and can expose
    # new products. It runs before barcode searches, which are more likely to
    # be throttled by KSP.
    if not api_price_rows and len(url_need) < max_items:
        for need_key, path in KSP_CATEGORY_PATHS.items():
            for page in range(1, max(1, category_pages) + 1):
                if len(url_need) >= max_items:
                    break
                category_requests += 1
                try:
                    response = _get(
                        session,
                        urljoin(KSP_BASE_URL, path),
                        params={"page": page},
                    )
                    found = extract_ksp_item_urls(response.text)
                    for item_url in found:
                        add_url(item_url, need_key)
                    if page > 1 and not found:
                        break
                except Exception as exc:  # pragma: no cover - network behavior
                    errors.append(f"category {need_key} page {page}: {type(exc).__name__}: {exc}")
                    break

    # Exact barcode search safely links KSP offers to the catalog we already trust.
    if not api_price_rows:
        known_barcodes = {str(item.get("barcode") or "") for item in known}
        for target in targets:
            if len(url_need) >= max_items or search_requests >= discovery_limit:
                break
            barcode = str(target.get("barcode") or "").strip()
            need_key = str(target.get("need_key") or "").strip() or None
            if not barcode or barcode in known_barcodes:
                continue
            search_requests += 1
            try:
                response = _get(
                    session,
                    f"{KSP_BASE_URL}/mob/cat/",
                    params={"search": barcode},
                )
                for item_url in extract_ksp_item_urls(response.text):
                    add_url(item_url, need_key)
            except Exception as exc:  # pragma: no cover - network behavior
                errors.append(f"search {barcode}: {type(exc).__name__}: {exc}")

    price_rows: list[dict[str, Any]] = list(api_price_rows)
    promo_rows: list[dict[str, Any]] = list(api_promo_rows)
    fetched_urls = 0
    parsed_urls = 0
    html_targets = [] if api_price_rows else list(url_need.items())[:max_items]
    for product_url, expected_need in html_targets:
        item_match = _ITEM_URL_RE.search(product_url)
        item_id = (item_match.group("plain") or item_match.group("legacy")) if item_match else None
        variants = [product_url]
        if item_id:
            variants.extend([
                f"{KSP_BASE_URL}/web/item/{item_id}",
                f"{KSP_BASE_URL}/?print={item_id}",
            ])
        parsed = None
        for variant in variants:
            item_page_attempts += 1
            try:
                response = _get(session, variant)
                fetched_urls += 1
                parsed = parse_ksp_product_html(
                    response.text,
                    product_url,
                    expected_need=expected_need,
                )
                if parsed:
                    break
            except Exception as exc:  # pragma: no cover - network behavior
                errors.append(f"{variant}: {type(exc).__name__}: {exc}")
        if not parsed:
            continue
        parsed_urls += 1
        price_rows.append(parsed["price_row"])
        if parsed.get("promo_row"):
            promo_rows.append(parsed["promo_row"])

    # One current offer per barcode. Prefer the last successfully parsed page.
    price_by_barcode = {str(row["barcode"]): row for row in price_rows}
    promo_by_barcode = {str(row["barcode"]): row for row in promo_rows}
    price_rows = list(price_by_barcode.values())
    promo_rows = [promo_by_barcode[barcode] for barcode in price_by_barcode if barcode in promo_by_barcode]

    result = {
        "pass_name": "ksp_official_online",
        "file_types": ["OFFICIAL_PRODUCT_PAGES"],
        "limit": max_items,
        "files_seen": api_responses + fetched_urls,
        "price_rows": price_rows,
        "promo_rows": promo_rows,
        "store_rows": ([
            {
                "source_name": "KSP",
                "branch_code": KSP_ONLINE_BRANCH,
                "subchain_id": None,
                "branch_name": "KSP אונליין",
                "city": None,
                "address": "משלוח ארצי או איסוף לפי זמינות",
            }
        ] if price_rows else []),
        "errors": errors[:30],
        "kind_counts": {
            "official_json_api": api_responses,
            "official_product_page": fetched_urls,
        },
        "sample_files": list(url_need.keys())[:12],
        "price_schema_diagnostics": [],
    }
    stats = {
        "source": KSP_BASE_URL,
        "fulfillment_mode": "online",
        "known_item_targets": len(known),
        "catalog_targets": len(targets),
        "search_requests": search_requests,
        "api_search_requests": api_search_requests,
        "api_item_requests": api_item_requests,
        "api_responses": api_responses,
        "api_items_parsed": api_parsed,
        "api_transport": "cloudflare_relay" if relay_url else "direct",
        "relay_configured": bool(relay_url and relay_token),
        "relay_auth_failed": any(
            "relay authorization rejected" in error for error in errors
        ),
        "category_requests": category_requests,
        "item_urls_discovered": len(url_need),
        "item_page_attempts": item_page_attempts,
        "item_pages_fetched": fetched_urls,
        "item_pages_parsed": parsed_urls,
        "baby_prices": len(price_rows),
        "active_promotions": len(promo_rows),
        "blocked": bool(errors) and api_responses == 0 and fetched_urls == 0,
        "errors": errors[:12],
    }

    if owns_session:
        session.close()
    return [result], stats
