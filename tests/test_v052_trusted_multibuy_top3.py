"""Regression coverage for trusted multi-buy offers and top-three results."""

from __future__ import annotations

from pathlib import Path

from baby_worker.product_types import PRODUCT_TYPES
from baby_worker.promotions import normalize_promotion_terms, parse_bundle_description
from baby_worker.worker import merge_prices_and_promos


ROOT = Path(__file__).resolve().parents[1]
MISLEADING_RESTRICTION = "נוטרילון 800 גרם עד 4 יח מעל 75"


def _price(source: str, barcode: str, need_key: str = "formula", *, subchain_id=None):
    return {
        "source_name": source,
        "subchain_id": subchain_id,
        "branch_code": "051",
        "barcode": barcode,
        "need_key": need_key,
        "dimension_type": "stage" if need_key == "formula" else "none",
        "dimension_value": "3" if need_key == "formula" else None,
        "brand": "Nutrilon",
        "product_name": "נוטרילון מוצר בדיקה",
        "package_quantity": 800,
        "package_unit": "גרם",
        "regular_price": 63.9,
        "source_updated_at": "2026-08-27T16:40:32+00:00",
        "raw_source": {},
    }


def _invalid_promo(source: str, barcode: str, *, subchain_id=None):
    return {
        "source_name": source,
        "subchain_id": subchain_id,
        "branch_code": "051",
        "barcode": barcode,
        "promo_price": 2.5,
        "promo_total_price": 5,
        "promo_min_quantity": 2,
        "promo_description": MISLEADING_RESTRICTION,
        "requires_club": True,
        "raw_promo": {"min_qty": 2},
    }


def test_v052_live_yochananof_restriction_is_not_parsed_as_two_for_five():
    assert parse_bundle_description(MISLEADING_RESTRICTION) is None

    normalized = normalize_promotion_terms(
        {
            "promo_min_quantity": 2,
            "promo_description": MISLEADING_RESTRICTION,
            "raw_promo": {
                "min_qty": 2,
                "explicit_price": 110,
                "total_price": None,
            },
        },
        63.9,
    )
    assert normalized["promo_min_quantity"] == 2
    assert normalized["promo_total_price"] == 110
    assert normalized["promo_price"] == 55
    assert normalized["promo_validation_reason"] is None

    repaired = normalize_promotion_terms(
        {
            "promo_min_quantity": 2,
            "promo_total_price": 5,
            "promo_price": 2.5,
            "promo_description": MISLEADING_RESTRICTION,
            "raw_promo": {"min_qty": 2, "explicit_price": 110},
        },
        63.9,
    )
    assert repaired["promo_total_price"] == 110
    assert repaired["promo_price"] == 55
    assert repaired["promo_validation_reason"] is None

    merged = merge_prices_and_promos(
        [_price("YOHANANOF", "8712400802536")],
        [{
            "source_name": "YOHANANOF",
            "branch_code": "051",
            "barcode": "8712400802536",
            "promo_price": None,
            "promo_total_price": None,
            "promo_min_quantity": 2,
            "promo_description": MISLEADING_RESTRICTION,
            "requires_club": True,
            "raw_promo": {"min_qty": 2, "explicit_price": 110},
        }],
    )[0]
    assert merged["promo_min_quantity"] == 2
    assert merged["promo_total_price"] == 110
    assert merged["promo_price"] == 55
    assert merged["effective_price"] == 55


def test_v052_impossible_quantity_deals_are_rejected_for_every_product_and_chain():
    normalized = normalize_promotion_terms(
        {
            "promo_min_quantity": 2,
            "promo_total_price": 5,
            "promo_price": 2.5,
            "promo_description": MISLEADING_RESTRICTION,
        },
        63.9,
    )
    assert normalized["promo_price"] is None
    assert normalized["promo_validation_reason"] == (
        "bundle_total_below_single_regular_price"
    )

    retailers = (
        ("RAMI_LEVY", None),
        ("YOHANANOF", None),
        ("SHUFERSAL", "001"),
        ("SHUFERSAL", "005"),
        ("OSHER_AD", None),
        ("SUPER_PHARM", None),
        ("KSP", None),
    )
    for source, subchain_id in retailers:
        for index, need_key in enumerate(PRODUCT_TYPES):
            barcode = f"{len(source):02d}{index:011d}"
            merged = merge_prices_and_promos(
                [_price(source, barcode, need_key, subchain_id=subchain_id)],
                [_invalid_promo(source, barcode, subchain_id=subchain_id)],
            )[0]
            assert merged["promo_price"] is None, (source, need_key)
            assert merged["promo_total_price"] is None, (source, need_key)
            assert merged["promo_min_quantity"] == 1, (source, need_key)
            assert merged["effective_price"] == 63.9, (source, need_key)


def test_v052_valid_quantity_deals_keep_exact_total_unit_price_and_saving_basis():
    cases = (
        (2, 110, 63.9, 55, 17.8),
        (2, 25, 14.9, 12.5, 4.8),
        (3, 80, 34.5, 26.6667, 23.5),
    )
    for quantity, total, regular, unit, saving in cases:
        normalized = normalize_promotion_terms(
            {
                "promo_min_quantity": quantity,
                # Official publishers sometimes place the bundle total here.
                "promo_price": total,
            },
            regular,
        )
        assert normalized["promo_total_price"] == total
        assert normalized["promo_price"] == unit
        assert round(regular * quantity - total, 4) == saving


def test_v052_ui_shows_three_offers_first_and_workflows_refresh_automatically():
    frontend = (ROOT / "index.html").read_text("utf-8")
    main_flow = (ROOT / ".github/workflows/update-baby-prices.yml").read_text("utf-8")
    special_flow = (
        ROOT / ".github/workflows/update-special-retailers.yml"
    ).read_text("utf-8")

    assert "const DEFAULT_VISIBLE_OFFERS=3" in frontend
    assert "visibleOfferRows(rankedResults,comparisonExpanded)" in frontend
    assert 'data-comparison-offers' in frontend
    assert 'data-product-offers=' in frontend
    assert "הצג אפשרויות נוספות" in frontend
    assert 'cron: "17 * * * *"' in main_flow
    assert 'ENRICHMENT_LIMIT: "0"' in main_flow
    assert 'CHEAPERSAL_IMAGE_LOOKUP_LIMIT: "0"' in main_flow
    assert 'cron: "47 * * * *"' in special_flow
    assert 'SPECIAL_RETAILER_MAINTENANCE_HOURS: "4"' in special_flow
