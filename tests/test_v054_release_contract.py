"""Release contract for v0.54 login, images, push and direct buying."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_returning_login_does_not_repeat_name_field():
    frontend = (ROOT / "index.html").read_text("utf-8")
    assert 'id="signupNameField"' in frontend
    assert 'class="field hidden" id="signupNameField"' in frontend
    assert 'if(!signupMode)return enterSignupMode()' in frontend
    assert 'נכנסים לחשבון הקיים עם אימייל וסיסמה' in frontend


def test_every_device_is_prompted_and_existing_subscription_is_resynced():
    frontend = (ROOT / "index.html").read_text("utf-8")
    worker = (ROOT / "service-worker.js").read_text("utf-8")
    edge = (ROOT / "supabase/functions/send-notification/index.ts").read_text("utf-8")
    assert 'id="pushNudge"' in frontend
    assert "syncExistingPushSubscription" in frontend
    assert 'save_push_subscription_v032' in frontend
    assert 'vibrate:[180,80,180]' in worker
    assert '#buy=' in worker
    assert "TTL: 86400" in edge
    assert "urgency: 'high'" in edge


def test_notification_opens_local_prices_and_safe_alternatives():
    frontend = (ROOT / "index.html").read_text("utf-8")
    assert 'openProductPrices(productId)' in frontend
    assert 'לחצו למחיר הזול ולחלופות באזור' in frontend
    assert '📍 ניווט לסניף' in frontend
    assert '💡 חלופה זולה באזור:' in frontend
    assert 'product.need_key!=="formula"' in frontend
    assert 'product.allow_alternatives!==false' in frontend


def test_image_action_runs_independently_and_uses_verified_barcodes():
    workflow = (ROOT / ".github/workflows/collect-product-images.yml").read_text("utf-8")
    collector = (ROOT / "collect_product_images.py").read_text("utf-8")
    images = (ROOT / "baby_worker/product_images.py").read_text("utf-8")
    assert 'cron: "17 */6 * * *"' in workflow
    assert 'PRODUCT_IMAGE_LOOKUP_LIMIT: "72"' in workflow
    assert "ProductImageEnricher" in collector
    assert "baby_retail_prices" in images
    assert "SUPER_PHARM_IMAGE_BASE" in images
    assert "verified barcode" in images
