"""Regression contract for server-verified device Push in v0.55."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v055_only_reports_push_active_after_server_registration():
    frontend = (ROOT / "index.html").read_text("utf-8")
    assert "pushServerRegistered" in frontend
    assert 'id="testPush"' in frontend
    assert "sendPushTest" in frontend
    assert "המכשיר רשום גם בדפדפן וגם בשרת" in frontend
    assert "שני מכשירים או שני פרופילי דפדפן נפרדים" in frontend


def test_v055_test_push_requires_a_real_authenticated_user():
    frontend = (ROOT / "index.html").read_text("utf-8")
    edge = (ROOT / "supabase/functions/send-notification/index.ts").read_text("utf-8")
    assert '"Authorization":`Bearer ${accessToken}`' in frontend
    assert "body:JSON.stringify({action:\"test\"})" in frontend
    assert "admin.auth.getUser(token)" in edge
    assert "authentication_required" in edge
    assert "invalid_session" in edge
    assert "push-test-${crypto.randomUUID()}" in edge


def test_v055_no_subscription_is_recorded_as_a_failure():
    edge = (ROOT / "supabase/functions/send-notification/index.ts").read_text("utf-8")
    assert "push_error: 'no_active_subscription'" in edge
    assert "last_error: 'no_active_subscription'" in edge
    assert "return json({ error: 'no_active_subscription' }, 409)" in edge


def test_v055_forces_a_fresh_service_worker_cache():
    worker = (ROOT / "service-worker.js").read_text("utf-8")
    assert 'const CACHE="baby-smart-v056-pilot-ready"' in worker
