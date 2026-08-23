from baby_worker.classifier import classify_need, parse_dimension, parse_package_quantity
from baby_worker.cheapersal_prices import CheaperSalPriceClient, CheaperSalPriceFallback
from baby_worker.product_images import ProductImageEnricher, extract_product_image
import baby_worker.product_images as product_images_module
from baby_worker.ksp import _get_json, parse_ksp_api_product, parse_ksp_product_html
from baby_worker.superpharm_online import (
    extract_superpharm_category_urls,
    extract_superpharm_product_urls,
    parse_superpharm_product_html,
)
from baby_worker.worker import (
    _sp_candidates_from_html,
    assess_ingestion_outcome,
    merge_prices_and_promos,
    to_db_price,
    save_official_catalog_rows,
    _interleave_catalog_targets,
    _special_retailer_batch,
    _special_batch_with_lookup_result,
)
from baby_worker.xmlfeeds import parse_price_rows, parse_promotions, parse_stores

def test_classifier():
    assert classify_need("×”××’×™×¡ ××§×¡×˜×¨×” ×§×¨ ×—×™×ª×•×œ×™× ×ž×™×“×” 3 40 ×™×—×™×“×•×ª") == "diapers"
    assert classify_need("×ž×’×‘×•× ×™× ×œ×—×™× ×”××’×™×¡ 4x56") == "wipes"
    assert classify_need("×ž×˜×¨× ×” ×—×œ×‘×™ ×©×œ×‘ 2 700 ×’×¨×") == "formula"
    assert classify_need("×©×§×™×•×ª ×œ×—×™×ª×•×œ×™× ×ž×œ×•×›×œ×›×™×") == "diaper_bags"

def test_expanded_baby_product_classifier():
    examples = {
        "×ž×©×—×ª ×”×—×ª×œ×” ×œ×ª×™× ×•×§ ×¡×•×“×•×§×¨× 125 ×’×¨×": "diaper_cream",
        "×ž×©×˜×—×™ ×”×—×ª×œ×” ×—×“ ×¤×¢×ž×™×™× 10 ×™×—×™×“×•×ª": "changing_pads",
        "×©×§×™×•×ª ×œ×—×™×ª×•×œ×™× ×ž×œ×•×›×œ×›×™× 100 ×™×—×™×“×•×ª": "diaper_bags",
        "×©×ž×Ÿ ××ž×‘×˜ ×œ×ª×™× ×•×§ ××ž×•×œ 500 ×ž×œ": "bath_oil",
        "×©×ž×¤×• ×•×¡×‘×•×Ÿ ×œ×ª×™× ×•×§ ×“×•×§×˜×•×¨ ×¤×™×©×¨ 700 ×ž×œ": "baby_wash",
        "×§×¨× ×’×•×£ ×œ×ª×™× ×•×§ ×ž×•×¡×˜×œ×” 200 ×’×¨×": "body_cream",
        "×’×³×œ ×›×‘×™×¡×” ×œ×ª×™× ×•×§ ×¡× ×• ×ž×§×¡×™×ž×” 1 ×œ×™×˜×¨": "baby_laundry",
    }
    for name, expected in examples.items():
        assert classify_need(name) == expected, name
    assert classify_need("×©×ž×¤×• ×œ×©×™×¢×¨ ×¨×’×™×œ") is None
    assert classify_need("×§×¨× ×’×•×£ ×œ×ž×‘×•×’×¨×™×") is None


def test_baby_only_classifier_rejects_adult_and_general_products():
    rejected = [
        "×—×™×ª×•×œ×™ ×œ×™×œ×” ×œ×ž×‘×•×’×¨×™× ×ž×™×“×” L 10 ×™×—×™×“×•×ª",
        "×“×œ×™ ×ž×’×‘×•× ×™× ×œ×¨×¦×¤×” 400 ×™×—×™×“×•×ª",
        "×ž×’×‘×•× ×™× ×œ×—×™× ×œ× ×™×§×•×™ ×”×ž×˜×‘×—",
        "×ž×’×‘×•× ×™× ×œ×”×¡×¨×ª ××™×¤×•×¨",
        "×ž×’×‘×•× ×™× ×œ×—×™× ×œ×œ× ×©× ×ž×•×ª×’ 72 ×™×—×™×“×•×ª",
        "×“×™×™×¡×ª ×ž×˜×¨× ×” ×“×’× ×™× 200 ×’×¨×",
        "×ž×—×™×ª ×ž×˜×¨× ×” ×ª×¤×•×— ×•×‘× × ×”",
        "×ž×˜×¨× ×” ×¤××•×¥ ×¤×™×¨×•×ª",
        "×›×¤×™×ª ×œ×”×›× ×ª ×ª×ž×´×œ",
        "×¦×™×“× ×™×ª ×ž×˜×¨× ×” ×œ×‘×§×‘×•×§",
        "×¤×“×™××©×•×¨ ×¤×•×¨×ž×•×œ×” ×œ×™×œ×“×™×",
        "×¤×™× ×•×§ ×ª×—×œ×™×‘ ×¨×—×¦×” ×©×ž×Ÿ ××¨×’×Ÿ",
        "×©×ž×Ÿ ×¨×—×¦×” ×œ×ž×‘×•×’×¨×™× 500 ×ž×œ",
        "×©×ž×¤×• ×œ×™×œ×“×™× ×¡×¨×§×œ 1 ×œ×™×˜×¨",
    ]
    for name in rejected:
        assert classify_need(name) is None, name

    accepted = {
        "×”××’×™×¡ ×—×™×ª×•×œ×™ ×œ×™×œ×” ×ž×™×“×” 5": "diapers",
        "×¤×ž×¤×¨×¡ ×—×™×ª×•×œ×™ ×©×—×™×™×” ×ž×™×“×” 4": "diapers",
        "×ž×’×‘×•× ×™× ×œ×—×™× ×œ×ª×™× ×•×§ ×œ×œ× ×‘×™×©×•×": "wipes",
        "×ž×’×‘×•× ×™× ×œ×—×™× Huggies Natural Care": "wipes",
        "×ž×˜×¨× ×” ×¦×ž×—×™×ª ×ž×’×™×œ ×©× ×” 700 ×’×¨×": "formula",
        "×ž×–×•×Ÿ ×œ×ª×™× ×•×§×•×ª ×¦×ž×—×™ ×©×œ×‘ 1": "formula",
        "×©×ž×Ÿ ××ž×‘×˜ ××ž×•×œ 500 ×ž×œ": "bath_oil",
    }
    for name, expected in accepted.items():
        assert classify_need(name) == expected, name


def test_verified_product_image_extraction():
    image = "https://images.openfoodfacts.org/images/products/729/front_he.12.400.jpg"
    assert extract_product_image({"image_url": image}) == image
    assert extract_product_image({
        "selected_images": {"front": {"display": {"he": image}}},
    }) == image
    assert extract_product_image({"product": {"images": [{"url": image}]}}) == image
    assert extract_product_image({"image_url": "http://unsafe.example/photo.jpg"}) is None
    assert extract_product_image({"image_url": "javascript:alert(1)"}) is None
    assert extract_product_image({"website": image}) is None


def test_image_enrichment_verifies_exact_barcode():
    class FakeDatabase:
        def __init__(self):
            self.patches = []

        def select(self, table, params):
            if table == "products":
                return [{"preferred_barcode": "7290000000011"}]
            assert table == "baby_product_catalog"
            return [{"barcode": "7290000000011", "need_key": "diapers"}]

        def patch(self, table, filters, row):
            self.patches.append((table, filters, row))

    db = FakeDatabase()
    enricher = ProductImageEnricher(db, limit=1, cheapersal_limit=0)
    enricher._fetch_open_product_image = lambda barcode: (
        "https://images.openfoodfacts.org/images/products/729/front.jpg"
    )
    stats = enricher.enrich_missing_images()
    assert stats["checked"] == 1
    assert stats["saved"] == 1
    assert db.patches[0][1] == {"barcode": "7290000000011"}
    assert db.patches[0][2]["image_source"] == "Open Products Facts Â· verified barcode"


def test_product_photo_is_rejected_when_barcode_does_not_match():
    class FakeResponse:
        status_code = 200
        ok = True

        def __init__(self, actual_barcode):
            self.actual_barcode = actual_barcode

        def json(self):
            return {
                "product": {
                    "code": self.actual_barcode,
                    "image_front_url": "https://images.example.test/baby-product.jpg",
                },
            }

    enricher = ProductImageEnricher(object(), limit=1, cheapersal_limit=0)
    original_get = product_images_module.requests.get
    try:
        product_images_module.requests.get = lambda *args, **kwargs: FakeResponse(
            "7290000000099"
        )
        assert enricher._fetch_open_product_image("7290000000011") is None

        product_images_module.requests.get = lambda *args, **kwargs: FakeResponse(
            "7290000000011"
        )
        assert enricher._fetch_open_product_image("7290000000011") == (
            "https://images.example.test/baby-product.jpg"
        )
    finally:
        product_images_module.requests.get = original_get

def test_expanded_product_package_quantities():
    assert parse_package_quantity("×©×ž×Ÿ ××ž×‘×˜ ×œ×ª×™× ×•×§ 500 ×ž×´×œ", "bath_oil") == (500, "×ž×´×œ")
    assert parse_package_quantity("×’×³×œ ×›×‘×™×¡×” ×œ×ª×™× ×•×§ 1.5 ×œ×™×˜×¨", "baby_laundry") == (1500, "×ž×´×œ")
    assert parse_package_quantity("×ž×©×—×ª ×”×—×ª×œ×” 125 ×’×¨×", "diaper_cream") == (125, "×’×¨×")
    assert parse_package_quantity("×ž×©×˜×—×™ ×”×—×ª×œ×” 10 ×™×—×™×“×•×ª", "changing_pads") == (10, "×™×—×™×“×•×ª")

def test_dimensions():
    assert parse_dimension("×¤×ž×¤×¨×¡ ×ž×™×“×” 4+", "diapers") == ("size", "4+")
    assert parse_dimension("Huggies NB newborn", "diapers") == ("size", "NB")
    assert parse_dimension("×¡×™×ž×™×œ××§ ×’×•×œ×“ ×©×œ×‘ 1", "formula") == ("stage", "1")

def test_quantities():
    assert parse_package_quantity("×ž×’×‘×•× ×™× 4x56", "wipes")[0] == 224
    assert parse_package_quantity("×ª×ž×´×œ 700 ×’×¨×", "formula")[0] == 700

def test_xml_price_and_store():
    price_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Root>
      <StoreId>123</StoreId><SubChainId>005</SubChainId>
      <Items>
        <Item>
          <ItemCode>7290000000001</ItemCode>
          <ItemName>×”××’×™×¡ ×—×™×ª×•×œ×™× ×ž×™×“×” 3 40 ×™×—×™×“×•×ª</ItemName>
          <ManufacturerName>Huggies</ManufacturerName>
          <QtyInPackage>40</QtyInPackage>
          <ItemPrice>39.90</ItemPrice>
        </Item>
        <Item>
          <ItemCode>7290000000002</ItemCode>
          <ItemName>×§×•×œ×” 1.5 ×œ×™×˜×¨</ItemName>
          <ItemPrice>7.90</ItemPrice>
        </Item>
      </Items>
    </Root>""".encode("utf-8")
    rows = parse_price_rows(price_xml, "SHUFERSAL", "PriceFull7290027600007-005-123-202608160800.xml")
    assert len(rows) == 1
    assert rows[0]["branch_code"] == "123"
    assert rows[0]["subchain_id"] == "005"
    assert rows[0]["need_key"] == "diapers"
    assert rows[0]["dimension_value"] == "3"
    assert rows[0]["package_quantity"] == 40

    stores_xml = """<Root><Stores><Store>
      <StoreId>123</StoreId><SubChainId>005</SubChainId><StoreName>BE ×©×“×¨×•×ª</StoreName>
      <Address>×¨×—×•×‘ ×œ×“×•×’×ž×” 1</Address><City>×©×“×¨×•×ª</City>
    </Store></Stores></Root>""".encode("utf-8")
    stores = parse_stores(stores_xml, "SHUFERSAL", "Stores7290027600007-20260816.xml")
    assert stores[0]["branch_code"] == "123"
    assert stores[0]["city"] == "×©×“×¨×•×ª"


def test_xml_imports_expanded_product_categories():
    price_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Root><StoreId>456</StoreId><Items>
      <Item><ItemCode>7290000000011</ItemCode>
        <ItemName>×ž×©×—×ª ×”×—×ª×œ×” ×œ×ª×™× ×•×§ ×¡×•×“×•×§×¨× 125 ×’×¨×</ItemName>
        <ManufacturerName>Sudocrem</ManufacturerName><ItemPrice>24.90</ItemPrice></Item>
      <Item><ItemCode>7290000000012</ItemCode>
        <ItemName>×©×ž×Ÿ ××ž×‘×˜ ×œ×ª×™× ×•×§ ××ž×•×œ 500 ×ž×´×œ</ItemName>
        <ManufacturerName>Emol</ManufacturerName><ItemPrice>34.90</ItemPrice></Item>
      <Item><ItemCode>7290000000013</ItemCode>
        <ItemName>×’×³×œ ×›×‘×™×¡×” ×œ×ª×™× ×•×§ ×¡× ×• ×ž×§×¡×™×ž×” 1 ×œ×™×˜×¨</ItemName>
        <ManufacturerName>Sano</ManufacturerName><ItemPrice>29.90</ItemPrice></Item>
      <Item><ItemCode>7290000000014</ItemCode>
        <ItemName>×ž×©×˜×—×™ ×”×—×ª×œ×” ×—×“ ×¤×¢×ž×™×™× 10 ×™×—×™×“×•×ª</ItemName>
        <ManufacturerName>BabySitter</ManufacturerName><ItemPrice>19.90</ItemPrice></Item>
      <Item><ItemCode>7290000000015</ItemCode>
        <ItemName>×©×ž×¤×• ×¨×’×™×œ ×œ×ž×‘×•×’×¨×™×</ItemName><ItemPrice>12.90</ItemPrice></Item>
    </Items></Root>""".encode("utf-8")
    rows = parse_price_rows(price_xml, "RAMI_LEVY", "PriceFull7290058140886-001-456-202608210800.xml")
    assert {row["need_key"] for row in rows} == {
        "diaper_cream", "bath_oil", "baby_laundry", "changing_pads"
    }
    quantities = {row["need_key"]: row["package_quantity"] for row in rows}
    assert quantities == {
        "diaper_cream": 125, "bath_oil": 500, "baby_laundry": 1000,
        "changing_pads": 10,
    }


def test_catalog_collects_products_from_every_supermarket():
    class FakeDatabase:
        def __init__(self):
            self.saved = []

        def select(self, table, params):
            assert table == "baby_product_catalog"
            return []

        def upsert(self, table, rows, on_conflict):
            assert table == "baby_product_catalog"
            assert on_conflict == "barcode"
            self.saved.extend(rows)

    db = FakeDatabase()
    rows = [
        {"chain_id": "rami_levy", "need_key": "diaper_cream", "barcode": "7290000000011",
         "product_name": "×ž×©×—×ª ×”×—×ª×œ×” ×¡×•×“×•×§×¨× 125 ×’×¨×", "brand": "Sudocrem",
         "package_quantity": 125, "package_unit": "×’×¨×"},
        {"chain_id": "shufersal", "need_key": "baby_wash", "barcode": "7290000000012",
         "product_name": "×¡×‘×•×Ÿ ×œ×ª×™× ×•×§ ×“×•×§×˜×•×¨ ×¤×™×©×¨ 700 ×ž×´×œ", "brand": "Dr. Fischer",
         "package_quantity": 700, "package_unit": "×ž×´×œ"},
        {"chain_id": "osher_ad", "need_key": "baby_laundry", "barcode": "7290000000013",
         "product_name": "×’×³×œ ×›×‘×™×¡×” ×œ×ª×™× ×•×§ ×¡× ×• 1 ×œ×™×˜×¨", "brand": "Sano",
         "package_quantity": 1000, "package_unit": "×ž×´×œ"},
    ]
    stats = save_official_catalog_rows(db, rows)
    assert stats == {
        "candidates": 3, "already_known": 0, "saved": 3, "images_saved": 0,
    }
    assert {row["need_key"] for row in db.saved} == {
        "diaper_cream", "baby_wash", "baby_laundry"
    }
    assert all(row["source_name"].endswith("transparency") for row in db.saved)


def test_zero_source_numbers_are_safely_sanitized():
    base = {
        "source_name": "RAMI_LEVY", "subchain_id": None, "branch_code": "003",
        "barcode": "8435495819363", "need_key": "baby_laundry",
        "dimension_type": "none", "dimension_value": None, "brand": "Test",
        "product_name": "×—×•×ž×¨ ×›×‘×™×¡×” ×œ×ª×™× ×•×§", "package_quantity": 0,
        "package_unit": "×™×—×™×“×•×ª", "regular_price": 17.9,
        "promo_price": 0, "promo_min_quantity": 0, "promo_total_price": 0,
        "requires_club": False, "source_updated_at": None, "raw_source": {},
    }
    row = to_db_price(base)
    assert row["package_quantity"] is None
    assert row["promo_price"] is None
    assert row["promo_min_quantity"] is None
    assert row["promo_total_price"] is None

    merged = merge_prices_and_promos([base], [{
        "source_name": "RAMI_LEVY", "branch_code": "003",
        "barcode": "8435495819363", "promo_price": 0,
        "promo_min_quantity": 0, "promo_total_price": 0,
        "promo_description": "×¨×©×•×ž×ª ××¤×¡", "requires_club": False,
    }])
    assert merged[0]["promo_price"] is None
    assert merged[0]["promo_min_quantity"] == 1
    assert merged[0]["promo_total_price"] is None


def test_multi_buy_promotion_terms():
    promo_xml = """<Root><StoreId>123</StoreId><Promotions><Promotion>
      <PromotionDescription>2 ××¨×™×–×•×ª ×‘-70</PromotionDescription>
      <PromotionTotalPrice>70</PromotionTotalPrice><MinQty>2</MinQty>
      <PromotionStartDate>2026-08-01</PromotionStartDate>
      <PromotionEndDate>2026-08-31</PromotionEndDate>
      <RequiresClub>false</RequiresClub>
      <PromotionItems><Item><ItemCode>7290000000001</ItemCode></Item></PromotionItems>
    </Promotion></Promotions></Root>""".encode("utf-8")
    rows = parse_promotions(
        promo_xml,
        "SHUFERSAL",
        "PromoFull7290027600007-002-123-202608190800.xml",
    )
    assert len(rows) == 1
    assert rows[0]["promo_price"] == 35
    assert rows[0]["promo_total_price"] == 70
    assert rows[0]["promo_min_quantity"] == 2
    assert rows[0]["requires_club"] is False


def test_ksp_official_product_parser():
    page = """<!doctype html><html><head>
      <meta property="product:price:amount" content="39.90">
      <script type="application/ld+json">{
        "@context":"https://schema.org","@type":"Product",
        "name":"Pampers ×—×™×ª×•×œ×™× ×ž×™×“×” 4 44 ×™×—×™×“×•×ª",
        "gtin13":"8700216596701","brand":{"name":"Pampers"},
        "regularPrice":"54.90",
        "offers":{"@type":"Offer","price":"39.90","priceValidUntil":"2026-08-31"}
      }</script>
    </head><body></body></html>"""
    parsed = parse_ksp_product_html(
        page,
        "https://ksp.co.il/mob/item/440079",
        expected_need="diapers",
        fetched_at="2026-08-19T12:00:00+00:00",
    )
    assert parsed is not None
    assert parsed["price_row"]["barcode"] == "8700216596701"
    assert parsed["price_row"]["branch_code"] == "online"
    assert parsed["price_row"]["regular_price"] == 54.9
    assert parsed["promo_row"]["promo_price"] == 39.9


def test_ksp_print_page_fallback_parser():
    page = """<html><body>
      ×©× ×”×ž×•×¦×¨: Pampers ×—×™×ª×•×œ×™× ×ž×™×“×” 4 44 ×™×—×™×“×•×ª
      ×ž×¡×¤×¨ ×ž×•×¦×¨: 440079 ×ª××¨×™×š ×ª×•×§×£: 20-08-2026
      ×ž×—×™×¨ ××©×¨××™: 39.90 â‚ª ×‘×¨×§×•×“: 8700216596701
    </body></html>"""
    parsed = parse_ksp_product_html(
        page,
        "https://ksp.co.il/mob/item/440079",
        expected_need="diapers",
        fetched_at="2026-08-20T12:00:00+00:00",
    )
    assert parsed is not None
    assert parsed["price_row"]["regular_price"] == 39.9
    assert parsed["price_row"]["barcode"] == "8700216596701"


def test_ksp_json_api_barcode_search_parser():
    payload = {
        "result": {
            "name": "Pampers ×—×™×ª×•×œ×™× ×ž×™×“×” 5 37 ×™×—×™×“×•×ª",
            "uin": "440079",
            "price": 54.9,
            "min_price": 44.9,
            "brandName": "Pampers",
            "labels": [{"msg": "×ž×—×™×¨ ×œ×—×‘×¨×™ ×ž×•×¢×“×•×Ÿ"}],
        }
    }
    parsed = parse_ksp_api_product(
        payload,
        expected_barcode="8700216596701",
        expected_need="diapers",
        fetched_at="2026-08-20T12:00:00+00:00",
    )
    assert parsed is not None
    assert parsed["price_row"]["barcode"] == "8700216596701"
    assert parsed["price_row"]["regular_price"] == 54.9
    assert parsed["promo_row"]["promo_price"] == 44.9
    assert parsed["promo_row"]["requires_club"] is True


def test_ksp_json_api_detail_finds_barcode_in_specification():
    payload = {
        "result": {
            "data": {
                "name": "×ž×’×‘×•× ×™× ×œ×—×™× ×œ×ª×™× ×•×§ ×œ×œ× ×‘×™×©×•× 4 ×™×—×™×“×•×ª",
                "price": 24.9,
                "brandName": "Huggies",
            },
            "specification": [
                {"name": "×‘×¨×§×•×“", "value": "7290000195537"},
            ],
        }
    }
    parsed = parse_ksp_api_product(payload, item_id="70286", expected_need="wipes")
    assert parsed is not None
    assert parsed["price_row"]["barcode"] == "7290000195537"
    assert parsed["price_row"]["regular_price"] == 24.9
    assert parsed["promo_row"] is None


def test_ksp_json_api_accepts_expanded_baby_categories():
    payload = {
        "result": {
            "name": "×¡×‘×•×Ÿ ×•×©×ž×¤×• ×œ×ª×™× ×•×§ 500 ×ž×´×œ",
            "uin": "990001",
            "price": 22.9,
            "brandName": "Dr. Fischer",
        }
    }
    parsed = parse_ksp_api_product(
        payload,
        expected_barcode="7290000000201",
        expected_need="baby_wash",
    )
    assert parsed is not None
    assert parsed["price_row"]["need_key"] == "baby_wash"
    assert parsed["price_row"]["barcode"] == "7290000000201"


def test_ksp_relay_routes_and_authenticates_request():
    class Response:
        ok = True
        status_code = 200
        url = "https://relay.example/ksp/category?search=7290000191225"
        text = '{"result":{"items":[]}}'
        headers = {"Content-Type": "application/json"}

        def json(self):
            return {"result": {"items": []}}

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    session = Session()
    payload = _get_json(
        session,
        "category/",
        params={"search": "7290000191225"},
        relay_url="https://relay.example/",
        relay_token="secret-token",
    )
    assert payload == {"result": {"items": []}}
    assert session.calls[0][0] == "https://relay.example/ksp/category"
    assert session.calls[0][1]["headers"]["Authorization"] == "Bearer secret-token"
    assert session.calls[0][1]["params"] == {"search": "7290000191225"}


def test_superpharm_promo_filename_and_merge():
    filename = "Promo7290172900007-000-123-20260819-120000.gz"
    links = _sp_candidates_from_html(f'<a href="Download/{filename}">{filename}</a>')
    assert len(links) == 1
    assert links[0]["kind"] == "promo"
    prices = [{
        "source_name": "SUPER_PHARM", "branch_code": "123", "barcode": "7290000000001",
        "need_key": "diapers", "dimension_type": "size", "dimension_value": "3",
        "regular_price": 50, "source_updated_at": "2026-08-19T10:00:00+00:00",
    }]
    promos = [{
        "source_name": "SUPER_PHARM", "branch_code": "123", "barcode": "7290000000001",
        "promo_price": 40, "promo_description": "×ž×‘×¦×¢", "promo_start_at": "2026-08-01",
        "promo_end_at": "2099-08-31", "promo_min_quantity": 1,
        "promo_total_price": 40, "requires_club": True,
    }]
    merged = merge_prices_and_promos(prices, promos)
    assert merged[0]["effective_price"] == 40
    assert merged[0]["requires_club"] is True


def test_superpharm_online_product_and_promo_parser():
    page = """<html><head>
      <meta property="og:title" content="×”××’×™×¡ - ×ž×’×‘×•× ×™× ×œ×—×™× ×œ×ª×™× ×•×§ ×œ×œ× ×‘×™×©×•×">
      <script type="application/ld+json">{
        "@type":"Product", "name":"×ž×’×‘×•× ×™× ×œ×—×™× ×œ×ª×™× ×•×§ ×œ×œ× ×‘×™×©×•× ×ž××¨×– ×¨×‘×™×¢×™×™×”",
        "gtin13":"7290000195537", "brand":{"name":"×”××’×™×¡"},
        "regularPrice":"28.90", "offers":{"price":"17.90"}
      }</script>
    </head><body>
      <h1>×ž×’×‘×•× ×™× ×œ×—×™× ×œ×ª×™× ×•×§ ×œ×œ× ×‘×™×©×•× ×ž××¨×– ×¨×‘×™×¢×™×™×”</h1>
      <div>28.90</div><div>17.90</div><div>×”×ž×—×™×¨ ×‘×ª×•×§×£ ×¢×“ 25.08.2026</div>
      <div>×‘×¨×§×•×“ ×ž×•×¦×¨: 7290000195537</div>
    </body></html>"""
    parsed = parse_superpharm_product_html(
        page,
        "https://shop.super-pharm.co.il/example/p/263254",
        expected_need="wipes",
        fetched_at="2026-08-20T12:00:00+00:00",
    )
    assert parsed is not None
    assert parsed["price_row"]["regular_price"] == 28.9
    assert parsed["promo_row"]["promo_price"] == 17.9
    assert parsed["promo_row"]["promo_end_at"] == "2026-08-25T23:59:59+00:00"


def test_superpharm_online_multi_buy_parser():
    page = """<html><head><script type="application/ld+json">{
      "@type":"Product", "name":"×¤×ž×¤×¨×¡ ×—×™×ª×•×œ×™× ×ž×™×“×” 2 39 ×™×—×™×“×•×ª",
      "gtin13":"8006540156551", "brand":{"name":"×¤×ž×¤×¨×¡"},
      "offers":{"price":"57.90"}
    }</script></head><body>
      <h1>×¤×ž×¤×¨×¡ ×—×™×ª×•×œ×™× ×ž×™×“×” 2 39 ×™×—×™×“×•×ª</h1>
      <div>57.90</div><div>×ž×—×™×¨ ×œ-2 ×™×—×™×“×•×ª 85 â‚ª</div>
      <div>×‘×¨×§×•×“ ×ž×•×¦×¨: 8006540156551</div>
    </body></html>"""
    parsed = parse_superpharm_product_html(
        page,
        "https://shop.super-pharm.co.il/example/p/625472",
        expected_need="diapers",
    )
    assert parsed is not None
    assert parsed["promo_row"]["promo_price"] == 42.5
    assert parsed["promo_row"]["promo_min_quantity"] == 2
    assert parsed["promo_row"]["promo_total_price"] == 85


def test_superpharm_category_product_url_extraction():
    page = """<a href="/infants/diapers/item-name/p/625472?source=grid">×ž×•×¦×¨</a>
    <script>{"url":"/infants/wipes/other/p/263254"}</script>"""
    urls = extract_superpharm_product_urls(page)
    assert urls == [
        "https://shop.super-pharm.co.il/infants/diapers/item-name/p/625472",
        "https://shop.super-pharm.co.il/infants/wipes/other/p/263254",
    ]


def test_superpharm_discovers_all_supported_navigation_categories():
    page = """
      <a href="/baby/changing-pads/c/100">×ž×©×˜×—×™ ×”×—×ª×œ×” ×•×›×™×¡×•×™×™×</a>
      <a href="/baby/diaper-cream/c/101">×ž×©×—×ª ×”×—×ª×œ×” ×œ×ª×™× ×•×§</a>
      <a href="/baby/bath-oil/c/102">×©×ž×Ÿ ×¨×—×¦×” ×œ×ª×™× ×•×§</a>
      <a href="/baby/body-cream/c/103">×§×¨× ×’×•×£ ×œ×ª×™× ×•×§</a>
      <a href="/adult/body-cream/c/104">×§×¨× ×’×•×£ ×œ×ž×‘×•×’×¨×™×</a>
    """
    found = extract_superpharm_category_urls(page)
    assert {need for need, _ in found} == {
        "changing_pads", "diaper_cream", "bath_oil", "body_cream",
    }


def test_catalog_batch_interleaves_every_product_type():
    rows = []
    for index, need_key in enumerate((
        "diapers", "wipes", "diaper_cream", "changing_pads", "diaper_bags",
        "formula", "baby_wash", "bath_oil", "body_cream", "baby_laundry",
    ), start=1):
        rows.append({"barcode": f"7290000000{index:03d}", "need_key": need_key})
    ordered = _interleave_catalog_targets(rows)
    assert len(ordered) == 10
    assert {row["need_key"] for row in ordered} == {row["need_key"] for row in rows}


def test_special_retailer_cursor_advances_only_after_complete_lookup():
    import os

    class FakeDatabase:
        def __init__(self, runs=None):
            self.runs = runs or []

        def select(self, table, params):
            assert table == "feed_ingestion_runs"
            return self.runs

    rows = [
        {"barcode": f"729000000{i:04d}", "need_key": "wipes"}
        for i in range(55)
    ]
    selected, planned, _ = _special_retailer_batch(FakeDatabase(), rows)
    assert len(selected) == 48
    assert planned["mode"] == "bootstrap"
    assert planned["due"] is True

    incomplete = _special_batch_with_lookup_result(
        planned, {"attempted": 48, "shared_cache_entries": 47},
    )
    assert incomplete["committed"] is False
    assert incomplete["next_cursor"] == 0

    complete = _special_batch_with_lookup_result(
        planned, {"attempted": 48, "shared_cache_entries": 48},
    )
    assert complete["committed"] is True
    assert complete["next_cursor"] == 48

    first_chain_only = _special_batch_with_lookup_result(
        planned,
        {"attempted": 48, "shared_cache_entries": 48},
        allow_commit=False,
    )
    assert first_chain_only["provider_complete"] is True
    assert first_chain_only["committed"] is False
    assert first_chain_only["next_cursor"] == 0

    last_run = {
        "finished_at": "2999-01-01T00:00:00+00:00",
        "details": {"special_retailer_batch": {
            **complete,
            "next_cursor": 0,
            "cycles_completed": 1,
            "wrapped": True,
        }},
    }
    original_event = os.environ.get("GITHUB_EVENT_NAME")
    original_force = os.environ.get("SPECIAL_RETAILER_FORCE_RUN")
    try:
        # The test must not inherit workflow_dispatch from the GitHub runner.
        os.environ["GITHUB_EVENT_NAME"] = "schedule"
        os.environ.pop("SPECIAL_RETAILER_FORCE_RUN", None)
        _, maintenance, _ = _special_retailer_batch(FakeDatabase([last_run]), rows)
        assert maintenance["mode"] == "maintenance"
        assert maintenance["due"] is False

        # A real manual workflow run must still override the maintenance wait.
        os.environ["GITHUB_EVENT_NAME"] = "workflow_dispatch"
        _, forced, _ = _special_retailer_batch(FakeDatabase([last_run]), rows)
        assert forced["due"] is True
        assert forced["forced"] is True
    finally:
        if original_event is None:
            os.environ.pop("GITHUB_EVENT_NAME", None)
        else:
            os.environ["GITHUB_EVENT_NAME"] = original_event
        if original_force is None:
            os.environ.pop("SPECIAL_RETAILER_FORCE_RUN", None)
        else:
            os.environ["SPECIAL_RETAILER_FORCE_RUN"] = original_force


def test_strict_source_outcome_is_not_false_success():
    blocked_data = {
        "files_seen": 0,
        "errors": ["blocked HTTP 403 from retailer"],
    }
    assert assess_ingestion_outcome("KSP", blocked_data, 0)[0] == "failed"
    assert assess_ingestion_outcome("SUPER_PHARM", {"files_seen": 1}, 0)[0] == "partial"
    assert assess_ingestion_outcome("KSP", blocked_data, 1) == ("success", None)


def test_cheapersal_price_fallback_normalizes_matching_chain():
    payload = {
        "success": True,
        "data": {
            "product": {
                "barcode": "7290000195537",
                "name": "×ž×’×‘×•× ×™× ×œ×—×™× ×œ×ª×™× ×•×§ ×œ×œ× ×‘×™×©×•× ×ž××¨×– ×¨×‘×™×¢×™×™×”",
                "manufacturer": "×”××’×™×¡",
                "unitQty": "4x56",
            },
            "prices": [
                {
                    "price": 28.90,
                    "chain": {"id": "sp", "name": "×¡×•×¤×¨ ×¤××¨×"},
                    "branch": {
                        "id": "online",
                        "name": "×¡×•×¤×¨ ×¤××¨× ××•× ×œ×™×™×Ÿ",
                        "isOnline": True,
                    },
                    "promo": {
                        "promoPrice": 17.90,
                        "description": "×ž×‘×¦×¢",
                        "validUntil": "2026-08-31T00:00:00Z",
                        "requiresClub": True,
                    },
                },
                {
                    "price": 30.90,
                    "chain": {"id": "other", "name": "×¨×©×ª ××—×¨×ª"},
                    "branch": {"id": "1", "name": "××—×¨"},
                },
            ],
        },
    }

    class Response:
        status_code = 200
        ok = True
        text = "{}"

        def json(self):
            return payload

    def fake_get(*args, **kwargs):
        return Response()

    import baby_worker.cheapersal_prices as cheapersal_prices

    original_requests = cheapersal_prices.requests
    cheapersal_prices.requests = type("RequestsMock", (), {"get": staticmethod(fake_get)})
    try:
        fallback = CheaperSalPriceFallback("csal_test", limit=1)
        source_pass, stats = fallback.collect(
            "SUPER_PHARM",
            [{"barcode": "7290000195537", "need_key": "wipes"}],
        )
        assert stats["baby_prices"] == 1
        assert source_pass["price_rows"][0]["source_name"] == "SUPER_PHARM"
        assert source_pass["price_rows"][0]["promo_price"] == 17.9
        assert source_pass["store_rows"][0]["branch_code"] == "online"
    finally:
        cheapersal_prices.requests = original_requests


def test_cheapersal_shared_lookup_fetches_once_for_both_retailers():
    payload = {
        "success": True,
        "data": {
            "product": {
                "barcode": "7290000000101",
                "name": "×¡×‘×•×Ÿ ×¨×—×¦×” ×œ×ª×™× ×•×§ 500 ×ž×´×œ",
                "manufacturer": "Dr. Fischer",
            },
            "prices": [
                {
                    "price": 22.9,
                    "chain": {"name": "×¡×•×¤×¨ ×¤××¨×"},
                    "branch": {"id": "sp-1", "name": "×¡×•×¤×¨ ×¤××¨× ×©×“×¨×•×ª", "city": "×©×“×¨×•×ª"},
                },
                {
                    "price": 19.9,
                    "chain": {"name": "KSP"},
                    "branch": {"id": "online", "name": "KSP ××•× ×œ×™×™×Ÿ", "isOnline": True},
                },
            ],
        },
    }
    calls = []

    class Response:
        status_code = 200
        ok = True
        text = "{}"
        headers = {}

        def json(self):
            return payload

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    import baby_worker.cheapersal_prices as cheapersal_prices

    original_requests = cheapersal_prices.requests
    cheapersal_prices.requests = type("RequestsMock", (), {"get": staticmethod(fake_get)})
    try:
        client = CheaperSalPriceClient(
            "csal_test", limit=1, online=False, minimum_interval=0,
        )
        target = [{"barcode": "7290000000101", "need_key": "baby_wash"}]
        sp_pass, sp_stats = CheaperSalPriceFallback(
            "csal_test", client=client,
        ).collect("SUPER_PHARM", target)
        ksp_pass, ksp_stats = CheaperSalPriceFallback(
            "csal_test", client=client,
        ).collect("KSP", target)
        assert len(calls) == 1
        assert len(sp_pass["price_rows"]) == 1
        assert len(ksp_pass["price_rows"]) == 1
        assert sp_stats["network_requests"] == 1
        assert ksp_stats["network_requests"] == 0
        assert ksp_stats["cache_hits"] == 1
    finally:
        cheapersal_prices.requests = original_requests


def test_cheapersal_timeout_consumes_shared_budget():
    import baby_worker.cheapersal_prices as cheapersal_prices

    calls = []

    def failing_get(*args, **kwargs):
        calls.append((args, kwargs))
        raise TimeoutError("simulated provider timeout")

    original_requests = cheapersal_prices.requests
    cheapersal_prices.requests = type(
        "RequestsMock", (), {"get": staticmethod(failing_get)}
    )
    try:
        client = CheaperSalPriceClient(
            "csal_test", limit=1, online=False, minimum_interval=0,
        )
        target = [
            {"barcode": "7290000000201", "need_key": "baby_wash"},
            {"barcode": "7290000000202", "need_key": "bath_oil"},
        ]
        _, stats = CheaperSalPriceFallback(
            "csal_test", client=client,
        ).collect("KSP", target)
        assert len(calls) == 1
        assert client.remaining == 0
        assert stats["network_requests"] == 1
        assert stats["attempted"] == 1
    finally:
        cheapersal_prices.requests = original_requests


def test_partial_official_ksp_result_still_runs_catalog_completion():
    import asyncio
    import baby_worker.worker as worker

    def source_pass(name, barcode):
        return {
            "pass_name": name,
            "file_types": ["TEST"],
            "limit": 1,
            "files_seen": 1,
            "price_rows": [{"barcode": barcode}],
            "promo_rows": [],
            "store_rows": [],
            "errors": [],
            "kind_counts": {"test": 1},
            "sample_files": [barcode],
            "price_schema_diagnostics": [],
        }

    calls = []

    class FakeFallback:
        def __init__(self, *args, **kwargs):
            pass

        def collect(self, requested_chain, targets):
            calls.append((requested_chain, targets))
            return source_pass("catalog_completion", "7290000000202"), {
                "fallback_used": True,
                "attempted": len(targets),
            }

    original_official = worker.collect_ksp_official
    original_fallback = worker.CheaperSalPriceFallback
    worker.collect_ksp_official = lambda **kwargs: (
        [source_pass("official", "7290000000201")], {}
    )
    worker.CheaperSalPriceFallback = FakeFallback
    try:
        result = asyncio.run(worker.collect_source(
            "KSP",
            10,
            {
                "ksp_catalog_targets": [
                    {"barcode": "7290000000202", "need_key": "bath_oil"},
                ],
                "ksp_known_items": [],
                "special_retailer_batch": {"version": "v049"},
            },
            object(),
        ))
        assert calls == [("KSP", [
            {"barcode": "7290000000202", "need_key": "bath_oil"},
        ])]
        assert {row["barcode"] for row in result["price_rows"]} == {
            "7290000000201", "7290000000202",
        }
    finally:
        worker.collect_ksp_official = original_official
        worker.CheaperSalPriceFallback = original_fallback
