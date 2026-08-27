"""Authorized, quota-aware Super-Pharm catalog import from CheaperSal's API."""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .cheapersal_prices import (
    CheaperSalPriceClient,
    _barcode,
    _branch_coordinates,
    _clean,
    _matches_chain,
    _money,
)
from .classifier import classify_need, infer_brand, parse_dimension, parse_package_quantity
from .promotions import normalize_promotion_terms
from .enrichment import API_BASE
from .product_types import SUPPORTED_PRODUCT_TYPES


SUPER_PHARM_ONLINE_PROVIDER_ID = "5f186c4390ae893cc6e86587"
SUPER_PHARM_ONLINE_CODE = "online"
BABY_CATEGORY_MARKERS = (
    "baby", "babies", "infant", "toddler", "newborn", "diaper", "napp",
    "formula", "maternity", "feeding", "pacifier", "תינוק", "פעוט",
    "החתל", "חיתול", "טיטול", "מגבונ", "תמל", "תמ״ל", 'תמ"ל',
    "תחליפי חלב", "תחליף חלב", "מטרנה", "סימילאק", "נוטרילון", "מוצצ",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _is_baby_category(value: str) -> bool:
    text = _clean(value).lower().replace("-", " ").replace("_", " ")
    return any(marker.lower() in text for marker in BABY_CATEGORY_MARKERS)


def discover_baby_categories(data: Any) -> list[str]:
    """Accept both the documented flat category list and future category trees."""
    categories = data
    if isinstance(data, dict):
        categories = data.get("categories") or data.get("items") or []
    if not isinstance(categories, list):
        return []

    result: list[str] = []
    seen: set[str] = set()

    def visit(node: dict[str, Any], parent_is_baby: bool = False) -> None:
        if not isinstance(node, dict):
            return
        slug = _clean(node.get("slug") or node.get("key"))
        name = " ".join(
            _clean(node.get(field))
            for field in ("name", "title", "description", "slug")
            if node.get(field)
        )
        relevant = parent_is_baby or _is_baby_category(name)
        if relevant and slug and slug not in seen:
            seen.add(slug)
            result.append(slug)
        for key in ("children", "subcategories", "categories", "items"):
            children = node.get(key)
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict):
                        visit(child, relevant)

    for category in categories:
        if isinstance(category, dict):
            visit(category)
    return result


def _online_branch_row() -> dict[str, Any]:
    return {
        "source_name": "SUPER_PHARM",
        "branch_code": SUPER_PHARM_ONLINE_CODE,
        "subchain_id": None,
        "branch_name": "סופר-פארם אונליין",
        "city": None,
        "address": "מחיר באתר האונליין · מחיר וזמינות בסניף אינם מאומתים",
    }


def branch_from_provider(value: dict[str, Any]) -> dict[str, Any] | None:
    """Keep provider branch ids stable so existing branch prices remain linked."""
    provider_id = _clean(value.get("id") or value.get("branchId"))
    is_online = bool(value.get("isOnline")) or provider_id == SUPER_PHARM_ONLINE_PROVIDER_ID
    branch_code = (
        SUPER_PHARM_ONLINE_CODE
        if is_online
        else provider_id or _clean(value.get("storeId"))
    )
    if not branch_code:
        return None

    row = {
        "source_name": "SUPER_PHARM",
        "branch_code": branch_code,
        "subchain_id": None,
        "branch_name": _clean(value.get("name")) or (
            "סופר-פארם אונליין" if is_online else "סופר-פארם"
        ),
        "city": _clean(value.get("city")) or None,
        "address": _clean(value.get("address")) or (
            "מחיר באתר האונליין · מחיר וזמינות בסניף אינם מאומתים"
            if is_online else None
        ),
    }
    latitude, longitude = _branch_coordinates(value)
    if not is_online and latitude is not None and longitude is not None:
        row.update({"latitude": latitude, "longitude": longitude})
    return row


def product_from_provider(
    value: dict[str, Any], *, category_slug: str | None = None
) -> dict[str, Any] | None:
    """Normalize only products the existing strict baby-only classifier accepts."""
    barcode = _barcode(value.get("barcode") or value.get("itemCode") or value.get("ean"))
    name = _clean(value.get("name") or value.get("productName") or value.get("title"))
    price = _money(value.get("price") or value.get("regularPrice") or value.get("originalPrice"))
    need = classify_need(name)
    if not barcode or not name or price is None or need not in SUPPORTED_PRODUCT_TYPES:
        return None

    manufacturer = _clean(
        value.get("brand") or value.get("manufacturer") or value.get("manufacturerName")
    ) or None
    dimension_type, dimension_value = parse_dimension(name, need)
    package_count = value.get("qtyInPackage") or value.get("packageQuantity")
    unit_quantity = value.get("unitQty") or value.get("unitQuantity")
    package_quantity, package_unit = parse_package_quantity(
        name,
        need,
        str(package_count) if package_count not in (None, "") else None,
        str(unit_quantity) if unit_quantity not in (None, "") else None,
    )
    updated_at = value.get("updatedAt") or value.get("lastUpdated") or utcnow()
    return {
        "source_name": "SUPER_PHARM",
        "branch_code": SUPER_PHARM_ONLINE_CODE,
        "subchain_id": None,
        "barcode": barcode,
        "need_key": need,
        "dimension_type": dimension_type,
        "dimension_value": dimension_value,
        "brand": infer_brand(name, manufacturer),
        "product_name": name,
        "package_quantity": package_quantity,
        "package_unit": package_unit,
        "regular_price": price,
        "promo_price": None,
        "promo_description": None,
        "promo_start_at": None,
        "promo_end_at": None,
        "promo_min_quantity": 1,
        "promo_total_price": None,
        "requires_club": False,
        "source_updated_at": updated_at,
        "last_seen_at": utcnow(),
        "raw_source": {
            "source": "cheapersal_authorized_online_branch_products",
            "provider_branch_id": SUPER_PHARM_ONLINE_PROVIDER_ID,
            "price_scope": "online_only",
            "in_store_price_verified": False,
            "in_store_stock_verified": False,
            "category_slug": category_slug,
            "product": value,
        },
    }


def promo_from_provider(
    value: dict[str, Any], price_rows: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    barcode = _barcode(value.get("itemCode") or value.get("barcode") or value.get("ean"))
    product = price_rows.get(barcode)
    if not product:
        return None
    quantity = max(1, _as_int(
        value.get("minQty") or value.get("minQuantity") or value.get("quantity"), 1
    ))
    total = _money(value.get("totalPrice") or value.get("promoTotalPrice"))
    candidate = {
        "source_name": "SUPER_PHARM",
        "branch_code": SUPER_PHARM_ONLINE_CODE,
        "subchain_id": None,
        "barcode": barcode,
        "promo_price": _money(
            value.get("promoPrice")
            or value.get("price")
            or value.get("discountedPrice")
        ),
        "promo_description": _clean(
            value.get("description") or value.get("name") or value.get("productName")
        ) or "מבצע אונליין בסופר-פארם",
        "promo_start_at": value.get("validFrom") or value.get("startDate"),
        "promo_end_at": value.get("validUntil") or value.get("endDate"),
        "promo_min_quantity": quantity,
        "promo_total_price": total,
        "requires_club": bool(value.get("requiresClub")),
        "raw_promo": value,
    }
    normalized = normalize_promotion_terms(candidate, product["regular_price"])
    if normalized.get("promo_price") is None:
        return None
    return normalized


def _quota_reset_at(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    reset = (moment + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return reset.isoformat()


class SuperPharmBulkImporter:
    """Read up to 200 products per request instead of one request per barcode."""

    def __init__(
        self,
        client: CheaperSalPriceClient,
        *,
        state: dict[str, Any] | None = None,
        request_limit: int | None = None,
    ):
        self.client = client
        self.state = dict(state or {})
        self.request_limit = max(1, int(
            request_limit
            if request_limit is not None
            else os.environ.get("CHEAPERSAL_BULK_REQUEST_LIMIT", "10")
        ))
        self.branch_page_limit = max(
            1, int(os.environ.get("CHEAPERSAL_BULK_BRANCH_PAGES", "3"))
        )
        self.unfiltered_page_limit = max(
            1, int(os.environ.get("CHEAPERSAL_BULK_UNFILTERED_PAGES", "3"))
        )
        self.category_limit = max(
            1, int(os.environ.get("CHEAPERSAL_BULK_CATEGORY_LIMIT", "24"))
        )
        self.branch_refresh_hours = max(
            1.0, float(os.environ.get("CHEAPERSAL_BULK_BRANCH_REFRESH_HOURS", "24"))
        )
        self.started_requests = client.network_requests
        self.errors: list[str] = []

    def _used(self) -> int:
        return self.client.network_requests - self.started_requests

    def _can_request(self, reserve: int = 0) -> bool:
        return bool(
            self.client.api_key
            and not self.client.quota_error_code
            and self._used() + reserve < self.request_limit
            and self.client.remaining > reserve
            and (
                self.client.provider_remaining is None
                or self.client.provider_remaining > self.client.provider_reserve + reserve
            )
        )

    def _request(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | list[Any] | None:
        if not self._can_request():
            return None
        try:
            return self.client.get_json(path, params)
        except Exception as exc:
            self.errors.append(f"{path}: {type(exc).__name__}: {str(exc)[:260]}")
            return None

    def _branches_due(self) -> bool:
        if _as_int(self.state.get("geocoded_branches")) <= 0:
            return True
        stamp = self.state.get("branches_refreshed_at")
        if not stamp:
            return True
        try:
            previous = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            return True
        return datetime.now(timezone.utc) >= previous + timedelta(hours=self.branch_refresh_hours)

    def _chain_id(self) -> str:
        cached = _clean(self.state.get("provider_chain_id"))
        if cached:
            return cached
        response = self._request("chains")
        values = response if isinstance(response, list) else (
            response.get("chains", []) if isinstance(response, dict) else []
        )
        for value in values:
            if not isinstance(value, dict):
                continue
            if _matches_chain("SUPER_PHARM", {"chain": value}):
                chain_id = _clean(value.get("id") or value.get("chainId"))
                if chain_id:
                    self.state["provider_chain_id"] = chain_id
                    return chain_id
        if response is not None:
            self.errors.append("Super-Pharm was not found in the authorized chain list")
        return ""

    def _branches(self) -> list[dict[str, Any]]:
        if not self._branches_due():
            return [_online_branch_row()]
        chain_id = self._chain_id()
        if not chain_id:
            return [_online_branch_row()]

        rows: dict[str, dict[str, Any]] = {}
        total = None
        complete = False
        for page in range(self.branch_page_limit):
            if not self._can_request(reserve=2):
                break
            response = self._request(
                f"chains/{chain_id}/branches", {"limit": 200, "skip": page * 200}
            )
            if not isinstance(response, dict):
                break
            entries = response.get("branches")
            if not isinstance(entries, list):
                entries = response.get("items") if isinstance(response.get("items"), list) else []
            total = _as_int(response.get("total"), total or len(entries))
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                normalized = branch_from_provider(entry)
                if normalized:
                    rows[str(normalized["branch_code"])] = normalized
            if not entries or (page + 1) * 200 >= total:
                complete = True
                break

        rows.setdefault(SUPER_PHARM_ONLINE_CODE, _online_branch_row())
        geocoded = sum(
            1 for row in rows.values()
            if row.get("latitude") is not None and row.get("longitude") is not None
        )
        if len(rows) > 1:
            self.state.update({
                "branches_refreshed_at": utcnow(),
                "branches_discovered": len(rows),
                "geocoded_branches": geocoded,
                "branch_refresh_complete": complete,
                "provider_branch_total": total,
            })
        return list(rows.values())

    def _categories(self) -> list[str | None]:
        cached = self.state.get("baby_categories")
        if isinstance(cached, list) and cached:
            return [str(item) for item in cached if str(item)][:self.category_limit]
        response = self._request("categories") if self._can_request(reserve=2) else None
        categories = discover_baby_categories(response)[:self.category_limit]
        if categories:
            self.state["baby_categories"] = categories
            return categories
        return [None]

    def _products(
        self, categories: list[str | None]
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        products: dict[str, dict[str, Any]] = {}
        pages: list[dict[str, Any]] = []
        offsets = self.state.get("category_offsets")
        offsets = dict(offsets) if isinstance(offsets, dict) else {}
        completed = set(self.state.get("completed_categories") or [])
        cursor = _as_int(self.state.get("category_cursor")) % max(1, len(categories))
        cycles_before = _as_int(self.state.get("catalog_cycles_completed"))
        unfiltered_pages = 0

        while self._can_request(reserve=1):
            category = categories[cursor % len(categories)]
            category_key = category or "__all__"
            if category is None and unfiltered_pages >= self.unfiltered_page_limit:
                break
            skip = max(0, _as_int(offsets.get(category_key)))
            parameters: dict[str, Any] = {"limit": 200, "skip": skip}
            if category:
                parameters["category"] = category
            response = self._request(
                f"branches/{SUPER_PHARM_ONLINE_PROVIDER_ID}/products", parameters
            )
            if not isinstance(response, dict):
                cursor = (cursor + 1) % len(categories)
                if self.client.quota_error_code:
                    break
                if len(pages) >= len(categories):
                    break
                pages.append({"category": category, "skip": skip, "received": 0, "error": True})
                continue

            entries = response.get("products")
            if not isinstance(entries, list):
                entries = response.get("items") if isinstance(response.get("items"), list) else []
            accepted = 0
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                normalized = product_from_provider(entry, category_slug=category)
                if normalized:
                    products[str(normalized["barcode"])] = normalized
                    accepted += 1
            total = _as_int(response.get("total"), skip + len(entries))
            has_more = bool(entries and skip + len(entries) < total)
            offsets[category_key] = skip + len(entries) if has_more else 0
            if not has_more:
                completed.add(category_key)
            pages.append({
                "category": category,
                "skip": skip,
                "received": len(entries),
                "baby_products": accepted,
                "total": total,
                "has_more": has_more,
            })
            if category is None:
                unfiltered_pages += 1
            cursor = (cursor + 1) % len(categories)
            all_keys = {item or "__all__" for item in categories}
            if all_keys.issubset(completed) and not any(offsets.get(key) for key in all_keys):
                self.state["catalog_cycles_completed"] = cycles_before + 1
                completed.clear()
                break

        self.state.update({
            "category_cursor": cursor,
            "category_offsets": offsets,
            "completed_categories": sorted(completed),
        })
        return products, pages

    def _promotions(
        self, products: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not products or not self._can_request():
            return []
        response = self._request(
            "promos",
            {"branchId": SUPER_PHARM_ONLINE_PROVIDER_ID, "limit": 200, "skip": 0},
        )
        entries = response.get("promos", []) if isinstance(response, dict) else []
        if not isinstance(entries, list):
            return []
        promos: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            normalized = promo_from_provider(entry, products)
            if normalized:
                promos[str(normalized["barcode"])] = normalized
        return list(promos.values())

    def collect(self) -> tuple[dict[str, Any], dict[str, Any]]:
        before = self.client.snapshot()
        branches = self._branches() if self.client.api_key else []
        categories = self._categories() if self.client.api_key else []
        products, pages = self._products(categories or [None]) if self.client.api_key else ({}, [])
        promos = self._promotions(products)
        after = self.client.snapshot()

        counts: dict[str, int] = defaultdict(int)
        for row in products.values():
            counts[str(row["need_key"])] += 1
        self.state.update({
            "version": "v050",
            "last_attempt_at": utcnow(),
            "online_provider_branch_id": SUPER_PHARM_ONLINE_PROVIDER_ID,
            "online_branch_code": SUPER_PHARM_ONLINE_CODE,
            "provider_remaining": self.client.provider_remaining,
            "quota_error_code": self.client.quota_error_code,
        })
        if products:
            self.state["last_success_at"] = utcnow()
        if self.client.quota_error_code == "DAILY_LIMIT_EXCEEDED":
            self.state["quota_reset_at"] = _quota_reset_at()

        stats = {
            **self.state,
            "source": API_BASE,
            "source_authorized": True,
            "price_scope": "online_only",
            "fallback_used": bool(self.client.api_key),
            "request_budget": self.request_limit,
            "network_requests": after["network_requests"] - before["network_requests"],
            "cache_hits": after["cache_hits"] - before["cache_hits"],
            "rate_limit_waits": after["rate_limit_waits"] - before["rate_limit_waits"],
            "provider_retries": after["provider_retries"] - before["provider_retries"],
            "remaining_shared_budget": self.client.remaining,
            "branches_saved": len(branches),
            "branches_geocoded_this_run": sum(
                1 for row in branches
                if row.get("latitude") is not None and row.get("longitude") is not None
            ),
            "baby_prices": len(products),
            "active_promotions": len(promos),
            "category_counts": dict(counts),
            "product_pages": pages,
            "errors": self.errors[:20],
        }
        source_pass = {
            "pass_name": "super_pharm_cheapersal_bulk_online",
            "file_types": ["CHEAPERSAL_AUTHORIZED_BULK_API"],
            "limit": self.request_limit,
            "files_seen": max(0, stats["network_requests"]),
            "price_rows": list(products.values()),
            "promo_rows": promos,
            "store_rows": branches,
            "errors": self.errors[:20],
            "kind_counts": {
                "cheapersal_bulk_product_pages": len(pages),
                "cheapersal_bulk_branch_rows": len(branches),
            },
            "sample_files": [
                f"category={page['category'] or 'all'}&skip={page['skip']}"
                for page in pages[:12]
            ],
            "price_schema_diagnostics": [],
        }
        return source_pass, stats
