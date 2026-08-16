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

# -------------------------
# Precision-first MVP rules
# -------------------------

DIAPERS_RE = re.compile(r"חיתול|טיטול|\bdiapers?\b|\bnapp(?:y|ies)\b", re.I)
DIAPER_EXCLUDE_RE = re.compile(
    r"שקיות?.{0,20}(?:חיתול|טיטול)|משחת\s*החתלה|קרם\s*החתלה|"
    r"חיתול(?:י|ים)?\s*שחייה|חיתולים?\s*ללילה|"
    r"diaper\s*(?:bags?|cream|rash)|swim\s*(?:diaper|nappy)|overnight\s*diaper|"
    r"מבוגרים|למבוגרים|adult|incontinence|continence",
    re.I,
)

# Formula: do NOT classify on the generic word "פורמולה" alone. That caused
# hair products and even soy sauce to enter the baby-formula bucket.
FORMULA_GENERIC_RE = re.compile(
    r"תמ[\"'״׳]?ל|תחליף\s*חלב|תרכובת\s+מזון\s+לתינוק|"
    r"infant\s*formula|baby\s*formula",
    re.I,
)
FORMULA_BRAND_RE = re.compile(r"מטרנה|סימילאק|נוטרילון|materna|similac|nutrilon", re.I)
FORMULA_EXCLUDE_RE = re.compile(
    r"דייס|מחית|מחיות|צידנית|ארוחת|פדיאשור|pediasure|baby\s*food|"
    r"מטרנה\s+אורגני\s+תפוח|"
    r"רוטב|סויה|שמפו|מרכך|קרם|לחות|מסכה|שיער|"
    r"porridge|puree|shampoo|conditioner|hair|sauce",
    re.I,
)

# Wipes: precision first. A generic "מגבונים" is not enough because retailer
# feeds also contain floor, toilet, makeup and household-cleaning wipes.
WIPES_WORD_RE = re.compile(r"מגבונ|wet\s*wipes?|\bwipes?\b", re.I)
WIPES_BABY_SIGNAL_RE = re.compile(
    r"תינוק|בייבי|baby|ניו\s*בורן|newborn|"
    r"האגיס|huggies|פמפרס|pampers|בייבי\s*סיטר|babysitter|"
    r"קמיל\s*בלו|דרדסים|בלנאום|טיטולים",
    re.I,
)
WIPES_EXCLUDE_RE = re.compile(
    r"טואלט|נייר\s*טואלט|רצפה|ניקוי|ניקיון|שיש|אבק|"
    r"הסרת\s*איפור|איפור|חלונות|מטבח|אמבטיה|רהיטים|"
    r"toilet|floor|clean(?:ing)?|makeup|kitchen|window|furniture",
    re.I,
)


def classify_need(name: str) -> Optional[str]:
    s = (name or "").strip()
    if not s:
        return None

    # Baby wipes first, but only with a baby signal and no household/toilet signal.
    if WIPES_WORD_RE.search(s):
        if WIPES_EXCLUDE_RE.search(s):
            return None
        if WIPES_BABY_SIGNAL_RE.search(s):
            return "wipes"
        return None

    if DIAPERS_RE.search(s) and not DIAPER_EXCLUDE_RE.search(s):
        return "diapers"

    if not FORMULA_EXCLUDE_RE.search(s):
        if FORMULA_GENERIC_RE.search(s) or FORMULA_BRAND_RE.search(s):
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
        if re.search(r"(?:^|\W)NB(?:\W|$)|ניו\s*בורן|new\s*born|newborn", s, re.I):
            return "size", "NB"
        m = re.search(r"(?:מידה|שלב|size|stage)\s*[:\-]?\s*(\d+\+?)", s, re.I)
        return "size", m.group(1) if m else None

    if need_key == "formula":
        m = re.search(r"(?:שלב|stage)\s*[:\-]?\s*([123])", s, re.I)
        if m:
            return "stage", m.group(1)

        # Common retailer shorthand: "Similac Gold Plus 1/2/3" without the word stage.
        m = re.search(r"(?:סימילאק\s+גולד\s+פלוס|similac\s+gold\s+plus)\s*([123])(?:\D|$)", s, re.I)
        if m:
            return "stage", m.group(1)

        return "stage", None

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
        kg = re.search(r"(\d+(?:\.\d+)?)\s*(?:ק[\"״]?ג|kg)\b", s, re.I)
        if kg:
            return float(kg.group(1)) * 1000.0, "גרם"
        g = re.search(r"(\d+(?:\.\d+)?)\s*(?:גרם|גר'|gr\b|grams?\b|\bg\b)", s, re.I)
        if g:
            return float(g.group(1)), "גרם"
        return None, "גרם"

    q = (qty_in_package or "").strip().replace(",", ".")
    if q and q.lower() not in {"לא ידוע", "unknown", "n/a"}:
        try:
            value = float(q)
            # Generic retailer Quantity fields are often "1" meaning one pack,
            # not one diaper/wipe. Never treat 1 as package content.
            if value > 1:
                return value, "יחידות"
        except ValueError:
            pass

    multi = re.search(r"(\d+)\s*[xX×*]\s*(\d+)", s)
    if multi:
        return float(int(multi.group(1)) * int(multi.group(2))), "יחידות"

    units = re.search(r"(\d+)\s*(?:יחידות|יחידה|יח['׳]?|units?|pcs?)\b", s, re.I)
    if units:
        value = float(units.group(1))
        if value > 1:
            return value, "יחידות"
    return None, "יחידות"
