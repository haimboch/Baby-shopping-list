"""Regression coverage for universal quantity promotions in v0.51."""

from __future__ import annotations

from datetime import datetime, timezone

from baby_worker.product_types import PRODUCT_TYPES
from baby_worker.promotions import (
    normalize_promotion_terms,
    normalize_promotion_timestamp,
    parse_bundle_description,
    promotion_is_active,
)
from baby_worker.worker import merge_prices_and_promos, to_db_price
from baby_worker.xmlfeeds import parse_promotions


def _price_row(
    source_name: str,
    barcode: str,
    need_key: str = "wipes",
    *,
    subchain_id: str | None = None,
    regular_price: float = 14.9,
) -> dict:
    return {
        "source_name": source_name,
        "subchain_id": subchain_id,
        "branch_code": "101",
        "barcode": barcode,
        "need_key": need_key,
        "dimension_type": "none",
        "dimension_value": None,
        "brand": "מותג בדיקה",
        "product_name": f"מוצר תינוקות {need_key}",
        "package_quantity": 1,
        "package_unit": "יחידות",
        "regular_price": regular_price,
        "source_updated_at": "2026-08-27T08:00:00+00:00",
        "raw_source": {},
    }


def _ambiguous_bundle_promo(
    source_name: str,
    barcode: str,
    *,
    subchain_id: str | None = None,
    quantity: int = 2,
    total: float = 25,
) -> dict:
    # Israeli transparency publishers commonly put the bundle total in a
    # field named PromotionPrice. The shared normalizer resolves it against
    # the regular package price.
    return {
        "source_name": source_name,
        "subchain_id": subchain_id,
        "branch_code": "101",
        "barcode": barcode,
        "promo_price": None,
        "promo_description": None,
        "promo_start_at": None,
        "promo_end_at": None,
        "promo_min_quantity": quantity,
        "promo_total_price": None,
        "requires_club": False,
        "raw_promo": {
            "min_qty": quantity,
            "explicit_price": total,
            "total_price": None,
        },
    }


def test_v051_parses_common_bundle_descriptions_conservatively():
    examples = {
        "2 ב-25 ₪": (2, 25),
        "2 יחידות במחיר של 25 ש\"ח": (2, 25),
        "מחיר ל-3 אריזות 80": (3, 80),
        "₪54 עבור 2 יחידות": (2, 54),
        "2/30": (2, 30),
    }
    for description, expected in examples.items():
        parsed = parse_bundle_description(description)
        assert parsed is not None, description
        assert (parsed["quantity"], parsed["total_price"]) == expected

    free = parse_bundle_description("1+1 יחידה חינם")
    assert free == {
        "quantity": 2,
        "paid_quantity": 1,
        "free_quantity": 1,
        "kind": "buy_get_free",
    }
    assert parse_bundle_description("מארז 4 יחידות 25 ₪") is None


def test_v051_normalizes_unit_total_and_description_only_promotions():
    total_in_price_field = normalize_promotion_terms({
        "promo_price": 25,
        "promo_min_quantity": 2,
    }, 14.9)
    assert total_in_price_field["promo_price"] == 12.5
    assert total_in_price_field["promo_total_price"] == 25

    effective_unit_field = normalize_promotion_terms({
        "promo_price": 12.5,
        "promo_min_quantity": 2,
    }, 14.9)
    assert effective_unit_field["promo_price"] == 12.5
    assert effective_unit_field["promo_total_price"] == 25

    description_only = normalize_promotion_terms({
        "promo_description": "2 ב-25",
    }, 14.9)
    assert description_only["promo_min_quantity"] == 2
    assert description_only["promo_total_price"] == 25
    assert description_only["promo_price"] == 12.5

    buy_one_get_one = normalize_promotion_terms({
        "promo_description": "1+1 חינם",
    }, 14.9)
    assert buy_one_get_one["promo_min_quantity"] == 2
    assert buy_one_get_one["promo_total_price"] == 14.9
    assert buy_one_get_one["promo_price"] == 7.45


def test_v051_applies_same_bundle_logic_to_every_product_and_retailer():
    retailers = (
        ("RAMI_LEVY", None, "rami_levy"),
        ("YOHANANOF", None, "yochananof"),
        ("SHUFERSAL", "001", "shufersal"),
        ("SHUFERSAL", "005", "be"),
        ("OSHER_AD", None, "osher_ad"),
        ("SUPER_PHARM", None, "super_pharm"),
        ("KSP", None, "ksp"),
    )
    for source_name, subchain_id, expected_chain in retailers:
        for index, need_key in enumerate(PRODUCT_TYPES):
            barcode = f"{len(source_name):02d}{index:011d}"
            merged = merge_prices_and_promos(
                [_price_row(source_name, barcode, need_key, subchain_id=subchain_id)],
                [_ambiguous_bundle_promo(
                    source_name, barcode, subchain_id=subchain_id,
                )],
            )[0]
            assert merged["promo_min_quantity"] == 2, (source_name, need_key)
            assert merged["promo_total_price"] == 25, (source_name, need_key)
            assert merged["promo_price"] == 12.5, (source_name, need_key)
            assert merged["effective_price"] == 12.5, (source_name, need_key)
            assert to_db_price(merged)["chain_id"] == expected_chain


def test_v051_resolves_live_feed_total_convention_and_best_concurrent_offer():
    price = _price_row("OSHER_AD", "8700216891103", regular_price=34.5)
    three_for_eighty = _ambiguous_bundle_promo(
        "OSHER_AD", "8700216891103", quantity=3, total=80,
    )
    two_for_sixty = _ambiguous_bundle_promo(
        "OSHER_AD", "8700216891103", quantity=2, total=60,
    )
    merged = merge_prices_and_promos(
        [price], [two_for_sixty, three_for_eighty],
    )[0]
    assert merged["promo_min_quantity"] == 3
    assert merged["promo_total_price"] == 80
    assert round(merged["promo_price"], 4) == 26.6667


def test_v051_xml_parser_keeps_distinct_deals_without_cross_contamination():
    content = b"""<Root><StoreId>101</StoreId><Promotions>
      <Promotion><Terms><PromotionPrice>25</PromotionPrice><MinQty>2</MinQty></Terms>
        <PromotionItems><Item><ItemCode>7290000000001</ItemCode></Item></PromotionItems>
      </Promotion>
      <Promotion><Terms><PromotionPrice>80</PromotionPrice><MinQty>3</MinQty></Terms>
        <PromotionItems><Item><ItemCode>7290000000002</ItemCode></Item></PromotionItems>
      </Promotion>
    </Promotions></Root>"""
    promos = parse_promotions(
        content, "RAMI_LEVY", "PromoFull7290058140886-001-101-202608270800.xml",
    )
    assert len(promos) == 2
    assert {row["barcode"] for row in promos} == {
        "7290000000001", "7290000000002",
    }
    assert {row["raw_promo"]["explicit_price"] for row in promos} == {25, 80}

    prices = [
        _price_row("RAMI_LEVY", "7290000000001", regular_price=14.9),
        _price_row("RAMI_LEVY", "7290000000002", regular_price=34.5),
    ]
    merged = merge_prices_and_promos(prices, promos)
    by_barcode = {row["barcode"]: row for row in merged}
    assert by_barcode["7290000000001"]["promo_total_price"] == 25
    assert by_barcode["7290000000002"]["promo_total_price"] == 80


def test_v051_date_only_promotions_last_through_end_of_day_in_israel():
    end = normalize_promotion_timestamp("2026-08-27", end_of_day=True)
    assert end is not None and end.startswith("2026-08-27T23:59:59.999999")
    assert promotion_is_active(
        {"promo_start_at": "2026-08-27", "promo_end_at": "2026-08-27"},
        datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
    )
    compact = normalize_promotion_timestamp("20260827080045")
    assert compact is not None and compact.startswith("2026-08-27T08:00:45")
