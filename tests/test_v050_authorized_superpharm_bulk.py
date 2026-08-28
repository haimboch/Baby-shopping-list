"""Offline regression coverage for the authorized v0.50 Super-Pharm importer."""

from __future__ import annotations

import os
import asyncio
from pathlib import Path

import baby_worker.cheapersal_prices as price_module
from baby_worker.cheapersal_bulk import (
    SUPER_PHARM_ONLINE_PROVIDER_ID,
    SuperPharmBulkImporter,
    branch_from_provider,
    discover_baby_categories,
    product_from_provider,
)
from baby_worker.cheapersal_prices import CheaperSalPriceClient
from baby_worker.product_images import extract_product_image
from baby_worker.worker import (
    _special_batch_with_bulk_result,
    merge_prices_and_promos,
    run_chain,
    to_db_branch,
    to_db_price,
)


ROOT = Path(__file__).resolve().parents[1]


def test_v050_discovers_nested_baby_categories_without_adult_categories():
    payload = [
        {
            "name": "מוצרי תינוקות",
            "slug": "baby",
            "children": [
                {"name": "חיתולים", "slug": "diapers"},
                {"name": "רחצה", "slug": "baby-wash"},
            ],
        },
        {"name": "ניקיון הבית", "slug": "cleaning"},
        {"name": "מוצרי חלב", "slug": "dairy"},
    ]
    assert discover_baby_categories(payload) == ["baby", "diapers", "baby-wash"]


def test_v050_bulk_parser_only_accepts_verified_baby_products():
    diapers = product_from_provider({
        "barcode": "7290000195537",
        "name": "האגיס חיתולים מידה 3 40 יחידות",
        "brand": "האגיס",
        "price": 44.9,
        "packageQuantity": 40,
        "image": "https://cdn.example.test/products/7290000195537.jpg",
    }, category_slug="baby")
    assert diapers is not None
    assert diapers["need_key"] == "diapers"
    assert diapers["dimension_value"] == "3"
    assert diapers["package_quantity"] == 40
    assert diapers["branch_code"] == "online"
    assert diapers["raw_source"]["price_scope"] == "online_only"
    assert diapers["raw_source"]["in_store_stock_verified"] is False
    assert extract_product_image(diapers["raw_source"]).endswith("7290000195537.jpg")

    assert product_from_provider({
        "barcode": "7290000195544",
        "name": "חיתולים למבוגרים מידה L",
        "price": 39,
    }) is None
    assert product_from_provider({
        "barcode": "7290000195551",
        "name": "מגבונים לניקוי הרצפה",
        "price": 12,
    }) is None


def test_v050_branch_import_preserves_provider_id_and_coordinates():
    branch = branch_from_provider({
        "id": "5f186c4390ae893cc6e8645b",
        "storeId": "042",
        "name": "סופר-פארם שדרות",
        "city": "שדרות",
        "address": "סמטת הפלדה 1",
        "location": {"lat": 31.525, "lon": 34.595},
    })
    assert branch is not None
    assert branch["branch_code"] == "5f186c4390ae893cc6e8645b"
    normalized = to_db_branch(branch)
    assert normalized["latitude"] == 31.525
    assert normalized["longitude"] == 34.595

    without_location = to_db_branch({
        "source_name": "SUPER_PHARM",
        "branch_code": "another",
        "branch_name": "סופר-פארם",
        "city": None,
        "address": None,
    })
    assert "latitude" not in without_location
    assert "longitude" not in without_location

    online = branch_from_provider({
        "id": SUPER_PHARM_ONLINE_PROVIDER_ID,
        "name": "סופר-פארם באינטרנט",
        "isOnline": True,
        "location": {"lat": 32.15, "lon": 34.83},
    })
    assert online is not None
    assert online["branch_code"] == "online"
    assert "latitude" not in online


def test_v050_bulk_import_collects_302_branches_and_200_products_per_call():
    calls: list[tuple[str, dict]] = []
    physical = [
        {
            "id": f"super-pharm-{index:03d}",
            "storeId": str(index),
            "name": "סופר-פארם שדרות" if index == 0 else f"סופר-פארם {index}",
            "city": "שדרות" if index == 0 else "תל אביב",
            "address": f"רחוב {index}",
            "location": {"lat": 31.525, "lon": 34.595},
            "isOnline": False,
        }
        for index in range(301)
    ]
    branch_rows = physical + [{
        "id": SUPER_PHARM_ONLINE_PROVIDER_ID,
        "name": "סופר-פארם באינטרנט",
        "isOnline": True,
    }]
    baby_rows = [
        {
            "barcode": str(7290000000000 + index),
            "name": f"האגיס חיתולים מידה 3 {40 + index} יחידות",
            "brand": "האגיס",
            "price": 44.9 + index / 100,
        }
        for index in range(200)
    ]
    baby_rows[199] = {
        "barcode": "7290000099999",
        "name": "מגבונים לניקוי הרצפה",
        "price": 12,
    }

    class Response:
        status_code = 200
        ok = True
        text = "{}"
        headers = {}

        def __init__(self, data):
            self.data = data

        def json(self):
            return {
                "success": True,
                "data": self.data,
                "meta": {"usage": {"remaining": 100 - len(calls)}},
            }

    def fake_get(url, **kwargs):
        params = kwargs.get("params") or {}
        calls.append((url, params))
        if url.endswith("/chains"):
            return Response([{"id": "chain-sp", "name": "סופר-פארם"}])
        if url.endswith("/chains/chain-sp/branches"):
            skip = int(params["skip"])
            return Response({
                "branches": branch_rows[skip:skip + 200],
                "total": len(branch_rows),
                "skip": skip,
                "limit": 200,
            })
        if url.endswith("/categories"):
            return Response([{"name": "תינוקות", "slug": "baby"}])
        if url.endswith(f"/branches/{SUPER_PHARM_ONLINE_PROVIDER_ID}/products"):
            assert int(params["limit"]) == 200
            skip = int(params["skip"])
            extra = [{
                "barcode": "7290000888881",
                "name": "מטרנה חלבי שלב 2 700 גרם",
                "brand": "מטרנה",
                "price": 69.9,
            }]
            all_rows = baby_rows + extra
            return Response({
                "products": all_rows[skip:skip + 200],
                "total": len(all_rows),
                "skip": skip,
                "limit": 200,
            })
        if url.endswith("/promos"):
            return Response({"promos": [{
                "itemCode": baby_rows[0]["barcode"],
                "productName": "האגיס חיתולים במבצע",
                "price": 35.9,
                "originalPrice": 44.9,
                "minQty": 2,
            }], "total": 1})
        raise AssertionError(f"Unexpected authorized API endpoint: {url}")

    original_requests = price_module.requests
    price_module.requests = type("RequestsMock", (), {"get": staticmethod(fake_get)})
    try:
        client = CheaperSalPriceClient("csal_test", limit=13, minimum_interval=0)
        source_pass, stats = SuperPharmBulkImporter(client, request_limit=10).collect()
        assert len(calls) == 7
        assert stats["network_requests"] == 7
        assert len(source_pass["store_rows"]) == 302
        assert stats["branches_geocoded_this_run"] == 301
        assert len(source_pass["price_rows"]) == 200
        assert stats["category_counts"] == {"diapers": 199, "formula": 1}
        assert len(source_pass["promo_rows"]) == 1

        merged = merge_prices_and_promos(
            source_pass["price_rows"], source_pass["promo_rows"]
        )
        promoted = next(row for row in merged if row["barcode"] == baby_rows[0]["barcode"])
        assert promoted["promo_price"] == 35.9
        assert promoted["promo_min_quantity"] == 2
        assert promoted["promo_total_price"] == 71.8
        assert to_db_price(promoted)["branch_code"] == "online"
        assert stats["catalog_cycles_completed"] == 1
    finally:
        price_module.requests = original_requests


def test_v050_daily_limit_stops_immediately_without_retries():
    calls = []

    class DailyQuotaResponse:
        status_code = 429
        ok = False
        text = '{"error":{"code":"DAILY_LIMIT_EXCEEDED"}}'
        headers = {"Retry-After": "60"}

        def json(self):
            return {
                "success": False,
                "error": {"code": "DAILY_LIMIT_EXCEEDED"},
            }

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        return DailyQuotaResponse()

    original_requests = price_module.requests
    price_module.requests = type("RequestsMock", (), {"get": staticmethod(fake_get)})
    try:
        client = CheaperSalPriceClient(
            "csal_test", limit=13, minimum_interval=0, max_rate_retries=3
        )
        source_pass, stats = SuperPharmBulkImporter(client, request_limit=10).collect()
        assert len(calls) == 1
        assert source_pass["price_rows"] == []
        assert stats["quota_error_code"] == "DAILY_LIMIT_EXCEEDED"
        assert stats["quota_reset_at"]
        assert client.remaining == 0
        assert client.provider_retries == 0
    finally:
        price_module.requests = original_requests


def test_v050_worker_saves_locations_without_overwriting_existing_geocoding():
    class Response:
        status_code = 200
        ok = True
        text = "{}"
        headers = {}

        def __init__(self, data):
            self.data = data

        def json(self):
            return {
                "success": True,
                "data": self.data,
                "meta": {"usage": {"remaining": 90}},
            }

    def fake_get(url, **kwargs):
        if url.endswith("/chains"):
            return Response([{"id": "chain-sp", "name": "סופר פארם"}])
        if url.endswith("/chains/chain-sp/branches"):
            return Response({
                "branches": [
                    {
                        "id": "sp-sderot",
                        "name": "סופר-פארם שדרות",
                        "city": "שדרות",
                        "location": {"lat": 31.525, "lon": 34.595},
                    },
                    {
                        "id": SUPER_PHARM_ONLINE_PROVIDER_ID,
                        "name": "סופר-פארם באינטרנט",
                        "isOnline": True,
                    },
                ],
                "total": 2,
            })
        if url.endswith("/categories"):
            return Response([{"name": "תינוקות", "slug": "baby"}])
        if url.endswith("/products"):
            return Response({
                "products": [{
                    "barcode": "7290000195537",
                    "name": "האגיס חיתולים מידה 3 40 יחידות",
                    "brand": "האגיס",
                    "price": 44.9,
                }],
                "total": 1,
            })
        if url.endswith("/promos"):
            return Response({"promos": [], "total": 0})
        raise AssertionError(url)

    class FakeDatabase:
        def __init__(self):
            self.upserts = []
            self.patches = []

        def insert_returning(self, table, row):
            assert table == "feed_ingestion_runs"
            return {"id": "run-1"}

        def select(self, table, params):
            assert table in {"baby_retail_prices", "baby_product_catalog"}
            return []

        def upsert(self, table, rows, on_conflict):
            if rows:
                self.upserts.append((table, rows, on_conflict))

        def patch(self, table, filters, row):
            self.patches.append((table, filters, row))

    original_requests = price_module.requests
    price_module.requests = type("RequestsMock", (), {"get": staticmethod(fake_get)})
    db = FakeDatabase()
    try:
        client = CheaperSalPriceClient("csal_test", limit=13, minimum_interval=0)
        asyncio.run(run_chain(
            "SUPER_PHARM",
            db,
            False,
            20,
            None,
            {"special_retailer_batch": {"batch_size": 48}},
            client,
        ))
        branch_batches = [rows for table, rows, _ in db.upserts if table == "retail_branches"]
        assert len(branch_batches) == 2
        assert branch_batches[0][0]["branch_code"] == "online"
        assert "latitude" not in branch_batches[0][0]
        assert branch_batches[1][0]["branch_code"] == "sp-sderot"
        assert branch_batches[1][0]["latitude"] == 31.525
        assert branch_batches[1][0]["longitude"] == 34.595
        price_batches = [rows for table, rows, _ in db.upserts if table == "baby_retail_prices"]
        assert price_batches[0][0]["branch_code"] == "online"
        run_updates = [row for table, _, row in db.patches if table == "feed_ingestion_runs"]
        assert run_updates[0]["details"]["branches_geocoded"] == 1
        assert run_updates[0]["details"]["super_pharm_bulk"]["baby_prices"] == 1
    finally:
        price_module.requests = original_requests


def test_v050_successful_bulk_bootstrap_uses_four_hour_maintenance():
    previous = {"batch_size": 48, "cycles_completed": 0, "next_cursor": 48}
    committed = _special_batch_with_bulk_result(
        previous, {"baby_prices": 200, "network_requests": 7}
    )
    assert committed["provider_complete"] is True
    assert committed["committed"] is True
    assert committed["bulk_catalog_import"] is True
    assert committed["cycles_completed"] >= 1
    assert committed["lookup_attempted"] == 7


def test_v050_workflows_keep_the_shared_free_api_budget_safe():
    main = (ROOT / ".github/workflows/update-baby-prices.yml").read_text("utf-8")
    special = (ROOT / ".github/workflows/update-special-retailers.yml").read_text("utf-8")
    assert 'cron: "17 * * * *"' in main
    assert 'ENRICHMENT_LIMIT: "0"' in main
    assert 'CHEAPERSAL_IMAGE_LOOKUP_LIMIT: "0"' in main
    assert 'CHEAPERSAL_PRICE_LOOKUP_LIMIT: "13"' in special
    assert 'CHEAPERSAL_BULK_REQUEST_LIMIT: "10"' in special
    assert 'CHEAPERSAL_PROVIDER_REQUEST_RESERVE: "5"' in special
    assert 'SUPER_PHARM_DIRECT_ENABLED: "false"' in special
    assert 'CHEAPERSAL_IMAGE_LOOKUP_LIMIT: "0"' in special
    assert 6 * 13 <= 100


def test_v050_frontend_marks_online_estimates_without_claiming_store_stock():
    frontend = (ROOT / "index.html").read_text("utf-8")
    service = (ROOT / "service-worker.js").read_text("utf-8")
    assert "Dashboard v0.5" in frontend
    assert "SUPER_PHARM_ONLINE_PROVIDER_ID" in frontend
    assert "nearestSuperPharm" in frontend
    assert "online_price_reference:true" in frontend
    assert "in_store_price_verified:false" in frontend
    assert "in_store_stock_verified:false" in frontend
    assert "מחיר וזמינות בסניף אינם מאומתים" in frontend
    assert "אומדן לפי מחיר אונליין" in frontend
    assert "baby-smart-v05" in service
