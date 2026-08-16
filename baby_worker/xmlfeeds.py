from __future__ import annotations
import gzip
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Iterable

from .classifier import classify_need, infer_brand, parse_dimension, parse_package_quantity

CODE_KEYS = ("ItemCode", "Barcode", "BarCode", "ProductCode", "Code")
NAME_KEYS = ("ItemName", "ProductName", "Name")
PRICE_KEYS = ("ItemPrice", "Price", "RegularPrice")
MANUFACTURER_KEYS = ("ManufacturerName", "Manufacturer", "BrandName", "Brand")
QTY_KEYS = ("QtyInPackage", "QuantityInPackage", "Quantity")
UNIT_QTY_KEYS = ("UnitQty", "UnitQuantity", "UnitOfMeasure", "UnitOfMeasurePrice")
STORE_KEYS = ("StoreId", "StoreID", "StoreCode")
SUBCHAIN_KEYS = ("SubChainId", "SubChainID", "SubChainCode")
UPDATED_KEYS = ("LastUpdateDate", "PricesLastUpdate", "UpdateDate", "LastUpdate")

def local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag

def clean_bytes(content: bytes) -> bytes:
    if content[:2] == b"\x1f\x8b":
        return gzip.decompress(content)
    return content

def parse_root(content: bytes) -> ET.Element:
    data = clean_bytes(content)
    # Some publishers prepend a UTF-8 BOM.
    data = data.lstrip(b"\xef\xbb\xbf")
    return ET.fromstring(data)

def child_map(el: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in list(el):
        if len(list(c)) == 0:
            out[local(c.tag)] = (c.text or "").strip()
    return out

def first(d: dict[str, str], keys: Iterable[str]) -> str | None:
    # Exact first, then case-insensitive fallback.
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, ""):
            return v
    return None

def root_first(root: ET.Element, keys: Iterable[str]) -> str | None:
    wanted = {k.lower() for k in keys}
    for el in root.iter():
        if local(el.tag).lower() in wanted and el.text and el.text.strip():
            return el.text.strip()
    return None

def safe_float(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ".").strip())
    except ValueError:
        return None

def branch_from_filename(file_name: str) -> tuple[str | None, str | None]:
    """Return (subchain, branch) using common IL transparency filename formats."""
    # Typical: PriceFull7290027600007-005-123-202608160800.xml
    stem = re.sub(r"\.(?:gz|xml)+$", "", file_name, flags=re.I)
    m = re.search(r"(?:PriceFull|Price|PromoFull|Promo|Stores?)(\d{10,14})[-_](\d{1,3})[-_](\d{1,6})[-_]\d{8,14}", stem, re.I)
    if m:
        return m.group(2), m.group(3)
    # Older variants: PriceFull<chain>-<branch>-<timestamp>
    m = re.search(r"(?:PriceFull|Price|PromoFull|Promo)(\d{10,14})[-_](\d{1,6})[-_]\d{8,14}", stem, re.I)
    if m:
        return None, m.group(2)
    return None, None

def file_kind(file_name: str) -> str:
    n = file_name.lower()
    if "stores" in n or n.startswith("store"):
        return "stores"
    if "promofull" in n or "promosfull" in n:
        return "promo_full"
    if "pricefull" in n or "pricesfull" in n:
        return "price_full"
    if "promo" in n:
        return "promo"
    if "price" in n:
        return "price"
    return "unknown"

def parse_stores(content: bytes, source_name: str, file_name: str) -> list[dict[str, Any]]:
    root = parse_root(content)
    rows: list[dict[str, Any]] = []
    fallback_subchain, _ = branch_from_filename(file_name)
    for el in root.iter():
        d = child_map(el)
        store = first(d, STORE_KEYS)
        if not store:
            continue
        # Avoid treating root/header nodes as a store unless they have some store details.
        branch_name = first(d, ("StoreName", "BranchName", "Name"))
        city = first(d, ("City", "CityName"))
        address = first(d, ("Address", "StoreAddress"))
        if not any([branch_name, city, address]):
            continue
        subchain = first(d, SUBCHAIN_KEYS) or fallback_subchain
        rows.append({
            "source_name": source_name,
            "branch_code": str(store),
            "subchain_id": str(subchain) if subchain else None,
            "branch_name": branch_name,
            "city": city,
            "address": address,
        })
    return dedupe(rows, ("source_name", "branch_code"))

def parse_price_rows(content: bytes, source_name: str, file_name: str) -> list[dict[str, Any]]:
    root = parse_root(content)
    root_store = root_first(root, STORE_KEYS)
    root_subchain = root_first(root, SUBCHAIN_KEYS)
    fn_subchain, fn_branch = branch_from_filename(file_name)
    branch_code = root_store or fn_branch or "unknown"
    subchain = root_subchain or fn_subchain
    updated = root_first(root, UPDATED_KEYS)

    rows: list[dict[str, Any]] = []
    for el in root.iter():
        d = child_map(el)
        barcode = first(d, CODE_KEYS)
        name = first(d, NAME_KEYS)
        price = safe_float(first(d, PRICE_KEYS))
        if not barcode or not name or price is None or price <= 0:
            continue

        need_key = classify_need(name)
        if not need_key:
            continue

        manufacturer = first(d, MANUFACTURER_KEYS)
        qty_in_package = first(d, QTY_KEYS)
        unit_qty = first(d, UNIT_QTY_KEYS)
        dimension_type, dimension_value = parse_dimension(name, need_key)
        package_quantity, package_unit = parse_package_quantity(
            name, need_key, qty_in_package, unit_qty
        )
        rows.append({
            "source_name": source_name,
            "subchain_id": subchain,
            "branch_code": str(branch_code),
            "barcode": str(barcode).strip(),
            "need_key": need_key,
            "dimension_type": dimension_type,
            "dimension_value": dimension_value,
            "brand": infer_brand(name, manufacturer),
            "product_name": name,
            "package_quantity": package_quantity,
            "package_unit": package_unit,
            "regular_price": price,
            "source_updated_at": updated,
            "raw_source": {
                "file_name": file_name,
                "manufacturer": manufacturer,
                "qty_in_package": qty_in_package,
                "unit_qty": unit_qty,
            },
        })
    return dedupe(rows, ("source_name", "branch_code", "barcode"))

def parse_promotions(content: bytes, source_name: str, file_name: str) -> list[dict[str, Any]]:
    """Best-effort promo parser.

    v0.1 only uses an explicit promo price safely when quantity <= 1.
    Multi-buy promotions remain descriptive so we do not claim a false per-pack price.
    """
    root = parse_root(content)
    root_store = root_first(root, STORE_KEYS)
    root_subchain = root_first(root, SUBCHAIN_KEYS)
    fn_subchain, fn_branch = branch_from_filename(file_name)
    branch_code = root_store or fn_branch or "unknown"
    subchain = root_subchain or fn_subchain

    promo_rows: list[dict[str, Any]] = []
    promo_price_keys = ("PromotionPrice", "PromoPrice", "DiscountedPrice")
    min_qty_keys = ("MinQty", "MinimumQuantity", "MinQuantity")
    desc_keys = ("PromotionDescription", "Description", "PromoDescription")
    start_keys = ("PromotionStartDate", "StartDate")
    end_keys = ("PromotionEndDate", "EndDate")
    club_keys = ("Clubs", "ClubId", "RequiresClub", "ClubCode")

    for el in root.iter():
        d = child_map(el)
        desc = first(d, desc_keys)
        explicit = safe_float(first(d, promo_price_keys))
        min_qty = safe_float(first(d, min_qty_keys)) or 1.0
        if not desc and explicit is None:
            continue

        # Item codes may be descendants under PromotionItems.
        codes = []
        for sub in el.iter():
            if local(sub.tag).lower() in {k.lower() for k in CODE_KEYS} and sub.text:
                codes.append(sub.text.strip())
        codes = list(dict.fromkeys(codes))
        if not codes:
            continue

        promo_price = explicit if explicit is not None and min_qty <= 1 else None
        requires_club = bool(first(d, club_keys))
        for code in codes:
            promo_rows.append({
                "source_name": source_name,
                "subchain_id": subchain,
                "branch_code": str(branch_code),
                "barcode": str(code),
                "promo_price": promo_price,
                "promo_description": desc,
                "promo_start_at": first(d, start_keys),
                "promo_end_at": first(d, end_keys),
                "requires_club": requires_club,
                "raw_promo": {"min_qty": min_qty, "explicit_price": explicit, "file_name": file_name},
            })
    return dedupe(promo_rows, ("source_name", "branch_code", "barcode"))

def dedupe(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    m: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        m[tuple(row.get(k) for k in keys)] = row
    return list(m.values())
