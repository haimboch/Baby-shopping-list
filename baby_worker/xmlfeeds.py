from __future__ import annotations
import gzip
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .classifier import classify_need, infer_brand, parse_dimension, parse_package_quantity

CODE_KEYS = ("ItemCode", "Barcode", "BarCode", "ProductCode", "Code")
NAME_KEYS = ("ItemName", "ProductName", "Name")
DESCRIPTION_KEYS = (
    "ManufacturerItemDescription", "ManufactureItemDescription",
    "ItemDescription", "ProductDescription", "Description",
    "LongDescription", "ItemLongName"
)
PRICE_KEYS = ("ItemPrice", "Price", "RegularPrice")
MANUFACTURER_KEYS = ("ManufacturerName", "ManufactureName", "Manufacturer", "BrandName", "Brand")
QTY_KEYS = (
    "QtyInPackage", "QuantityInPackage", "PackageQuantity", "PackageQty",
    "UnitsInPackage", "NumberOfUnits", "NoOfUnits", "PackQty"
)
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
    """Direct product/header fields, including XML attributes.

    Some transparency publishers serialize simple fields as attributes instead
    of child elements. Existing element-based feeds continue to behave exactly
    as before.
    """
    out: dict[str, str] = {}
    for k, v in el.attrib.items():
        if v not in (None, ""):
            out[local(k)] = str(v).strip()
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
        for ak, av in el.attrib.items():
            if local(ak).lower() in wanted and str(av).strip():
                return str(av).strip()
        if local(el.tag).lower() in wanted:
            value = "".join(el.itertext()).strip()
            if value:
                return value
    return None

RECOGNIZED_PRODUCT_KEYS = tuple(dict.fromkeys(
    CODE_KEYS + NAME_KEYS + DESCRIPTION_KEYS + PRICE_KEYS + MANUFACTURER_KEYS +
    QTY_KEYS + UNIT_QTY_KEYS + STORE_KEYS + SUBCHAIN_KEYS + UPDATED_KEYS +
    ("Quantity", "UnitQty", "UnitQuantity", "UnitOfMeasure",
     "UnitOfMeasurePrice", "ManufactureName", "ManufacturerName")
))


def _recognized_key(name: str) -> str | None:
    low = name.lower()
    for key in RECOGNIZED_PRODUCT_KEYS:
        if key.lower() == low:
            return key
    return None


def minimal_product_maps(root: ET.Element) -> list[dict[str, str]]:
    """Find the smallest XML subtrees containing code + name + price.

    This is a fallback for publishers that wrap fields in extra XML levels or
    store fields as attributes. The smallest qualifying subtree is selected so
    the document root cannot accidentally become one giant product row.
    """
    candidates: list[dict[str, str]] = []

    def visit(el: ET.Element) -> tuple[dict[str, str], bool]:
        merged: dict[str, str] = {}

        for ak, av in el.attrib.items():
            key = _recognized_key(local(ak))
            value = str(av).strip()
            if key and value and key not in merged:
                merged[key] = value

        own_key = _recognized_key(local(el.tag))
        if own_key:
            value = "".join(el.itertext()).strip()
            if value:
                merged.setdefault(own_key, value)

        descendant_has_candidate = False
        for child in list(el):
            child_map_, child_has_candidate = visit(child)
            if child_has_candidate:
                descendant_has_candidate = True
            for k, v in child_map_.items():
                if v and k not in merged:
                    merged[k] = v

        qualifies = bool(
            first(merged, CODE_KEYS)
            and first(merged, NAME_KEYS)
            and safe_float(first(merged, PRICE_KEYS)) is not None
        )

        is_minimal_candidate = qualifies and not descendant_has_candidate
        if is_minimal_candidate:
            candidates.append(dict(merged))

        return merged, descendant_has_candidate or is_minimal_candidate

    visit(root)
    return candidates


def price_file_diagnostics(content: bytes, file_name: str) -> dict[str, Any]:
    """Compact diagnostics that do not depend on baby-product classification."""
    root = parse_root(content)
    tag_counts: dict[str, int] = {}
    for el in root.iter():
        t = local(el.tag)
        tag_counts[t] = tag_counts.get(t, 0) + 1

    direct_product_shapes = 0
    sample_names: list[str] = []
    for el in root.iter():
        d = child_map(el)
        code = first(d, CODE_KEYS)
        name = first(d, NAME_KEYS)
        price = safe_float(first(d, PRICE_KEYS))
        if code and name and price is not None:
            direct_product_shapes += 1
            if len(sample_names) < 5:
                sample_names.append(str(name)[:120])

    fallback_maps = minimal_product_maps(root) if direct_product_shapes == 0 else []
    if not sample_names:
        for d in fallback_maps[:5]:
            name = first(d, NAME_KEYS)
            if name:
                sample_names.append(str(name)[:120])

    common_tags = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:18]
    return {
        "file_name": file_name,
        "content_bytes": len(clean_bytes(content)),
        "root_tag": local(root.tag),
        "total_elements": sum(tag_counts.values()),
        "direct_product_shapes": direct_product_shapes,
        "fallback_product_shapes": len(fallback_maps),
        "sample_names": sample_names,
        "common_tags": [{"tag": k, "count": v} for k, v in common_tags],
    }


def _parse_product_map(
    d: dict[str, str],
    source_name: str,
    file_name: str,
    branch_code: str,
    subchain: str | None,
    updated: str | None,
) -> dict[str, Any] | None:
    barcode = first(d, CODE_KEYS)
    name = first(d, NAME_KEYS)
    price = safe_float(first(d, PRICE_KEYS))
    if not barcode or not name or price is None or price <= 0:
        return None

    need_key = classify_need(name)
    if not need_key:
        return None

    manufacturer = first(d, MANUFACTURER_KEYS)
    qty_in_package = meaningful(first(d, QTY_KEYS))
    unit_qty = meaningful(first(d, ("UnitQty", "UnitQuantity")))
    meta_text = metadata_blob(d, name, manufacturer)
    dimension_type, dimension_value = parse_dimension(meta_text, need_key)
    package_quantity, package_unit = extract_package_quantity(
        d, need_key, name, manufacturer
    )
    return {
        "source_name": source_name,
        "subchain_id": subchain,
        "branch_code": str(branch_code),
        "barcode": str(barcode).strip(),
        "need_key": need_key,
        "dimension_type": dimension_type,
        "dimension_value": dimension_value,
        "brand": infer_brand(meta_text, manufacturer),
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
            "metadata_candidates": metadata_candidates(d),
        },
    }


def metadata_candidates(d: dict[str, str]) -> dict[str, str]:
    """Keep useful product metadata without storing the entire retailer row."""
    wanted_fragments = (
        "name", "description", "manufacturer", "brand", "qty", "quantity",
        "package", "unit", "measure", "weight", "volume", "size", "stage"
    )
    out: dict[str, str] = {}
    for k, v in d.items():
        if not v:
            continue
        kl = k.lower()
        if any(fragment in kl for fragment in wanted_fragments):
            out[k] = v
    return out


def metadata_blob(d: dict[str, str], name: str, manufacturer: str | None) -> str:
    """Text used for metadata extraction, not for product classification."""
    parts: list[str] = [name]
    if manufacturer:
        parts.append(manufacturer)

    for key in DESCRIPTION_KEYS:
        value = first(d, (key,))
        if value:
            parts.append(value)

    for _, value in metadata_candidates(d).items():
        if value and value not in parts:
            parts.append(value)

    return " ".join(parts)


UNKNOWN_VALUES = {"לא ידוע", "unknown", "n/a", "na", "none", "null", "-"}


def meaningful(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in UNKNOWN_VALUES:
        return None
    return s


def descriptive_blob(d: dict[str, str], name: str, manufacturer: str | None) -> str:
    """Human product text only, excluding pricing/unit-of-measure metadata."""
    parts: list[str] = [name]
    if manufacturer:
        parts.append(manufacturer)
    for key in DESCRIPTION_KEYS:
        value = meaningful(first(d, (key,)))
        if value and value not in parts:
            parts.append(value)
    return " ".join(parts)


def _unit_count_from_text(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:יחידות|יחידה|יח['׳]?|units?|pcs?)\b", text or "", re.I)
    if not m:
        return None
    value = float(m.group(1))
    return value if value > 1 else None


def extract_package_quantity(
    d: dict[str, str], need_key: str, name: str, manufacturer: str | None
) -> tuple[float | None, str | None]:
    """Interpret retailer quantity fields according to the baby-product category.

    Israeli transparency feeds often use Quantity differently by product type:
    formula: package weight (e.g. 700/800/850 grams)
    diapers: number of diapers when available
    wipes: total wipes, or number of packs for multipacks
    """
    desc_text = descriptive_blob(d, name, manufacturer)
    explicit_pack = meaningful(first(d, QTY_KEYS))
    raw_quantity = safe_float(first(d, ("Quantity",)))
    unit_qty = meaningful(first(d, ("UnitQty", "UnitQuantity")))
    unit_measure = meaningful(first(d, ("UnitOfMeasure",)))

    # First honor explicit package fields when they contain a meaningful number.
    if explicit_pack:
        qty, unit = parse_package_quantity(desc_text, need_key, explicit_pack, unit_qty)
        if qty is not None:
            return qty, unit

    if need_key == "formula":
        unit_text = f"{unit_qty or ''} {unit_measure or ''}"
        grams_signal = bool(re.search(r"גרם|gr\b|grams?\b|\bg\b", unit_text, re.I))
        if raw_quantity is not None and raw_quantity > 1 and grams_signal:
            return raw_quantity, "גרם"
        # Do NOT feed UnitOfMeasure='100 גרם' into this fallback, because that
        # is a comparison unit, not the package weight.
        return parse_package_quantity(desc_text, need_key, None, unit_qty)

    if need_key == "diapers":
        units_signal = bool(re.search(r"יחיד|units?|pcs?", f"{unit_qty or ''} {unit_measure or ''}", re.I))
        if raw_quantity is not None and raw_quantity > 1 and units_signal:
            return raw_quantity, "יחידות"
        return parse_package_quantity(desc_text, need_key, None, unit_qty)

    if need_key == "wipes":
        per_pack = _unit_count_from_text(desc_text)
        if raw_quantity is not None and raw_quantity > 1:
            # Small Quantity values are commonly the number of packs in a
            # multipack. Example: Quantity=4 + description='75 יחידות' => 300.
            if raw_quantity <= 12 and per_pack is not None and per_pack >= 10:
                return raw_quantity * per_pack, "יחידות"
            # Larger values are normally the total wipe count (e.g. 224).
            if raw_quantity >= 10:
                return raw_quantity, "יחידות"
        if per_pack is not None:
            return per_pack, "יחידות"
        return parse_package_quantity(desc_text, need_key, None, unit_qty)

    return parse_package_quantity(desc_text, need_key, explicit_pack, unit_qty)


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

def timestamp_from_filename(file_name: str) -> str | None:
    """Extract retailer publication timestamp from common transparency filenames."""
    stem = re.sub(r"\.(?:gz|xml)+$", "", file_name, flags=re.I)
    # Retailer chain ids are also long digit sequences, so anchor dates to 20xx.
    matches = list(re.finditer(r"(20\d{6})[-_]?(\d{4,6})(?:\D|$)", stem))
    for m in reversed(matches):
        date_part, time_part = m.group(1), m.group(2)
        if len(time_part) == 4:
            time_part += "00"
        try:
            dt = datetime.strptime(date_part + time_part, "%Y%m%d%H%M%S")
        except ValueError:
            continue
        return dt.replace(tzinfo=timezone.utc).isoformat()
    return None


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
    updated = root_first(root, UPDATED_KEYS) or timestamp_from_filename(file_name)

    rows: list[dict[str, Any]] = []
    direct_product_shapes = 0

    # Fast path used by Rami Levy and standard transparency XML.
    for el in root.iter():
        d = child_map(el)
        barcode = first(d, CODE_KEYS)
        name = first(d, NAME_KEYS)
        price = safe_float(first(d, PRICE_KEYS))
        if barcode and name and price is not None:
            direct_product_shapes += 1

        parsed = _parse_product_map(
            d, source_name, file_name, str(branch_code), subchain, updated
        )
        if parsed:
            rows.append(parsed)

    # Only when the document has no standard product-shaped nodes do we invoke
    # the nested/attribute fallback. This leaves proven Rami parsing untouched.
    if direct_product_shapes == 0:
        for d in minimal_product_maps(root):
            parsed = _parse_product_map(
                d, source_name, file_name, str(branch_code), subchain, updated
            )
            if parsed:
                rows.append(parsed)

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
