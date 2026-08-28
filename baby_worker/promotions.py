from __future__ import annotations

import re
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo


ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("\u00a0", " ").replace(",", ".")
    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _quantity(value: Any) -> int | None:
    number = _number(value)
    if number is None or not float(number).is_integer():
        return None
    quantity = int(number)
    return quantity if 1 <= quantity <= 100 else None


def _clean_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u200e", "").replace("\u200f", "").replace("\u00a0", " ")
    text = text.replace("־", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


_UNIT_WORDS = (
    r"(?:יחידות?|יחידה|יח[\'׳״]?|אריזות?|אריזה|מארזים?|מארז|"
    r"חבילות?|חבילה|פריטים?|פריט|packs?|items?|pcs?)"
)
_CURRENCY = r"(?:₪|ש[\"״׳']?ח|שח|nis)?"
_AMOUNT = r"\d+(?:[.,]\d{1,2})?"


_BUNDLE_PATTERNS = (
    # "2 ב-25", "2 יחידות במחיר 25", "2 for ₪25", "2/25".
    re.compile(
        rf"(?<!\d)(?P<quantity>\d{{1,3}})(?!\d)\s*(?:{_UNIT_WORDS}\s*)?"
        rf"(?:במחיר(?:\s+של)?|ב|for|/)\s*[-:]?\s*{_CURRENCY}\s*"
        rf"(?P<total>{_AMOUNT})\s*{_CURRENCY}(?!\d)",
        re.IGNORECASE,
    ),
    # "מחיר ל-2 יחידות 25 ₪".
    re.compile(
        rf"(?<!\w)(?:מחיר\s*)?ל(?!\w)\s*-?\s*"
        rf"(?P<quantity>\d{{1,3}})(?!\d)"
        rf"(?:(?:\s*{_UNIT_WORDS})(?:\s*ב\s*-?\s*)?|\s*ב\s*-?\s*|\s+)"
        rf"{_CURRENCY}\s*"
        rf"(?P<total>{_AMOUNT})\s*{_CURRENCY}(?!\d)",
        re.IGNORECASE,
    ),
    # "25 ₪ ל-2 יחידות" and "₪25 for 2 packs".
    re.compile(
        rf"{_CURRENCY}\s*(?P<total>{_AMOUNT})\s*{_CURRENCY}\s*"
        rf"(?<!\w)(?:ל|עבור|for)(?!\w)\s*-?\s*"
        rf"(?P<quantity>\d{{1,3}})(?!\d)\s*"
        rf"(?:{_UNIT_WORDS})?(?!\d)",
        re.IGNORECASE,
    ),
)


_BUY_GET_PATTERN = re.compile(
    rf"(?<!\d)(?P<paid>\d{{1,2}})\s*\+\s*(?P<free>\d{{1,2}})\s*"
    rf"(?:{_UNIT_WORDS}\s*)?(?:חינם|מתנה|free)",
    re.IGNORECASE,
)


def parse_bundle_description(description: Any) -> dict[str, float | int | str] | None:
    """Extract a conservative fixed-total or buy/get promotion from free text.

    The parser deliberately requires an explicit promotion separator (for
    example ``ב``, ``for`` or ``/``), so package text such as "4 units, ₪25"
    cannot be mistaken for a multi-buy offer.
    """

    text = _clean_text(description)
    if not text:
        return None

    for pattern in _BUNDLE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        quantity = _quantity(match.group("quantity"))
        total = _number(match.group("total"))
        if quantity is None or quantity <= 1 or total is None:
            continue
        return {
            "quantity": quantity,
            "total_price": round(total, 4),
            "kind": "fixed_total",
        }

    match = _BUY_GET_PATTERN.search(text)
    if match:
        paid = _quantity(match.group("paid"))
        free = _quantity(match.group("free"))
        if paid and free and paid + free <= 100:
            return {
                "quantity": paid + free,
                "paid_quantity": paid,
                "free_quantity": free,
                "kind": "buy_get_free",
            }
    return None


def _promotion_validation_reason(
    quantity: int,
    total: float | None,
    unit_price: float | None,
    regular: float | None,
) -> str | None:
    if quantity > 1:
        if unit_price is None or total is None:
            return "incomplete_multi_buy_terms"
        if abs(total - unit_price * quantity) > max(0.02, total * 0.005):
            return "inconsistent_multi_buy_total"
        if regular is not None and total < regular - 0.01:
            # A quantity deal whose entire basket costs less than one regular
            # package is almost always a split-number/parser error. 1+1 free
            # remains valid because its total equals one regular package.
            return "bundle_total_below_single_regular_price"
        if regular is not None and total >= regular * quantity - 0.005:
            return "multi_buy_has_no_saving"
    if regular is not None and unit_price is not None and unit_price >= regular:
        return "promotion_has_no_saving"
    return None


def normalize_promotion_terms(
    promotion: dict[str, Any],
    regular_price: Any = None,
) -> dict[str, Any]:
    """Return one unambiguous per-package promotion representation.

    ``promo_price`` is always the effective price per purchasable package,
    while ``promo_total_price`` is the amount paid for the minimum quantity.
    Ambiguous retailer fields are resolved only when explicit terms or a
    regular price make the interpretation safe.
    """

    normalized = dict(promotion or {})
    description = _clean_text(
        normalized.get("promo_description")
        or normalized.get("description")
        or normalized.get("name")
    )
    regular = _number(regular_price)
    structured_quantity = _quantity(normalized.get("promo_min_quantity"))
    quantity = structured_quantity or 1
    total = _number(normalized.get("promo_total_price"))
    unit_price = _number(normalized.get("promo_price"))

    raw = normalized.get("raw_promo")
    if not isinstance(raw, dict):
        raw = {}
    raw_explicit_price = _number(
        raw.get("explicit_price")
        or raw.get("promoPrice")
        or raw.get("discountedPrice")
    )
    raw_total_price = _number(raw.get("total_price") or raw.get("totalPrice"))
    if unit_price is None:
        unit_price = raw_explicit_price
    if total is None:
        total = raw_total_price

    parsed = parse_bundle_description(description)
    if parsed:
        parsed_quantity = int(parsed["quantity"])
        # Human-readable terms are the safest fallback when provider fields
        # were flattened to an effective unit price.
        if structured_quantity is None or structured_quantity <= 1:
            quantity = parsed_quantity
        quantities_agree = quantity == parsed_quantity
        if (
            parsed.get("kind") == "fixed_total"
            and quantities_agree
            and total is None
        ):
            total = float(parsed["total_price"])
        elif (
            parsed.get("kind") == "buy_get_free"
            and regular is not None
            and quantities_agree
            and total is None
        ):
            total = round(regular * int(parsed["paid_quantity"]), 4)

    if quantity > 1:
        if total is not None:
            unit_price = round(total / quantity, 4)
        elif unit_price is not None and regular is not None:
            if unit_price < regular:
                # Providers such as CheaperSal expose an effective unit price.
                total = round(unit_price * quantity, 4)
            elif unit_price / quantity < regular:
                # Some transparency publishers put the bundle total in the
                # field named PromotionPrice.
                total = round(unit_price, 4)
                unit_price = round(total / quantity, 4)
            else:
                unit_price = None
        elif unit_price is not None:
            # Without a regular price we cannot distinguish unit from bundle.
            unit_price = None
    else:
        if unit_price is None and total is not None:
            unit_price = total
        if total is None and unit_price is not None:
            total = unit_price

    validation_reason = _promotion_validation_reason(
        quantity, total, unit_price, regular
    )

    # If free text produced impossible direct fields, retry the untouched
    # structured source values before discarding the promotion. This repairs
    # rows such as the live Yohananof case: parsed 2-for-5, raw value 110.
    if validation_reason and quantity > 1 and regular is not None:
        fallbacks: list[tuple[str, float]] = []
        if raw_total_price is not None:
            fallbacks.append(("total", raw_total_price))
        if raw_explicit_price is not None:
            fallbacks.append(("ambiguous", raw_explicit_price))
        for kind, value in fallbacks:
            if kind == "total":
                candidate_total = value
                candidate_unit = round(candidate_total / quantity, 4)
            elif value < regular:
                candidate_unit = value
                candidate_total = round(candidate_unit * quantity, 4)
            elif value / quantity < regular:
                candidate_total = value
                candidate_unit = round(candidate_total / quantity, 4)
            else:
                continue
            candidate_reason = _promotion_validation_reason(
                quantity, candidate_total, candidate_unit, regular
            )
            if candidate_reason is None:
                total = candidate_total
                unit_price = candidate_unit
                validation_reason = None
                break

    if validation_reason is not None:
        unit_price = None

    normalized.update({
        "promo_price": round(unit_price, 4) if unit_price is not None else None,
        "promo_description": description or None,
        "promo_min_quantity": quantity,
        "promo_total_price": round(total, 4) if total is not None else None,
        "requires_club": bool(normalized.get("requires_club")),
        # Diagnostic only. The database writer intentionally stores only the
        # public promotion columns, while tests/logs can explain rejections.
        "promo_validation_reason": validation_reason,
    })
    return normalized


def normalize_promotion_timestamp(value: Any, *, end_of_day: bool = False) -> str | None:
    """Normalize retailer timestamps without expiring date-only deals at 00:00."""

    if value in (None, ""):
        return None
    text_value = str(value).strip()
    if not text_value:
        return None

    iso_candidate = text_value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
        if "T" not in text_value and " " not in text_value:
            parsed = datetime.combine(
                parsed.date(),
                time.max if end_of_day else time.min,
                tzinfo=ISRAEL_TZ,
            )
        elif parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ISRAEL_TZ)
        return parsed.isoformat()
    except ValueError:
        pass

    for fmt, date_only in (
        ("%Y%m%d%H%M%S", False),
        ("%Y%m%d%H%M", False),
        ("%Y-%m-%d %H:%M:%S", False),
        ("%d/%m/%Y %H:%M", False),
        ("%Y%m%d", True),
        ("%d/%m/%Y", True),
    ):
        try:
            parsed_value = datetime.strptime(text_value, fmt)
        except ValueError:
            continue
        if date_only:
            parsed_value = datetime.combine(
                parsed_value.date(),
                time.max if end_of_day else time.min,
                tzinfo=ISRAEL_TZ,
            )
        else:
            parsed_value = parsed_value.replace(tzinfo=ISRAEL_TZ)
        return parsed_value.isoformat()
    return None


def promotion_is_active(
    promotion: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    start = normalize_promotion_timestamp(promotion.get("promo_start_at"))
    end = normalize_promotion_timestamp(
        promotion.get("promo_end_at"), end_of_day=True
    )
    try:
        if start and datetime.fromisoformat(start) > now:
            return False
        if end and datetime.fromisoformat(end) < now:
            return False
    except ValueError:
        return False
    return True
