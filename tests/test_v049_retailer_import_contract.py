from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_special_retailer_workflow_is_separate_and_staged():
    main_flow = (ROOT / ".github/workflows/update-baby-prices.yml").read_text("utf-8")
    special = (ROOT / ".github/workflows/update-special-retailers.yml").read_text("utf-8")
    assert 'ENABLED_CHAINS: "shufersal,be,rami_levy,yochananof,osher_ad"' in main_flow
    assert 'ENABLED_CHAINS: "super_pharm,ksp"' in special
    assert 'SPECIAL_RETAILER_BATCH_SIZE: "48"' in special
    assert 'cron: "47 * * * *"' in special
    assert 'SPECIAL_RETAILER_MAINTENANCE_HOURS: "4"' in special
    assert 'CHEAPERSAL_ONLINE_ONLY: "false"' in special
    assert 'CHEAPERSAL_MIN_INTERVAL_SECONDS: "7"' in special
    assert 'group: baby-price-update' in special


def test_v049_has_shared_cache_cursor_and_coverage_diagnostics():
    worker = (ROOT / "baby_worker/worker.py").read_text("utf-8")
    fallback = (ROOT / "baby_worker/cheapersal_prices.py").read_text("utf-8")
    assert "special_retailer_batch" in worker
    assert "SPECIAL_RETAILER_COVERAGE" in worker
    assert "_interleave_catalog_targets" in worker
    assert "class CheaperSalPriceClient" in fallback
    assert "cache_hits" in fallback
    assert "retry_after_seconds" in fallback
    assert "self.remaining -= 1" in fallback
    assert "SPECIAL_RETAILER_AUTO" in worker
    assert "_special_batch_with_lookup_result" in worker
    assert '"committed": committed' in worker


def test_all_product_types_are_accepted_by_official_parsers():
    ksp = (ROOT / "baby_worker/ksp.py").read_text("utf-8")
    superpharm = (ROOT / "baby_worker/superpharm_online.py").read_text("utf-8")
    assert "need_key not in SUPPORTED_PRODUCT_TYPES" in ksp
    assert "need_key not in SUPPORTED_PRODUCT_TYPES" in superpharm
    assert "extract_superpharm_category_urls" in superpharm


def test_no_secret_is_embedded_in_repository_files():
    relay = (ROOT / "cloudflare-ksp-relay/src/index.js").read_text("utf-8")
    workflow = (ROOT / ".github/workflows/update-special-retailers.yml").read_text("utf-8")
    assert "env.RELAY_TOKEN" in relay
    assert "secrets.KSP_RELAY_TOKEN" in workflow
    assert "test-secret" not in workflow
