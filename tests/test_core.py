from baby_worker.classifier import classify_need, parse_dimension, parse_package_quantity
from baby_worker.cheapersal_prices import CheaperSalPriceFallback
from baby_worker.ksp import _get_json, parse_ksp_api_product, parse_ksp_product_html
from baby_worker.superpharm_online import (
    extract_superpharm_product_urls,
    parse_superpharm_product_html,
)
from baby_worker.worker import (
    _sp_candidates_from_html,
    assess_ingestion_outcome,
    merge_prices_and_promos,
)
from baby_worker.xmlfeeds import parse_price_rows, parse_promotions, parse_stores

def test_classifier():
    assert classify_need("האגיס אקסטרה קר חיתולים מידה 3 40 יחידות") == "diapers"
    assert classify_need("מגבונים לחים האגיס 4x56") == "wipes"
    assert classify_need("מטרנה חלבי שלב 2 700 גרם") == "formula"
    assert classify_need("שקיות לחיתולים מלוכלכים") is None

def test_dimensions():
    assert parse_dimension("פמפרס מידה 4+", "diapers") == ("size", "4+")
    assert parse_dimension("Huggies NB newborn", "diapers") == ("size", "NB")
    assert parse_dimension("סימילאק גולד שלב 1", "formula") == ("stage", "1")

def test_quantities():
    assert parse_package_quantity("מגבונים 4x56", "wipes")[0] == 224
    assert parse_package_quantity("תמ״ל 700 גרם", "formula")[0] == 700

def test_xml_price_and_store():
    price_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Root>
      <StoreId>123</StoreId><SubChainId>005</SubChainId>
      <Items>
        <Item>
          <ItemCode>7290000000001</ItemCode>
          <ItemName>האגיס חיתולים מידה 3 40 יחידות</ItemName>
          <ManufacturerName>Huggies</ManufacturerName>
          <QtyInPackage>40</QtyInPackage>
          <ItemPrice>39.90</ItemPrice>
        </Item>
        <Item>
          <ItemCode>7290000000002</ItemCode>
          <ItemName>קולה 1.5 ליטר</ItemName>
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
      <StoreId>123</StoreId><SubChainId>005</SubChainId><StoreName>BE שדרות</StoreName>
      <Address>רחוב לדוגמה 1</Address><City>שדרות</City>
    </Store></Stores></Root>""".encode("utf-8")
    stores = parse_stores(stores_xml, "SHUFERSAL", "Stores7290027600007-20260816.xml")
    assert stores[0]["branch_code"] == "123"
    assert stores[0]["city"] == "שדרות"


def test_multi_buy_promotion_terms():
    promo_xml = """<Root><StoreId>123</StoreId><Promotions><Promotion>
      <PromotionDescription>2 אריזות ב-70</PromotionDescription>
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
        "name":"Pampers חיתולים מידה 4 44 יחידות",
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
      שם המוצר: Pampers חיתולים מידה 4 44 יחידות
      מספר מוצר: 440079 תאריך תוקף: 20-08-2026
      מחיר אשראי: 39.90 ₪ ברקוד: 8700216596701
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
            "name": "Pampers חיתולים מידה 5 37 יחידות",
            "uin": "440079",
            "price": 54.9,
            "min_price": 44.9,
            "brandName": "Pampers",
            "labels": [{"msg": "מחיר לחברי מועדון"}],
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
                "name": "מגבונים לחים לתינוק ללא בישום 4 יחידות",
                "price": 24.9,
                "brandName": "Huggies",
            },
            "specification": [
                {"name": "ברקוד", "value": "7290000195537"},
            ],
        }
    }
    parsed = parse_ksp_api_product(payload, item_id="70286", expected_need="wipes")
    assert parsed is not None
    assert parsed["price_row"]["barcode"] == "7290000195537"
    assert parsed["price_row"]["regular_price"] == 24.9
    assert parsed["promo_row"] is None


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
        "promo_price": 40, "promo_description": "מבצע", "promo_start_at": "2026-08-01",
        "promo_end_at": "2099-08-31", "promo_min_quantity": 1,
        "promo_total_price": 40, "requires_club": True,
    }]
    merged = merge_prices_and_promos(prices, promos)
    assert merged[0]["effective_price"] == 40
    assert merged[0]["requires_club"] is True


def test_superpharm_online_product_and_promo_parser():
    page = """<html><head>
      <meta property="og:title" content="האגיס - מגבונים לחים לתינוק ללא בישום">
      <script type="application/ld+json">{
        "@type":"Product", "name":"מגבונים לחים לתינוק ללא בישום מארז רביעייה",
        "gtin13":"7290000195537", "brand":{"name":"האגיס"},
        "regularPrice":"28.90", "offers":{"price":"17.90"}
      }</script>
    </head><body>
      <h1>מגבונים לחים לתינוק ללא בישום מארז רביעייה</h1>
      <div>28.90</div><div>17.90</div><div>המחיר בתוקף עד 25.08.2026</div>
      <div>ברקוד מוצר: 7290000195537</div>
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
      "@type":"Product", "name":"פמפרס חיתולים מידה 2 39 יחידות",
      "gtin13":"8006540156551", "brand":{"name":"פמפרס"},
      "offers":{"price":"57.90"}
    }</script></head><body>
      <h1>פמפרס חיתולים מידה 2 39 יחידות</h1>
      <div>57.90</div><div>מחיר ל-2 יחידות 85 ₪</div>
      <div>ברקוד מוצר: 8006540156551</div>
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
    page = """<a href="/infants/diapers/item-name/p/625472?source=grid">מוצר</a>
    <script>{"url":"/infants/wipes/other/p/263254"}</script>"""
    urls = extract_superpharm_product_urls(page)
    assert urls == [
        "https://shop.super-pharm.co.il/infants/diapers/item-name/p/625472",
        "https://shop.super-pharm.co.il/infants/wipes/other/p/263254",
    ]


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
                "name": "מגבונים לחים לתינוק ללא בישום מארז רביעייה",
                "manufacturer": "האגיס",
                "unitQty": "4x56",
            },
            "prices": [
                {
                    "price": 28.90,
                    "chain": {"id": "sp", "name": "סופר פארם"},
                    "branch": {
                        "id": "online",
                        "name": "סופר פארם אונליין",
                        "isOnline": True,
                    },
                    "promo": {
                        "promoPrice": 17.90,
                        "description": "מבצע",
                        "validUntil": "2026-08-31T00:00:00Z",
                        "requiresClub": True,
                    },
                },
                {
                    "price": 30.90,
                    "chain": {"id": "other", "name": "רשת אחרת"},
                    "branch": {"id": "1", "name": "אחר"},
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
