from __future__ import annotations
import os
import time
from datetime import datetime, timezone
from typing import Any
import requests

from .classifier import classify_need, infer_brand, parse_dimension, parse_package_quantity
from .supabase_rest import SupabaseREST

API_BASE = "https://api.cheapersal.co.il/api/v1"

NEED_META = {
    "diapers": {"category": "החתלה", "need_name": "טיטולים"},
    "formula": {"category": "האכלה", "need_name": "תמ״ל"},
    "wipes": {"category": "החתלה", "need_name": "מגבונים"},
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class MetadataEnricher:
    """Persistent, rate-limited barcode metadata enrichment.

    Retailer transparency files remain the source of prices.
    Cheapersal is queried only for barcodes not yet present in our catalog.
    """

    def __init__(self, db: SupabaseREST, api_key: str, limit: int = 8):
        self.db = db
        self.api_key = (api_key or "").strip()
        self.remaining = max(0, int(limit))
        self._last_call_monotonic: float | None = None

    def _catalog_known(self, barcodes: list[str]) -> set[str]:
        if not barcodes:
            return set()
        known: set[str] = set()
        # Keep URL size modest.
        for i in range(0, len(barcodes), 40):
            chunk = barcodes[i:i+40]
            quoted = ",".join(chunk)
            rows = self.db.select(
                "baby_product_catalog",
                {
                    "select": "barcode",
                    "active": "eq.true",
                    "barcode": f"in.({quoted})",
                },
            )
            known.update(str(r.get("barcode")) for r in rows if r.get("barcode"))
        return known

    def _fetch_product(self, barcode: str) -> dict[str, Any] | None:
        if not self.api_key or self.remaining <= 0:
            return None

        # Space calls so repeated manual runs do not burst past the provider's
        # per-minute rate limit. 6.2 sec = <10 calls/minute.
        if self._last_call_monotonic is not None:
            elapsed = time.monotonic() - self._last_call_monotonic
            if elapsed < 6.2:
                time.sleep(6.2 - elapsed)

        r = requests.get(
            f"{API_BASE}/products/{barcode}",
            headers={"X-API-Key": self.api_key},
            timeout=30,
        )
        self._last_call_monotonic = time.monotonic()
        self.remaining -= 1

        if r.status_code == 404:
            return None
        if not r.ok:
            raise RuntimeError(f"Cheapersal {r.status_code}: {r.text[:500]}")
        payload = r.json()
        if not payload.get("success"):
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    def _catalog_row(self, expected_need: str, product: dict[str, Any]) -> dict[str, Any] | None:
        barcode = str(product.get("barcode") or "").strip()
        name = str(product.get("name") or "").strip()
        manufacturer = str(product.get("manufacturer") or "").strip() or None
        unit_qty = str(product.get("unitQty") or "").strip() or None
        description = str(product.get("description") or "").strip() or None

        if not barcode or not name:
            return None

        detected_need = classify_need(name)
        if detected_need != expected_need:
            # Never contaminate the catalog because a barcode was attached to
            # the wrong need upstream.
            return None

        brand = infer_brand(name, manufacturer)
        if not brand:
            # baby_product_catalog currently requires a non-null brand.
            return None

        dimension_type, dimension_value = parse_dimension(name, expected_need)
        package_quantity, package_unit = parse_package_quantity(
            name, expected_need, None, unit_qty
        )

        meta = NEED_META[expected_need]
        need_detail = None
        if dimension_value:
            need_detail = ("מידה " if dimension_type == "size" else "שלב ") + dimension_value

        return {
            "barcode": barcode,
            "category": meta["category"],
            "need_name": meta["need_name"],
            "need_detail": need_detail,
            "need_key": expected_need,
            "dimension_type": dimension_type,
            "dimension_value": dimension_value,
            "brand": brand,
            "product_name": name,
            "variant": description,
            "package_quantity": package_quantity,
            "package_unit": package_unit or ("גרם" if expected_need == "formula" else "יחידות"),
            "active": True,
            "source_name": "Cheapersal metadata enrichment",
            "verified_at": utcnow(),
        }

    def enrich_missing_catalog(self, price_rows: list[dict[str, Any]]) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "attempted": 0,
            "catalog_saved": 0,
            "skipped": 0,
            "errors": [],
            "remaining_budget": self.remaining,
        }
        if not self.api_key or self.remaining <= 0:
            stats["skipped"] = len(price_rows)
            stats["reason"] = "no_api_key_or_budget"
            return stats

        # Prefer rows where dimension/package info is missing; prioritize
        # diapers/formula before wipes because size/stage is essential there.
        priority = {"diapers": 0, "formula": 1, "wipes": 2}
        by_barcode: dict[str, dict[str, Any]] = {}
        for row in price_rows:
            barcode = str(row.get("barcode") or "").strip()
            need = row.get("need_key")
            if not barcode or need not in NEED_META:
                continue
            if row.get("dimension_value") is not None and row.get("package_quantity") is not None and row.get("brand"):
                continue
            by_barcode.setdefault(barcode, row)

        candidates = sorted(
            by_barcode.values(),
            key=lambda r: (
                priority.get(r.get("need_key"), 9),
                0 if r.get("dimension_value") is None else 1,
                0 if r.get("package_quantity") is None else 1,
                str(r.get("barcode")),
            ),
        )
        known = self._catalog_known([str(r["barcode"]) for r in candidates])
        candidates = [r for r in candidates if str(r["barcode"]) not in known]

        for row in candidates:
            if self.remaining <= 0:
                break
            barcode = str(row["barcode"])
            expected_need = str(row["need_key"])
            stats["attempted"] += 1
            try:
                product = self._fetch_product(barcode)
                if not product:
                    stats["skipped"] += 1
                    continue
                catalog = self._catalog_row(expected_need, product)
                if not catalog:
                    stats["skipped"] += 1
                    continue
                self.db.upsert("baby_product_catalog", [catalog], "barcode", batch=1)
                stats["catalog_saved"] += 1
            except Exception as exc:
                stats["errors"].append(f"{barcode}: {type(exc).__name__}: {exc}")

        stats["remaining_budget"] = self.remaining
        return stats
