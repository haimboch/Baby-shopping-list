"""Attach verified product photos by matching the exact product barcode."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from .supabase_rest import SupabaseREST


OPEN_PRODUCT_API = "https://world.openfoodfacts.org/api/v3/product"
CHEAPERSAL_API = "https://api.cheapersal.co.il/api/v1/products"
IMAGE_FIELDS = "code,product_name,image_front_url,image_url,selected_images"
IMAGE_KEYS = (
    "image_front_url", "image_url", "imageUrl", "image", "photo", "picture",
    "thumbnail", "thumbnailUrl", "selected_images", "images", "front",
    "display", "small", "he", "en", "product", "data", "url", "src",
)


def _barcode(value: Any) -> str:
    return re.sub(r"\D", "", str(value or "")).lstrip("0") or "0"


def extract_product_image(value: Any, *, _depth: int = 0) -> str | None:
    """Return only an HTTPS image URL from a recognised image-shaped field."""
    if _depth > 8:
        return None
    if isinstance(value, str):
        candidate = value.strip()
        parsed = urlparse(candidate)
        return candidate if parsed.scheme.lower() == "https" and parsed.netloc else None
    if isinstance(value, list):
        for entry in value:
            found = extract_product_image(entry, _depth=_depth + 1)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None
    for key in IMAGE_KEYS:
        if key in value:
            found = extract_product_image(value[key], _depth=_depth + 1)
            if found:
                return found
    return None


class ProductImageEnricher:
    """Gradually enrich missing photos without guessing by name or manufacturer."""

    def __init__(
        self,
        db: SupabaseREST,
        api_key: str = "",
        limit: int = 36,
        cheapersal_limit: int = 4,
    ):
        self.db = db
        self.api_key = str(api_key or "").strip()
        self.limit = max(0, int(limit))
        self.cheapersal_remaining = max(0, int(cheapersal_limit))
        self._last_cheapersal_call: float | None = None

    def _missing_catalog(self) -> list[dict[str, Any]]:
        retry_before = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        filters = {
            "select": "barcode,need_key,brand,product_name,image_checked_at",
            "active": "eq.true",
            "image_url": "is.null",
            "or": f"(image_checked_at.is.null,image_checked_at.lt.{retry_before})",
            "order": "image_checked_at.asc.nullsfirst,barcode.asc",
            "limit": str(self.limit),
        }
        tracked = self.db.select(
            "products",
            {
                "select": "preferred_barcode",
                "preferred_barcode": "not.is.null",
                "is_active": "eq.true",
                "limit": "250",
            },
        )
        barcodes = sorted({
            str(row.get("preferred_barcode") or "").strip()
            for row in tracked
            if re.fullmatch(r"\d{8,14}", str(row.get("preferred_barcode") or ""))
        })
        prioritized = []
        if barcodes:
            prioritized = self.db.select(
                "baby_product_catalog",
                {**filters, "barcode": f"in.({','.join(barcodes)})"},
            )
        seen = {str(row.get("barcode")) for row in prioritized}
        general = self.db.select("baby_product_catalog", filters)
        return (prioritized + [
            row for row in general if str(row.get("barcode")) not in seen
        ])[:self.limit]

    def _fetch_open_product_image(self, barcode: str) -> str | None:
        response = requests.get(
            f"{OPEN_PRODUCT_API}/{barcode}",
            params={"product_type": "all", "fields": IMAGE_FIELDS},
            headers={
                "User-Agent": (
                    "BabySmartList/0.45 "
                    "(+https://github.com/haimboch/Baby-shopping-list)"
                ),
                "Accept": "application/json",
            },
            timeout=12,
            allow_redirects=True,
        )
        if response.status_code in (404, 410):
            return None
        if not response.ok:
            raise RuntimeError(f"Open Products Facts HTTP {response.status_code}")
        payload = response.json()
        product = payload.get("product") if isinstance(payload, dict) else None
        if not isinstance(product, dict):
            return None
        actual_barcode = product.get("code") or payload.get("code")
        if not actual_barcode or _barcode(actual_barcode) != _barcode(barcode):
            return None
        return extract_product_image(product)

    def _fetch_cheapersal_image(self, barcode: str) -> str | None:
        if not self.api_key or self.cheapersal_remaining <= 0:
            return None
        if self._last_cheapersal_call is not None:
            elapsed = time.monotonic() - self._last_cheapersal_call
            if elapsed < 6.2:
                time.sleep(6.2 - elapsed)
        response = requests.get(
            f"{CHEAPERSAL_API}/{barcode}",
            headers={"X-API-Key": self.api_key, "Accept": "application/json"},
            timeout=20,
        )
        self._last_cheapersal_call = time.monotonic()
        self.cheapersal_remaining -= 1
        if response.status_code in (404, 410):
            return None
        if not response.ok:
            raise RuntimeError(f"Cheapersal image lookup HTTP {response.status_code}")
        payload = response.json()
        product = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(product, dict):
            return None
        actual_barcode = product.get("barcode") or product.get("code")
        if actual_barcode and _barcode(actual_barcode) != _barcode(barcode):
            return None
        return extract_product_image(product)

    def enrich_missing_images(self) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "checked": 0, "saved": 0, "missing": 0, "errors": [],
        }
        if self.limit <= 0:
            return stats
        candidates = self._missing_catalog()
        for row in candidates:
            barcode = str(row.get("barcode") or "").strip()
            if not barcode:
                continue
            stats["checked"] += 1
            image = None
            source = None
            try:
                image = self._fetch_open_product_image(barcode)
                if image:
                    source = "Open Products Facts · verified barcode"
                elif self.api_key and self.cheapersal_remaining > 0:
                    image = self._fetch_cheapersal_image(barcode)
                    if image:
                        source = "Cheapersal · verified barcode"
                update = {
                    "image_checked_at": datetime.now(timezone.utc).isoformat(),
                }
                if image:
                    update.update({"image_url": image, "image_source": source})
                    stats["saved"] += 1
                else:
                    stats["missing"] += 1
                self.db.patch("baby_product_catalog", {"barcode": barcode}, update)
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                stats["errors"].append(
                    f"{barcode}: {type(exc).__name__}: {str(exc)[:160]}"
                )
        return stats


def image_limits_from_environment() -> tuple[int, int]:
    return (
        int(os.environ.get("PRODUCT_IMAGE_LOOKUP_LIMIT", "36")),
        int(os.environ.get("CHEAPERSAL_IMAGE_LOOKUP_LIMIT", "4")),
    )
