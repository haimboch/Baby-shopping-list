from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v046_frontend_enforces_trustworthy_basket_rules():
    source = (ROOT / "index.html").read_text(encoding="utf-8")

    assert 'const PRICE_STALE_HOURS=36;' in source
    assert '&&priceFreshness(r).eligible' in source
    assert 'key==="other"||key==="formula"?false:$("pallow").checked' in source
    assert 'product.need_key!=="formula"&&product.allow_alternatives!==false' in source
    assert 'baby_mvp_data_health_v046' in source
    assert 'id="mvpReadiness"' in source


def test_v046_sql_view_has_explicit_api_grant_and_invoker_security():
    sql = (ROOT / "supabase" / "mvp_launch_readiness_v046.sql").read_text(
        encoding="utf-8"
    )

    assert "with (security_invoker = true)" in sql
    assert "grant select on public.baby_mvp_data_health_v046 to authenticated, service_role" in sql
    assert "allow_alternatives = false" in sql
