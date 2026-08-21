from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

from .classifier import (
    classify_need,
    infer_brand,
    parse_dimension,
    parse_package_quantity,
)
from .enrichment import API_BASE
from .product_types import SUPPORTED_PRODUCT_TYPES


CHAIN_MATCHERS = {
    "KSP": ("ksp", "קיי.אס.פי", "קיי אס פי"),
    "SUPER_PHARM": ("super-pharm", "super pharm", "סופר פארם", "סופר-פארם"),
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _money(value: Any) -> float | None:
    if value in (None, ""):
        return None
    match = re.search(r"\d{1,5}(?:[.,]\d{1,2})?", str(value))
    if not match:
        return None
    try:
        amount = float(match.group(0).replace(",", "."))
    except ValueError:
        return None
    return amount if 0 < amount < 100_000 else None


def _barcode(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if 8 <= len(digits) <= 14 else ""


def _matches_chain(requested: str, price: dict[str, Any]) -> bool:
    chain = price.get("chain") if isinstance(price.get("chain"), dict) else {}
    haystack = " ".join(
        _clean(x).lower()
        for x in (
            chain.get("name"),
            chain.get("id"),
            price.get("chainName"),
            price.get("chainId"),
        )
        if x not in (None, "")
    )
    return any(marker.lower() in haystack for marker in CHAIN_MATCHERS.get(requested, ()))


def _promo_fields(price: dict[str, Any]) -> dict[str, Any]:
    promo = price.get("promo") if isinstance(price.get("promo"), dict) else {}
    promo_price = _money(
        promo.get("promoPrice")
        or promo.get("price")
        or promo.get("discountedPrice")
    )
    total_price = _money(promo.get("totalPrice"))
    min_qty = promo.get("minQty") or promo.get("minQuantity") or promo.get("quantity") or 1
    try:
        min_qty = int(float(str(min_qty)))
    except ValueError:
        min_qty = 1
    if total_price is not None and min_qty > 1:
        promo_price = round(total_price / min_qty, 4)
    return {
        "promo_price": promo_price,
        "promo_description": _clean(promo.get("description") or promo.get("name")) or None,
        "promo_end_at": promo.get("validUntil") or promo.get("endDate"),
        "promo_min_quantity": min_qty,
        "promo_total_price": total_price,
        "requires_club": bool(promo.get("requiresClub")),
    }


def _row_from_price(
    requested_chain: str,
    product: dict[str, Any],
    price: dict[str, Any],
    expected_need: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    amount = _money(price.get("price") or price.get("regularPrice"))
    if amount is None:
        return None, None

    barcode = _barcode(product.get("barcode"))
    name = _clean(product.get("name") or price.get("productName"))
    if not barcode or not name:
        return None, None
    detected_need = classify_need(name)
    if detected_need != expected_need:
        return None, None

    branch = price.get("branch") if isinstance(price.get("branch"), dict) else {}
    is_online = bool(branch.get("isOnline")) or str(price.get("online")).lower() == "true"
    branch_code = _clean(
        branch.get("storeId")
        or branch.get("id")
        or price.get("branchId")
        or ("online" if is_online else "")
    )
    if not branch_code:
        branch_code = "online"

    brand = infer_brand(name, product.get("manufacturer"))
    dimension_type, dimension_value = parse_dimension(name, expected_need)
    package_quantity, package_unit = parse_package_quantity(
        name,
        expected_need,
        None,
        product.get("unitQty") or product.get("unitQuantity"),
    )
    promo = _promo_fields(price)
    effective = promo.get("promo_price")
    promo_price = float(effective) if effective and 0 < float(effective) < amount else None

    row = {
        "source_name": requested_chain,
        "branch_code": branch_code,
        "subchain_id": None,
        "barcode": barcode,
        "need_key": expected_need,
        "dimension_type": dimension_type,
        "dimension_value": dimension_value,
        "brand": brand,
        "product_name": name,
        "package_quantity": package_quantity,
        "package_unit": package_unit,
        "regular_price": amount,
        "promo_price": promo_price,
        "promo_description": promo.get("promo_description") if promo_price else None,
        "promo_start_at": None,
        "promo_end_at": promo.get("promo_end_at") if promo_price else None,
        "promo_min_quantity": promo.get("promo_min_quantity") if promo_price else 1,
        "promo_total_price": promo.get("promo_total_price") if promo_price else None,
        "requires_club": bool(promo.get("requires_club")) if promo_price else False,
        "source_updated_at": utcnow(),
        "last_seen_at": utcnow(),
        "raw_source": {
            "source": "cheapersal_product_prices",
            "chain": price.get("chain"),
            "branch": branch,
            "product": product,
        },
    }

    promo_row = None
    if promo_price:
        promo_row = {
            "source_name": requested_chain,
            "branch_code": branch_code,
            "subchain_id": None,
            "barcode": barcode,
            "promo_price": promo_price,
            "promo_description": row["promo_description"] or "מבצע CheaperSal",
            "promo_start_at": None,
            "promo_end_at": row["promo_end_at"],
            "promo_min_quantity": row["promo_min_quantity"],
            "promo_total_price": row["promo_total_price"] or promo_price,
            "requires_club": row["requires_club"],
            "raw_promo": price.get("promo"),
        }
    return row, promo_row


def _branch_row(requested_chain: str, price: dict[str, Any]) -> dict[str, Any]:
    branch = price.get("branch") if isinstance(price.get("branch"), dict) else {}
    is_online = bool(branch.get("isOnline")) or str(price.get("online")).lower() == "true"
    branch_code = _clean(branch.get("storeId") or branch.get("id") or price.get("branchId"))
    if not branch_code:
        branch_code = "online"
    default_name = "KSP אונליין" if requested_chain == "KSP" else "סופר-פארם אונליין"
    return {
        "source_name": requested_chain,
        "branch_code": branch_code,
        "subchain_id": None,
        "branch_name": _clean(branch.get("name")) or default_name,
        "city": _clean(branch.get("city")) or None,
        "address": _clean(branch.get("address")) or ("אונליין" if is_online else None),
    }


class CheaperSalPriceFallback:
    def __init__(self, api_key: str, *, limit: int = 18, online: bool = True):
        self.api_key = (api_key or "").strip()
        self.remaining = max(0, int(limit))
        self.online = online
        self._last_call_monotonic: float | None = None

    def _get_prices(self, barcode: str) -> dict[str, Any] | None:
        if not self.api_key or self.remaining <= 0:
            return None
        if self._last_call_monotonic is not None:
            elapsed = time.monotonic() - self._last_call_monotonic
            if elapsed < 6.2:
                time.sleep(6.2 - elapsed)
        response = requests.get(
            f"{API_BASE}/products/{barcode}/prices",
            params={"online": "true"} if self.online else None,
            headers={"X-API-Key": self.api_key},
            timeout=35,
        )
        self._last_call_monotonic = time.monotonic()
        self.remaining -= 1
        if response.status_code == 404:
            return None
        if not response.ok:
            raise RuntimeError(f"CheaperSal {response.status_code}: {response.text[:400]}")
        payload = response.json()
        if not payload.get("success"):
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    def collect(
        self,
        requested_chain: str,
        catalog_targets: list[dict[str, str]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        prices: list[dict[str, Any]] = []
        promos: list[dict[str, Any]] = []
        branches: dict[str, dict[str, Any]] = {}
        attempted = 0
        errors: list[str] = []
        matched = 0

        if not self.api_key or self.remaining <= 0:
            reason = "no_api_key_or_budget"
            return self._empty_pass(requested_chain, reason), {
                "fallback_used": False,
                "reason": reason,
                "attempted": 0,
                "matched_prices": 0,
                "baby_prices": 0,
                "errors": [],
            }

        seen: set[str] = set()
        for target in catalog_targets:
            if self.remaining <= 0:
                break
            barcode = _barcode(target.get("barcode"))
            need_key = _clean(target.get("need_key"))
            if not barcode or barcode in seen or need_key not in SUPPORTED_PRODUCT_TYPES:
                continue
            seen.add(barcode)
            attempted += 1
            try:
                data = self._get_prices(barcode)
                if not data:
                    continue
                product = data.get("product") if isinstance(data.get("product"), dict) else {}
                if not product.get("barcode"):
                    product = {**product, "barcode": barcode}
                rows = data.get("prices") if isinstance(data.get("prices"), list) else []
                for price in rows:
                    if not isinstance(price, dict) or not _matches_chain(requested_chain, price):
                        continue
                    matched += 1
                    row, promo = _row_from_price(requested_chain, product, price, need_key)
                    if not row:
                        continue
                    prices.append(row)
                    branches[row["branch_code"]] = _branch_row(requested_chain, price)
                    if promo:
                        promos.append(promo)
            except Exception as exc:
                errors.append(f"{barcode}: {type(exc).__name__}: {exc}")

        by_key: dict[tuple[str, str], dict[str, Any]] = {}
        promo_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for row in prices:
            by_key[(str(row["branch_code"]), str(row["barcode"]))] = row
        for promo in promos:
            promo_by_key[(str(promo["branch_code"]), str(promo["barcode"]))] = promo

        prices = list(by_key.values())
        promos = [promo_by_key[key] for key in by_key if key in promo_by_key]
        result = {
            "pass_name": f"{requested_chain.lower()}_cheapersal_fallback",
            "file_types": ["CHEAPERSAL_PRODUCT_PRICES"],
            "limit": attempted,
            "files_seen": attempted,
            "price_rows": prices,
            "promo_rows": promos,
            "store_rows": list(branches.values()) if prices else [],
            "errors": errors[:30],
            "kind_counts": {"cheapersal_product_prices": attempted},
            "sample_files": sorted(seen)[:12],
            "price_schema_diagnostics": [],
        }
        stats = {
            "source": API_BASE,
            "fallback_used": True,
            "online": self.online,
            "attempted": attempted,
            "matched_prices": matched,
            "baby_prices": len(prices),
            "active_promotions": len(promos),
            "remaining_budget": self.remaining,
            "errors": errors[:12],
        }
        return result, stats

    def _empty_pass(self, requested_chain: str, reason: str) -> dict[str, Any]:
        return {
            "pass_name": f"{requested_chain.lower()}_cheapersal_fallback",
            "file_types": ["CHEAPERSAL_PRODUCT_PRICES"],
            "limit": 0,
            "files_seen": 0,
            "price_rows": [],
            "promo_rows": [],
            "store_rows": [],
            "errors": [reason],
            "kind_counts": {},
            "sample_files": [],
            "price_schema_diagnostics": [],
        }
