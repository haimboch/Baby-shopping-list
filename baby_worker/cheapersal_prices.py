from __future__ import annotations

import os
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
from .rate_limit import record_call, retry_after_seconds, wait_for_slot


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


def _provider_error_code(response: Any) -> str:
    """Differentiate an exhausted daily quota from a short per-minute burst."""
    try:
        payload = response.json()
    except (TypeError, ValueError, AttributeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("code") or "").strip().upper()
    return str(payload.get("code") or "").strip().upper()


def _branch_coordinates(branch: dict[str, Any]) -> tuple[float | None, float | None]:
    location = branch.get("location") if isinstance(branch.get("location"), dict) else {}
    geojson = location.get("coordinates")
    lat = location.get("lat", location.get("latitude", branch.get("latitude")))
    lon = location.get(
        "lon", location.get("lng", location.get("longitude", branch.get("longitude")))
    )
    if isinstance(geojson, (list, tuple)) and len(geojson) >= 2:
        lon = geojson[0] if lon in (None, "") else lon
        lat = geojson[1] if lat in (None, "") else lat
    try:
        latitude, longitude = float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None
    if not (29.0 <= latitude <= 34.0 and 34.0 <= longitude <= 36.5):
        return None, None
    return latitude, longitude


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
    branch_code = "online" if is_online else _clean(
        branch.get("id")
        or branch.get("storeId")
        or price.get("branchId")
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
    branch_code = "online" if is_online else _clean(
        branch.get("id") or branch.get("storeId") or price.get("branchId")
    )
    if not branch_code:
        branch_code = "online"
    default_name = "KSP אונליין" if requested_chain == "KSP" else "סופר-פארם אונליין"
    result = {
        "source_name": requested_chain,
        "branch_code": branch_code,
        "subchain_id": None,
        "branch_name": _clean(branch.get("name")) or default_name,
        "city": _clean(branch.get("city")) or None,
        "address": _clean(branch.get("address")) or ("אונליין" if is_online else None),
    }
    latitude, longitude = _branch_coordinates(branch)
    if not is_online and latitude is not None and longitude is not None:
        result.update({"latitude": latitude, "longitude": longitude})
    return result


class CheaperSalPriceClient:
    """One shared, cached and rate-limited price client for the whole worker run."""

    def __init__(
        self,
        api_key: str,
        *,
        limit: int = 48,
        online: bool = False,
        minimum_interval: float = 7.0,
        max_rate_retries: int = 1,
    ):
        self.api_key = (api_key or "").strip()
        self.remaining = max(0, int(limit))
        self.online = bool(online)
        self.minimum_interval = max(0.0, float(minimum_interval))
        self.max_rate_retries = max(0, int(max_rate_retries))
        self.cache: dict[str, dict[str, Any] | None] = {}
        self.failures: dict[str, str] = {}
        self.network_requests = 0
        self.cache_hits = 0
        self.cached_error_hits = 0
        self.rate_limit_waits = 0
        self.provider_retries = 0
        self.provider_remaining: int | None = None
        self.provider_reserve = max(
            0, int(os.environ.get("CHEAPERSAL_PROVIDER_REQUEST_RESERVE", "0"))
        )
        self.quota_error_code: str | None = None
        self.api_cache: dict[tuple[str, tuple[tuple[str, str], ...]], Any] = {}

    def can_lookup(self, barcode: str) -> bool:
        return bool(
            self.api_key
            and (
                barcode in self.cache
                or barcode in self.failures
                or (
                    self.remaining > 0
                    and not self.quota_error_code
                    and (
                        self.provider_remaining is None
                        or self.provider_remaining > self.provider_reserve
                    )
                )
            )
        )

    def snapshot(self) -> dict[str, int]:
        return {
            "network_requests": self.network_requests,
            "cache_hits": self.cache_hits,
            "cached_error_hits": self.cached_error_hits,
            "rate_limit_waits": self.rate_limit_waits,
            "provider_retries": self.provider_retries,
        }

    def _request_path(self, path: str, params: dict[str, Any] | None = None):
        if self.remaining <= 0:
            raise RuntimeError("shared CheaperSal request budget exhausted")
        if self.quota_error_code:
            raise RuntimeError(f"CheaperSal provider quota unavailable: {self.quota_error_code}")
        if (
            self.provider_remaining is not None
            and self.provider_remaining <= self.provider_reserve
        ):
            raise RuntimeError("CheaperSal provider request reserve reached")
        waited = wait_for_slot("cheapersal", self.minimum_interval)
        if waited > 0:
            self.rate_limit_waits += 1
        # Consume the budget before the network call so timeouts cannot create
        # unlimited attempts (the v0.48 behavior that exceeded the limit).
        self.remaining -= 1
        self.network_requests += 1
        try:
            return requests.get(
                f"{API_BASE}/{path.lstrip('/')}",
                params=params,
                headers={"X-API-Key": self.api_key},
                timeout=35,
            )
        finally:
            record_call("cheapersal")

    def _request(self, barcode: str):
        return self._request_path(
            f"products/{barcode}/prices",
            {"online": "true"} if self.online else None,
        )

    def _remember_provider_usage(self, payload: dict[str, Any]) -> None:
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
        value = usage.get("remaining")
        if value in (None, ""):
            return
        try:
            self.provider_remaining = max(0, int(value))
        except (TypeError, ValueError):
            return

    def _handle_provider_limit(self, response: Any) -> bool:
        code = _provider_error_code(response)
        if code in {"DAILY_LIMIT_EXCEEDED", "MONTHLY_LIMIT_EXCEEDED"}:
            self.quota_error_code = code
            self.provider_remaining = 0
            self.remaining = 0
            return True
        return False

    def get_json(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | list[Any] | None:
        """Read another documented CheaperSal endpoint using the shared budget."""
        if not self.api_key:
            return None
        key = (
            path.strip("/"),
            tuple(sorted((str(name), str(value)) for name, value in (params or {}).items())),
        )
        if key in self.api_cache:
            self.cache_hits += 1
            return self.api_cache[key]
        for attempt in range(self.max_rate_retries + 1):
            response = self._request_path(path, params)
            if response.status_code == 429:
                if self._handle_provider_limit(response):
                    raise RuntimeError(
                        f"CheaperSal quota exhausted: {self.quota_error_code}"
                    )
                if attempt < self.max_rate_retries and self.remaining > 0:
                    self.provider_retries += 1
                    self.rate_limit_waits += 1
                    time.sleep(retry_after_seconds(response))
                    continue
            if response.status_code == 404:
                self.api_cache[key] = None
                return None
            if not response.ok:
                raise RuntimeError(f"CheaperSal {response.status_code}: {response.text[:400]}")
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("CheaperSal returned an invalid response envelope")
            self._remember_provider_usage(payload)
            if not payload.get("success"):
                raise RuntimeError(f"CheaperSal rejected the request: {payload.get('error')}")
            data = payload.get("data")
            result = data if isinstance(data, (dict, list)) else None
            self.api_cache[key] = result
            return result
        return None

    def get_prices(self, barcode: str) -> dict[str, Any] | None:
        if barcode in self.cache:
            self.cache_hits += 1
            return self.cache[barcode]
        if barcode in self.failures:
            self.cached_error_hits += 1
            raise RuntimeError(f"cached lookup failure: {self.failures[barcode]}")
        if not self.api_key:
            return None

        try:
            for attempt in range(self.max_rate_retries + 1):
                response = self._request(barcode)
                if response.status_code == 429:
                    if self._handle_provider_limit(response):
                        raise RuntimeError(
                            f"CheaperSal quota exhausted: {self.quota_error_code}"
                        )
                    if attempt < self.max_rate_retries and self.remaining > 0:
                        self.provider_retries += 1
                        self.rate_limit_waits += 1
                        time.sleep(retry_after_seconds(response))
                        continue
                if response.status_code == 404:
                    self.cache[barcode] = None
                    return None
                if not response.ok:
                    raise RuntimeError(
                        f"CheaperSal {response.status_code}: {response.text[:400]}"
                    )
                payload = response.json()
                if isinstance(payload, dict):
                    self._remember_provider_usage(payload)
                data = payload.get("data") if payload.get("success") else None
                normalized = data if isinstance(data, dict) else None
                self.cache[barcode] = normalized
                return normalized
            return None
        except Exception as exc:
            self.failures[barcode] = f"{type(exc).__name__}: {str(exc)[:240]}"
            raise


class CheaperSalPriceFallback:
    def __init__(
        self,
        api_key: str,
        *,
        limit: int = 18,
        online: bool = True,
        client: CheaperSalPriceClient | None = None,
    ):
        self.client = client or CheaperSalPriceClient(
            api_key,
            limit=limit,
            online=online,
            minimum_interval=float(os.environ.get("CHEAPERSAL_MIN_INTERVAL_SECONDS", "7")),
            max_rate_retries=int(os.environ.get("CHEAPERSAL_MAX_RATE_RETRIES", "1")),
        )
        self.api_key = self.client.api_key
        self.online = self.client.online

    @property
    def remaining(self) -> int:
        return self.client.remaining

    def _get_prices(self, barcode: str) -> dict[str, Any] | None:
        return self.client.get_prices(barcode)

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

        if not self.api_key:
            reason = "no_api_key_or_budget"
            return self._empty_pass(requested_chain, reason), {
                "fallback_used": False,
                "reason": reason,
                "attempted": 0,
                "matched_prices": 0,
                "baby_prices": 0,
                "errors": [],
            }

        before = self.client.snapshot()
        seen: set[str] = set()
        for target in catalog_targets:
            barcode = _barcode(target.get("barcode"))
            need_key = _clean(target.get("need_key"))
            if not barcode or barcode in seen or need_key not in SUPPORTED_PRODUCT_TYPES:
                continue
            if not self.client.can_lookup(barcode):
                break
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
        after = self.client.snapshot()
        stats = {
            "source": API_BASE,
            "fallback_used": True,
            "online": self.online,
            "attempted": attempted,
            "matched_prices": matched,
            "baby_prices": len(prices),
            "active_promotions": len(promos),
            "remaining_budget": self.remaining,
            "network_requests": after["network_requests"] - before["network_requests"],
            "cache_hits": after["cache_hits"] - before["cache_hits"],
            "cached_error_hits": after["cached_error_hits"] - before["cached_error_hits"],
            "rate_limit_waits": after["rate_limit_waits"] - before["rate_limit_waits"],
            "provider_retries": after["provider_retries"] - before["provider_retries"],
            "shared_cache_entries": len(self.client.cache),
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
