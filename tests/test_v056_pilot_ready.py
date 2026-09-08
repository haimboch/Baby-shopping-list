"""Release contract for the v0.56 Pilot Ready layer."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = (ROOT / "index.html").read_text("utf-8")
MIGRATION = (
    ROOT / "supabase/migrations/20260907235608_v056_pilot_ready.sql"
).read_text("utf-8")
WORKER = (ROOT / "service-worker.js").read_text("utf-8")


def test_v056_exposes_pilot_install_feedback_and_privacy_flows():
    assert "Pilot Ready v0.56" in FRONTEND
    assert 'id="pilotReadinessSummary"' in FRONTEND
    assert 'id="installApp"' in FRONTEND
    assert 'id="helpdlg"' in FRONTEND
    assert 'id="privacydlg"' in FRONTEND
    assert 'id="deletionRequests"' in FRONTEND
    assert 'window.addEventListener("beforeinstallprompt"' in FRONTEND
    assert 'window.addEventListener("appinstalled"' in FRONTEND
    assert 'baby-smart-v056-pilot-ready' in WORKER


def test_v056_metrics_are_allowlisted_and_can_be_disabled_on_device():
    assert "const PILOT_EVENTS=new Set" in FRONTEND
    assert "const PILOT_META_KEYS=new Set" in FRONTEND
    assert "safePilotMetadata" in FRONTEND
    assert "pilotAnalyticsEnabled" in FRONTEND
    assert "PILOT_ANALYTICS_KEY" in FRONTEND
    assert 'sb.from("pilot_events").insert' in FRONTEND
    assert "אין בהם סיסמה, אימייל, תוכן חופשי או מיקום מדויק" in FRONTEND


def test_v056_new_tables_use_rls_and_minimal_api_grants():
    assert "create table if not exists public.pilot_events" in MIGRATION
    assert "create table if not exists public.pilot_feedback_reports" in MIGRATION
    assert "create table if not exists public.data_deletion_requests" in MIGRATION
    for table in (
        "pilot_events",
        "pilot_feedback_reports",
        "data_deletion_requests",
    ):
        assert f"alter table public.{table} enable row level security" in MIGRATION
        assert (
            f"revoke all on table public.{table} from public, anon, authenticated"
            in MIGRATION
        )
    assert "grant insert on table public.pilot_events to authenticated" in MIGRATION
    assert "private.is_household_member(household_id)" in MIGRATION
    assert "user_id = (select auth.uid())" in MIGRATION


def test_v056_readiness_rpc_checks_membership_before_household_aggregates():
    assert "function public.get_pilot_readiness_v056" in MIGRATION
    assert "security definer" in MIGRATION
    assert "set search_path = ''" in MIGRATION
    assert "household_access_denied" in MIGRATION
    assert "hm.user_id = v_user" in MIGRATION
    assert (
        "revoke all on function public.get_pilot_readiness_v056(uuid) from public, anon, authenticated"
        in MIGRATION
    )
    assert (
        "grant execute on function public.get_pilot_readiness_v056(uuid) to authenticated, service_role"
        in MIGRATION
    )


def test_v056_household_deletion_requests_are_owner_only_and_reversible():
    assert "private.is_household_owner(household_id)" in MIGRATION
    assert "grant update (status)" in MIGRATION
    assert "status = 'pending'" in MIGRATION
    assert "status = 'cancelled'" in MIGRATION
    assert '.update({status:"cancelled"})' in FRONTEND
    assert '.select("id").maybeSingle()' in FRONTEND
    assert "בקשת המחיקה בוטלה" in FRONTEND


def test_v056_keeps_formula_exact_only_and_push_deep_links():
    assert 'formula:{name:"תמ״ל"' in FRONTEND
    assert "alternatives:false" in FRONTEND
    assert 'allow_alternatives:key!=="formula"' in FRONTEND
    assert '#buy=' in WORKER
    assert 'openProductPrices(product.id)' in FRONTEND
