-- Baby Smart List v0.45: baby-only catalog and verified product photographs.
-- Safe to rerun. Irrelevant products are hidden, never deleted.

begin;

alter table public.baby_product_catalog
  add column if not exists image_url text,
  add column if not exists image_source text,
  add column if not exists image_checked_at timestamptz;

create or replace function private.derive_baby_need_key(p_name text)
returns text
language sql
immutable
set search_path = ''
as $$
  select case
    when coalesce(p_name, '') ilike '%שקיות%חיתול%' then 'diaper_bags'
    when coalesce(p_name, '') ilike '%משטח%החתלה%' then 'changing_pads'
    when coalesce(p_name, '') ilike '%משחת%החתלה%' then 'diaper_cream'
    when coalesce(p_name, '') ilike '%מגבונ%' then 'wipes'
    when coalesce(p_name, '') ilike '%תמ״ל%'
      or coalesce(p_name, '') ilike '%תמ"ל%'
      or coalesce(p_name, '') ilike '%פורמולה%' then 'formula'
    when coalesce(p_name, '') ilike '%שמן%אמבט%'
      or coalesce(p_name, '') ilike '%אמול%' then 'bath_oil'
    when coalesce(p_name, '') ilike '%סבון%רחצה%'
      or coalesce(p_name, '') ilike '%שמפו%תינוק%' then 'baby_wash'
    when coalesce(p_name, '') ilike '%קרם%גוף%' then 'body_cream'
    when coalesce(p_name, '') ilike '%כביסה%' then 'baby_laundry'
    when coalesce(p_name, '') ilike '%טיטול%'
      or coalesce(p_name, '') ilike '%חיתול%' then 'diapers'
    else 'other'
  end;
$$;

update public.baby_product_catalog
set need_key = case need_key
  when 'bath_soap' then 'baby_wash'
  when 'laundry' then 'baby_laundry'
end
where need_key in ('bath_soap', 'laundry');

update public.baby_product_catalog
set active = false
where active = true
  and (
    concat_ws(' ', product_name, brand) ~* 'מבוגר|adult|incontinence|דליפת[[:space:]]*שתן'
    or (
      need_key = 'wipes'
      and (
        concat_ws(' ', product_name, brand) !~*
          'תינוק|בייבי|baby|האגיס|huggies|פמפרס|pampers|בייבי.?סיטר|babysitter|infant|newborn|פעוט'
        or concat_ws(' ', product_name, brand) ~*
          'רצפ|טואלט|שירותים|איפור|מטבח|floor|toilet|make.?up|kitchen'
      )
    )
    or (
      need_key = 'formula'
      and concat_ws(' ', product_name, brand) ~*
        'דייס|(^|[^א-ת])מחי(ת|ות)|פאוץ|סקו{0,2}[ייו]*ז|כפית|צידנית|פדיאשור|pediasure|puree|cereal|pouch|spoon'
    )
    or (
      need_key = 'bath_oil'
      and concat_ws(' ', product_name, brand) !~*
        'תינוק|בייבי|baby|אמול|בלנאום|מוסטלה|mustela|infant|פעוט'
    )
    or (
      need_key in ('baby_wash', 'baby_laundry', 'body_cream')
      and concat_ws(' ', product_name, brand) !~*
        'תינוק|בייבי|baby|infant|newborn|פעוט'
    )
  );

create or replace view public.baby_normalized_prices
with (security_invoker = true)
as
select
  p.chain_id,
  p.branch_code,
  p.barcode,
  p.need_key,
  p.dimension_type,
  p.dimension_value,
  p.brand,
  p.product_name,
  p.package_quantity,
  p.package_unit,
  p.regular_price,
  p.promo_price,
  offer.effective_price,
  p.promo_description,
  p.promo_start_at,
  p.promo_end_at,
  p.requires_club,
  p.source_updated_at,
  p.last_seen_at,
  p.raw_source,
  n.canonical_package_quantity,
  n.canonical_package_unit,
  n.quantity_source,
  n.raw_quantity_conflict,
  n.unresolved_quantity_conflict,
  case
    when p.need_key = 'formula'
      and n.canonical_package_unit = 'גרם'
      and n.canonical_package_quantity > 0
      then round(offer.effective_price * 100.0 / n.canonical_package_quantity, 4)
    when p.need_key in ('diaper_cream', 'body_cream')
      and n.canonical_package_unit = 'גרם'
      and n.canonical_package_quantity > 0
      then round(offer.effective_price * 100.0 / n.canonical_package_quantity, 4)
    when p.need_key in ('baby_wash', 'bath_oil', 'baby_laundry')
      and n.canonical_package_unit = 'מ״ל'
      and n.canonical_package_quantity > 0
      then round(offer.effective_price * 100.0 / n.canonical_package_quantity, 4)
    when p.need_key in ('diapers', 'wipes', 'changing_pads', 'diaper_bags')
      and n.canonical_package_unit = 'יחידות'
      and n.canonical_package_quantity > 0
      then round(offer.effective_price / n.canonical_package_quantity, 4)
    when p.need_key not in ('diapers', 'wipes', 'formula')
      then offer.effective_price
    else null::numeric
  end as normalized_unit_price,
  case
    when p.need_key in ('formula', 'diaper_cream', 'body_cream')
      and n.canonical_package_unit = 'גרם'
      and n.canonical_package_quantity > 0
      then '₪ ל-100 גרם'
    when p.need_key in ('baby_wash', 'bath_oil', 'baby_laundry')
      and n.canonical_package_unit = 'מ״ל'
      and n.canonical_package_quantity > 0
      then '₪ ל-100 מ״ל'
    when p.need_key in ('diapers', 'wipes', 'changing_pads', 'diaper_bags')
      and n.canonical_package_unit = 'יחידות'
      and n.canonical_package_quantity > 0
      then '₪ ליחידה'
    when p.need_key not in ('diapers', 'wipes', 'formula')
      then '₪ לאריזה'
    else null::text
  end as normalized_unit_label,
  p.promo_min_quantity,
  p.promo_total_price,
  catalog.image_url
from public.baby_retail_prices p
inner join public.baby_product_catalog catalog
  on catalog.barcode = p.barcode
  and catalog.active = true
  and catalog.need_key = p.need_key
left join public.baby_product_quantity_normalization n on n.barcode = p.barcode
cross join lateral (
  select case
    when p.promo_price is not null
      and p.promo_price > 0
      and p.promo_price < p.regular_price
      and (p.promo_start_at is null or p.promo_start_at <= now())
      and (p.promo_end_at is null or p.promo_end_at >= now())
      then p.promo_price
    else p.regular_price
  end as effective_price
) offer;

grant select on public.baby_normalized_prices to authenticated, service_role;

commit;
