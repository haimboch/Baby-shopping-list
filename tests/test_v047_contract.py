from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v047_inventory_preserves_purchase_history():
    source = (ROOT / "index.html").read_text(encoding="utf-8")
    worker = (ROOT / "baby_worker" / "product_images.py").read_text(
        encoding="utf-8"
    )

    assert '.eq("household_id",household.id).eq("is_active",true)' in source
    assert '.update({is_active:false,updated_by:session.user.id})' in source
    assert 'id="manageInventory"' in source
    assert 'id="inventoryManager"' in source
    assert '"is_active": "eq.true"' in worker


def test_v047_formula_requires_a_brand_without_automatic_substitution():
    source = (ROOT / "index.html").read_text(encoding="utf-8")

    assert 'key==="formula"&&!preferredProduct' in source
    assert 'product.need_key==="formula"&&!preferred' in source
    assert 'rows=rows.filter(row=>String(row.barcode)===String(product.preferred_barcode))' in source
    assert 'formula?"בחרו יצרן *":"כל המותגים"' in source


def test_v047_partial_baskets_and_savings_are_visible():
    source = (ROOT / "index.html").read_text(encoding="utf-8")

    assert "function buildShoppingBaskets(wanted,nearby,priceRows)" in source
    assert 'status:"missing"' in source
    assert 'status:"replaced"' in source
    assert 'data-basket-choice="suggestion"' in source
    assert 'id="actualSavings"' in source
    assert 'id="potentialSavings"' in source
    assert 'sb.from("household_monthly_savings_v047")' in source


def test_v047_family_choice_and_visible_names():
    source = (ROOT / "index.html").read_text(encoding="utf-8")

    assert 'id="ofamilychoice"' in source
    assert 'id="ocreatefamily"' in source
    assert 'id="oshowjoin"' in source
    assert 'id="shareFamily"' in source
    assert '.select("user_id,role,created_at,display_name")' in source
    assert "data:{full_name:fullName}" in source


def test_v047_sql_is_secure_and_non_destructive():
    sql = (ROOT / "supabase" / "flexible_family_savings_v047.sql").read_text(
        encoding="utf-8"
    )

    assert "add column if not exists is_active boolean not null default true" in sql
    assert "set is_active = false" in sql
    assert "products_one_active_need_v047" in sql
    assert "household_members_one_family_v047" in sql
    assert "with (security_invoker = true)" in sql
    assert "revoke all on public.household_monthly_savings_v047 from anon" in sql
    assert (
        "grant select on public.household_monthly_savings_v047 to authenticated, service_role"
        in sql
    )
