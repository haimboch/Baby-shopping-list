"""Release contract for exact packages, verified photos and device push."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_exact_package_identity_is_visible_and_required():
    frontend = (ROOT / "index.html").read_text("utf-8")

    assert "Pilot Ready v0.56" in frontend
    assert "אריזות וברקודים" in frontend
    assert 'data-select-package=' in frontend
    assert "▥ ברקוד ${esc(p.preferred_barcode)}" in frontend
    assert "!p.preferred_barcode" in frontend
    assert "תמונת האריזה טרם זמינה" in frontend
    assert 'id="packagePhotoInput"' in frontend
    assert 'sb.storage.from("baby-package-images").upload' in frontend


def test_push_ui_preferences_and_product_deep_link_are_active():
    frontend = (ROOT / "index.html").read_text("utf-8")
    worker = (ROOT / "service-worker.js").read_text("utf-8")
    function = (
        ROOT / "supabase/functions/send-notification/index.ts"
    ).read_text("utf-8")

    assert '<div class="push-card">' in frontend
    assert "push_enabled:true" in frontend
    assert "push_enabled:false" in frontend
    assert "handleAppHash" in frontend
    assert "#buy=" in worker
    assert "product_id: notification.product_id" in function


def test_notification_pipeline_has_one_scanner_and_dedupes():
    main = (ROOT / ".github/workflows/update-baby-prices.yml").read_text("utf-8")
    special = (ROOT / ".github/workflows/update-special-retailers.yml").read_text("utf-8")
    scan = (ROOT / ".github/workflows/scan-baby-notifications.yml").read_text("utf-8")
    migration = (
        ROOT
        / "supabase/migrations/20260906_v053_release_notifications_and_dedupe.sql"
    ).read_text("utf-8")

    assert "generate_price_notification_events_v035" not in main
    assert "generate_price_notification_events_v035" not in special
    assert 'workflows: ["Update Baby Prices", "Update KSP and Super-Pharm"]' in scan
    assert "scan_deal_notifications_v032" in scan
    assert "alter table public.notifications enable trigger" in migration
    assert "on conflict (user_id, dedupe_key)" in migration
    assert "where notification_type = 'price_deal'" in migration


def test_official_retailer_pages_feed_verified_image_pipeline():
    superpharm = (ROOT / "baby_worker/superpharm_online.py").read_text("utf-8")
    ksp = (ROOT / "baby_worker/ksp.py").read_text("utf-8")
    special = (ROOT / ".github/workflows/update-special-retailers.yml").read_text("utf-8")
    images = (ROOT / ".github/workflows/collect-product-images.yml").read_text("utf-8")

    for source in (superpharm, ksp):
        assert '"og:image"' in source
        assert '"image_url": image_url' in source
    assert 'CHEAPERSAL_IMAGE_LOOKUP_LIMIT: "0"' in special
    assert 'CHEAPERSAL_IMAGE_LOOKUP_LIMIT: "1"' in images


def test_household_package_photos_are_private_and_scoped():
    migration = (
        ROOT / "supabase/migrations/20260906_v053_private_package_photos.sql"
    ).read_text("utf-8")

    assert "'baby-package-images'" in migration
    assert "false," in migration
    assert "for select to authenticated" in migration
    assert "for insert to authenticated" in migration
    assert "for update to authenticated" in migration
    assert "hm.household_id::text = (storage.foldername(name))[1]" in migration
    assert "hm.user_id = (select auth.uid())" in migration
