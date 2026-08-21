from __future__ import annotations
import re
from typing import Optional, Tuple

from .product_types import CREAM_PRODUCT_TYPES, LIQUID_PRODUCT_TYPES

HEBREW_QUOTES = "\"'״׳"

BRANDS = [
    (re.compile(r"האגיס|huggies", re.I), "Huggies"),
    (re.compile(r"פמפרס|pampers", re.I), "Pampers"),
    (re.compile(r"בייבי\s*סיטר|baby\s*sitter|babysitter", re.I), "BabySitter"),
    (re.compile(r"מטרנה|materna", re.I), "Materna"),
    (re.compile(r"סימילאק|similac", re.I), "Similac"),
    (re.compile(r"נוטרילון|nutrilon", re.I), "Nutrilon"),
    (re.compile(r"מוסטלה|mustela", re.I), "Mustela"),
    (re.compile(r"סבוקלם|sebocalm", re.I), "SeboCalm"),
    (re.compile(r"ד[״\"']?ר\s*פישר|דוקטור\s*פישר|dr\.?\s*fischer", re.I), "Dr. Fischer"),
    (re.compile(r"ג[׳']?ונסון|johnson", re.I), "Johnson's"),
    (re.compile(r"מאמי\s*קר|mommy\s*care", re.I), "Mommy Care"),
    (re.compile(r"וולדה|weleda", re.I), "Weleda"),
    (re.compile(r"בפנטן|bepanthen", re.I), "Bepanthen"),
    (re.compile(r"סודוקרם|sudocrem", re.I), "Sudocrem"),
    (re.compile(r"בדין|badin", re.I), "Badin"),
    (re.compile(r"סנו\s*מקסימה|sano\s*maxima", re.I), "Sano Maxima"),
]

WIPES_RE = re.compile(r"מגבונ|wet\s*wipes?|\bwipes?\b", re.I)
DIAPERS_RE = re.compile(r"חיתול|טיטול|\bdiapers?\b|\bnapp(?:y|ies)\b", re.I)
BABY_MARKER_RE = re.compile(
    r"תינוק|בייבי|\bbaby\b|infants?|new\s*born|newborn|ניו\s*בורן|פעוט",
    re.I,
)
BABY_WIPES_BRAND_RE = re.compile(
    r"האגיס|huggies|פמפרס|pampers|בייבי\s*סיטר|baby\s*sitter|babysitter",
    re.I,
)
ADULT_PRODUCT_RE = re.compile(
    r"(?:ל)?מבוגר(?:ים|ות)?|\badults?\b|incontinence|דליפת\s*שתן",
    re.I,
)
NON_BABY_WIPES_RE = re.compile(
    r"רצפ|טואלט|שירותים|איפור|מסיר.{0,8}איפור|מטבח|ניקוי\s*(?:כללי|בית)|"
    r"disinfect|make.?up|floor|toilet|kitchen",
    re.I,
)
FORMULA_RE = re.compile(
    r"תמ[\"'״׳]?ל|תחליף\s*חלב|פורמולה|מזון\s+לתינוק|"
    r"מטרנה|סימילאק|נוטרילון|materna|similac|nutrilon|infant\s*formula",
    re.I,
)
FORMULA_EXCLUDE_RE = re.compile(
    r"דייס|(?<![א-ת])מחי(?:ת|ות)|פאוץ|סקו{0,2}[ייו]*ז|כפית|צידנית|פדיאשור|"
    r"pediasure|puree|cereal|\bpouch\b|\bspoon\b",
    re.I,
)
BABY_BATH_OIL_BRAND_RE = re.compile(r"אמול|בלנאום|מוסטלה|mustela", re.I)

# Exclusions reduce false positives such as diaper bags / diaper cream.
DIAPER_EXCLUDE_RE = re.compile(
    r"שקיות?.{0,20}(?:חיתול|טיטול)|משחת\s*החתלה|קרם\s*החתלה|"
    r"diaper\s*(?:bags?|cream|rash)",
    re.I,
)
DIAPER_CREAM_RE = re.compile(r"משח(?:ה|ת|ות).{0,18}(?:החתלה|תפרחת|חיתול)|קרם.{0,12}(?:החתלה|תפרחת)|(?:diaper|nappy).{0,12}(?:cream|rash)|בפנטן\s*בייבי|סודוקרם", re.I)
CHANGING_PADS_RE = re.compile(r"משטח(?:י|ים|ון)?.{0,18}החתלה|משטח.{0,18}החלפ(?:ה|ת)|changing\s*(?:pads?|mats?)", re.I)
DIAPER_BAGS_RE = re.compile(r"שקי(?:ת|ות).{0,20}(?:חיתול|טיטול|החתלה)|(?:diaper|nappy)\s*bags?", re.I)
BATH_OIL_RE = re.compile(r"שמן.{0,18}(?:אמבט|רחצה)|(?:אמבט|רחצה).{0,18}שמן|אמול(?:\s|$)|bath\s*oil", re.I)
BABY_LAUNDRY_RE = re.compile(r"(?:כביסה|מרכך|אבקה|ג[׳'’]?ל).{0,25}(?:תינוק|בייבי|baby)|(?:תינוק|בייבי|baby).{0,25}(?:כביסה|מרכך|אבקה)|baby.{0,12}(?:laundry|detergent|softener)", re.I)
BABY_WASH_RE = re.compile(r"(?:סבון|שמפו|תרחיץ|אל.?סבון|קצף.{0,8}אמבט).{0,25}(?:תינוק|בייבי|baby)|(?:תינוק|בייבי|baby).{0,25}(?:סבון|שמפו|תרחיץ)|baby.{0,12}(?:wash|shampoo|soap)", re.I)
BODY_CREAM_RE = re.compile(r"(?:קרם\s*(?:גוף|לחות)|תחליב\s*(?:גוף|לחות)).{0,25}(?:תינוק|בייבי|baby)|(?:תינוק|בייבי|baby).{0,25}(?:קרם|תחליב)|baby.{0,12}(?:lotion|body\s*cream)", re.I)

def classify_need(name: str) -> Optional[str]:
    s = (name or "").strip()
    if not s or ADULT_PRODUCT_RE.search(s):
        return None
    if DIAPER_CREAM_RE.search(s):
        return "diaper_cream"
    if CHANGING_PADS_RE.search(s):
        return "changing_pads"
    if DIAPER_BAGS_RE.search(s):
        return "diaper_bags"
    if WIPES_RE.search(s) and not NON_BABY_WIPES_RE.search(s) and (
        BABY_MARKER_RE.search(s) or BABY_WIPES_BRAND_RE.search(s)
    ):
        return "wipes"
    if DIAPERS_RE.search(s) and not DIAPER_EXCLUDE_RE.search(s):
        return "diapers"
    if FORMULA_RE.search(s) and not FORMULA_EXCLUDE_RE.search(s):
        return "formula"
    if BATH_OIL_RE.search(s) and (
        BABY_MARKER_RE.search(s) or BABY_BATH_OIL_BRAND_RE.search(s)
    ):
        return "bath_oil"
    if BABY_LAUNDRY_RE.search(s):
        return "baby_laundry"
    if BABY_WASH_RE.search(s):
        return "baby_wash"
    if BODY_CREAM_RE.search(s):
        return "body_cream"
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

    if need_key in LIQUID_PRODUCT_TYPES:
        liters = re.search(r"(\d+(?:\.\d+)?)\s*(?:ליטר|liters?|litres?|\bl\b)", s, re.I)
        if liters:
            return float(liters.group(1)) * 1000.0, "מ״ל"
        milliliters = re.search(r"(\d+(?:\.\d+)?)\s*(?:מ[״\"']?ל|milliliters?|\bml\b)", s, re.I)
        if milliliters:
            return float(milliliters.group(1)), "מ״ל"

    if need_key in CREAM_PRODUCT_TYPES:
        grams = re.search(r"(\d+(?:\.\d+)?)\s*(?:גרם|גר'|grams?|\bg\b)", s, re.I)
        if grams:
            return float(grams.group(1)), "גרם"

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
