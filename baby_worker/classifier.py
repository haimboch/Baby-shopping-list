from __future__ import annotations
import re
from typing import Optional, Tuple

HEBREW_QUOTES = "\"'״׳"

BRANDS = [
    (re.compile(r"האגיס|huggies", re.I), "Huggies"),
    (re.compile(r"פמפרס|pampers", re.I), "Pampers"),
    (re.compile(r"בייבי\s*סיטר|baby\s*sitter|babysitter", re.I), "BabySitter"),
    (re.compile(r"מטרנה|materna", re.I), "Materna"),
    (re.compile(r"סימילאק|similac", re.I), "Similac"),
    (re.compile(r"נוטרילון|nutrilon", re.I), "Nutrilon"),
]

WIPES_RE = re.compile(r"מגבונ|wet\s*wipes?|\bwipes?\b", re.I)
DIAPERS_RE = re.compile(r"חיתול|טיטול|\bdiapers?\b|\bnapp(?:y|ies)\b", re.I)
FORMULA_RE = re.compile(
    r"תמ[\"'״׳]?ל|תחליף\s*חלב|פורמולה|מזון\s+לתינוק|"
    r"מטרנה|סימילאק|נוטרילון|materna|similac|nutrilon|infant\s*formula",
    re.I,
)

# Exclusions reduce false positives such as diaper bags / diaper cream.
DIAPER_EXCLUDE_RE = re.compile(
    r"שקיות?.{0,20}(?:חיתול|טיטול)|משחת\s*החתלה|קרם\s*החתלה|"
    r"diaper\s*(?:bags?|cream|rash)",
    re.I,
)

def classify_need(name: str) -> Optional[str]:
    s = (name or "").strip()
    if not s:
        return None
    if WIPES_RE.search(s):
        return "wipes"
    if DIAPERS_RE.search(s) and not DIAPER_EXCLUDE_RE.search(s):
        return "diapers"
    if FORMULA_RE.search(s):
        return "formula"
    return None

def infer_brand(name: str, manufacturer: str | None = None) -> str | None:
    hay = f"{name or ''} {manufacturer or ''}"
    for regex, brand in BRANDS:
        if regex.search(hay):
            return brand
    value = (manufacturer or "").strip()
    return value or None

def parse_dimension(name: str, need_key: str) -> Tuple[str, Optional[str]]:
    s = name or ""
    if need_key == "diapers":
        # NB is common for newborn products.
        if re.search(r"(?:^|\W)NB(?:\W|$)|ניו\s*בורן|new\s*born|newborn", s, re.I):
            return "size", "NB"
        m = re.search(r"(?:מידה|שלב|size|stage)\s*[:\-]?\s*(\d+\+?)", s, re.I)
        return "size", m.group(1) if m else None
    if need_key == "formula":
        m = re.search(r"(?:שלב|stage)\s*[:\-]?\s*(\d+)", s, re.I)
        return "stage", m.group(1) if m else None
    return "none", None

def parse_package_quantity(
    name: str,
    need_key: str,
    qty_in_package: str | None = None,
    unit_qty: str | None = None,
) -> tuple[float | None, str | None]:
    parts = " ".join(x for x in [qty_in_package or "", unit_qty or "", name or ""] if x)
    s = parts.replace(",", ".")

    if need_key == "formula":
        # Prefer grams. Convert kilograms to grams when explicitly supplied.
        kg = re.search(r"(\d+(?:\.\d+)?)\s*(?:ק[\"״]?ג|kg)\b", s, re.I)
        if kg:
            return float(kg.group(1)) * 1000.0, "גרם"
        g = re.search(r"(\d+(?:\.\d+)?)\s*(?:גרם|גר'|gr\b|grams?\b|\bg\b)", s, re.I)
        if g:
            return float(g.group(1)), "גרם"
        return None, "גרם"

    # Explicit package count field is usually more trustworthy.
    q = (qty_in_package or "").strip().replace(",", ".")
    if q:
        try:
            value = float(q)
            if value > 0:
                return value, "יחידות"
        except ValueError:
            pass

    # Multi-pack wipes: "4x56", "4 X 56", "4×56".
    multi = re.search(r"(\d+)\s*[xX×]\s*(\d+)", s)
    if multi:
        return float(int(multi.group(1)) * int(multi.group(2))), "יחידות"

    units = re.search(r"(\d+)\s*(?:יחידות|יחידה|יח['׳]?|units?|pcs?)\b", s, re.I)
    if units:
        return float(units.group(1)), "יחידות"
    return None, "יחידות"
